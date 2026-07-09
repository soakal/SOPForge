"""Steps-review gate: POST /sessions?stage=1 (and /ui/upload, /ui/build,
which always stage) hold generation until the user confirms which captured
steps to keep. Exercises the whole thing through a real TestClient with the
shared stub LLM client, same pattern as test_server.py."""

import shutil
import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from pipeline.config import default_config_path
from pipeline.manifest import load_manifest
from pipeline.server import create_app

from _stub_llm import stub_llm_client_factory

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _make_client(tmp_path):
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


def _create_staged_session(client, tmp_path):
    manifest_json, files = _manifest_and_files(tmp_path)
    resp = client.post(
        "/sessions", data={"manifest_json": manifest_json, "stage": "1"}, files=files
    )
    return resp


def _wait_for_terminal_status(client, session_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/sessions/{session_id}/status").json()
        if status["status"] in ("done", "error"):
            return status
        time.sleep(0.05)
    raise AssertionError(f"session {session_id} never reached a terminal status")


def test_staged_session_does_not_auto_submit(tmp_path):
    client = _make_client(tmp_path)
    resp = _create_staged_session(client, tmp_path)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "staged"

    # jobs never touched it -- status endpoint must reflect "staged" too,
    # not crash on a job that was never submitted.
    status_resp = client.get(f"/sessions/{body['session_id']}/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "staged"


def test_ui_sessions_page_renders_checklist_for_staged_session(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_staged_session(client, tmp_path).json()["session_id"]

    page = client.get(f"/ui/sessions/{session_id}")
    assert page.status_code == 200
    assert "Review captured steps" in page.text
    for step_id in ("step-001", "step-002", "step-003"):
        assert step_id in page.text
    assert page.text.count('type="checkbox" name="keep"') == 3
    assert f"/sessions/{session_id}/raw/001.png" in page.text
    # Each card also carries an editable position number, pre-filled with
    # its current 1-based position, so steps can be reordered before
    # confirming.
    assert 'name="pos-step-001" value="1"' in page.text
    assert 'name="pos-step-002" value="2"' in page.text
    assert 'name="pos-step-003" value="3"' in page.text


def test_ui_sessions_page_has_a_shared_lightbox_not_per_card_overlays(tmp_path):
    """Clicking a screenshot thumbnail should show it full size via ONE
    shared modal (O(1) markup), not an overlay duplicated per card (O(n))."""
    client = _make_client(tmp_path)
    session_id = _create_staged_session(client, tmp_path).json()["session_id"]

    page = client.get(f"/ui/sessions/{session_id}")
    assert page.status_code == 200
    text = page.text

    assert text.count('id="lightbox"') == 1
    assert text.count('class="shot"') == 3  # one per step, same as the checkbox count
    assert "preventDefault" in text
    assert "Escape" in text
    assert "max-width:95vw" in text
    assert "max-height:90vh" in text


def test_raw_screenshot_route_serves_staged_image(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_staged_session(client, tmp_path).json()["session_id"]

    resp = client.get(f"/sessions/{session_id}/raw/001.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_confirm_steps_drops_unchecked_steps_before_generation(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_staged_session(client, tmp_path).json()["session_id"]

    resp = client.post(
        f"/ui/sessions/{session_id}/confirm-steps",
        data={"keep": ["step-001", "step-003"]},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    status = _wait_for_terminal_status(client, session_id)
    assert status["status"] == "done"

    manifest = load_manifest(tmp_path / "sessions" / session_id / "manifest.json")
    assert manifest.step_ids() == ["step-001", "step-003"]

    report = client.get(f"/sessions/{session_id}/report").json()
    # step-002 (the dropped step) is gone; step-003's empty metadata is still
    # flagged, proving the sidecar report reflects the reduced manifest.
    assert "step-002" not in report["empty_metadata_steps"]
    assert "step-003" in report["empty_metadata_steps"]


def test_confirm_steps_reorders_steps_before_generation(tmp_path):
    """The position number, not just the keep checkbox, controls the
    manifest's final step order -- here moving step-003 to the front."""
    client = _make_client(tmp_path)
    session_id = _create_staged_session(client, tmp_path).json()["session_id"]

    resp = client.post(
        f"/ui/sessions/{session_id}/confirm-steps",
        data={
            "keep": ["step-001", "step-002", "step-003"],
            "pos-step-001": "2",
            "pos-step-002": "3",
            "pos-step-003": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    status = _wait_for_terminal_status(client, session_id)
    assert status["status"] == "done"

    manifest = load_manifest(tmp_path / "sessions" / session_id / "manifest.json")
    assert manifest.step_ids() == ["step-003", "step-001", "step-002"]


def test_confirm_steps_decimal_position_inserts_without_renumbering(tmp_path):
    """A decimal position ("1.5") inserts a step between two others without
    needing to renumber every other card -- the review page's whole point."""
    client = _make_client(tmp_path)
    session_id = _create_staged_session(client, tmp_path).json()["session_id"]

    resp = client.post(
        f"/ui/sessions/{session_id}/confirm-steps",
        data={
            "keep": ["step-001", "step-002", "step-003"],
            "pos-step-001": "1",
            "pos-step-002": "3",
            "pos-step-003": "1.5",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    _wait_for_terminal_status(client, session_id)

    manifest = load_manifest(tmp_path / "sessions" / session_id / "manifest.json")
    assert manifest.step_ids() == ["step-001", "step-003", "step-002"]


def test_confirm_steps_reorder_without_position_keeps_submission_order(tmp_path):
    """No pos-* fields at all (the documented curl API shape, and every
    caller that predates the reorder feature) must keep working exactly as
    before -- steps land in whatever order `keep` was submitted in."""
    client = _make_client(tmp_path)
    session_id = _create_staged_session(client, tmp_path).json()["session_id"]

    resp = client.post(
        f"/ui/sessions/{session_id}/confirm-steps",
        data={"keep": ["step-003", "step-001"]},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    _wait_for_terminal_status(client, session_id)

    manifest = load_manifest(tmp_path / "sessions" / session_id / "manifest.json")
    assert manifest.step_ids() == ["step-003", "step-001"]


def test_confirm_steps_rejects_non_numeric_position(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_staged_session(client, tmp_path).json()["session_id"]

    resp = client.post(
        f"/ui/sessions/{session_id}/confirm-steps",
        data={"keep": ["step-001"], "pos-step-001": "not-a-number"},
    )
    assert resp.status_code == 400


def test_confirm_steps_reorders_photo_build_session(tmp_path, monkeypatch):
    """Same reorder mechanism, proven on the manifest-free screenshots-only
    build (POST /ui/build) -- confirms the shared confirm-steps route needs
    no per-flow branching to support reordering."""
    import io

    import pipeline.server as server_module

    # Vision is enabled by default; stub it so this never attempts a real
    # network call to the (unreachable in tests) default Ollama endpoint --
    # this test is about reordering, not captioning.
    monkeypatch.setattr(server_module, "caption_images", lambda paths, *a, **k: [None] * len(paths))

    client = _make_client(tmp_path)

    def png(color):
        buf = io.BytesIO()
        Image.new("RGB", (120, 90), color).save(buf, "PNG")
        return buf.getvalue()

    files = [
        ("files", ("a.png", png((10, 10, 10)), "image/png")),
        ("files", ("b.png", png((20, 20, 20)), "image/png")),
        ("files", ("c.png", png((30, 30, 30)), "image/png")),
    ]
    resp = client.post(
        "/ui/build", data={"title": "Reorder Photo SOP"}, files=files, follow_redirects=False
    )
    assert resp.status_code == 303
    session_id = resp.headers["location"].rsplit("/", 1)[-1]

    resp = client.post(
        f"/ui/sessions/{session_id}/confirm-steps",
        data={
            "keep": ["step-001", "step-002", "step-003"],
            "pos-step-001": "3",
            "pos-step-002": "2",
            "pos-step-003": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    status = _wait_for_terminal_status(client, session_id)
    assert status["status"] == "done"

    manifest = load_manifest(tmp_path / "sessions" / session_id / "manifest.json")
    assert manifest.step_ids() == ["step-003", "step-002", "step-001"]


def test_confirm_steps_rejects_empty_selection(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_staged_session(client, tmp_path).json()["session_id"]

    resp = client.post(f"/ui/sessions/{session_id}/confirm-steps", data={})
    assert resp.status_code == 400


def test_confirm_steps_rejects_already_submitted_session(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_staged_session(client, tmp_path).json()["session_id"]

    first = client.post(
        f"/ui/sessions/{session_id}/confirm-steps",
        data={"keep": ["step-001"]},
        follow_redirects=False,
    )
    assert first.status_code == 303

    second = client.post(
        f"/ui/sessions/{session_id}/confirm-steps",
        data={"keep": ["step-001"]},
    )
    assert second.status_code == 409


def test_ui_upload_form_stages_and_delete_works_on_staged_session(tmp_path):
    client = _make_client(tmp_path)
    manifest_json, files = _manifest_and_files(tmp_path)
    manifest_file = ("manifest.json", manifest_json, "application/json")

    resp = client.post(
        "/ui/upload",
        data={},
        files=[("manifest_file", manifest_file)] + files,
        follow_redirects=False,
    )
    assert resp.status_code == 303
    session_id = resp.headers["location"].rsplit("/", 1)[-1]

    page = client.get(f"/ui/sessions/{session_id}")
    assert "Review captured steps" in page.text

    # Deleting a still-staged (never-submitted) session must not crash on
    # jobs.status() returning {} for a job id that was never registered.
    delete_resp = client.post(f"/ui/sessions/{session_id}/delete", follow_redirects=False)
    assert delete_resp.status_code == 303
