"""sopforge-server FastAPI app: POST /sessions (manifest + PNGs), GET
status/report, doc downloads, and the plain-HTML review page — end to end
through a real TestClient, with the shared stub LLM client
(tests/pipeline/_stub_llm.py) injected via create_app's llm_client_factory
so step generation never makes a real network call here.

Generation runs on a background job (task-05), so POST /sessions returns
"queued"/"processing" immediately — tests poll /status until "done" (or a
terminal "error") before asserting on downstream endpoints."""

import json
import re
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pipeline.config import default_config_path
from pipeline.manifest import load_manifest
from pipeline.server import create_app

from _stub_llm import stub_llm_client_factory

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _make_client(tmp_path):
    # Isolate the runtime config to a writable temp copy so the config-editor
    # test never touches the real per-user ~/SOPForge/models.toml.
    cfg = tmp_path / "models.toml"
    shutil.copyfile(default_config_path(), cfg)
    app = create_app(
        sessions_root=tmp_path / "sessions",
        llm_client_factory=stub_llm_client_factory,
        narrative_llm_client_factory=stub_llm_client_factory,
        config_path=cfg,
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


def _confirm_all_steps(client, session_id):
    """/ui/upload and /ui/build always stage (see server.py's steps-review
    gate) -- this keeps every step and submits for generation, standing in
    for a user who reviewed the checklist and didn't drop anything."""
    page = client.get(f"/ui/sessions/{session_id}")
    step_ids = re.findall(r'name="keep" value="(step-\d+)"', page.text)
    assert step_ids, "expected a steps-review checklist with at least one step"
    resp = client.post(
        f"/ui/sessions/{session_id}/confirm-steps",
        data={"keep": step_ids},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def test_create_session_and_check_status(tmp_path):
    client = _make_client(tmp_path)
    resp = _create_session(client, tmp_path)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("queued", "processing", "done")
    session_id = body["session_id"]

    status = _wait_for_terminal_status(client, session_id)
    assert status == {"status": "done"}


def test_capture_session_gets_an_auto_generated_title(tmp_path):
    """A real capture session's manifest almost never has a title (nothing in
    the capture flow asks the user for one) -- without an auto-title the
    library/session page would show the raw session id, a timestamp+uuid
    blob. Proves generate_title_and_overview gets called from window titles
    + generated step text (server.py's _synthesize_narration_from_steps) and
    the result lands on manifest.session.title."""

    class _TitleGeneratingClient:
        def chat(self, messages, **kwargs):
            return '{"title": "Configure SmartDeploy Console", "overview": "Sets it up."}'

    cfg = tmp_path / "models.toml"
    shutil.copyfile(default_config_path(), cfg)
    app = create_app(
        sessions_root=tmp_path / "sessions",
        llm_client_factory=lambda: _TitleGeneratingClient(),
        config_path=cfg,
    )
    client = TestClient(app)

    manifest_json, files = _manifest_and_files(tmp_path)
    resp = client.post("/sessions", data={"manifest_json": manifest_json}, files=files)
    session_id = resp.json()["session_id"]
    status = _wait_for_terminal_status(client, session_id)
    assert status["status"] == "done"

    page = client.get(f"/ui/sessions/{session_id}")
    assert "Configure SmartDeploy Console" in page.text


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


def test_step_mismatch_after_generation_fails_the_job_loudly(tmp_path, monkeypatch):
    """assembler.check_1to1_mapping is wired into _generate as defense in
    depth for CLAUDE.md invariant L1 -- generation.py stays sequential in
    normal operation, but a mismatched step list must still fail the job
    loudly (status: error) instead of silently shipping a doc whose steps
    don't match the manifest."""
    import pipeline.server as server_module

    def _dropping_render_steps_llm_mode(
        manifest, screenshots_dir, annotated_dir, llm_client, on_progress=None, max_concurrency=1
    ):
        kept = manifest.steps[1:]  # deliberately drop the first step
        results = [{"step_id": s.id, "text": "x", "used_fallback": False} for s in kept]
        annotated = [annotated_dir / s.screenshot for s in kept]
        return results, annotated

    monkeypatch.setattr(server_module, "render_steps_llm_mode", _dropping_render_steps_llm_mode)

    client = _make_client(tmp_path)
    session_id, status = _create_and_wait(client, tmp_path)

    assert status["status"] == "error"
    assert "mismatched step list" in status["error"]


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


def test_upload_with_transcript_places_narration_under_each_step(tmp_path):
    """A .md/.txt transcript uploaded with the session must have its narration
    placed under the matching step (by step label) in the generated docs, and
    be recorded in the sidecar report."""
    client = _make_client(tmp_path)
    manifest_json, files = _manifest_and_files(tmp_path)
    transcript = (
        "Step 1: First, open the console.\n\n"
        "Step 2: Then enter the computer name.\n\n"
        "Step 3: Finally, check the downloads."
    )
    files.append(("transcript_file", ("narration.md", transcript.encode(), "text/markdown")))

    resp = client.post("/sessions", data={"manifest_json": manifest_json}, files=files)
    session_id = resp.json()["session_id"]
    status = _wait_for_terminal_status(client, session_id)
    assert status["status"] == "done"

    md = client.get(f"/sessions/{session_id}/doc.md").text
    assert "First, open the console." in md
    assert "Then enter the computer name." in md
    assert "**Narration:**" in md

    html_doc = client.get(f"/sessions/{session_id}/doc.html").text
    assert "check the downloads" in html_doc

    report = client.get(f"/sessions/{session_id}/report").json()
    assert "transcript" in report


class _NarrativeStub:
    """Stands in for the [narrative]-section LLM used by the semantic
    transcript pipeline -- distinguishes stage 1's boundary-picking call
    from stage 2's polish call by a marker only the boundary prompt
    contains ("starts_with" appears in semantic_align's own JSON-shape
    instructions, never in narration_polish's)."""

    def chat(self, messages, **kwargs):
        content = messages[0]["content"]
        if "starts_with" in content:
            return json.dumps(
                [
                    {"step": 1, "starts_with": "first click save"},
                    {"step": 2, "starts_with": "then enter the computer name"},
                    {"step": 3, "starts_with": "then click somewhere in chrome"},
                ]
            )
        return json.dumps(
            [
                {"step_id": "step-001", "text": "First, click Save."},
                {"step_id": "step-002", "text": "Then enter the computer name."},
                {"step_id": "step-003", "text": "Then click somewhere in Chrome."},
            ]
        )


def test_unstructured_transcript_gets_semantic_placement_and_polish(tmp_path):
    """A transcript with no blank lines/labels -- the exact real-world shape
    that collapses onto step 1 under deterministic placement -- gets picked
    up by the semantic LLM pipeline instead: narration lands on all three
    steps, polished, and the sidecar report records how."""
    cfg = tmp_path / "models.toml"
    shutil.copyfile(default_config_path(), cfg)
    app = create_app(
        sessions_root=tmp_path / "sessions",
        llm_client_factory=stub_llm_client_factory,
        narrative_llm_client_factory=lambda: _NarrativeStub(),
        config_path=cfg,
    )
    client = TestClient(app)

    manifest_json, files = _manifest_and_files(tmp_path)
    transcript = "first click save then enter the computer name then click somewhere in chrome"
    files.append(("transcript_file", ("t.md", transcript.encode(), "text/markdown")))

    resp = client.post("/sessions", data={"manifest_json": manifest_json}, files=files)
    session_id = resp.json()["session_id"]
    status = _wait_for_terminal_status(client, session_id)
    assert status["status"] == "done"

    md = client.get(f"/sessions/{session_id}/doc.md").text
    assert "First, click Save." in md
    assert "Then enter the computer name." in md
    assert "Then click somewhere in Chrome." in md

    report = client.get(f"/sessions/{session_id}/report").json()
    placement = report["transcript_placement"]
    assert placement["mode"] == "semantic-llm"
    assert placement["boundaries_resolved"] == 3
    assert set(placement["steps_polished"]) == {"step-001", "step-002", "step-003"}


def test_build_from_screenshots_and_transcript_without_a_manifest(tmp_path, monkeypatch):
    """Manifest-free mode: POST /ui/build with just images (+ a transcript)
    produces a full SOP -- one step per image, in order, with the transcript
    text placed under each, and all export formats generated. Vision captioning
    is stubbed off (returns None per image) so this exercises the transcript
    fallback deterministically, with no network call."""
    import io

    import pipeline.server as server_module

    monkeypatch.setattr(server_module, "caption_images", lambda paths, *a, **k: [None] * len(paths))

    client = _make_client(tmp_path)

    def png(color):
        buf = io.BytesIO()
        Image.new("RGB", (200, 150), color).save(buf, "PNG")
        return buf.getvalue()

    files = [
        ("files", ("first.png", png((200, 0, 0)), "image/png")),
        ("files", ("second.png", png((0, 200, 0)), "image/png")),
        (
            "transcript_file",
            ("t.md", b"1. Open the first screen.\n2. Then the second.", "text/markdown"),
        ),
    ]
    resp = client.post(
        "/ui/build", data={"title": "My Photo SOP"}, files=files, follow_redirects=False
    )
    assert resp.status_code == 303
    session_id = resp.headers["location"].rsplit("/", 1)[-1]
    _confirm_all_steps(client, session_id)
    status = _wait_for_terminal_status(client, session_id)
    assert status["status"] == "done"

    md = client.get(f"/sessions/{session_id}/doc.md").text
    assert md.startswith("# My Photo SOP")
    assert md.count("## Step") == 2  # one step per image
    assert "Open the first screen." in md
    assert "Then the second." in md

    assert client.get(f"/sessions/{session_id}/doc.docx").status_code == 200
    assert client.get(f"/sessions/{session_id}/doc.pdf").status_code == 200


def test_build_requires_at_least_one_image(tmp_path):
    client = _make_client(tmp_path)
    resp = client.post("/ui/build", data={"title": "x"}, files=[])
    assert resp.status_code == 400


def test_build_uses_vision_captions_when_available(tmp_path, monkeypatch):
    """When vision captioning succeeds, each step's text is the caption (in
    order), overriding the transcript's own placement."""
    import io

    import pipeline.server as server_module

    def fake_caption_images(paths, narration, endpoint, model, **kwargs):
        assert "dictated" in narration  # narration passed through as context
        return [f"Vision caption for image {i + 1}." for i in range(len(paths))]

    monkeypatch.setattr(server_module, "caption_images", fake_caption_images)

    client = _make_client(tmp_path)

    def png(color):
        buf = io.BytesIO()
        Image.new("RGB", (120, 90), color).save(buf, "PNG")
        return buf.getvalue()

    files = [
        ("files", ("a.png", png((10, 10, 10)), "image/png")),
        ("files", ("b.png", png((20, 20, 20)), "image/png")),
        ("transcript_file", ("t.txt", b"a dictated blob of narration", "text/plain")),
    ]
    resp = client.post(
        "/ui/build", data={"title": "Vision SOP"}, files=files, follow_redirects=False
    )
    session_id = resp.headers["location"].rsplit("/", 1)[-1]
    _confirm_all_steps(client, session_id)
    status = _wait_for_terminal_status(client, session_id)
    assert status["status"] == "done"

    md = client.get(f"/sessions/{session_id}/doc.md").text
    assert "Vision caption for image 1." in md
    assert "Vision caption for image 2." in md
    report = client.get(f"/sessions/{session_id}/report").json()
    assert "vision" in report


def test_photo_build_reports_caption_progress(tmp_path, monkeypatch):
    """Photo-mode's processing page shows a progress bar too, driven by
    caption completions instead of step generations."""
    import io
    import threading

    import pipeline.server as server_module

    reached = threading.Event()
    release = threading.Event()

    def gated_caption_images(paths, narration, endpoint, model, on_progress=None, **kwargs):
        if on_progress:
            on_progress(1, len(paths))
        reached.set()
        release.wait(timeout=5)
        return [f"Caption {i}." for i in range(len(paths))]

    monkeypatch.setattr(server_module, "caption_images", gated_caption_images)

    client = _make_client(tmp_path)

    def png(color):
        buf = io.BytesIO()
        Image.new("RGB", (120, 90), color).save(buf, "PNG")
        return buf.getvalue()

    files = [
        ("files", ("a.png", png((10, 10, 10)), "image/png")),
        ("files", ("b.png", png((20, 20, 20)), "image/png")),
    ]
    resp = client.post(
        "/ui/build", data={"title": "Progress SOP"}, files=files, follow_redirects=False
    )
    session_id = resp.headers["location"].rsplit("/", 1)[-1]
    _confirm_all_steps(client, session_id)

    assert reached.wait(timeout=5)
    page = client.get(f"/ui/sessions/{session_id}")
    assert "1 / 2 steps (50%)" in page.text

    release.set()
    status = _wait_for_terminal_status(client, session_id)
    assert status["status"] == "done"


def test_photo_build_unstructured_transcript_gets_semantic_placement(tmp_path, monkeypatch):
    """Photo-mode's synthetic manifest has no real window/element data (see
    photo_build.py), so there's no UIA context for the semantic pipeline to
    use -- but an unstructured transcript (no blank lines/labels) must still
    distribute across steps instead of collapsing onto step 1, same as the
    real-capture flow."""
    import io

    import pipeline.server as server_module

    monkeypatch.setattr(server_module, "caption_images", lambda paths, *a, **k: [None] * len(paths))

    class _PhotoNarrativeStub:
        def chat(self, messages, **kwargs):
            content = messages[0]["content"]
            if "starts_with" in content:
                return json.dumps(
                    [
                        {"step": 1, "starts_with": "first open the file"},
                        {"step": 2, "starts_with": "then save it"},
                    ]
                )
            return json.dumps(
                [
                    {"step_id": "step-001", "text": "First, open the file."},
                    {"step_id": "step-002", "text": "Then save it."},
                ]
            )

    cfg = tmp_path / "models.toml"
    shutil.copyfile(default_config_path(), cfg)
    app = create_app(
        sessions_root=tmp_path / "sessions",
        llm_client_factory=stub_llm_client_factory,
        narrative_llm_client_factory=lambda: _PhotoNarrativeStub(),
        config_path=cfg,
    )
    client = TestClient(app)

    def png(color):
        buf = io.BytesIO()
        Image.new("RGB", (120, 90), color).save(buf, "PNG")
        return buf.getvalue()

    files = [
        ("files", ("a.png", png((10, 10, 10)), "image/png")),
        ("files", ("b.png", png((20, 20, 20)), "image/png")),
        ("transcript_file", ("t.md", b"first open the file then save it", "text/markdown")),
    ]
    resp = client.post(
        "/ui/build", data={"title": "Photo Semantic SOP"}, files=files, follow_redirects=False
    )
    session_id = resp.headers["location"].rsplit("/", 1)[-1]
    _confirm_all_steps(client, session_id)
    status = _wait_for_terminal_status(client, session_id)
    assert status["status"] == "done"

    md = client.get(f"/sessions/{session_id}/doc.md").text
    assert "First, open the file." in md
    assert "Then save it." in md

    report = client.get(f"/sessions/{session_id}/report").json()
    assert report["transcript_placement"]["mode"] == "semantic-llm"


def test_photo_build_caption_beats_placed_narration_and_informs_context(tmp_path, monkeypatch):
    """When a caption succeeds for one image, it wins as that step's text;
    the successful caption is also passed through as that step's own
    context to the semantic aligner (there's no real window/element data
    in a synthetic manifest for it to use instead)."""
    import io

    import pipeline.server as server_module

    monkeypatch.setattr(
        server_module,
        "caption_images",
        lambda paths, *a, **k: ["A caption for the first screen.", None],
    )

    seen_prompts = []

    class _PhotoNarrativeStub:
        def chat(self, messages, **kwargs):
            content = messages[0]["content"]
            seen_prompts.append(content)
            if "starts_with" in content:
                return json.dumps(
                    [
                        {"step": 1, "starts_with": "first thing happens"},
                        {"step": 2, "starts_with": "second thing happens"},
                    ]
                )
            return json.dumps([{"step_id": "step-002", "text": "Second thing happens."}])

    cfg = tmp_path / "models.toml"
    shutil.copyfile(default_config_path(), cfg)
    app = create_app(
        sessions_root=tmp_path / "sessions",
        llm_client_factory=stub_llm_client_factory,
        narrative_llm_client_factory=lambda: _PhotoNarrativeStub(),
        config_path=cfg,
    )
    client = TestClient(app)

    def png(color):
        buf = io.BytesIO()
        Image.new("RGB", (120, 90), color).save(buf, "PNG")
        return buf.getvalue()

    files = [
        ("files", ("a.png", png((10, 10, 10)), "image/png")),
        ("files", ("b.png", png((20, 20, 20)), "image/png")),
        (
            "transcript_file",
            ("t.md", b"first thing happens then second thing happens", "text/markdown"),
        ),
    ]
    resp = client.post(
        "/ui/build",
        data={"title": "Photo Caption Context SOP"},
        files=files,
        follow_redirects=False,
    )
    session_id = resp.headers["location"].rsplit("/", 1)[-1]
    _confirm_all_steps(client, session_id)
    status = _wait_for_terminal_status(client, session_id)
    assert status["status"] == "done"

    md = client.get(f"/sessions/{session_id}/doc.md").text
    assert "A caption for the first screen." in md  # vision caption wins for step 1
    assert "Second thing happens." in md  # placed + polished narration for step 2

    assert seen_prompts  # the boundary-picking call actually happened
    assert "A caption for the first screen." in seen_prompts[0]  # caption fed as context


def test_photo_build_caption_length_mismatch_fails_the_job_loudly(tmp_path, monkeypatch):
    """Same invariant-L1 defense as the capture-flow test above, exercised on
    the manifest-free photo-build path: if caption_images ever returned the
    wrong number of captions, the zip(manifest.steps, captions) in
    _generate_photo would silently truncate step_results -- the wired-in
    check_1to1_mapping call must catch that and fail the job loudly instead."""
    import io

    import pipeline.server as server_module

    monkeypatch.setattr(
        server_module,
        "caption_images",
        lambda paths, *a, **k: ["only one caption"],  # wrong length: one per image expected
    )

    cfg = tmp_path / "models.toml"
    shutil.copyfile(default_config_path(), cfg)
    app = create_app(
        sessions_root=tmp_path / "sessions",
        llm_client_factory=stub_llm_client_factory,
        narrative_llm_client_factory=stub_llm_client_factory,
        config_path=cfg,
    )
    client = TestClient(app)

    def png(color):
        buf = io.BytesIO()
        Image.new("RGB", (120, 90), color).save(buf, "PNG")
        return buf.getvalue()

    files = [
        ("files", ("a.png", png((10, 10, 10)), "image/png")),
        ("files", ("b.png", png((20, 20, 20)), "image/png")),
    ]
    resp = client.post(
        "/ui/build", data={"title": "Mismatch SOP"}, files=files, follow_redirects=False
    )
    session_id = resp.headers["location"].rsplit("/", 1)[-1]
    _confirm_all_steps(client, session_id)
    status = _wait_for_terminal_status(client, session_id)

    assert status["status"] == "error"
    assert "mismatched step list" in status["error"]


def test_photo_build_headings_do_not_fabricate_a_click(tmp_path, monkeypatch):
    """Regression: photo_build.py's synthetic steps carry a placeholder
    action="click" with an empty element for every step (no click ever
    happened -- these are just uploaded screenshots). step_heading used to
    fall back to a fabricated "Click the screen" for every single heading in
    a manifest-free build; it must now fall back to a bare "Step N" instead,
    since there's no real action to describe."""
    import io

    import pipeline.server as server_module

    monkeypatch.setattr(server_module, "caption_images", lambda paths, *a, **k: [None] * len(paths))

    client = _make_client(tmp_path)

    def png(color):
        buf = io.BytesIO()
        Image.new("RGB", (120, 90), color).save(buf, "PNG")
        return buf.getvalue()

    files = [
        ("files", ("a.png", png((10, 10, 10)), "image/png")),
        ("files", ("b.png", png((20, 20, 20)), "image/png")),
    ]
    resp = client.post(
        "/ui/build", data={"title": "No Fabrication SOP"}, files=files, follow_redirects=False
    )
    session_id = resp.headers["location"].rsplit("/", 1)[-1]
    _confirm_all_steps(client, session_id)
    status = _wait_for_terminal_status(client, session_id)
    assert status["status"] == "done"

    md = client.get(f"/sessions/{session_id}/doc.md").text
    assert "Click the screen" not in md
    assert "## Step 1" in md
    assert "## Step 2" in md


def test_photo_build_canonicalizes_inconsistent_transcript_spelling(tmp_path, monkeypatch):
    """Regression: a real photo-mode document shipped three different
    spellings of the same product name ("Hillshire" in the title,
    "Hilsshier"/"Hilschier" in two step texts) because a raw narration
    transcript is placed verbatim with no manifest ground truth to
    round-trip-gate it against (see consistency.py's module docstring).
    consistency.canonicalize_terms should now merge near-duplicate
    spellings into one, anchored on the user-typed title when it matches
    one of the variants."""
    import io

    import pipeline.server as server_module

    monkeypatch.setattr(server_module, "caption_images", lambda paths, *a, **k: [None] * len(paths))

    client = _make_client(tmp_path)

    def png(color):
        buf = io.BytesIO()
        Image.new("RGB", (120, 90), color).save(buf, "PNG")
        return buf.getvalue()

    files = [
        ("files", ("a.png", png((10, 10, 10)), "image/png")),
        ("files", ("b.png", png((20, 20, 20)), "image/png")),
        (
            "transcript_file",
            (
                "t.md",
                b"Step 1: Select the 'Hilsshier Windows 11.7z' archive and click "
                b"Extract all.\nStep 2: Extract the 'Hilschier Windows 11.7z' "
                b"archiving file.",
                "text/markdown",
            ),
        ),
    ]
    resp = client.post(
        "/ui/build",
        data={"title": "Hilschier Driver Install"},
        files=files,
        follow_redirects=False,
    )
    session_id = resp.headers["location"].rsplit("/", 1)[-1]
    _confirm_all_steps(client, session_id)
    status = _wait_for_terminal_status(client, session_id)
    assert status["status"] == "done"

    md = client.get(f"/sessions/{session_id}/doc.md").text
    assert "Hillshire" not in md
    assert "Hilsshier" not in md
    assert md.count("Hilschier") == 3  # title + step 1 + step 2, one consistent spelling

    report = client.get(f"/sessions/{session_id}/report").json()
    assert report["consistency"] == [{"canonical": "Hilschier", "variants": ["Hilsshier"]}]


def test_add_transcript_to_existing_session_then_rerender(tmp_path):
    """A transcript can be attached from the review page after the fact: POST
    /ui/sessions/{id}/transcript saves it and re-renders, and the narration
    then appears in the regenerated doc."""
    client = _make_client(tmp_path)
    session_id, status = _create_and_wait(client, tmp_path)
    assert status["status"] == "done"
    assert "Narration" not in client.get(f"/sessions/{session_id}/doc.md").text

    transcript = "1. Open the console.\n2. Enter the name.\n3. Check downloads."
    resp = client.post(
        f"/ui/sessions/{session_id}/transcript",
        files=[("transcript_file", ("n.md", transcript.encode(), "text/markdown"))],
        follow_redirects=False,
    )
    assert resp.status_code == 303
    status = _wait_for_terminal_status(client, session_id)
    assert status["status"] == "done"
    md = client.get(f"/sessions/{session_id}/doc.md").text
    assert "Open the console." in md
    assert "**Narration:**" in md


def test_add_bad_transcript_to_session_returns_400(tmp_path):
    client = _make_client(tmp_path)
    session_id, _ = _create_and_wait(client, tmp_path)
    resp = client.post(
        f"/ui/sessions/{session_id}/transcript",
        files=[("transcript_file", ("bad.xyz", b"nope", "application/octet-stream"))],
    )
    assert resp.status_code == 400


def test_upload_bad_transcript_extension_returns_400(tmp_path):
    client = _make_client(tmp_path)
    manifest_json, files = _manifest_and_files(tmp_path)
    files.append(("transcript_file", ("audio.mp3", b"not a transcript", "audio/mpeg")))
    resp = client.post("/sessions", data={"manifest_json": manifest_json}, files=files)
    assert resp.status_code == 400
    assert "transcript" in resp.json()["detail"]


def test_upload_missing_screenshots_returns_400_naming_them(tmp_path):
    """A manifest whose referenced screenshots aren't all uploaded must be
    rejected up front with a clear, actionable 400 that names the missing
    files -- not accepted and then failed in the background with a cryptic
    FileNotFoundError. Regression test for the "internal error" a user hits
    when they miss a PNG in the upload form's multi-select."""
    client = _make_client(tmp_path)
    manifest_json, files = _manifest_and_files(tmp_path)
    # Drop everything except the first screenshot (002.png, 003.png missing).
    partial = files[:1]

    resp = client.post("/sessions", data={"manifest_json": manifest_json}, files=partial)
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "002.png" in detail and "003.png" in detail
    assert "001.png" not in detail  # the one that WAS provided isn't reported missing

    # Nothing was persisted for the rejected upload.
    assert not any((tmp_path / "sessions").iterdir())


def test_ui_upload_missing_screenshots_returns_400(tmp_path):
    """The browser upload form (POST /ui/upload) enforces the same
    screenshot-coverage check as the JSON API."""
    client = _make_client(tmp_path)
    manifest_json, files = _manifest_and_files(tmp_path)
    upload_files = [("manifest_file", ("manifest.json", manifest_json, "application/json"))]
    upload_files += files[:1]  # only 001.png

    resp = client.post("/ui/upload", files=upload_files, follow_redirects=False)
    assert resp.status_code == 400
    assert "002.png" in resp.json()["detail"]


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
    # The traversal filename reduces to the basename "escape.png", which is
    # not one of the manifest's referenced screenshots, so the upload is
    # rejected up front (missing-screenshots 400) before any file is written.
    assert resp.status_code == 400
    # Critically, nothing was ever written outside sessions_root -- and, since
    # the upload is rejected before the write loop runs, nothing named
    # escape.png was written anywhere at all.
    sessions_root = tmp_path / "sessions"
    assert not (sessions_root / "escape.png").exists()
    assert not list(sessions_root.rglob("escape.png"))


def test_unwritable_sessions_root_fails_loudly_at_startup(tmp_path, monkeypatch):
    """If --sessions-root isn't writable (the real bug: an unelevated server
    pointed at a Program Files dir it couldn't write), the server must fail at
    startup with a clear message -- not start fine and then throw a bare 500
    from deep in the ingest path on every upload. mkdir(exist_ok=True) alone
    can't catch this (it succeeds on an existing-but-unwritable dir), so a
    write probe does."""
    real_write = Path.write_text

    def boom(self, *args, **kwargs):
        if self.name == ".sopforge-write-test":
            raise PermissionError("Access to the path is denied")
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(RuntimeError, match="not writable"):
        create_app(sessions_root=tmp_path / "sessions", llm_client_factory=stub_llm_client_factory)


def test_config_page_renders_and_saves(tmp_path):
    """GET /ui/config renders the editor; POST saves valid changes to the
    isolated config file and they read back via GET /config."""
    client = _make_client(tmp_path)

    page = client.get("/ui/config")
    assert page.status_code == 200
    assert "Configuration" in page.text
    assert "qwen3:32b" in page.text  # current steps model shown
    assert 'name="steps_max_concurrency"' in page.text
    assert 'name="vision_max_concurrency"' in page.text
    assert 'name="document_author"' in page.text
    assert 'name="document_doc_no_prefix"' in page.text

    resp = client.post(
        "/ui/config",
        data={
            "steps_provider": "openrouter",
            "steps_endpoint": "http://x/v1",
            "steps_model": "anthropic/claude-3.5-haiku",
            "steps_max_concurrency": "5",
            "narrative_provider": "ollama",
            "narrative_endpoint": "http://192.168.200.60:11434/v1",
            "narrative_model": "qwen3:32b",
            "narrative_passes": "3",
            "vision_provider": "ollama",
            "vision_endpoint": "http://192.168.200.60:11434/v1",
            "vision_model": "qwen2.5vl:7b",
            "vision_enabled": "on",
            "vision_max_concurrency": "2",
            "document_author": "Jane Q",
            "document_doc_no_prefix": "SOP",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    cfg = client.get("/config").json()
    assert cfg["steps"]["provider"] == "openrouter"
    assert cfg["steps"]["model"] == "anthropic/claude-3.5-haiku"
    assert cfg["steps"]["max_concurrency"] == 5
    assert cfg["vision"]["enabled"] is True
    assert cfg["vision"]["max_concurrency"] == 2
    assert cfg["document"]["author"] == "Jane Q"
    assert cfg["document"]["doc_no_prefix"] == "SOP"


def test_config_save_preserves_document_and_vision_concurrency_when_form_omits_them(tmp_path):
    """Regression: an earlier version of the save handler rebuilt the config
    purely from named form fields, and never included document.*/
    vision.max_concurrency in that rebuild -- so ANY save (even one that only
    changes, say, the steps model) silently reset those two to their pydantic
    defaults, discarding whatever was actually configured. A save that omits
    document_author (e.g. a client that hasn't loaded the new fields yet)
    must still preserve the existing value, not reset it."""
    client = _make_client(tmp_path)

    first = client.post(
        "/ui/config",
        data={
            "steps_provider": "ollama",
            "steps_endpoint": "http://x/v1",
            "steps_model": "qwen3:32b",
            "narrative_provider": "ollama",
            "narrative_endpoint": "http://x/v1",
            "narrative_model": "qwen3:32b",
            "vision_provider": "ollama",
            "vision_endpoint": "http://x/v1",
            "vision_model": "qwen2.5vl:7b",
            "vision_max_concurrency": "3",
            "document_author": "Jane Q",
            "document_doc_no_prefix": "SOP",
        },
        follow_redirects=False,
    )
    assert first.status_code == 303

    # A second save that only changes the steps model, submitted WITHOUT the
    # document_author field at all (simulating a stale/partial form post).
    second = client.post(
        "/ui/config",
        data={
            "steps_provider": "ollama",
            "steps_endpoint": "http://x/v1",
            "steps_model": "qwen3:14b",
            "narrative_provider": "ollama",
            "narrative_endpoint": "http://x/v1",
            "narrative_model": "qwen3:32b",
            "vision_provider": "ollama",
            "vision_endpoint": "http://x/v1",
            "vision_model": "qwen2.5vl:7b",
        },
        follow_redirects=False,
    )
    assert second.status_code == 303

    cfg = client.get("/config").json()
    assert cfg["steps"]["model"] == "qwen3:14b"  # the actual intended change took effect
    assert cfg["document"]["author"] == "Jane Q"  # preserved, not reset to "SOPForge"
    assert cfg["vision"]["max_concurrency"] == 3  # preserved, not reset to 4


def test_config_page_model_datalists(tmp_path):
    """The Model fields are <input list> + <datalist> suggestions, not plain
    free text -- still accept any typed value (Ollama pulls, new models). The
    canonical datalist per field is scoped to the CURRENTLY-SAVED provider
    (the default fixture config is all-ollama), and a per-provider datalist
    exists for every provider that field's suggestions dict knows about, so
    the provider-select's onchange handler can swap in the right options."""
    from pipeline.webui.pages import _MODEL_SUGGESTIONS

    client = _make_client(tmp_path)

    page = client.get("/ui/config")
    assert page.status_code == 200
    text = page.text

    for key, default_model in (
        ("steps", "qwen3:32b"),
        ("narrative", "qwen3:32b"),
        ("vision", "qwen2.5vl:7b"),
    ):
        suggestions_id = f"{key}_model_suggestions"
        assert f'list="{suggestions_id}"' in text
        match = re.search(rf'<datalist id="{suggestions_id}">(.*?)</datalist>', text, re.DOTALL)
        assert match, f"missing canonical datalist for {key}"
        assert default_model in match.group(1)

        for provider in _MODEL_SUGGESTIONS[key]:
            per_provider_id = f"{key}_model_suggestions_{provider}"
            assert f'<datalist id="{per_provider_id}">' in text, (
                f"missing per-provider datalist for {key}/{provider}"
            )

    assert "anthropic" not in _MODEL_SUGGESTIONS["vision"], (
        "vision suggestions should not have a bare anthropic entry"
    )
    vision_datalists = re.findall(
        r'<datalist id="vision_model_suggestions[^"]*">(.*?)</datalist>', text, re.DOTALL
    )
    assert vision_datalists, "vision datalists missing"
    for options_html in vision_datalists:
        options = re.findall(r'<option value="([^"]*)">', options_html)
        assert not any(opt.startswith("claude-") for opt in options), (
            "vision datalist should not suggest bare anthropic models"
        )

    resp = client.post(
        "/ui/config",
        data={
            "steps_provider": "ollama",
            "steps_endpoint": "http://192.168.200.60:11434/v1",
            "steps_model": "my-custom:latest",
            "narrative_provider": "ollama",
            "narrative_endpoint": "http://192.168.200.60:11434/v1",
            "narrative_model": "qwen3:32b",
            "narrative_passes": "3",
            "vision_provider": "ollama",
            "vision_endpoint": "http://192.168.200.60:11434/v1",
            "vision_model": "qwen2.5vl:7b",
            "vision_enabled": "on",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    cfg = client.get("/config").json()
    assert cfg["steps"]["model"] == "my-custom:latest"


def test_config_provider_select_switches_suggestions(tmp_path):
    """Each Provider <select> carries an onchange handler that swaps the
    canonical datalist's contents to match the newly-selected provider's
    per-provider datalist -- and every id it could reference actually exists
    on the page (no dangling getElementById reference)."""
    from pipeline.webui.pages import _PROVIDERS, _VISION_PROVIDERS

    client = _make_client(tmp_path)
    text = client.get("/ui/config").text

    for key, providers in (
        ("steps", _PROVIDERS),
        ("narrative", _PROVIDERS),
        ("vision", _VISION_PROVIDERS),
    ):
        select_match = re.search(rf'<select name="{key}_provider"[^>]*>', text)
        assert select_match, f"missing provider select for {key}"
        select_tag = select_match.group(0)
        assert "onchange=" in select_tag
        assert f"{key}_model_suggestions" in select_tag

        for provider in providers:
            per_provider_id = f"{key}_model_suggestions_{provider}"
            assert f'<datalist id="{per_provider_id}">' in text, (
                f"select for {key} can select {provider} but no datalist {per_provider_id} exists"
            )


def test_config_model_inputs_focus_clear(tmp_path):
    """Model inputs clear on focus (so the browser shows the full unfiltered
    suggestion list instead of just the current value, since a datalist
    filters to options matching the current text) and restore the original
    value on blur only if left empty."""
    client = _make_client(tmp_path)
    text = client.get("/ui/config").text

    for key in ("steps", "narrative", "vision"):
        input_match = re.search(rf'<input type="text" name="{key}_model"[^>]*>', text)
        assert input_match, f"missing model input for {key}"
        input_tag = input_match.group(0)
        assert 'value="' in input_tag
        assert f'list="{key}_model_suggestions"' in input_tag
        assert "onfocus=\"this.dataset.prev=this.value;this.value=''\"" in input_tag
        assert "onblur=\"if(!this.value)this.value=this.dataset.prev||''\"" in input_tag


def test_config_save_empty_model_keeps_existing(tmp_path):
    """A submitted empty narrative_model (e.g. a race with the focus-clear
    trick, or a stray Enter mid-clear) must NOT blank out the saved model --
    it should fall back to whatever was already saved."""
    client = _make_client(tmp_path)

    before = client.get("/config").json()
    existing_narrative_model = before["narrative"]["model"]
    assert existing_narrative_model  # sanity: fixture config has a real model

    resp = client.post(
        "/ui/config",
        data={
            "steps_provider": "ollama",
            "steps_endpoint": "http://192.168.200.60:11434/v1",
            "steps_model": "qwen3:14b",
            "narrative_provider": "ollama",
            "narrative_endpoint": "http://192.168.200.60:11434/v1",
            "narrative_model": "",
            "narrative_passes": "3",
            "vision_provider": "ollama",
            "vision_endpoint": "http://192.168.200.60:11434/v1",
            "vision_model": "qwen2.5vl:7b",
            "vision_enabled": "on",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    after = client.get("/config").json()
    assert after["narrative"]["model"] == existing_narrative_model


def test_config_save_rejects_invalid_provider(tmp_path):
    client = _make_client(tmp_path)
    resp = client.post(
        "/ui/config",
        data={
            "steps_provider": "not-a-provider",
            "steps_endpoint": "http://x",
            "steps_model": "m",
            "narrative_provider": "ollama",
            "narrative_endpoint": "http://x",
            "narrative_model": "m",
            "narrative_passes": "1",
            "vision_provider": "ollama",
            "vision_endpoint": "http://x",
            "vision_model": "m",
        },
    )
    assert resp.status_code == 400


def test_config_save_rejects_cross_site_origin(tmp_path):
    client = _make_client(tmp_path)
    resp = client.post(
        "/ui/config",
        data={"steps_provider": "ollama", "steps_endpoint": "http://x", "steps_model": "m"},
        headers={"Origin": "http://evil.example.com"},
    )
    assert resp.status_code == 403


def test_csrf_rejects_lookalike_host(tmp_path):
    # "http://127.0.0.1.evil.com" must NOT pass a prefix check -- the guard
    # compares the exact host.
    client = _make_client(tmp_path)
    resp = client.post(
        "/ui/config",
        data={"steps_provider": "ollama", "steps_endpoint": "http://x", "steps_model": "m"},
        headers={"Origin": "http://127.0.0.1.evil.com"},
    )
    assert resp.status_code == 403


def test_shutdown_rejects_cross_site_origin(tmp_path):
    # /shutdown is a state-changing POST -- the CSRF guard must cover it too, so
    # a malicious page can't auto-submit a form to kill the server.
    client = _make_client(tmp_path)
    resp = client.post("/shutdown", headers={"Origin": "http://evil.example.com"})
    assert resp.status_code == 403


def test_version_endpoint_and_library_footer_report_the_package_version(tmp_path):
    """The running version must be discoverable both programmatically (GET
    /version) and visually (library page footer), so a user can confirm which
    build they're on."""
    from pipeline import __version__

    client = _make_client(tmp_path)

    resp = client.get("/version")
    assert resp.status_code == 200
    assert resp.json() == {"version": __version__}

    page = client.get("/ui")
    assert page.status_code == 200
    assert __version__ in page.text


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
