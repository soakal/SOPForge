"""Phase 6h ("Forge" UI): the vendored IBM Plex fonts (served locally, also
from a frozen bundle), the library table's stat tiles and review queue, and
the "no new settings" rule (AC9) for the rebuilt configuration page. The
browser-driven checks (both drop-zone paths, no external requests, fonts
actually applied) live in test_ui_smoke.py behind the `ui` marker."""

import json
import re
import shutil
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from pipeline.manifest import load_manifest
from pipeline.server import create_app
from pipeline.webui.library_view import ai_written_count, library_stats, review_count
from pipeline.webui.pages import FONT_FILES, render_library_page

from _stub_llm import stub_llm_client_factory

REPO = Path(__file__).resolve().parent.parent.parent
FIXTURES = REPO / "fixtures"
FONT_DIR = REPO / "assets" / "fonts" / "ibm-plex"

# Every form field the configuration page posted before 6h. AC9: the rebuilt
# page may restyle these, never add to them.
CONFIG_FIELDS = {
    "steps_provider",
    "steps_model",
    "steps_endpoint",
    "steps_max_concurrency",
    "narrative_provider",
    "narrative_model",
    "narrative_endpoint",
    "narrative_passes",
    "vision_enabled",
    "vision_provider",
    "vision_model",
    "vision_endpoint",
    "vision_max_concurrency",
    "polish_enabled",
    "polish_provider",
    "polish_model",
    "polish_endpoint",
    "document_author",
    "document_doc_no_prefix",
    "transcription_enabled",
    "transcription_model_size",
    "transcription_device",
    "transcription_compute_type",
}


def _make_client(tmp_path):
    app = create_app(
        sessions_root=tmp_path / "sessions",
        llm_client_factory=stub_llm_client_factory,
        narrative_llm_client_factory=stub_llm_client_factory,
    )
    return TestClient(app)


def _create_and_wait(client, tmp_path):
    manifest_path = FIXTURES / "sample-manifest.json"
    manifest = load_manifest(manifest_path)
    files = []
    for step in manifest.steps:
        p = tmp_path / step.screenshot
        Image.new("RGB", (320, 200), (255, 255, 255)).save(p)
        files.append(("files", (step.screenshot, p.open("rb"), "image/png")))
    resp = client.post(
        "/sessions", data={"manifest_json": manifest_path.read_text(encoding="utf-8")}, files=files
    )
    session_id = resp.json()["session_id"]
    deadline = time.monotonic() + 10
    while client.get(f"/sessions/{session_id}/status").json()["status"] != "done":
        assert time.monotonic() < deadline, "session never reached done"
        time.sleep(0.05)
    return session_id, len(manifest.steps)


def test_vendored_plex_fonts_and_license_are_present():
    for name in FONT_FILES:
        assert (FONT_DIR / name).read_bytes()[:4] == b"wOF2", name
    license_text = (FONT_DIR / "LICENSE").read_text(encoding="utf-8")
    assert "SIL Open Font License" in license_text


def test_server_spec_bundles_the_plex_fonts():
    spec = (REPO / "sopforge-server.spec").read_text(encoding="utf-8")
    assert '("assets/fonts/ibm-plex/*.woff2", "assets/fonts/ibm-plex")' in spec
    assert '("assets/fonts/ibm-plex/LICENSE", "assets/fonts/ibm-plex")' in spec


def test_font_route_serves_only_the_declared_faces(tmp_path):
    client = _make_client(tmp_path)
    for name in FONT_FILES:
        resp = client.get(f"/ui/assets/fonts/{name}")
        assert resp.status_code == 200, name
        assert resp.headers["content-type"] == "font/woff2"
        assert resp.content == (FONT_DIR / name).read_bytes()
    for bad in ("LICENSE", "..%2F..%2Fconfig%2Fmodels.toml", "IBMPlexSans-Thin.woff2"):
        assert client.get(f"/ui/assets/fonts/{bad}").status_code == 404, bad


