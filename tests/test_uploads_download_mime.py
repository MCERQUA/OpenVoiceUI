"""
/uploads/ content types for DOWNLOAD formats (2026-10-12).

The F-5 stored-XSS guard serves every non-media upload as a text/plain attachment so an
uploaded .html/.svg/.js can never render in our origin. That also caught inert office
formats: an .xlsx went out as `text/plain; charset=utf-8`, and iOS Safari (opened from the
Messages thread) showed the spreadsheet as mojibake and saved it as "<name>.xlsx.txt"
(a client's weekly report sheet).

Contract, from the guard's own intent rather than from the fix:
  * office documents / CSV / zip keep their REAL mime, still as an ATTACHMENT, still with
    nosniff and the locked-down sandbox CSP (they are downloads, never rendered inline);
  * anything that CAN execute (html, svg, js, xml) is still a text/plain attachment;
  * unknown extensions still default to text/plain (fail closed);
  * inline media is unchanged.
"""
import pytest

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import routes.static_files as sf
    files = {
        "reports/weekly-report-2026-10-12.xlsx": b"PK\x03\x04xlsx",
        "brief.docx": b"PK\x03\x04docx",
        "deck.pptx": b"PK\x03\x04pptx",
        "legacy.xls": b"\xd0\xcf\x11\xe0",
        "leads.csv": b"name,phone\nA,1\n",
        "bundle.zip": b"PK\x03\x04",
        "evil.html": b"<script>alert(1)</script>",
        "evil.svg": b"<svg onload=alert(1)></svg>",
        "evil.js": b"alert(1)",
        "evil.xml": b"<x/>",
        "notes.weird": b"???",
        "logo.png": b"\x89PNG\r\n\x1a\n",
        "report.pdf": b"%PDF-1.4",
        "PERMITS-UPPER.XLSX": b"PK\x03\x04xlsx",
    }
    for name, data in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    monkeypatch.setattr(sf, "UPLOADS_DIR", tmp_path)
    monkeypatch.setattr(sf, "UPLOADS_SHADOW_LOG", tmp_path / ".uploads-auth-shadow.jsonl")
    monkeypatch.setattr(sf, "UPLOADS_REQUIRE_AUTH", False)
    from app import create_app
    app, _ = create_app(config_override={"TESTING": True})
    if "static_files" not in app.blueprints:
        app.register_blueprint(sf.static_files_bp)
    return app.test_client()


@pytest.mark.parametrize("path,mime", [
    ("reports/weekly-report-2026-10-12.xlsx", XLSX),
    ("PERMITS-UPPER.XLSX", XLSX),                     # extension match is case-insensitive
    ("brief.docx", DOCX),
    ("deck.pptx", PPTX),
    ("legacy.xls", "application/vnd.ms-excel"),
    ("leads.csv", "text/csv"),
    ("bundle.zip", "application/zip"),
])
def test_download_formats_keep_their_real_mime_as_attachment(client, path, mime):
    r = client.get("/uploads/" + path)
    assert r.status_code == 200
    assert r.headers["Content-Type"].split(";")[0] == mime
    name = path.rsplit("/", 1)[-1]
    assert r.headers["Content-Disposition"] == f'attachment; filename="{name}"'
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in r.headers["Content-Security-Policy"]


@pytest.mark.parametrize("path", ["evil.html", "evil.svg", "evil.js", "evil.xml", "notes.weird"])
def test_executable_and_unknown_types_still_downgraded(client, path):
    r = client.get("/uploads/" + path)
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert r.headers["Content-Disposition"].startswith("attachment;")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in r.headers["Content-Security-Policy"]


@pytest.mark.parametrize("path,mime", [("logo.png", "image/png"), ("report.pdf", "application/pdf")])
def test_inline_media_unchanged(client, path, mime):
    r = client.get("/uploads/" + path)
    assert r.status_code == 200
    assert r.headers["Content-Type"].split(";")[0] == mime
    assert not r.headers.get("Content-Disposition", "").startswith("attachment")
    assert "sandbox" not in r.headers["Content-Security-Policy"]
