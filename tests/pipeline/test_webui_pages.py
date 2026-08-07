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


def test_live_generation_reports_progress_on_the_processing_page(tmp_path, monkeypatch):
    """End-to-end wiring check: generate_all_steps' on_progress callback ->
    JobRunner.set_progress -> GET /ui/sessions/{id}'s live processing page,
    not just each layer in isolation."""
    import threading

    import pipeline.generation as generation_module

    reached_second_step = threading.Event()
    release = threading.Event()
    # generate_all_steps calls _request_reply directly (task-04's
    # memoization refactor split generate_step_text into
    # _request_reply + _finalize_reply) -- gate that, not the no-longer-
    # called generate_step_text, or this event never fires.
    real_request_reply = generation_module._request_reply
    call_count = {"n": 0}

    def gated_request_reply(step, llm_client, use_vision=False, screenshot_dir=None):
        call_count["n"] += 1
        # Pause going into the SECOND call so step 1's progress (1 of 3) has
        # already been reported by the time this test inspects the page.
        if call_count["n"] == 2:
            reached_second_step.set()
            release.wait(timeout=5)
        return real_request_reply(
            step, llm_client, use_vision=use_vision, screenshot_dir=screenshot_dir
        )

    monkeypatch.setattr(generation_module, "_request_reply", gated_request_reply)

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

    assert reached_second_step.wait(timeout=5)
    page = client.get(f"/ui/sessions/{session_id}")
    assert "1 / 3 steps (33%)" in page.text

    release.set()
    _wait_until_done(client, session_id)


def test_transcript_collapse_warning_renders_as_a_flagged_card():
    from pipeline.webui.pages import render_session_page

    warned_report = {
        "transcript": "1 transcript block(s) placed in order across 1 of 19 step(s) -- WARNING: the whole transcript landed on step 1."
    }
    page = render_session_page("sid", "Title", "date", warned_report, {})
    assert '<section class="card" data-status="yellow"><h2>Transcript placement</h2>' in page
    assert "WARNING" in page

    # An ordinary (non-degenerate) placement note stays as plain muted text,
    # not a flagged card.
    normal_report = {"transcript": "2 transcript block(s) placed in order across 2 of 2 step(s)"}
    normal_page = render_session_page("sid", "Title", "date", normal_report, {})
    assert '<p class="muted">Transcript:' in normal_page
    assert 'data-status="yellow"><h2>Transcript placement</h2>' not in normal_page


def test_narration_transcription_failure_renders_as_a_flagged_card():
    from pipeline.webui.pages import render_session_page

    failed_report = {
        "narration_transcription": (
            "narration.wav could not be transcribed (see server log) -- "
            "doc generated from steps only"
        )
    }
    page = render_session_page("sid", "Title", "date", failed_report, {})
    assert '<section class="card" data-status="yellow"><h2>Narration transcription</h2>' in page
    assert "could not be transcribed" in page

    # A successful transcription note stays as plain muted text, not a
    # flagged card.
    ok_report = {
        "narration_transcription": "narration.wav transcribed locally and placed onto steps"
    }
    ok_page = render_session_page("sid", "Title", "date", ok_report, {})
    assert '<p class="muted">narration.wav transcribed locally' in ok_page
    assert 'data-status="yellow"><h2>Narration transcription</h2>' not in ok_page

    # No narration_transcription key at all (today's only path) -- no note.
    no_report = {}
    no_page = render_session_page("sid", "Title", "date", no_report, {})
    assert "Narration transcription" not in no_page


def test_session_page_shows_llm_preflight_result_without_a_section_status_tag():
    from pipeline.webui.pages import render_session_page

    report = {
        "llm_preflight": {
            "provider": "ollama",
            "model": "qwen3:32b",
            "status": "error",
            "detail": "connection refused",
            "latency_ms": None,
        }
    }
    page = render_session_page("sid", "Title", "date", report, {})
    assert "LLM preflight" in page
    assert "connection refused" in page
    assert "qwen3:32b" in page
    # Must be a plain <div>, not a <section data-status=...> -- the UI-smoke
    # test counts exactly 3 of those, one per fixed sidecar-report category.
    assert (
        '<div class="card" style="border-left:4px solid var(--bad)"><strong>LLM preflight' in page
    )


