"""Server export download endpoints (AC1 rollup): /doc.docx, /doc.pdf,
/doc.single.html, /export.md.zip — each with correct content-type and
content-disposition, serving structurally valid content.

Generation runs on a background job (task-05), so session creation polls
/status until "done" before any export endpoint is hit."""

import io
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader

from pipeline.doc_meta import DocMeta, download_basename
from pipeline.manifest import load_manifest
from pipeline.server import create_app

from _stub_llm import stub_llm_client_factory

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _base(manifest):
    """6g download base name: SOP Factory style title + revision."""
    title = manifest.session.title or manifest.session.id
    return download_basename(DocMeta(title, None, "", "", "1.0"))


def _make_client(tmp_path):
    app = create_app(
        sessions_root=tmp_path / "sessions",
        llm_client_factory=stub_llm_client_factory,
        narrative_llm_client_factory=stub_llm_client_factory,
    )
    return TestClient(app)


def _wait_until_done(client, session_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/sessions/{session_id}/status").json()
        if status["status"] == "done":
            return
        if status["status"] == "error":
            raise AssertionError(f"session failed: {status.get('error')}")
        time.sleep(0.05)
    raise AssertionError(f"session {session_id} never reached done")


def _create_session(tmp_path):
    client = _make_client(tmp_path)
    manifest_path = FIXTURES / "sample-manifest.json"
    manifest = load_manifest(manifest_path)
    shots_dir = tmp_path / "shots"
    shots_dir.mkdir(exist_ok=True)
    files = []
    for step in manifest.steps:
        p = shots_dir / step.screenshot
        Image.new("RGB", (1920, 1080), (255, 255, 255)).save(p)
        files.append(("files", (step.screenshot, p.open("rb"), "image/png")))
    resp = client.post(
        "/sessions",
        data={"manifest_json": manifest_path.read_text(encoding="utf-8")},
        files=files,
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]
    _wait_until_done(client, session_id)
    return client, session_id, manifest


def test_doc_docx_endpoint(tmp_path):
    client, session_id, manifest = _create_session(tmp_path)
    resp = client.get(f"/sessions/{session_id}/doc.docx")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    # Downloaded filename reflects the session's title/id, not a generic
    # "doc.docx" every session would otherwise share.
    base = _base(manifest)
    assert f'filename="{base}.docx"' in resp.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert "word/document.xml" in zf.namelist()


def test_doc_pdf_endpoint(tmp_path):
    client, session_id, manifest = _create_session(tmp_path)
    resp = client.get(f"/sessions/{session_id}/doc.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/pdf")
    base = _base(manifest)
    assert f'filename="{base}.pdf"' in resp.headers["content-disposition"]
    assert resp.content[:5] == b"%PDF-"
    assert len(PdfReader(io.BytesIO(resp.content)).pages) > len(manifest.steps)


def test_doc_single_html_endpoint(tmp_path):
    client, session_id, manifest = _create_session(tmp_path)
    resp = client.get(f"/sessions/{session_id}/doc.single.html")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    base = _base(manifest)
    assert f'filename="{base}.html"' in resp.headers["content-disposition"]
    assert resp.text.startswith("<!doctype html>")
    assert "data:image/png;base64," in resp.text


def test_export_md_zip_endpoint(tmp_path):
    client, session_id, manifest = _create_session(tmp_path)
    resp = client.get(f"/sessions/{session_id}/export.md.zip")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/zip")
    base = _base(manifest)
    assert f'filename="{base}_Markdown.zip"' in resp.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        assert any(name.endswith(".md") for name in names)
        for step in manifest.steps:
            assert any(name.endswith(f"images/{step.screenshot}") for name in names)


def test_unknown_session_404s_on_all_export_endpoints(tmp_path):
    client = _make_client(tmp_path)
    for path in ("doc.docx", "doc.pdf", "doc.single.html", "export.md.zip"):
        resp = client.get(f"/sessions/does-not-exist/{path}")
        assert resp.status_code == 404, path


def test_walkthrough_endpoint(tmp_path):
    """Phase 6d: every capture session also ships a click-through walkthrough
    with one entry per step, served as a download like the other exports."""
    client, session_id, manifest = _create_session(tmp_path)
    resp = client.get(f"/sessions/{session_id}/walkthrough.html")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    base = _base(manifest)
    assert f'filename="{base}_Walkthrough.html"' in resp.headers["content-disposition"]
    for step in manifest.steps:
        assert f'"id": "{step.id}"' in resp.text


def test_spl_endpoint(tmp_path):
    """Phase 6f: every session also ships the SPL PowerPoint deck."""
    client, session_id, manifest = _create_session(tmp_path)
    resp = client.get(f"/sessions/{session_id}/spl.pptx")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    base = _base(manifest)
    assert f'filename="{base}_SPL.pptx"' in resp.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert "ppt/presentation.xml" in zf.namelist()
    report = client.get(f"/sessions/{session_id}/report").json()
    assert "spl_error" not in report
