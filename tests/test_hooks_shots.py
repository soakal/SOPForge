"""InputRecorder logic is verified by driving its callback methods directly
rather than injecting real OS-level clicks/keystrokes: this build VM denies
synthetic input outright (pynput Controller and a hand-rolled ctypes SendInput
both fail — SendInput returns 0 with GetLastError=ERROR_ACCESS_DENIED) so it
can never reach WH_MOUSE_LL/WH_KEYBOARD_LL hooks here — confirmed by direct
repro and documented in .claude/skills/uia-notes.md. That's an environment
limitation, not a defect in InputRecorder: real hardware input does reach
those hooks, which is what matters for an actual capture session.
ScreenshotWriter's naming/file-write logic is exercised for real (bytes
actually hit disk), but the underlying mss session is faked too — real GDI
BitBlt capture also fails on this VM (see uia-notes.md) and is unverified
here; it needs a normal desktop session to confirm."""

import time

from pynput import keyboard, mouse

import mss.exception
import capture.hooks as hooks_module
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


def test_start_stop_lifecycle_wires_real_pynput_listeners():
    """start()/stop() do reach real pynput.Listener objects in this
    environment (only *injected* input is blocked, not listener installation
    — see module docstring), so a wiring bug here (swapped callbacks, wrong
    kwarg) would not be caught by the direct-callback tests above."""
    recorder = InputRecorder(on_event=lambda e: None)
    recorder.start()
    try:
        assert recorder._mouse_listener.running
        assert recorder._keyboard_listener.running
    finally:
        recorder.stop()
    assert not recorder._mouse_listener.running
    assert not recorder._keyboard_listener.running


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


def test_capture_against_real_gdi_never_raises(tmp_path):
    """No monkeypatching here: real mss.grab() (BitBlt) has been observed to
    both fail and succeed on this build VM across this session — see
    uia-notes.md, it's apparently intermittent rather than a hard permanent
    block. So this doesn't assert which branch fires, only that capture()
    always produces a valid file either way (the deterministic mocked test
    below proves the fallback logic itself)."""
    writer = ScreenshotWriter(tmp_path)
    filename, monitor_idx, is_placeholder, origin = writer.capture(100, 100)
    path = tmp_path / filename
    assert path.exists()
    assert path.stat().st_size > 0
    assert monitor_idx >= 1
    assert isinstance(is_placeholder, bool)
    assert len(origin) == 2


def test_capture_falls_back_to_placeholder_on_mocked_grab_failure(tmp_path, monkeypatch):
    """Deterministic version of the above, independent of this VM's specific
    GDI restriction — proves the fallback triggers on the exact exception
    type mss raises, sized to the target monitor."""

    class RaisingSct(_FakeSct):
        def grab(self, monitor):
            raise mss.exception.ScreenShotError("simulated BitBlt failure")

    monkeypatch.setattr(shots_module.mss, "mss", lambda: RaisingSct())

    writer = ScreenshotWriter(tmp_path)
    filename, _, is_placeholder, _origin = writer.capture(100, 100)
    path = tmp_path / filename
    assert path.exists()
    assert is_placeholder is True

    from PIL import Image

    with Image.open(path) as img:
        assert img.size == (824, 1560)


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


def _wait_until(predicate, timeout=2.0, interval=0.01):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_typing_burst_flushes_on_idle_without_click():
    """The bug this closes: a burst with no following click (e.g. a terminal
    command typed then the window is switched away from via keyboard) used to
    sit unflushed until stop() -- whose screenshot then showed whatever was
    on screen at that later, unrelated moment."""
    events = []
    recorder = InputRecorder(on_event=events.append, type_flush_idle=0.05)
    recorder._on_press(keyboard.KeyCode.from_char("a"))

    assert _wait_until(lambda: len(events) == 1)
    assert events[0]["action"] == "type"
    assert events[0]["text_summary"] == TYPE_SUMMARY
    recorder.stop()
    assert len(events) == 1  # stop() must not re-flush an already-flushed burst


