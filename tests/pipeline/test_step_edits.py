"""Manual per-step edit persistence (edits.json) and its interaction with a
full rerender: preserved by default, discardable via ?discard_edits=1, and
never silently reworded by the optional polish pass. No edit ROUTE exists
yet (that's the next task) -- these tests write edits.json directly to the
session dir, exactly the way the future edit route will."""

import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from pipeline.config import default_config_path, load_models_config, save_models_config
from pipeline.server import create_app

import pipeline.server as server_module

from _stub_llm import stub_llm_client_factory


def _stub_assemble_docx(monkeypatch):
    """Replaces the real docx renderer (which needs the external sop_lib
    package, unavailable in this sandbox -- see README) with a trivial
    stub, so a test can reach status "done" for real and actually verify
    behavior instead of early-returning on `if status != "done"`."""

    def fake(manifest, step_results, annotated_dir, docx_path, **kwargs):
        Path(docx_path).write_bytes(b"fake docx")
        return str(docx_path), []

    monkeypatch.setattr(server_module, "assemble_docx", fake)


def test_load_edits_defaults_to_empty_dict(tmp_path):
    assert server_module._load_edits(tmp_path) == {}


def test_save_load_clear_edit_round_trip(tmp_path):
    server_module._save_edit(tmp_path, "step-001", "A human wrote this.")
    edits = server_module._load_edits(tmp_path)
    assert edits["step-001"]["text"] == "A human wrote this."
    assert "edited_utc" in edits["step-001"]

    assert server_module._clear_edit(tmp_path, "step-001") is True
    assert server_module._load_edits(tmp_path) == {}
    assert server_module._clear_edit(tmp_path, "step-001") is False  # already gone


def test_clear_all_edits_removes_the_file(tmp_path):
    server_module._save_edit(tmp_path, "step-001", "x")
    server_module._save_edit(tmp_path, "step-002", "y")
    assert (tmp_path / "edits.json").exists()
    server_module._clear_all_edits(tmp_path)
    assert not (tmp_path / "edits.json").exists()
    assert server_module._load_edits(tmp_path) == {}


def test_load_edits_tolerates_a_corrupt_file(tmp_path):
    (tmp_path / "edits.json").write_text("not json", encoding="utf-8")
    assert server_module._load_edits(tmp_path) == {}


def test_load_edits_tolerates_valid_json_with_the_wrong_shape(tmp_path):
    """Caught by automated PR review: json.loads(...)["steps"] only guards
    OSError/ValueError/KeyError -- a file that parses fine as JSON but
    isn't the expected object (a bare list/string/number, or a "steps"
    value that isn't itself an object) must degrade to {} the same as a
    missing file, not raise TypeError/AttributeError mid-generation."""
    for bad_content in ("[]", '"just a string"', "123", '{"steps": "not an object"}', "{}"):
        (tmp_path / "edits.json").write_text(bad_content, encoding="utf-8")
        assert server_module._load_edits(tmp_path) == {}, bad_content


def test_apply_manual_edits_tolerates_a_malformed_per_step_entry(tmp_path):
    """A "steps" object whose per-step value isn't {"text": str, ...} (a
    bare string, or a dict missing "text") must be treated as no-edit for
    that step, not raise."""
    (tmp_path / "edits.json").write_text(
        json.dumps({"steps": {"step-001": "not a dict", "step-002": {"edited_utc": "x"}}}),
        encoding="utf-8",
    )
    step_results = [
        {"step_id": "step-001", "text": "original one", "used_fallback": False},
        {"step_id": "step-002", "text": "original two", "used_fallback": False},
    ]
    applied = server_module._apply_manual_edits(tmp_path, step_results)
    assert applied == []
    assert step_results[0]["text"] == "original one"
    assert step_results[1]["text"] == "original two"


