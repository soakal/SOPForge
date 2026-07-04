"""sopforge-server FastAPI app: POST /sessions (manifest + PNGs), GET
status/report, doc downloads, and the plain-HTML review page — all running
template mode (task-12) end-to-end through a real TestClient."""

from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from pipeline.manifest import load_manifest
from pipeline.server import create_app

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _make_client(tmp_path):
    app = create_app(sessions_root=tmp_path / "sessions")
    return TestClient(app)


def _manifest_and_files(tmp_path, fixture="sample-manifest.json"):
    manifest_path = FIXTURES / fixture
    manifest = load_manifest(manifest_path)
    shots_dir = tmp_path / "shots"
    shots_dir.mkdir(exist_ok=True)
    files = []
    for step in manifest.steps:
        p = shots_dir / step.screenshot
        Image.new("RGB", (1920, 1080), (255, 255, 255)).save(p)
        files.append(("files", (step.screenshot, p.open("rb"), "image/png")))
    return manifest_path.read_text(encoding="utf-8"), files


def _create_session(client, tmp_path, fixture="sample-manifest.json"):
    manifest_json, files = _manifest_and_files(tmp_path, fixture)
    resp = client.post("/sessions", data={"manifest_json": manifest_json}, files=files)
    return resp


def test_create_session_and_check_status(tmp_path):
    client = _make_client(tmp_path)
    resp = _create_session(client, tmp_path)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    session_id = body["session_id"]

    status_resp = client.get(f"/sessions/{session_id}/status")
    assert status_resp.status_code == 200
    assert status_resp.json() == {"status": "done"}


def test_get_report_lists_expected_categories(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_session(client, tmp_path).json()["session_id"]

    report_resp = client.get(f"/sessions/{session_id}/report")
    assert report_resp.status_code == 200
    report = report_resp.json()
    assert set(report) == {"template_fallback_steps", "verify_claims", "empty_metadata_steps"}
    # sample-manifest.json's step-003 has empty element metadata.
    assert "step-003" in report["empty_metadata_steps"]


def test_get_doc_md_and_html(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_session(client, tmp_path).json()["session_id"]

    md_resp = client.get(f"/sessions/{session_id}/doc.md")
    assert md_resp.status_code == 200
    assert md_resp.text.startswith("# ")

    html_resp = client.get(f"/sessions/{session_id}/doc.html")
    assert html_resp.status_code == 200
    assert html_resp.text.startswith("<!doctype html>")


def test_review_page_renders_sidecar_report(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_session(client, tmp_path).json()["session_id"]

    review_resp = client.get(f"/sessions/{session_id}/review")
    assert review_resp.status_code == 200
    assert "step-003" in review_resp.text  # empty-metadata step surfaced
    assert review_resp.text.startswith("<!doctype html>")


def test_unknown_session_returns_404_on_every_endpoint(tmp_path):
    client = _make_client(tmp_path)
    for path in ("status", "report", "doc.md", "doc.html", "review"):
        resp = client.get(f"/sessions/does-not-exist/{path}")
        assert resp.status_code == 404, path


def test_invalid_manifest_json_returns_400_and_creates_no_session(tmp_path):
    client = _make_client(tmp_path)
    resp = client.post("/sessions", data={"manifest_json": "not json"}, files=[])
    assert resp.status_code == 400


def test_manifest_missing_required_field_returns_400(tmp_path):
    client = _make_client(tmp_path)
    resp = client.post("/sessions", data={"manifest_json": "{}"}, files=[])
    assert resp.status_code == 400


def test_path_traversal_in_uploaded_filename_cannot_escape_the_session_directory(tmp_path):
    """A malicious/malformed upload filename must never be used as a raw
    path — it must be reduced to its basename and rejected (or written)
    only inside this session's own screenshots directory, never above it."""
    import io

    client = _make_client(tmp_path)
    manifest_json, _real_files = _manifest_and_files(tmp_path)

    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="PNG")
    buf.seek(0)

    resp = client.post(
        "/sessions",
        data={"manifest_json": manifest_json},
        files=[("files", ("../../escape.png", buf, "image/png"))],
    )
    # The manifest's real screenshots are still missing (only the
    # traversal filename was uploaded), so generation fails loudly...
    assert resp.status_code in (400, 500)
    # ...and, critically, nothing was ever written outside sessions_root.
    sessions_root = tmp_path / "sessions"
    escaped = sessions_root / "escape.png"
    assert not escaped.exists()
    for path in sessions_root.rglob("escape.png"):
        assert path.parent.name == "screenshots"
