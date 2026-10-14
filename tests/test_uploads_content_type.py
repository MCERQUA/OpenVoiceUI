"""
/uploads/ content types (2026-10-14). F-5 (2026-07-15) serves every non-media upload as a text/plain
attachment so html/svg/js can never render in our origin. That also served office files as
text/plain: an .xlsx permit sheet reached a client's phone labelled text/plain + nosniff, which a
mobile browser may show as raw bytes instead of offering the spreadsheet app. Cases come from the
F-5 spec + the new requirement, not from the implementation:
  - html / svg / xml / js stay text/plain attachments with the sandbox CSP (F-5 must hold)
  - known-safe media stay inline with their real type and no sandbox (2026-08-02)
  - office / archive / csv files: real type, still an ATTACHMENT, still nosniff + the sandbox CSP
"""
import pytest


@pytest.fixture()
def up(tmp_path, monkeypatch):
    import routes.static_files as sf
    files = {
        "sheet.xlsx": b"PK\x03\x04xlsx", "SHEET2.XLSX": b"PK\x03\x04xlsx", "doc.docx": b"PK\x03\x04docx",
        "deck.pptx": b"PK\x03\x04pptx", "old.xls": b"\xd0\xcf\x11\xe0", "data.csv": b"a,b\n1,2\n",
        "bundle.zip": b"PK\x03\x04", "page.html": b"<script>alert(1)</script>",
        "pic.svg": b"<svg onload=alert(1)>", "feed.xml": b"<x/>", "app.js": b"alert(1)",
        "logo.png": b"\x89PNG\r\n\x1a\n", "report.pdf": b"%PDF-1.4",
    }
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    monkeypatch.setattr(sf, "UPLOADS_DIR", tmp_path)
    monkeypatch.setattr(sf, "UPLOADS_SHADOW_LOG", tmp_path / ".uploads-auth-shadow.jsonl")
    monkeypatch.setattr(sf, "UPLOADS_REQUIRE_AUTH", False)
    return sf


@pytest.fixture()
def client(up):
    from app import create_app
    app, _ = create_app(config_override={"TESTING": True})
    if "static_files" not in app.blueprints:
        app.register_blueprint(up.static_files_bp)
    return app.test_client()


@pytest.mark.parametrize("name,mime", [
    ("sheet.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("SHEET2.XLSX", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("doc.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("deck.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ("old.xls", "application/vnd.ms-excel"),
    ("data.csv", "text/csv"),
    ("bundle.zip", "application/zip"),
])
def test_office_files_keep_real_type_as_locked_attachment(client, name, mime):
    r = client.get(f"/uploads/{name}")
    assert r.status_code == 200
    assert r.headers["Content-Type"].split(";")[0].strip() == mime
    assert r.headers["Content-Disposition"].startswith("attachment;")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in r.headers["Content-Security-Policy"]


@pytest.mark.parametrize("name", ["page.html", "pic.svg", "feed.xml", "app.js"])
def test_renderable_types_still_text_plain_attachment(client, name):
    r = client.get(f"/uploads/{name}")
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/plain")
    assert r.headers["Content-Disposition"].startswith("attachment;")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in r.headers["Content-Security-Policy"]


@pytest.mark.parametrize("name,mime", [("logo.png", "image/png"), ("report.pdf", "application/pdf")])
def test_media_still_inline_without_sandbox(client, name, mime):
    r = client.get(f"/uploads/{name}")
    assert r.status_code == 200
    assert r.headers["Content-Type"].split(";")[0] == mime
    assert "attachment" not in r.headers.get("Content-Disposition", "")
    assert "sandbox" not in r.headers["Content-Security-Policy"]