def test_font_route_reads_from_the_frozen_bundle(tmp_path, monkeypatch):
    """In the frozen EXE the fonts come from sys._MEIPASS (where the spec's
    datas put them), not the source tree."""
    client = _make_client(tmp_path)
    bundle = tmp_path / "bundle"
    shutil.copytree(FONT_DIR, bundle / "assets" / "fonts" / "ibm-plex")
    marker = bundle / "assets" / "fonts" / "ibm-plex" / "IBMPlexSans-Regular.woff2"
    marker.write_bytes(b"wOF2-from-bundle")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    resp = client.get("/ui/assets/fonts/IBMPlexSans-Regular.woff2")
    assert resp.status_code == 200
    assert resp.content == b"wOF2-from-bundle"


def test_pages_reference_no_external_resources(tmp_path):
    client = _make_client(tmp_path)
    session_id, _ = _create_and_wait(client, tmp_path)
    for path in ("/ui", "/ui?view=review", f"/ui/sessions/{session_id}", "/ui/config"):
        page = client.get(path).text
        for pattern in (r'src="(?:https?:)?//', r'href="(?:https?:)?//', r"url\((?:https?:)?//"):
            assert not re.search(pattern, page), (path, pattern)
        assert "@import" not in page, path
        assert "fonts.googleapis" not in page and "cdn" not in page.lower(), path


def test_library_shows_table_stat_tiles_and_review_queue(tmp_path):
    client = _make_client(tmp_path)
    session_id, step_count = _create_and_wait(client, tmp_path)

    page = client.get("/ui").text
    for header in ("Procedure", "Doc no.", "Steps", "Status", "Exports", "Updated"):
        assert f'<th scope="col">{header}</th>' in page
    for label in ("Finished", "Need review", "Steps captured", "Steps AI-written"):
        assert label in page
    # The stub LLM fails every round-trip -> every step is template fallback,
    # so 0% AI-written and the session needs review.
    assert f"(0 of {step_count})" in page
    assert '<span class="count" aria-label="1 need review">1</span>' in page
    for path in ("doc.docx", "doc.pdf", "doc.single.html", "export.md.zip", "walkthrough.html"):
        assert f'href="/sessions/{session_id}/{path}"' in page
    assert ">WALK</a>" in page
    assert ">SPL</a>" in page
    session_page = client.get(f"/ui/sessions/{session_id}").text
    walk_href = f'href="/sessions/{session_id}/walkthrough.html"'
    assert walk_href in session_page
    assert client.get(f"/sessions/{session_id}/walkthrough.html").status_code == 200
    assert f'href="/sessions/{session_id}/spl.pptx"' in session_page
    assert client.get(f"/sessions/{session_id}/spl.pptx").status_code == 200
    assert 'id="new"' in page  # the drop zone

    queue = client.get("/ui", params={"view": "review"}).text
    assert f"/ui/sessions/{session_id}" in queue
    assert 'aria-current="page"><svg' in queue and "Review queue" in queue


