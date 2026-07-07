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

from _stub_llm import stub_llm_client_factory

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _make_client(tmp_path):
    app = create_app(
        sessions_root=tmp_path / "sessions", llm_client_factory=stub_llm_client_factory
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


def test_root_path_serves_the_same_library_page(tmp_path):
    """GET / is the frozen EXE's health/UI-smoke target (task-10) — it
    must serve real UI markup, not a 404 or a bare JSON welcome message."""
    client = _make_client(tmp_path)
    session_id = _create_and_wait(client, tmp_path)

    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.text.startswith("<!doctype html>")
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
    real_render = server_module.render_steps_llm_mode

    def gated_render(*args, **kwargs):
        reached.set()
        release.wait(timeout=5)
        return real_render(*args, **kwargs)

    monkeypatch.setattr(server_module, "render_steps_llm_mode", gated_render)

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
    # While still processing, the page must auto-refresh so it turns into the
    # finished review page on its own -- otherwise the user is stuck on a stale
    # "processing" snapshot forever.
    assert 'http-equiv="refresh"' in page.text

    release.set()
    _wait_until_done(client, session_id)

    # Once done, the page is the real review page and no longer auto-refreshes.
    done_page = client.get(f"/ui/sessions/{session_id}")
    assert 'http-equiv="refresh"' not in done_page.text
    assert "Downloads" in done_page.text


def test_processing_page_refreshes_while_pending_but_not_on_error():
    from pipeline.webui.pages import render_session_processing_page

    for state in ("queued", "processing"):
        page = render_session_processing_page("sid", {"status": state})
        assert 'http-equiv="refresh"' in page, state

    # A terminal error must NOT keep refreshing (nothing left to wait for) and
    # must surface the error text.
    err = render_session_processing_page("sid", {"status": "error", "error": "boom"})
    assert 'http-equiv="refresh"' not in err
    assert "boom" in err


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
    # section is yellow. Step generation is LLM-backed now; the stub LLM
    # client (tests/pipeline/_stub_llm.py) deterministically fails every
    # step's round-trip check, so every step falls back to its template ->
    # "Template-fallback steps" is genuinely red here. "Verify claims" is
    # green regardless -- narration/claim-coverage still isn't wired into
    # the live server's request path at all.
    session_id = _create_and_wait(client, tmp_path)

    resp = client.get(f"/ui/sessions/{session_id}")
    matches = re.findall(r'data-status="(\w+)"[^<]*<h2>([^<]+)</h2>', resp.text)
    title_to_status = {title: status for status, title in matches}
    assert title_to_status["Template-fallback steps"] == "red"
    assert title_to_status["Verify claims"] == "green"
    assert title_to_status["Empty-metadata steps"] == "yellow"


def test_session_page_has_rerender_form(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_and_wait(client, tmp_path)

    resp = client.get(f"/ui/sessions/{session_id}")
    assert f'<form method="post" action="/ui/sessions/{session_id}/rerender">' in resp.text
    assert "<button" in resp.text


def test_session_page_download_links_all_resolve(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_and_wait(client, tmp_path)

    resp = client.get(f"/ui/sessions/{session_id}")
    hrefs = re.findall(r'<a href="([^"]+)" data-download=', resp.text)
    assert len(hrefs) == 4
    for href in hrefs:
        download_resp = client.get(href)
        assert download_resp.status_code == 200, href


def test_rerender_form_submission_actually_rerenders(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_and_wait(client, tmp_path)

    # Matches what the session page's <form> actually submits to -- the
    # UI route redirects back to the session page instead of returning
    # JSON, since a browser form POST would otherwise navigate to a raw
    # JSON blob.
    resp = client.post(f"/ui/sessions/{session_id}/rerender", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/ui/sessions/{session_id}"
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
