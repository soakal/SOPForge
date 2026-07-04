"""Tray application: pystray icon with a recording indicator, a global
start/stop hotkey, and an Exit menu item, wired to a Recorder session."""

import tempfile
from pathlib import Path

import pystray
from PIL import Image, ImageDraw
from pynput import keyboard

from capture.recorder import Recorder

DEFAULT_CAPTURES_ROOT = Path.home() / "SOPForge" / "captures"
DEFAULT_HOTKEY = "<ctrl>+<alt>+r"


def _make_icon(color):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, 56, 56), fill=color)
    return img


IDLE_ICON = _make_icon((120, 120, 120, 255))
RECORDING_ICON = _make_icon((220, 40, 40, 255))


class TrayApp:
    """One tray icon + one global hotkey, wired to at most one active
    Recorder session at a time."""

    def __init__(self, captures_root=DEFAULT_CAPTURES_ROOT, hotkey=DEFAULT_HOTKEY):
        self.captures_root = Path(captures_root)
        self._recorder = None
        self._icon = pystray.Icon(
            "sopforge",
            IDLE_ICON,
            "SOPForge (idle)",
            menu=pystray.Menu(
                pystray.MenuItem("Start/Stop recording", self.toggle_recording),
                pystray.MenuItem("Exit", self.exit),
            ),
        )
        self._hotkey_listener = keyboard.GlobalHotKeys({hotkey: self.toggle_recording})

    @property
    def is_recording(self):
        return self._recorder is not None

    def toggle_recording(self):
        if self.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        if self.is_recording:
            return
        self._recorder = Recorder(self.captures_root)
        self._recorder.start()
        self._icon.icon = RECORDING_ICON
        self._icon.title = "SOPForge (recording)"

    def _stop_recording(self):
        if not self.is_recording:
            return
        self._recorder.stop()
        self._recorder = None
        self._icon.icon = IDLE_ICON
        self._icon.title = "SOPForge (idle)"

    def exit(self):
        self._stop_recording()
        self._icon.stop()

    def run(self):
        """Blocking: runs the tray icon's event loop until Exit is chosen."""
        self._hotkey_listener.start()
        self._hotkey_listener.wait()
        try:
            self._icon.run()
        finally:
            self._hotkey_listener.stop()
            self._hotkey_listener.join()

    def self_check(self):
        """Initializes the tray icon, hotkey listener, and a full
        start/stop Recorder session, then tears everything down and
        returns — no blocking, no user input required. Used by
        `python -m capture --self-check`. Records into a throwaway temp
        directory rather than captures_root, so running the check never
        leaves a real (empty) session behind under the user's real capture
        location."""
        self._hotkey_listener.start()
        self._hotkey_listener.wait()
        real_captures_root = self.captures_root
        try:
            with tempfile.TemporaryDirectory(prefix="sopforge-selfcheck-") as tmp:
                self.captures_root = Path(tmp)

                def _setup(icon):
                    icon.visible = True
                    self._start_recording()
                    self._stop_recording()
                    icon.stop()

                self._icon.run(setup=_setup)
        finally:
            self.captures_root = real_captures_root
            self._hotkey_listener.stop()
            self._hotkey_listener.join()
