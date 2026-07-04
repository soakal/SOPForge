"""Click/type input hooks: pynput listeners that record event *metadata*
only. Typed content is never captured — a burst of keystrokes is summarized
as a fixed, content-free string (redaction-by-design, not a redaction pass
applied after the fact)."""

import time

from pynput import keyboard, mouse

TYPE_SUMMARY = "entered value in field (content not captured)"

_BUTTON_NAMES = {
    mouse.Button.left: "left",
    mouse.Button.right: "right",
    mouse.Button.middle: "middle",
}


def _button_name(button):
    return _BUTTON_NAMES.get(button, "left")


class InputRecorder:
    """Records clicks as individual events and contiguous keystroke bursts as
    a single summarized "type" event, flushed on the next click or on stop()."""

    def __init__(self, on_event):
        self.on_event = on_event
        self._typing = False
        self._last_pos = (0, 0)
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
        self._typing = True

    def _flush_typing(self):
        if not self._typing:
            return
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
        self._typing = False

    def start(self):
        self._mouse_listener = mouse.Listener(on_click=self._on_click)
        self._keyboard_listener = keyboard.Listener(on_press=self._on_press)
        self._mouse_listener.start()
        self._keyboard_listener.start()

    def stop(self):
        self._flush_typing()
        for listener in (self._mouse_listener, self._keyboard_listener):
            if listener is not None:
                listener.stop()
