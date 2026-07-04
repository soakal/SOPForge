"""Review web UI: library page (search box), per-session page (doc
preview iframe, colored sidecar report, re-render form, read-only config
panel). Plain HTML/JS, no build step, no Node — DOM-asserted via
TestClient, no real browser."""

import re
import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from pipeline.manifest import load_manifest
from pipeline.server import create_app

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _make_client(tmp_path):
    app = create_app(sessions_root=tmp_path / "sessions")
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


def _create_and_wait(client, tmp_path, fixture="sample-manifest.json"):
    manifest_path = FIXTURES / fixture
    manifest = load_manifest(manifest_path)
    shots_dir = tmp_path / f"shots-{fixture}"
    shots_dir.mkdir(exist_ok=True)
    files = []
    for step in manifest.steps:
        p = shots_dir / step.screenshot
        Image.new("RGB", (1920, 1080), (255, 255, 255)).save(p)
        files.append(("files", (step.screenshot, p.open("rb"), "image/png")))
    resp = client.post(
        "/sessions", data={"manifest_json": manifest_path.read_text(encoding="utf-8")}, files=files
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]
    _wait_until_done(client, session_id)
    return session_id


def test_library_page_lists_sessions_and_has_search_form(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_and_wait(client, tmp_path)

    resp = client.get("/ui")
    assert resp.status_code == 200
    assert resp.text.startswith("<!doctype html>")
    assert '<form method="get" action="/ui">' in resp.text
    assert '<input type="text" name="q"' in resp.text
    assert f"/ui/sessions/{session_id}" in resp.text


def test_library_page_search_filters_results(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_and_wait(client, tmp_path)

    all_entries = client.get("/library").json()
    title = next(e["title"] for e in all_entries if e["session_id"] == session_id)

    resp = client.get("/ui", params={"q": "definitely-not-a-real-title"})
    assert f"/ui/sessions/{session_id}" not in resp.text
    assert "No sessions yet." in resp.text

    resp = client.get("/ui", params={"q": title[:6]})
    assert f"/ui/sessions/{session_id}" in resp.text


def test_library_page_empty_state(tmp_path):
    client = _make_client(tmp_path)
    resp = client.get("/ui")
    assert resp.status_code == 200
    assert "No sessions yet." in resp.text


def test_session_page_shows_processing_state_before_done(tmp_path, monkeypatch):
    import threading

    import pipeline.server as server_module

    reached = threading.Event()
    release = threading.Event()
    real_render = server_module.render_steps_template_mode

    def gated_render(*args, **kwargs):
        reached.set()
        release.wait(timeout=5)
        return real_render(*args, **kwargs)

    monkeypatch.setattr(server_module, "render_steps_template_mode", gated_render)

    client = _make_client(tmp_path)
    manifest_path = FIXTURES / "sample-manifest.json"
    manifest = load_manifest(manifest_path)
    shots_dir = tmp_path / "shots"
    shots_dir.mkdir()
    files = []
    for step in manifest.steps:
        p = shots_dir / step.screenshot
        Image.new("RGB", (1920, 1080), (255, 255, 255)).save(p)
        files.append(("files", (step.screenshot, p.open("rb"), "image/png")))
    resp = client.post(
        "/sessions", data={"manifest_json": manifest_path.read_text(encoding="utf-8")}, files=files
    )
    session_id = resp.json()["session_id"]

    assert reached.wait(timeout=5)
    page = client.get(f"/ui/sessions/{session_id}")
    assert page.status_code == 200
    assert 'data-status="processing"' in page.text or 'data-status="queued"' in page.text

    release.set()
    _wait_until_done(client, session_id)


def test_doc_preview_iframe_image_references_actually_resolve(tmp_path):
    """Regression: doc.html's images are relative filenames (task-12's
    base_dir=annotated_dir), so a browser rendering the /ui iframe would
    resolve <img src="001.png"> against /sessions/{id}/001.png (same
    directory as doc.html itself) — this must not 404, or the preview
    shows every screenshot broken."""
    client = _make_client(tmp_path)
    session_id = _create_and_wait(client, tmp_path)

    doc_html = client.get(f"/sessions/{session_id}/doc.html").text
    img_srcs = re.findall(r'<img src="([^"]+)"', doc_html)
    assert img_srcs, "expected at least one <img> tag in doc.html"
    for src in img_srcs:
        img_resp = client.get(f"/sessions/{session_id}/{src}")
        assert img_resp.status_code == 200, f"{src} did not resolve"
        assert img_resp.headers["content-type"].startswith("image/")


def test_specific_routes_are_not_shadowed_by_the_image_catch_all_route(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_and_wait(client, tmp_path)

    for path in (
        "doc.md",
        "doc.pdf",
        "doc.docx",
        "doc.single.html",
        "export.md.zip",
        "report",
        "review",
        "status",
    ):
        resp = client.get(f"/sessions/{session_id}/{path}")
        assert resp.status_code == 200, path


def test_image_route_rejects_path_traversal(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_and_wait(client, tmp_path)

    for attempt in ("..%2f..%2fescape.png", "..%5cescape.png", "escape.png%00.png"):
        resp = client.get(f"/sessions/{session_id}/{attempt}")
        assert resp.status_code == 404, attempt


def test_session_page_shows_doc_preview_iframe(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_and_wait(client, tmp_path)

    resp = client.get(f"/ui/sessions/{session_id}")
    assert resp.status_code == 200
    assert f'<iframe src="/sessions/{session_id}/doc.html"' in resp.text


def test_session_page_colors_sidecar_sections_correctly(tmp_path):
    client = _make_client(tmp_path)
    # sample-manifest.json's step-003 has empty element metadata -> that
    # section should be yellow; template_fallback_steps is always empty in
    # template mode -> green; verify_claims is empty here too -> green.
    session_id = _create_and_wait(client, tmp_path)

    resp = client.get(f"/ui/sessions/{session_id}")
    matches = re.findall(r'data-status="(\w+)"[^<]*<h2>([^<]+)</h2>', resp.text)
    title_to_status = {title: status for status, title in matches}
    assert title_to_status["Template-fallback steps"] == "green"
    assert title_to_status["Verify claims"] == "green"
    assert title_to_status["Empty-metadata steps"] == "yellow"


def test_session_page_has_rerender_form(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_and_wait(client, tmp_path)

    resp = client.get(f"/ui/sessions/{session_id}")
    assert f'<form method="post" action="/sessions/{session_id}/rerender">' in resp.text
    assert "<button" in resp.text


def test_rerender_form_submission_actually_rerenders(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_and_wait(client, tmp_path)

    resp = client.post(f"/sessions/{session_id}/rerender")
    assert resp.status_code == 200
    _wait_until_done(client, session_id)

    page = client.get(f"/ui/sessions/{session_id}")
    assert page.status_code == 200


def test_session_page_shows_config_panel(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_and_wait(client, tmp_path)

    resp = client.get(f"/ui/sessions/{session_id}")
    config = client.get("/config").json()
    assert "Config (read-only)" in resp.text
    assert config["steps"]["model"] in resp.text
    assert config["narrative"]["model"] in resp.text


def test_unknown_session_ui_page_returns_404(tmp_path):
    client = _make_client(tmp_path)
    resp = client.get("/ui/sessions/does-not-exist")
    assert resp.status_code == 404
