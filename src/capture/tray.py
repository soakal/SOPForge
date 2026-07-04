"""Tray application: pystray icon with a recording indicator, a global
start/stop hotkey, and an Exit menu item, wired to a Recorder session."""

import tempfile
import threading
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
    Recorder session at a time. The menu handler and the hotkey listener
    each run on their own thread, so start/stop/exit are serialized under
    one lock — otherwise a menu click racing the hotkey could start two
    Recorders (one leaked) or crash on a None recorder."""

    def __init__(self, captures_root=DEFAULT_CAPTURES_ROOT, hotkey=DEFAULT_HOTKEY):
        self.captures_root = Path(captures_root)
        self._recorder = None
        self._lock = threading.Lock()
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
        with self._lock:
            if self.is_recording:
                self._stop_recording()
            else:
                self._start_recording()

    def _start_recording(self):
        """Caller must hold self._lock. Starts into a local variable first —
        only published to self._recorder once start() has actually
        succeeded, so a failed start() can't wedge is_recording True with no
        real hooks installed."""
        if self.is_recording:
            return
        recorder = Recorder(self.captures_root)
        recorder.start()
        self._recorder = recorder
        self._icon.icon = RECORDING_ICON
        self._icon.title = "SOPForge (recording)"

    def _stop_recording(self):
        """Caller must hold self._lock."""
        if not self.is_recording:
            return
        recorder, self._recorder = self._recorder, None
        try:
            recorder.stop()
        finally:
            self._icon.icon = IDLE_ICON
            self._icon.title = "SOPForge (idle)"

    def exit(self):
        try:
            with self._lock:
                self._stop_recording()
        finally:
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
        setup_error = []
        try:
            with tempfile.TemporaryDirectory(prefix="sopforge-selfcheck-") as tmp:
                self.captures_root = Path(tmp)

                def _setup(icon):
                    # pystray runs setup on its own thread with no exception
                    # handling of its own — an uncaught error here would kill
                    # that thread silently and icon.run() would hang forever
                    # waiting for a stop() that never comes. Always stop the
                    # icon, and surface the error to the caller afterward.
                    try:
                        icon.visible = True
                        with self._lock:
                            self._start_recording()
                            self._stop_recording()
                    except BaseException as exc:  # noqa: BLE001
                        setup_error.append(exc)
                    finally:
                        icon.stop()

                self._icon.run(setup=_setup)
        finally:
            self.captures_root = real_captures_root
            self._hotkey_listener.stop()
            self._hotkey_listener.join()
        if setup_error:
            raise setup_error[0]
