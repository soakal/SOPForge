"""sopforge-server FastAPI app: POST /sessions (manifest + PNGs), GET
status/report, doc downloads, and the plain-HTML review page — end to end
through a real TestClient, with the shared stub LLM client
(tests/pipeline/_stub_llm.py) injected via create_app's llm_client_factory
so step generation never makes a real network call here.

Generation runs on a background job (task-05), so POST /sessions returns
"queued"/"processing" immediately — tests poll /status until "done" (or a
terminal "error") before asserting on downstream endpoints."""

import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from pipeline.manifest import load_manifest
from pipeline.server import create_app

from _stub_llm import stub_llm_client_factory

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _make_client(tmp_path):
    app = create_app(
        sessions_root=tmp_path / "sessions", llm_client_factory=stub_llm_client_factory
    )
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


def _wait_for_terminal_status(client, session_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/sessions/{session_id}/status").json()
        if status["status"] in ("done", "error"):
            return status
        time.sleep(0.05)
    raise AssertionError(f"session {session_id} never reached a terminal status")


def _create_and_wait(client, tmp_path, fixture="sample-manifest.json"):
    session_id = _create_session(client, tmp_path, fixture).json()["session_id"]
    status = _wait_for_terminal_status(client, session_id)
    return session_id, status


def test_create_session_and_check_status(tmp_path):
    client = _make_client(tmp_path)
    resp = _create_session(client, tmp_path)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("queued", "processing", "done")
    session_id = body["session_id"]

    status = _wait_for_terminal_status(client, session_id)
    assert status == {"status": "done"}


def test_get_report_lists_expected_categories(tmp_path):
    client = _make_client(tmp_path)
    session_id, status = _create_and_wait(client, tmp_path)
    assert status["status"] == "done"

    report_resp = client.get(f"/sessions/{session_id}/report")
    assert report_resp.status_code == 200
    report = report_resp.json()
    assert {"template_fallback_steps", "verify_claims", "empty_metadata_steps"} <= set(report)
    # sample-manifest.json's step-003 has empty element metadata.
    assert "step-003" in report["empty_metadata_steps"]


def test_get_doc_md_and_html(tmp_path):
    client = _make_client(tmp_path)
    session_id, status = _create_and_wait(client, tmp_path)
    assert status["status"] == "done"

    md_resp = client.get(f"/sessions/{session_id}/doc.md")
    assert md_resp.status_code == 200
    assert md_resp.text.startswith("# ")

    html_resp = client.get(f"/sessions/{session_id}/doc.html")
    assert html_resp.status_code == 200
    assert html_resp.text.startswith("<!doctype html>")


def test_review_page_renders_sidecar_report(tmp_path):
    client = _make_client(tmp_path)
    session_id, status = _create_and_wait(client, tmp_path)
    assert status["status"] == "done"

    review_resp = client.get(f"/sessions/{session_id}/review")
    assert review_resp.status_code == 200
    assert "step-003" in review_resp.text  # empty-metadata step surfaced
    assert review_resp.text.startswith("<!doctype html>")


def test_report_and_doc_endpoints_409_while_not_done(tmp_path, monkeypatch):
    """Requesting a doc before the background job finishes must be a clear
    409, never a crash or a silently-empty/partial response. Forced
    deterministic via a gated stub (not a timing race against the real
    pipeline) — mirrors test_jobs.py's Event pattern."""
    import threading

    import pipeline.server as server_module

    reached = threading.Event()
    release = threading.Event()
    real_render = server_module.render_steps_llm_mode

    def gated_render(*args, **kwargs):
        reached.set()
        release.wait(timeout=5)
        return real_render(*args, **kwargs)

    monkeypatch.setattr(server_module, "render_steps_llm_mode", gated_render)

    client = _make_client(tmp_path)
    session_id = _create_session(client, tmp_path).json()["session_id"]

    assert reached.wait(timeout=5)
    status = client.get(f"/sessions/{session_id}/status").json()
    assert status["status"] in ("queued", "processing")
    resp = client.get(f"/sessions/{session_id}/report")
    assert resp.status_code == 409

    release.set()
    status = _wait_for_terminal_status(client, session_id)
    assert status["status"] == "done"


def test_unknown_session_returns_404_on_every_endpoint(tmp_path):
    client = _make_client(tmp_path)
    for path in ("status", "report", "doc.md", "doc.html", "review"):
        resp = client.get(f"/sessions/does-not-exist/{path}")
        assert resp.status_code == 404, path


def test_rerender_endpoint_requeues_and_reaches_done_again(tmp_path):
    client = _make_client(tmp_path)
    session_id, status = _create_and_wait(client, tmp_path)
    assert status["status"] == "done"

    rerender_resp = client.post(f"/sessions/{session_id}/rerender")
    assert rerender_resp.status_code == 200
    assert rerender_resp.json()["status"] in ("queued", "processing", "done")

    status = _wait_for_terminal_status(client, session_id)
    assert status == {"status": "done"}

    # Downstream content still there after re-render.
    report_resp = client.get(f"/sessions/{session_id}/report")
    assert report_resp.status_code == 200


def test_rerender_unknown_session_returns_404(tmp_path):
    client = _make_client(tmp_path)
    resp = client.post("/sessions/does-not-exist/rerender")
    assert resp.status_code == 404


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
    assert resp.status_code == 200  # upload accepted; generation fails in the background
    session_id = resp.json()["session_id"]
    status = _wait_for_terminal_status(client, session_id)
    # The manifest's real screenshots are still missing (only the
    # traversal filename was uploaded), so generation fails loudly...
    assert status["status"] == "error"
    # ...and, critically, nothing was ever written outside sessions_root.
    sessions_root = tmp_path / "sessions"
    escaped = sessions_root / "escape.png"
    assert not escaped.exists()
    for path in sessions_root.rglob("escape.png"):
        assert path.parent.name == "screenshots"


def test_stop_endpoint_triggers_process_exit_without_terminating_this_test(tmp_path, monkeypatch):
    """The process-stop endpoint (task-10) must call the real process-exit
    primitive to actually stop the frozen EXE — but calling the real one
    here would terminate the pytest process running this test. Replace it
    with a recording stub so the code path is genuinely exercised without
    ending anything."""
    import time as time_module

    import pipeline.server as server_module

    exit_calls = []
    monkeypatch.setattr(server_module.os, "_exit", lambda code: exit_calls.append(code))

    client = _make_client(tmp_path)
    resp = client.post("/shutdown")
    assert resp.status_code == 200
    assert resp.json() == {"status": "shutting down"}

    deadline = time_module.monotonic() + 2.0
    while time_module.monotonic() < deadline and not exit_calls:
        time_module.sleep(0.02)
    assert exit_calls == [0]
