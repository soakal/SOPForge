"""Click/type input hooks: pynput listeners that record event *metadata*
only. Typed content is never captured — a burst of keystrokes is summarized
as a fixed, content-free string (redaction-by-design, not a redaction pass
applied after the fact)."""

import threading
import time

from pynput import keyboard, mouse

TYPE_SUMMARY = "entered value in field (content not captured)"

_BUTTON_NAMES = {
    mouse.Button.left: "left",
    mouse.Button.right: "right",
    mouse.Button.middle: "middle",
}


def _button_name(button):
    return _BUTTON_NAMES.get(button, getattr(button, "name", "unknown"))


class InputRecorder:
    """Records clicks as individual events and contiguous keystroke bursts as
    a single summarized "type" event, flushed on the next click or on stop().
    `_on_press` runs on pynput's keyboard-listener thread while `_flush_typing`
    can run from the mouse-listener thread or the caller's thread (stop()), so
    the typing-state read/emit/clear is guarded by a lock to avoid losing a
    keypress that lands mid-flush."""

    def __init__(self, on_event):
        self.on_event = on_event
        self._typing = False
        self._last_pos = (0, 0)
        self._lock = threading.Lock()
        self._mouse_listener = None
        self._keyboard_listener = None

    def _on_click(self, x, y, button, pressed):
        if not pressed:
            return
        self._flush_typing()
        self._last_pos = (x, y)
        self.on_event(
            {
                "action": "click",
                "button": _button_name(button),
                "x": x,
                "y": y,
                "ts": time.time(),
            }
        )

    def _on_press(self, key):
        with self._lock:
            self._typing = True

    def _flush_typing(self):
        with self._lock:
            if not self._typing:
                return
            self._typing = False
            x, y = self._last_pos
        self.on_event(
            {
                "action": "type",
                "text_summary": TYPE_SUMMARY,
                "x": x,
                "y": y,
                "ts": time.time(),
            }
        )

    def start(self):
        self._mouse_listener = mouse.Listener(on_click=self._on_click)
        self._keyboard_listener = keyboard.Listener(on_press=self._on_press)
        self._mouse_listener.start()
        self._keyboard_listener.start()
        self._mouse_listener.wait()
        self._keyboard_listener.wait()

    def stop(self):
        self._flush_typing()
        for listener in (self._mouse_listener, self._keyboard_listener):
            if listener is not None:
                listener.stop()
