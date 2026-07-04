"""Recorder orchestration integration test: drives events through
`_process_event` directly (the same slow pipeline the queue-draining worker
thread calls) rather than injecting real OS-level clicks — this build VM
denies synthetic input outright (see .claude/skills/uia-notes.md), so no
OS-level injection could ever reach the real hooks here regardless of API.
What *is* real: UIA resolution against a live scratch window, manifest
writing/schema validation, and OCR-based redaction. Only the screenshot
backend (mss/GDI, also broken on this VM) is faked via the `fake_mss`
fixture."""

import json
import time
from pathlib import Path

import jsonschema

import capture.recorder as recorder_module
from capture.recorder import Recorder

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SCHEMA = json.loads((FIXTURES / "manifest.schema.json").read_text(encoding="utf-8"))


def test_scripted_session_produces_ordered_manifest_and_screenshots(
    scratch_window, fake_mss, tmp_path, monkeypatch
):
    rect = scratch_window.rectangle()
    # Offsets deep enough to land inside the Scintilla text area, not the tab
    # strip/toolbar band near the top of the window (matches task-05's
    # working offsets in tests/test_uia_resolver.py).
    p1 = (rect.left + 250, rect.top + 200)
    p2 = (rect.left + 300, rect.top + 250)

    # Spy on the redaction call to prove it's actually wired into the
    # pipeline (screenshot written to disk *before* redaction runs on it),
    # not just that the end state happens to look right.
    redact_calls = []
    real_redact_screenshot_tagged = recorder_module._redact_screenshot_tagged

    def spying_redact_screenshot_tagged(image_path, element=None, config=None, out_path=None):
        assert Path(image_path).exists() and Path(image_path).stat().st_size > 0
        redact_calls.append(Path(image_path))
        return real_redact_screenshot_tagged(
            image_path, element=element, config=config, out_path=out_path
        )

    monkeypatch.setattr(
        recorder_module, "_redact_screenshot_tagged", spying_redact_screenshot_tagged
    )

    recorder = Recorder(tmp_path, machine="TESTHOST", os_build="26100")
    recorder.start()
    try:
        recorder._process_event(
            {
                "action": "click",
                "button": "left",
                "x": p1[0],
                "y": p1[1],
                "ts": 1_700_000_000.0,
            }
        )
        recorder._process_event(
            {
                "action": "type",
                "text_summary": "entered value in field (content not captured)",
                "x": p1[0],
                "y": p1[1],
                "ts": 1_700_000_001.0,
            }
        )
        recorder._process_event(
            {
                "action": "click",
                "button": "left",
                "x": p2[0],
                "y": p2[1],
                "ts": 1_700_000_002.0,
            }
        )
    finally:
        manifest_path = recorder.stop()

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(SCHEMA).validate(data)

    assert [s["id"] for s in data["steps"]] == ["step-001", "step-002", "step-003"]
    assert [s["action"] for s in data["steps"]] == ["click", "type", "click"]

    session_dir = recorder.output_dir
    for step in data["steps"]:
        png = session_dir / step["screenshot"]
        assert png.exists()
        assert png.stat().st_size > 0
        assert isinstance(step["redactions"], list)

    # Redaction ran exactly once per step, over that step's own screenshot —
    # proves the capture-then-redact wiring, independent of whether any
    # pattern happened to match this run's (uniform gray, faked) pixels.
    assert len(redact_calls) == 3
    assert redact_calls == [session_dir / s["screenshot"] for s in data["steps"]]

    click_steps = [s for s in data["steps"] if s["action"] == "click"]
    assert all(s["window"]["process"].lower() == "notepad++.exe" for s in click_steps)
    assert all(s["window"]["class"] == "win32" for s in click_steps)


def test_redaction_result_is_attached_to_its_manifest_step(
    scratch_window, fake_mss, tmp_path, monkeypatch
):
    """Deterministic check that whatever redact_screenshot_tagged() returns
    ends up verbatim on the *correct* step's `redactions` field — would catch
    e.g. a password-heuristic blur mislabeled as reason "pattern", or a
    region silently dropped between redaction and manifest write."""
    fake_result = [{"region": [1, 2, 3, 4], "reason": "password_heuristic"}]
    monkeypatch.setattr(recorder_module, "_redact_screenshot_tagged", lambda *a, **k: fake_result)

    rect = scratch_window.rectangle()
    recorder = Recorder(tmp_path)
    recorder.start()
    try:
        recorder._process_event(
            {
                "action": "click",
                "button": "left",
                "x": rect.left + 250,
                "y": rect.top + 200,
                "ts": 1_700_000_000.0,
            }
        )
    finally:
        manifest_path = recorder.stop()

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["steps"][0]["redactions"] == fake_result


def test_hook_callback_returns_instantly_even_when_processing_is_slow(tmp_path, monkeypatch):
    """`_enqueue_event` is the actual pynput hook callback (wired via
    InputRecorder(on_event=self._enqueue_event)) — it must return almost
    instantly regardless of how slow resolve_at/screenshot/redaction are,
    or Windows will silently detach the low-level hook. This is why event
    ingestion (fast, hook thread) is decoupled from processing (slow, one
    dedicated worker thread) via a queue instead of processing inline."""

    def slow_resolve_at(x, y):
        time.sleep(1.0)
        return {
            "name": "",
            "control_type": "",
            "automation_id": "",
            "framework": "",
            "bounding_rect": None,
        }, {"title": "", "process": "", "class": ""}

    monkeypatch.setattr(recorder_module, "resolve_at", slow_resolve_at)

    recorder = Recorder(tmp_path)
    recorder.start()
    try:
        t0 = time.time()
        recorder._enqueue_event(
            {"action": "click", "button": "left", "x": 1, "y": 1, "ts": time.time()}
        )
        elapsed = time.time() - t0
        assert elapsed < 0.2, f"hook callback took {elapsed:.3f}s — must be near-instant"

        deadline = time.time() + 5.0
        while time.time() < deadline and len(recorder._builder.step_ids()) == 0:
            time.sleep(0.05)
        assert len(recorder._builder.step_ids()) == 1  # worker thread did process it
    finally:
        manifest_path = recorder.stop()

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(data["steps"]) == 1
