"""Recorder orchestration integration test: drives events through the same
`on_event` entry point InputRecorder itself calls, rather than injecting real
OS-level clicks — this build VM denies synthetic input outright (see
.claude/skills/uia-notes.md), so no OS-level injection could ever reach the
real hooks here regardless of API. What *is* real: UIA resolution against a
live scratch window, manifest writing/schema validation, and OCR-based
redaction. Only the screenshot backend (mss/GDI, also broken on this VM) is
faked via the `fake_mss` fixture."""

import json
from pathlib import Path

import jsonschema

from capture.recorder import Recorder

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SCHEMA = json.loads((FIXTURES / "manifest.schema.json").read_text(encoding="utf-8"))


def test_scripted_session_produces_ordered_manifest_and_screenshots(
    scratch_window, fake_mss, tmp_path
):
    rect = scratch_window.rectangle()
    # Offsets deep enough to land inside the Scintilla text area, not the tab
    # strip/toolbar band near the top of the window (matches task-05's
    # working offsets in tests/test_uia_resolver.py).
    p1 = (rect.left + 250, rect.top + 200)
    p2 = (rect.left + 300, rect.top + 250)

    recorder = Recorder(tmp_path, machine="TESTHOST", os_build="26100")
    recorder.start()
    try:
        recorder._on_input_event(
            {
                "action": "click",
                "button": "left",
                "x": p1[0],
                "y": p1[1],
                "ts": 1_700_000_000.0,
            }
        )
        recorder._on_input_event(
            {
                "action": "type",
                "text_summary": "entered value in field (content not captured)",
                "x": p1[0],
                "y": p1[1],
                "ts": 1_700_000_001.0,
            }
        )
        recorder._on_input_event(
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

    click_steps = [s for s in data["steps"] if s["action"] == "click"]
    assert all(s["window"]["process"].lower() == "notepad++.exe" for s in click_steps)
    assert all(s["window"]["class"] == "win32" for s in click_steps)