def test_session_page_hides_llm_preflight_card_when_absent():
    from pipeline.webui.pages import render_session_page

    page = render_session_page("sid", "Title", "date", {}, {})
    assert "LLM preflight" not in page


def test_session_page_shows_manually_edited_steps_without_a_section_status_tag():
    from pipeline.webui.pages import render_session_page

    report = {"manually_edited_steps": ["step-002", "step-005"]}
    page = render_session_page("sid", "Title", "date", report, {})
    assert "Manually edited steps" in page
    assert "step-002" in page
    assert "step-005" in page
    assert (
        '<div class="card" style="border-left:4px solid var(--warn)"><strong>Manually edited'
        in page
    )
    # The fixed three sidecar-report <section data-status=...> categories
    # must stay exactly 3 even with this card present (UI-smoke contract).
    assert page.count('<section class="card" data-status="') == 3


def test_session_page_hides_manually_edited_card_when_absent():
    from pipeline.webui.pages import render_session_page

    page = render_session_page("sid", "Title", "date", {}, {})
    assert "Manually edited steps" not in page


def test_processing_page_shows_progress_bar_when_available():
    from pipeline.webui.pages import render_session_processing_page

    page = render_session_processing_page(
        "sid", {"status": "processing", "progress": {"current": 3, "total": 10}}
    )
    assert '<progress value="3" max="10">' in page
    assert "3 / 10 steps (30%)" in page

    # No progress reported yet (e.g. still queued, or hasn't reached the
    # per-step loop) -- falls back to the plain spinner, no broken markup.
    queued = render_session_processing_page("sid", {"status": "queued", "progress": None})
    assert "<progress" not in queued


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
    assert f'<iframe name="docpreview" src="/sessions/{session_id}/doc.html"' in resp.text


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


def test_report_findings_render_as_thumbnail_rows_linked_to_their_step():
    from pipeline.webui.pages import render_session_page

    report = {"template_fallback_steps": ["step-002"]}
    steps = [
        {
            "step_id": "step-002",
            "text": "Click 'Save'.",
            "used_fallback": True,
            "screenshot": "002.png",
        }
    ]
    page = render_session_page("sid", "Title", "date", report, {}, steps=steps)
    assert 'src="/sessions/sid/002.png"' in page
    assert 'href="/sessions/sid/doc.html#step-002"' in page
    assert 'target="docpreview"' in page
    assert "Click &#x27;Save&#x27;." in page
    # The section contract (data-status + <h2> first) must still hold.
    assert '<section class="card" data-status="red"><h2>Template-fallback steps</h2>' in page


def test_report_findings_degrade_to_plain_ids_without_a_step_index():
    from pipeline.webui.pages import render_session_page

    report = {"template_fallback_steps": ["step-002"]}
    page = render_session_page("sid", "Title", "date", report, {}, steps=None)
    assert "step-002" in page
    assert "<img" not in page.split("Template-fallback steps</h2>", 1)[1].split("</section>", 1)[0]
    assert '<section class="card" data-status="red"><h2>Template-fallback steps</h2>' in page


def test_render_session_page_tolerates_malformed_step_entries():
    """Caught by automated PR review: a damaged steps.json entry (not a
    dict, or missing "step_id") used to raise TypeError/KeyError building
    steps_by_id, 500ing the whole review page instead of degrading the way
    every other "corrupt" path in this feature does."""
    from pipeline.webui.pages import render_session_page

    report = {"template_fallback_steps": ["step-002"]}
    steps = ["not a dict", {"screenshot": "no step_id here"}, None, 42]
    page = render_session_page("sid", "Title", "date", report, {}, steps=steps)
    # Must not raise, and must still render the section (degraded to a
    # plain id since no entry actually matched step-002).
    assert '<section class="card" data-status="red"><h2>Template-fallback steps</h2>' in page
    assert "step-002" in page


def test_session_page_has_a_named_doc_preview_iframe_and_lightbox():
    from pipeline.webui.pages import render_session_page

    page = render_session_page("sid", "Title", "date", {}, {})
    assert 'name="docpreview"' in page
    assert 'id="lightbox"' in page