def test_load_step_state_tolerates_valid_json_with_the_wrong_shape(tmp_path):
    for bad_content in ("[]", '"just a string"', "123"):
        (tmp_path / "steps.json").write_text(bad_content, encoding="utf-8")
        assert server_module._load_step_state(tmp_path) is None
        assert server_module._load_step_index(tmp_path) is None


def test_load_step_index_tolerates_a_non_list_steps_value(tmp_path):
    (tmp_path / "steps.json").write_text(
        json.dumps({"version": 2, "steps": "not a list"}), encoding="utf-8"
    )
    assert server_module._load_step_index(tmp_path) is None


def test_apply_manual_edits_overwrites_text_and_flags_only_matching_steps(tmp_path):
    server_module._save_edit(tmp_path, "step-002", "A human wrote this.")
    step_results = [
        {"step_id": "step-001", "text": "original one", "used_fallback": True},
        {"step_id": "step-002", "text": "original two", "used_fallback": True},
    ]
    applied = server_module._apply_manual_edits(tmp_path, step_results)
    assert applied == ["step-002"]
    assert step_results[0]["text"] == "original one"
    assert step_results[0].get("manually_edited") is None
    assert step_results[1]["text"] == "A human wrote this."
    assert step_results[1]["used_fallback"] is False
    assert step_results[1]["manually_edited"] is True


def test_apply_manual_edits_ignores_an_override_for_a_step_not_present(tmp_path):
    """L1 guard: an edit left over for a step_id that no longer exists in
    step_results (e.g. dropped via the steps-review page) must never
    fabricate an entry."""
    server_module._save_edit(tmp_path, "step-999", "orphaned edit")
    step_results = [{"step_id": "step-001", "text": "original", "used_fallback": False}]
    applied = server_module._apply_manual_edits(tmp_path, step_results)
    assert applied == []
    assert len(step_results) == 1
    assert step_results[0]["text"] == "original"


def test_apply_manual_edits_no_op_when_no_edits_exist(tmp_path):
    step_results = [{"step_id": "step-001", "text": "original", "used_fallback": False}]
    assert server_module._apply_manual_edits(tmp_path, step_results) == []
    assert step_results[0]["text"] == "original"


def _make_client(tmp_path):
    cfg = tmp_path / "models.toml"
    shutil.copyfile(default_config_path(), cfg)
    app = create_app(
        sessions_root=tmp_path / "sessions",
        llm_client_factory=stub_llm_client_factory,
        narrative_llm_client_factory=stub_llm_client_factory,
        config_path=cfg,
    )
    return TestClient(app), cfg