def test_rapid_typing_emits_single_type_event():
    events = []
    recorder = InputRecorder(on_event=events.append, type_flush_idle=0.1)
    for char in "hello":
        recorder._on_press(keyboard.KeyCode.from_char(char))
        time.sleep(0.02)  # well under the idle threshold -- keeps re-arming, not flushing

    assert _wait_until(lambda: len(events) == 1, timeout=2.0)
    assert [e["action"] for e in events] == ["type"]
    recorder.stop()
    assert [e["action"] for e in events] == ["type"]


def test_click_at_debounce_boundary_orders_type_before_click():
    """Races an idle-timeout flush against a click landing at nearly the same
    instant -- whichever thread wins the lock must run its guard/clear/emit
    to completion before the other proceeds, so the type event can never be
    dropped, duplicated, or enqueued after the click that followed it."""
    for _ in range(20):
        events = []
        recorder = InputRecorder(on_event=events.append, type_flush_idle=0.02)
        recorder._on_press(keyboard.KeyCode.from_char("a"))
        time.sleep(0.02)
        recorder._on_click(10, 10, mouse.Button.left, True)
        recorder.stop()
        assert [e["action"] for e in events] == ["type", "click"]


def test_stop_cancels_idle_timer_and_emits_no_late_event():
    events = []
    recorder = InputRecorder(on_event=events.append, type_flush_idle=0.3)
    recorder._on_press(keyboard.KeyCode.from_char("a"))
    recorder.stop()
    assert [e["action"] for e in events] == ["type"]
    time.sleep(0.4)
    assert [e["action"] for e in events] == ["type"]  # no late/duplicate flush after stop()


def test_type_event_ts_anchors_to_burst_start_not_flush_time():
    events = []
    recorder = InputRecorder(on_event=events.append, type_flush_idle=0.05)
    recorder._on_press(keyboard.KeyCode.from_char("a"))
    start_ts = time.time()
    assert _wait_until(lambda: len(events) == 1)
    recorder.stop()
    assert events[0]["ts"] <= start_ts + 0.02


def test_type_event_uses_last_click_position_when_a_click_preceded_it():
    events = []
    recorder = InputRecorder(on_event=events.append, type_flush_idle=0.05)
    recorder._on_click(50, 60, mouse.Button.left, True)
    recorder._on_press(keyboard.KeyCode.from_char("a"))
    assert _wait_until(lambda: len(events) == 2)
    recorder.stop()
    type_event = [e for e in events if e["action"] == "type"][0]
    assert (type_event["x"], type_event["y"]) == (50, 60)


def test_type_event_anchors_to_foreground_window_when_no_click_preceded_it(monkeypatch):
    monkeypatch.setattr(hooks_module.win32gui, "GetForegroundWindow", lambda: 4242)
    monkeypatch.setattr(hooks_module.win32gui, "GetWindowRect", lambda hwnd: (100, 200, 300, 400))
    events = []
    recorder = InputRecorder(on_event=events.append, type_flush_idle=0.05)
    recorder._on_press(keyboard.KeyCode.from_char("a"))
    assert _wait_until(lambda: len(events) == 1)
    recorder.stop()
    assert (events[0]["x"], events[0]["y"]) == (200, 300)  # center of the mocked rect


def test_type_event_falls_back_to_last_pos_when_foreground_lookup_fails(monkeypatch):
    def _raise():
        raise OSError("no foreground window")

    monkeypatch.setattr(hooks_module.win32gui, "GetForegroundWindow", _raise)
    events = []
    recorder = InputRecorder(on_event=events.append, type_flush_idle=0.05)
    recorder._on_press(keyboard.KeyCode.from_char("a"))
    assert _wait_until(lambda: len(events) == 1)
    recorder.stop()
    assert (events[0]["x"], events[0]["y"]) == (0, 0)  # default _last_pos, never clicked