def test_session_page_renders_an_edit_form_and_regenerate_button_per_step():
    from pipeline.webui.pages import render_session_page

    steps = [
        {
            "step_id": "step-001",
            "text": "Click the 'Save' <button>.",
            "used_fallback": False,
            "manually_edited": False,
            "screenshot": "001.png",
        }
    ]
    page = render_session_page("sid", "Title", "date", {}, {}, steps=steps, can_regenerate=True)
    assert 'id="edit-step-001"' in page
    assert 'action="/ui/sessions/sid/steps/step-001"' in page
    assert 'action="/ui/sessions/sid/steps/step-001/regenerate"' in page
    assert '<textarea name="text"' in page
    # The textarea content must be HTML-escaped (raw "<button>" would break markup).
    assert "&lt;button&gt;" in page
    assert "<button>" not in page.split('<textarea name="text"', 1)[1].split("</textarea>", 1)[0]


def test_session_page_hides_regenerate_when_can_regenerate_is_false():
    from pipeline.webui.pages import render_session_page

    steps = [{"step_id": "step-001", "text": "x", "used_fallback": False, "screenshot": ""}]
    page = render_session_page("sid", "Title", "date", {}, {}, steps=steps, can_regenerate=False)
    assert 'action="/ui/sessions/sid/steps/step-001"' in page
    assert "/regenerate" not in page


def test_session_page_omits_the_editor_without_a_step_index():
    from pipeline.webui.pages import render_session_page

    page = render_session_page("sid", "Title", "date", {}, {}, steps=None)
    assert "Edit steps" not in page
    assert "<textarea" not in page


def test_finding_row_links_to_the_matching_editor_anchor():
    from pipeline.webui.pages import render_session_page

    report = {"template_fallback_steps": ["step-002"]}
    steps = [
        {"step_id": "step-001", "text": "a", "used_fallback": False, "screenshot": ""},
        {"step_id": "step-002", "text": "b", "used_fallback": True, "screenshot": ""},
    ]
    page = render_session_page("sid", "Title", "date", report, {}, steps=steps)
    assert 'href="#edit-step-002"' in page
    assert 'id="edit-step-002"' in page


def test_finding_row_shows_edited_badge_when_manually_edited():
    from pipeline.webui.pages import render_session_page

    # The badge is rendered by _finding_row, only reached via _step_section
    # for the two step-id report categories -- empty_metadata_steps here
    # puts step-002 through that path so the badge is actually exercised.
    report = {"manually_edited_steps": ["step-002"], "empty_metadata_steps": ["step-002"]}
    steps = [
        {
            "step_id": "step-002",
            "text": "b",
            "used_fallback": False,
            "manually_edited": True,
            "screenshot": "",
        }
    ]
    page = render_session_page("sid", "Title", "date", report, {}, steps=steps)
    assert '<span class="pill edited">edited</span>' in page


def test_discard_edits_button_shown_only_when_steps_were_manually_edited():
    from pipeline.webui.pages import render_session_page

    with_edits = render_session_page(
        "sid", "Title", "date", {"manually_edited_steps": ["step-001"]}, {}
    )
    assert "discard_edits" in with_edits
    without_edits = render_session_page("sid", "Title", "date", {}, {})
    assert "discard_edits" not in without_edits


def test_session_page_finding_thumbnails_and_deep_links_actually_resolve(tmp_path):
    """End-to-end: the stub LLM (see _stub_llm.py) forces every step to
    template-fallback, so the "Template-fallback steps" section is
    populated -- every thumbnail src the page renders must actually
    resolve, and every doc.html#step-xxx anchor must land on a real id in
    that document (task-06)."""
    client = _make_client(tmp_path)
    session_id = _create_and_wait(client, tmp_path)

    page = client.get(f"/ui/sessions/{session_id}").text
    section = page.split("Template-fallback steps</h2>", 1)[1].split("</section>", 1)[0]
    srcs = re.findall(r'src="(/sessions/[^"]+)"', section)
    assert srcs  # the stub fails every step's round-trip, so this must be non-empty
    for src in srcs:
        resp = client.get(src)
        assert resp.status_code == 200, src
        assert resp.headers["content-type"].startswith("image/")

    hrefs = re.findall(r'href="(/sessions/[^"]+/doc\.html#step-[^"]+)"', section)
    assert hrefs
    for href in hrefs:
        path, step_id = href.split("#", 1)
        doc = client.get(path)
        assert doc.status_code == 200, href
        assert f'id="{step_id}"' in doc.text


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
