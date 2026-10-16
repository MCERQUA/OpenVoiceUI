"""Deployment hosts in the Content-Security-Policy come from CSP_TRUSTED_HOSTS (2026-10-16).

The app CSP (app.py) and the canvas-page CSP (routes/canvas.py) had the maintainers' hosted
domain written into script-src, style-src, connect-src and frame-src, so every public install
trusted another party's subdomains. A stock install now trusts no extra host; a deployment
lists its own in CSP_TRUSTED_HOSTS.

Also pinned here: the app CSP's img-src had a stray ';' that cut the map-tile hosts out of it,
and no separator after them, which glued "media-src" onto the last host so media-src never
applied.
"""
import pytest

KNOWN_DIRECTIVES = {
    'default-src', 'script-src', 'style-src', 'img-src', 'font-src', 'media-src', 'connect-src',
    'frame-src', 'worker-src', 'object-src', 'frame-ancestors', 'base-uri', 'form-action',
    'manifest-src', 'child-src', 'sandbox',
}


def _directives(csp: str) -> dict:
    out = {}
    for part in csp.split(';'):
        tokens = part.split()
        if tokens:
            out[tokens[0]] = tokens[1:]
    return out


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import routes.canvas as canvas
    (tmp_path / 'p.html').write_text('<html><head></head><body>x</body></html>')
    monkeypatch.setattr(canvas, 'CANVAS_PAGES_DIR', tmp_path)
    monkeypatch.setattr(canvas, 'CANVAS_REQUIRE_AUTH', False)
    from app import create_app
    app, _ = create_app(config_override={'TESTING': True})
    if 'canvas' not in app.blueprints:
        app.register_blueprint(canvas.canvas_bp)
    return app.test_client()


def _csps(client):
    app_csp = client.get('/health/live').headers['Content-Security-Policy']
    page = client.get('/pages/p.html')
    assert page.status_code == 200
    return app_csp, page.headers['Content-Security-Policy']


def test_stock_install_trusts_no_deployment_host(client, monkeypatch):
    monkeypatch.delenv('CSP_TRUSTED_HOSTS', raising=False)
    for csp in _csps(client):
        assert 'jam-bot' not in csp, csp


def test_app_csp_parses_into_known_directives(client, monkeypatch):
    monkeypatch.delenv('CSP_TRUSTED_HOSTS', raising=False)
    app_csp, page_csp = _csps(client)
    for csp in (app_csp, page_csp):
        unknown = set(_directives(csp)) - KNOWN_DIRECTIVES
        assert not unknown, f'not CSP directives (a missing or stray ";"): {unknown}'
    d = _directives(app_csp)
    assert d['media-src'] == ["'self'", 'blob:']
    assert 'https://*.tile.openstreetmap.org' in d['img-src']


def test_configured_hosts_reach_every_directive_that_needs_them(client, monkeypatch):
    monkeypatch.setenv('CSP_TRUSTED_HOSTS', 'https://*.example.test, https://games.example.test')
    app_csp, page_csp = _csps(client)
    a, p = _directives(app_csp), _directives(page_csp)
    for host in ('https://*.example.test', 'https://games.example.test'):
        assert host in a['script-src'] and host in a['frame-src']
        for directive in ('script-src', 'style-src', 'connect-src', 'frame-src'):
            assert host in p[directive], (directive, p[directive])
    assert 'wss://*.example.test' in p['connect-src']


def test_invalid_entries_cannot_inject_sources_or_directives(client, monkeypatch):
    monkeypatch.setenv('CSP_TRUSTED_HOSTS',
                       "https://ok.example.test 'unsafe-hashes' *; https://evil.test;script-src * "
                       "data: https://x.test/path")
    app_csp, page_csp = _csps(client)
    for csp in (app_csp, page_csp):
        assert 'https://ok.example.test' in csp
        for bad in ("'unsafe-hashes'", 'evil.test', 'x.test/path'):
            assert bad not in csp, (bad, csp)
        assert set(_directives(csp)) <= KNOWN_DIRECTIVES