def test_ai_written_prefers_steps_json_and_falls_back_to_report(tmp_path):
    report = {"template_fallback_steps": ["step-002"], "caption_fallback_steps": []}
    assert ai_written_count(tmp_path, report, 3) == 2
    (tmp_path / "steps.json").write_text(
        json.dumps(
            {
                "version": 2,
                "steps": [
                    {"step_id": "step-001", "used_fallback": False},
                    {"step_id": "step-002", "used_fallback": True},
                    {"step_id": "step-003", "used_fallback": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert ai_written_count(tmp_path, report, 3) == 1


def test_review_count_counts_each_flagged_step_once_plus_claims():
    report = {
        "template_fallback_steps": ["step-001", "step-002"],
        "empty_metadata_steps": ["step-002"],
        "verify_claims": [{"claim_id": "c1"}],
    }
    assert review_count(report) == 3
    assert review_count({**report, "uncaptured_value_steps": ["step-002", "step-004"]}) == 4
    assert review_count({}) == 0


def test_session_page_lists_uncaptured_values_and_omitted_screenshots():
    from pipeline.webui.pages import render_session_page

    report = {"uncaptured_value_steps": ["step-002"], "omitted_screenshot_steps": ["step-005"]}
    page = render_session_page("sid", "Title", "date", report, {})
    assert "Values not captured" in page and "step-002" in page
    assert "Omitted screenshots" in page and "step-005" in page
    assert 'action="/ui/sessions/sid/steps/step-005/keep-screenshot"' in page
    # Still exactly the three fixed sidecar sections (UI-smoke contract).
    assert page.count('<section class="card" data-status="') == 3
    assert "Values not captured" not in render_session_page("sid", "T", "d", {}, {})


def test_library_stats_ratio_covers_finished_documents_only():
    rows = [
        {"steps": 10, "ai_steps": 9, "needs_review": True},
        {"steps": 4, "ai_steps": 4, "needs_review": False},
        {"steps": 7, "ai_steps": None, "needs_review": True},  # still generating
    ]
    assert library_stats(rows) == {
        "procedures": 2,
        "total": 3,
        "need_review": 2,
        "steps": 14,
        "ai_steps": 13,
    }
    page = render_library_page([], rows=[], stats=library_stats(rows))
    assert "93%" in page and "(13 of 14)" in page
    assert "<b>2</b><span>Finished <i>(of 3 total)</i></span>" in page


def test_config_page_posts_exactly_the_existing_fields(tmp_path):
    client = _make_client(tmp_path)
    page = client.get("/ui/config").text
    form = page.split('<form method="post" action="/ui/config"', 1)[1].split("</form>", 1)[0]
    assert set(re.findall(r'name="([^"]+)"', form)) == CONFIG_FIELDS


def _wait_done(client, session_id):
    deadline = time.monotonic() + 10
    while client.get(f"/sessions/{session_id}/status").json()["status"] != "done":
        assert time.monotonic() < deadline, "re-export never finished"
        time.sleep(0.05)


def test_session_page_lists_phases_with_rename_forms(tmp_path):
    client = _make_client(tmp_path)
    session_id, _ = _create_and_wait(client, tmp_path)
    report = client.get(f"/sessions/{session_id}/report").json()
    page = client.get(f"/ui/sessions/{session_id}").text
    assert "<h2>Phases</h2>" in page
    for index, phase in enumerate(report["phases"]):
        assert f'action="/ui/sessions/{session_id}/phases/{index}"' in page
        for step_id in phase["step_ids"]:
            assert f'href="#edit-{step_id}"' in page


def test_rename_phase_rewrites_phases_json_and_reexports(tmp_path):
    client = _make_client(tmp_path)
    session_id, _ = _create_and_wait(client, tmp_path)
    phases_path = tmp_path / "sessions" / session_id / "phases.json"
    before = json.loads(phases_path.read_text(encoding="utf-8"))

    resp = client.post(
        f"/ui/sessions/{session_id}/phases/0",
        data={"name": "  Sign   in  "},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    _wait_done(client, session_id)

    after = json.loads(phases_path.read_text(encoding="utf-8"))
    assert after["source"] == "user"
    assert after["phases"][0]["name"] == "Sign in"
    assert [p["step_ids"] for p in after["phases"]] == [p["step_ids"] for p in before["phases"]]
    report = client.get(f"/sessions/{session_id}/report").json()
    assert report["phases"][0]["name"] == "Sign in"
    assert "Sign in" in client.get(f"/sessions/{session_id}/doc.html").text


def test_rename_phase_rejects_bad_input(tmp_path):
    client = _make_client(tmp_path)
    session_id, _ = _create_and_wait(client, tmp_path)
    url = f"/ui/sessions/{session_id}/phases"
    assert client.post(f"{url}/0", data={"name": "   "}).status_code == 400
    assert client.post(f"{url}/0", data={"name": "x" * 61}).status_code == 400
    assert client.post(f"{url}/99", data={"name": "Fine"}).status_code == 404
    assert client.post("/ui/sessions/nope/phases/0", data={"name": "Fine"}).status_code == 404


def test_keep_screenshot_adds_to_keep_list_and_preserves_names(tmp_path):
    client = _make_client(tmp_path)
    session_id, _ = _create_and_wait(client, tmp_path)
    phases_path = tmp_path / "sessions" / session_id / "phases.json"
    client.post(f"/ui/sessions/{session_id}/phases/0", data={"name": "Renamed"})
    _wait_done(client, session_id)

    resp = client.post(
        f"/ui/sessions/{session_id}/steps/step-002/keep-screenshot", follow_redirects=False
    )
    assert resp.status_code == 303
    _wait_done(client, session_id)
    data = json.loads(phases_path.read_text(encoding="utf-8"))
    assert data["keep_screenshots"] == ["step-002"]
    assert data["phases"][0]["name"] == "Renamed"  # a restore never loses a rename

    # ...and a later rename never loses a restore.
    client.post(f"/ui/sessions/{session_id}/phases/0", data={"name": "Again"})
    _wait_done(client, session_id)
    assert json.loads(phases_path.read_text(encoding="utf-8"))["keep_screenshots"] == ["step-002"]

    bad = client.post(f"/ui/sessions/{session_id}/steps/step-999/keep-screenshot")
    assert bad.status_code == 404


def test_phase_routes_refuse_sessions_without_phases(tmp_path):
    client = _make_client(tmp_path)
    resp = client.post(
        "/ui/build",
        files=[("files", ("a.png", _png(), "image/png"))],
        follow_redirects=False,
    )
    session_id = resp.headers["location"].rsplit("/", 1)[-1]
    client.post(f"/ui/sessions/{session_id}/confirm-steps", data={"keep": ["step-001"]})
    _wait_done(client, session_id)
    assert client.post(f"/ui/sessions/{session_id}/phases/0", data={"name": "X"}).status_code == 409
    assert (
        client.post(f"/ui/sessions/{session_id}/steps/step-001/keep-screenshot").status_code == 409
    )


def _png():
    import io

    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def test_session_page_shows_spl_overflow_and_error_notes():
    from pipeline.webui.pages import render_session_page

    page = render_session_page("sid", "T", "d", {"spl_text_overflow": ["Step 3 <body>"]}, {})
    assert "too long at 12pt" in page and "Step 3 &lt;body&gt;" in page
    page = render_session_page("sid", "T", "d", {"spl_error": "boom"}, {})
    assert "SPL deck not built" in page and "boom" in page
    assert page.count('<section class="card" data-status="') == 3
    assert "SPL deck" not in render_session_page("sid", "T", "d", {}, {})


def test_document_details_card_round_trips_through_metadata_route(tmp_path):
    client = _make_client(tmp_path)
    session_id, _ = _create_and_wait(client, tmp_path)
    report = client.get(f"/sessions/{session_id}/report").json()
    page = client.get(f"/ui/sessions/{session_id}").text
    assert f'action="/ui/sessions/{session_id}/metadata"' in page
    for key in ("title", "subtitle", "doc_no", "author", "revision"):
        assert f'name="{key}" id="meta-{key}"' in page
    assert f'value="{report["document"]["doc_no"]}"' in page
    # The library shows the same doc number the exports were stamped with.
    assert f'<td class="num">{report["document"]["doc_no"]}</td>' in client.get("/ui").text

    resp = client.post(
        f"/ui/sessions/{session_id}/metadata",
        data={"doc_no": "SOP-IT-777", "revision": "2.1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    _wait_done(client, session_id)
    assert '<td class="num">SOP-IT-777</td>' in client.get("/ui").text
    page = client.get(f"/ui/sessions/{session_id}").text
    assert 'value="SOP-IT-777"' in page and 'value="2.1"' in page


def test_library_never_assigns_doc_numbers_for_unexported_sessions(tmp_path):
    """Doc numbers are sequential and assigned at first export (doc_meta);
    a staged session shows none, and viewing the library must not consume
    a number from the counter."""
    client = _make_client(tmp_path)
    resp = client.post(
        "/ui/build", files=[("files", ("a.png", _png(), "image/png"))], follow_redirects=False
    )
    session_id = resp.headers["location"].rsplit("/", 1)[-1]
    page = client.get("/ui").text
    row = page.split(f'href="/ui/sessions/{session_id}"', 1)[1].split("</tr>", 1)[0]
    assert '<td class="num">&mdash;</td>' in row
    sessions_root = tmp_path / "sessions"
    assert not (sessions_root / "doc_numbers.json").exists()
    assert not (sessions_root / session_id / "doc_no.txt").exists()
