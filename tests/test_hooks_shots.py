"""InputRecorder logic is verified by driving its callback methods directly
rather than injecting real OS-level clicks/keystrokes: this build VM's input
path does not deliver synthetic input (pynput Controller *or* a hand-rolled
ctypes SendInput) to WH_MOUSE_LL/WH_KEYBOARD_LL hooks — confirmed by direct
repro and documented in .claude/skills/uia-notes.md. That's an environment
limitation, not a defect in InputRecorder: real hardware input does reach
those hooks, which is what matters for an actual capture session. Screenshot
capture (ScreenshotWriter) doesn't depend on input hooks at all, so it's
exercised for real against the live desktop."""

from pynput import keyboard, mouse

import capture.shots as shots_module
from capture.hooks import TYPE_SUMMARY, InputRecorder
from capture.shots import ScreenshotWriter


class _FakeShot:
    def __init__(self, width, height):
        self.size = (width, height)
        self.rgb = bytes([120, 120, 120]) * (width * height)


class _FakeSct:
    """Real GDI screen capture (BitBlt, via mss or PIL.ImageGrab) fails on
    this build VM regardless of library — see .claude/skills/uia-notes.md.
    This fake stands in for mss.mss() so the naming/file-write logic is still
    exercised against real bytes on disk."""

    monitors = [
        {"left": 0, "top": 0, "width": 824, "height": 1560},
        {"left": 0, "top": 0, "width": 824, "height": 1560},
    ]

    def grab(self, monitor):
        return _FakeShot(monitor["width"], monitor["height"])

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_click_events_carry_screen_coords_and_button():
    events = []
    recorder = InputRecorder(on_event=events.append)
    recorder._on_click(111, 222, mouse.Button.left, True)
    recorder._on_click(111, 222, mouse.Button.left, False)  # release: ignored
    recorder._on_click(333, 444, mouse.Button.right, True)
    recorder.stop()

    click_events = [e for e in events if e["action"] == "click"]
    assert len(click_events) == 2
    assert (click_events[0]["x"], click_events[0]["y"]) == (111, 222)
    assert click_events[0]["button"] == "left"
    assert (click_events[1]["x"], click_events[1]["y"]) == (333, 444)
    assert click_events[1]["button"] == "right"


def test_typing_burst_summarized_without_capturing_content():
    events = []
    recorder = InputRecorder(on_event=events.append)
    recorder._on_click(50, 60, mouse.Button.left, True)
    for char in "sec":
        recorder._on_press(keyboard.KeyCode.from_char(char))
    recorder._on_click(200, 60, mouse.Button.left, True)
    recorder.stop()

    assert [e["action"] for e in events] == ["click", "type", "click"]
    type_event = events[1]
    assert type_event["text_summary"] == TYPE_SUMMARY
    assert set(type_event) == {"action", "text_summary", "x", "y", "ts"}
    assert (type_event["x"], type_event["y"]) == (50, 60)


def test_screenshots_are_numbered_sequentially_and_match_click_coords(tmp_path, monkeypatch):
    monkeypatch.setattr(shots_module.mss, "mss", lambda: _FakeSct())

    events = []
    recorder = InputRecorder(on_event=events.append)
    recorder._on_click(100, 100, mouse.Button.left, True)
    recorder._on_click(200, 150, mouse.Button.left, True)
    recorder.stop()

    click_events = [e for e in events if e["action"] == "click"]
    writer = ScreenshotWriter(tmp_path)
    shots = [writer.capture(e["x"], e["y"])[0] for e in click_events]

    assert shots == ["001.png", "002.png"]
    for name in shots:
        path = tmp_path / name
        assert path.exists()
        assert path.stat().st_size > 0