def _create_and_wait(client, tmp_path):
    from pathlib import Path

    from PIL import Image

    from pipeline.manifest import load_manifest

    fixtures = Path(__file__).resolve().parent.parent.parent / "fixtures"
    manifest_path = fixtures / "sample-manifest.json"
    manifest = load_manifest(manifest_path)
    shots_dir = tmp_path / "shots"
    shots_dir.mkdir(exist_ok=True)
    files = []
    for step in manifest.steps:
        p = shots_dir / step.screenshot
        Image.new("RGB", (1920, 1080), (255, 255, 255)).save(p)
        files.append(("files", (step.screenshot, p.open("rb"), "image/png")))
    resp = client.post(
        "/sessions", data={"manifest_json": manifest_path.read_text(encoding="utf-8")}, files=files
    )
    session_id = resp.json()["session_id"]
    import time

    deadline = time.monotonic() + 10.0
    status = {"status": "queued"}
    while time.monotonic() < deadline:
        status = client.get(f"/sessions/{session_id}/status").json()
        if status["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    return session_id, status, manifest


def test_manual_edit_survives_a_full_rerender(tmp_path, monkeypatch):
    _stub_assemble_docx(monkeypatch)
    client, _cfg = _make_client(tmp_path)
    session_id, status, manifest = _create_and_wait(client, tmp_path)
    assert status["status"] == "done", status
    session_dir = tmp_path / "sessions" / session_id
    edited_step = manifest.steps[0].id
    server_module._save_edit(session_dir, edited_step, "A human's exact fix.")

    resp = client.post(f"/sessions/{session_id}/rerender")
    assert resp.status_code == 200
    status = _wait_done(client, session_id)
    assert status["status"] == "done", status

    md = (session_dir / "doc.md").read_text(encoding="utf-8")
    assert "A human's exact fix." in md
    report = json.loads((session_dir / "report.json").read_text(encoding="utf-8"))
    assert edited_step in report.get("manually_edited_steps", [])
    assert edited_step not in report.get("template_fallback_steps", [])


def test_rerender_with_discard_edits_drops_the_edit(tmp_path, monkeypatch):
    _stub_assemble_docx(monkeypatch)
    client, _cfg = _make_client(tmp_path)
    session_id, status, manifest = _create_and_wait(client, tmp_path)
    assert status["status"] == "done", status
    session_dir = tmp_path / "sessions" / session_id
    edited_step = manifest.steps[0].id
    server_module._save_edit(session_dir, edited_step, "A human's exact fix.")

    resp = client.post(f"/sessions/{session_id}/rerender", params={"discard_edits": "true"})
    assert resp.status_code == 200
    assert not (session_dir / "edits.json").exists()
    status = _wait_done(client, session_id)
    assert status["status"] == "done", status

    md = (session_dir / "doc.md").read_text(encoding="utf-8")
    assert "A human's exact fix." not in md
    report = json.loads((session_dir / "report.json").read_text(encoding="utf-8"))
    assert "manually_edited_steps" not in report


def test_manual_edit_is_not_rewritten_by_the_polish_pass(tmp_path, monkeypatch):
    _stub_assemble_docx(monkeypatch)

    def _uppercasing_polish(narrative_text, step_results, llm):
        polished_narrative = narrative_text.upper() if narrative_text else narrative_text
        polished = []
        for step in step_results:
            new_step = dict(step)
            new_step["text"] = step["text"].upper()
            polished.append(new_step)
        return polished_narrative, polished, {"attempted": True}

    import pipeline.server as srv

    real_generate_polish_fields = srv.generate_polish_fields
    srv.generate_polish_fields = _uppercasing_polish
    try:
        cfg = tmp_path / "models.toml"
        shutil.copyfile(default_config_path(), cfg)
        models_cfg = load_models_config(cfg)
        models_cfg.polish.enabled = True
        save_models_config(models_cfg, cfg)
        app = create_app(
            sessions_root=tmp_path / "sessions",
            llm_client_factory=stub_llm_client_factory,
            narrative_llm_client_factory=stub_llm_client_factory,
            polish_llm_client_factory=lambda section: object(),
            config_path=cfg,
        )
        client = TestClient(app)
        session_id, status, manifest = _create_and_wait(client, tmp_path)
        assert status["status"] == "done", status
        session_dir = tmp_path / "sessions" / session_id
        edited_step = manifest.steps[0].id
        other_step = manifest.steps[1].id
        server_module._save_edit(session_dir, edited_step, "lowercase human edit")

        resp = client.post(f"/sessions/{session_id}/rerender")
        assert resp.status_code == 200
        status = _wait_done(client, session_id)
        assert status["status"] == "done", status

        md = (session_dir / "doc.md").read_text(encoding="utf-8")
        assert "lowercase human edit" in md
        # Scope the "not rewritten" check to the edited step's OWN text
        # field, not the whole document -- a narrative paragraph elsewhere
        # in the doc may independently reference/quote the step (e.g. a
        # [verify] claim synthesized from step text) and that occurrence IS
        # legitimately subject to polish, since narrative_text isn't a
        # manual-edit target the way a step's own text is.
        state = json.loads((session_dir / "steps.json").read_text(encoding="utf-8"))
        edited_entry = next(s for s in state["steps"] if s["step_id"] == edited_step)
        assert edited_entry["text"] == "lowercase human edit"
        # sanity: a NON-edited step's text really did get uppercased, proving
        # the polish stub actually ran and this isn't a false negative.
        other_entry = next(s for s in state["steps"] if s["step_id"] == other_step)
        assert other_entry["text"].isupper()
    finally:
        srv.generate_polish_fields = real_generate_polish_fields


def test_edit_route_replaces_step_text_and_makes_no_llm_call(tmp_path, monkeypatch):
    _stub_assemble_docx(monkeypatch)
    calls = []

    class _CountingStub:
        def chat(self, messages, **kwargs):
            calls.append(messages)
            return "stub reply that never matches any manifest, forcing template fallback"

    cfg = tmp_path / "models.toml"
    shutil.copyfile(default_config_path(), cfg)
    app = create_app(
        sessions_root=tmp_path / "sessions",
        llm_client_factory=lambda: _CountingStub(),
        narrative_llm_client_factory=lambda: _CountingStub(),
        config_path=cfg,
    )
    client = TestClient(app)
    session_id, status, manifest = _create_and_wait(client, tmp_path)
    assert status["status"] == "done", status
    session_dir = tmp_path / "sessions" / session_id
    step_id = manifest.steps[0].id
    calls_before_edit = len(calls)

    resp = client.post(
        f"/ui/sessions/{session_id}/steps/{step_id}",
        data={"text": "Click the 'Save' button precisely here."},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    status = _wait_done(client, session_id)
    assert status["status"] == "done", status

    assert len(calls) == calls_before_edit  # the edit route made zero new LLM calls

    md = (session_dir / "doc.md").read_text(encoding="utf-8")
    assert "Click the 'Save' button precisely here." in md

    state = json.loads((session_dir / "steps.json").read_text(encoding="utf-8"))
    entry = next(s for s in state["steps"] if s["step_id"] == step_id)
    assert entry["manually_edited"] is True
    assert entry["used_fallback"] is False

    report = json.loads((session_dir / "report.json").read_text(encoding="utf-8"))
    assert step_id in report.get("manually_edited_steps", [])
    assert step_id not in report.get("template_fallback_steps", [])


def test_edit_never_changes_step_count_or_order(tmp_path, monkeypatch):
    _stub_assemble_docx(monkeypatch)
    client, _cfg = _make_client(tmp_path)
    session_id, status, manifest = _create_and_wait(client, tmp_path)
    assert status["status"] == "done", status
    session_dir = tmp_path / "sessions" / session_id
    step_id = manifest.steps[0].id

    resp = client.post(
        f"/ui/sessions/{session_id}/steps/{step_id}",
        data={"text": "An edit."},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    status = _wait_done(client, session_id)
    assert status["status"] == "done", status

    state = json.loads((session_dir / "steps.json").read_text(encoding="utf-8"))
    assert [s["step_id"] for s in state["steps"]] == manifest.step_ids()


def test_edit_rejects_empty_text_unknown_step_and_unfinished_session(tmp_path, monkeypatch):
    _stub_assemble_docx(monkeypatch)
    client, _cfg = _make_client(tmp_path)
    session_id, status, manifest = _create_and_wait(client, tmp_path)
    assert status["status"] == "done", status
    step_id = manifest.steps[0].id

    resp = client.post(f"/ui/sessions/{session_id}/steps/{step_id}", data={"text": "   "})
    assert resp.status_code == 400

    resp = client.post(f"/ui/sessions/{session_id}/steps/not-a-real-step", data={"text": "x"})
    assert resp.status_code == 404

    resp = client.post("/ui/sessions/not-a-real-session/steps/step-001", data={"text": "x"})
    assert resp.status_code == 404


def test_edit_on_a_pre_v2_session_returns_409(tmp_path, monkeypatch):
    _stub_assemble_docx(monkeypatch)
    client, _cfg = _make_client(tmp_path)
    session_id, status, manifest = _create_and_wait(client, tmp_path)
    assert status["status"] == "done", status
    session_dir = tmp_path / "sessions" / session_id
    step_id = manifest.steps[0].id
    # Hand-write a v1-shaped steps.json (no "version"/"narrative_text" keys)
    # to simulate a session generated before task-01 landed.
    (session_dir / "steps.json").write_text(
        json.dumps({"steps": [{"step_id": step_id, "text": "x", "used_fallback": False}]}),
        encoding="utf-8",
    )
    resp = client.post(
        f"/ui/sessions/{session_id}/steps/{step_id}",
        data={"text": "should not apply"},
        follow_redirects=False,
    )
    # The route validates the same preconditions _reexport_session would,
    # synchronously, before ever calling jobs.submit -- so a session too
    # old to edit 409s immediately, and the already-"done" session's status
    # (and its finished downloads) is left untouched rather than getting
    # flipped to "error" by the background job.
    assert resp.status_code == 409
    assert "predates" in resp.json()["detail"]
    status = client.get(f"/sessions/{session_id}/status").json()
    assert status["status"] == "done"


def test_edit_on_a_damaged_steps_json_returns_409_not_a_crash(tmp_path, monkeypatch):
    _stub_assemble_docx(monkeypatch)
    client, _cfg = _make_client(tmp_path)
    session_id, status, manifest = _create_and_wait(client, tmp_path)
    assert status["status"] == "done", status
    session_dir = tmp_path / "sessions" / session_id
    step_id = manifest.steps[0].id
    # version 2, but "steps" is a bare string -- parses as JSON fine, wrong shape.
    (session_dir / "steps.json").write_text(
        json.dumps({"version": 2, "narrative_text": None, "steps": "not a list"}),
        encoding="utf-8",
    )
    resp = client.post(
        f"/ui/sessions/{session_id}/steps/{step_id}",
        data={"text": "should not apply"},
        follow_redirects=False,
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "damaged" in detail or "predates" in detail
    status = client.get(f"/sessions/{session_id}/status").json()
    assert status["status"] == "done"


def test_regenerate_makes_exactly_one_llm_call_for_the_target_step(tmp_path, monkeypatch):
    _stub_assemble_docx(monkeypatch)
    calls = []

    class _RealisticStub:
        def chat(self, messages, **kwargs):
            calls.append(messages)
            content = messages[0]["content"]
            # Echo back something round-trip-passing regardless of which step
            # asked, by pulling the target name out of the prompt's own quotes.
            import re

            m = re.search(r"'([^']+)' in the '([^']+)' window", content)
            if m:
                return f"Click {m.group(1)} in the {m.group(2)} window."
            return "Click somewhere."

    cfg = tmp_path / "models.toml"
    shutil.copyfile(default_config_path(), cfg)
    app = create_app(
        sessions_root=tmp_path / "sessions",
        llm_client_factory=lambda: _RealisticStub(),
        narrative_llm_client_factory=stub_llm_client_factory,
        config_path=cfg,
    )
    client = TestClient(app)
    session_id, status, manifest = _create_and_wait(client, tmp_path)
    assert status["status"] == "done", status
    session_dir = tmp_path / "sessions" / session_id
    step_id = manifest.steps[0].id
    calls_before = len(calls)

    resp = client.post(
        f"/ui/sessions/{session_id}/steps/{step_id}/regenerate", follow_redirects=False
    )
    assert resp.status_code == 303
    status = _wait_done(client, session_id)
    assert status["status"] == "done", status

    assert len(calls) == calls_before + 1
    state = json.loads((session_dir / "steps.json").read_text(encoding="utf-8"))
    entry = next(s for s in state["steps"] if s["step_id"] == step_id)
    assert entry["manually_edited"] is False


def test_regenerate_discards_a_prior_manual_edit_for_that_step_only(tmp_path, monkeypatch):
    _stub_assemble_docx(monkeypatch)
    client, _cfg = _make_client(tmp_path)
    session_id, status, manifest = _create_and_wait(client, tmp_path)
    assert status["status"] == "done", status
    session_dir = tmp_path / "sessions" / session_id
    step_a, step_b = manifest.steps[0].id, manifest.steps[1].id
    server_module._save_edit(session_dir, step_a, "edit A")
    server_module._save_edit(session_dir, step_b, "edit B")

    resp = client.post(
        f"/ui/sessions/{session_id}/steps/{step_a}/regenerate", follow_redirects=False
    )
    assert resp.status_code == 303
    status = _wait_done(client, session_id)
    assert status["status"] == "done", status

    edits = server_module._load_edits(session_dir)
    assert step_a not in edits
    assert step_b in edits


def test_regenerate_is_refused_for_photo_mode_sessions(tmp_path, monkeypatch):
    import io
    import re

    _stub_assemble_docx(monkeypatch)
    monkeypatch.setattr(server_module, "caption_images", lambda paths, *a, **k: [None] * len(paths))

    client, _cfg = _make_client(tmp_path)

    def png(color):
        buf = io.BytesIO()
        from PIL import Image

        Image.new("RGB", (120, 90), color).save(buf, "PNG")
        return buf.getvalue()

    files = [("files", ("a.png", png((10, 10, 10)), "image/png"))]
    resp = client.post(
        "/ui/build", data={"title": "Photo SOP"}, files=files, follow_redirects=False
    )
    assert resp.status_code == 303
    session_id = resp.headers["location"].rsplit("/", 1)[-1]

    page = client.get(f"/ui/sessions/{session_id}")
    step_ids = re.findall(r'name="keep" value="(step-\d+)"', page.text)
    resp = client.post(
        f"/ui/sessions/{session_id}/confirm-steps",
        data={"keep": step_ids, **{f"pos-{sid}": str(i) for i, sid in enumerate(step_ids, 1)}},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    status = _wait_done(client, session_id)
    assert status["status"] == "done", status

    resp = client.post(f"/ui/sessions/{session_id}/steps/{step_ids[0]}/regenerate")
    assert resp.status_code == 409


def test_editing_a_step_in_photo_mode_does_not_corrupt_the_report(tmp_path, monkeypatch):
    """Caught by automated PR review: photo-mode sessions carry a SYNTHETIC
    manifest (every step's window/element deliberately empty) and their
    used_fallback means "no vision caption", not "template fallback" --
    _generate_photo always hardcodes template_fallback_steps/
    empty_metadata_steps to [] for exactly that reason. The edit route
    isn't blocked for photo mode, so its re-export must preserve that same
    semantics rather than rebuilding those two categories from the
    synthetic manifest via build_sidecar_report, which would flag every
    single step."""
    import io
    import re

    _stub_assemble_docx(monkeypatch)
    monkeypatch.setattr(server_module, "caption_images", lambda paths, *a, **k: [None] * len(paths))

    client, _cfg = _make_client(tmp_path)

    def png(color):
        buf = io.BytesIO()
        from PIL import Image

        Image.new("RGB", (120, 90), color).save(buf, "PNG")
        return buf.getvalue()

    files = [
        ("files", ("a.png", png((10, 10, 10)), "image/png")),
        ("files", ("b.png", png((20, 20, 20)), "image/png")),
    ]
    resp = client.post(
        "/ui/build", data={"title": "Photo SOP"}, files=files, follow_redirects=False
    )
    assert resp.status_code == 303
    session_id = resp.headers["location"].rsplit("/", 1)[-1]
    page = client.get(f"/ui/sessions/{session_id}")
    step_ids = re.findall(r'name="keep" value="(step-\d+)"', page.text)
    resp = client.post(
        f"/ui/sessions/{session_id}/confirm-steps",
        data={"keep": step_ids, **{f"pos-{sid}": str(i) for i, sid in enumerate(step_ids, 1)}},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    status = _wait_done(client, session_id)
    assert status["status"] == "done", status

    session_dir = tmp_path / "sessions" / session_id
    report_before = json.loads((session_dir / "report.json").read_text(encoding="utf-8"))
    assert report_before["template_fallback_steps"] == []
    assert report_before["empty_metadata_steps"] == []

    resp = client.post(
        f"/ui/sessions/{session_id}/steps/{step_ids[0]}",
        data={"text": "edited photo step"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    status = _wait_done(client, session_id)
    assert status["status"] == "done", status

    report_after = json.loads((session_dir / "report.json").read_text(encoding="utf-8"))
    assert report_after["template_fallback_steps"] == []
    assert report_after["empty_metadata_steps"] == []
    assert step_ids[0] in report_after.get("manually_edited_steps", [])


def test_regenerate_preserves_the_manual_edit_when_reexport_fails(tmp_path, monkeypatch):
    """Caught by automated PR review: _regenerate_step used to clear the
    manual edit BEFORE _reexport_session, which can 409 (e.g. a damaged/
    stale steps.json) -- losing the human's text with nothing to replace
    it. The edit must survive a failed regenerate attempt. The route now
    validates the same preconditions synchronously before jobs.submit, so
    this 409s immediately rather than via a background job -- either way,
    the manual edit and the session's "done" status must both survive."""
    _stub_assemble_docx(monkeypatch)
    client, _cfg = _make_client(tmp_path)
    session_id, status, manifest = _create_and_wait(client, tmp_path)
    assert status["status"] == "done", status
    session_dir = tmp_path / "sessions" / session_id
    step_id = manifest.steps[0].id
    server_module._save_edit(session_dir, step_id, "my careful fix")

    # Force _reexport_session to 409 by damaging steps.json's version gate.
    (session_dir / "steps.json").write_text(
        json.dumps({"steps": [{"step_id": step_id, "text": "x", "used_fallback": False}]}),
        encoding="utf-8",
    )

    resp = client.post(
        f"/ui/sessions/{session_id}/steps/{step_id}/regenerate", follow_redirects=False
    )
    assert resp.status_code == 409
    status = client.get(f"/sessions/{session_id}/status").json()
    assert status["status"] == "done"

    edits = server_module._load_edits(session_dir)
    assert edits[step_id]["text"] == "my careful fix"


def test_a_refused_edit_never_hides_the_sessions_finished_downloads(tmp_path, monkeypatch):
    """Caught by automated PR review: jobs.submit turns any exception into
    a terminal "error" status (JobRunner._worker), which used to make
    _require_done reject every download route for a session whose
    documents are still intact on disk. The route-level precondition check
    must keep the session downloadable when the edit itself is refused."""
    _stub_assemble_docx(monkeypatch)
    client, _cfg = _make_client(tmp_path)
    session_id, status, manifest = _create_and_wait(client, tmp_path)
    assert status["status"] == "done", status
    session_dir = tmp_path / "sessions" / session_id
    step_id = manifest.steps[0].id
    (session_dir / "steps.json").write_text(
        json.dumps({"steps": [{"step_id": step_id, "text": "x", "used_fallback": False}]}),
        encoding="utf-8",
    )

    resp = client.post(
        f"/ui/sessions/{session_id}/steps/{step_id}",
        data={"text": "should not apply"},
        follow_redirects=False,
    )
    assert resp.status_code == 409

    assert client.get(f"/sessions/{session_id}/doc.md").status_code == 200
    assert client.get(f"/ui/sessions/{session_id}").status_code == 200


def _wait_done(client, session_id, timeout=10.0):
    import time

    deadline = time.monotonic() + timeout
    status = {"status": "queued"}
    while time.monotonic() < deadline:
        status = client.get(f"/sessions/{session_id}/status").json()
        if status["status"] in ("done", "error"):
            return status
        time.sleep(0.05)
    return status
