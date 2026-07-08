"""Tray application: pystray icon with a recording indicator, a global
start/stop hotkey, and an Exit menu item, wired to a Recorder session.

When a recording stops, its manifest + screenshots are auto-uploaded to a
running sopforge-server (best-effort, on a background thread) and the
browser opens straight to that session's review page on success -- zero
manual steps between "stop recording" and seeing the generated doc. If the
server isn't running, the capture is already safe on disk and can be
uploaded later through the library page's upload form; nothing here ever
blocks the tray or raises on a failed upload (see capture/upload.py)."""

import logging
import tempfile
import threading
import webbrowser
from pathlib import Path

import httpx
import pystray
from PIL import Image, ImageDraw
from pynput import keyboard

from capture import __version__
from capture.recorder import Recorder
from capture.upload import server_url_from_env, upload_session

logger = logging.getLogger(__name__)

DEFAULT_CAPTURES_ROOT = Path.home() / "SOPForge" / "captures"
DEFAULT_HOTKEY = "<ctrl>+<alt>+r"


def _request_server_shutdown(server_url, timeout=2.0):
    """POST /shutdown to the running sopforge-server, best-effort. The server
    is headless (no window/icon of its own), so the tray's Exit is the one
    place a user can stop it -- but a failed call (server not running, already
    stopping, unreachable) must never keep the tray from closing, so this
    swallows everything and just logs."""
    try:
        with httpx.Client(timeout=timeout) as client:
            client.post(f"{server_url.rstrip('/')}/shutdown")
    except Exception:  # noqa: BLE001 - best-effort; Exit must not depend on the server
        logger.info("no running server to stop at %s (or it was already stopping)", server_url)


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

    def __init__(
        self,
        captures_root=DEFAULT_CAPTURES_ROOT,
        hotkey=DEFAULT_HOTKEY,
        server_url=None,
        upload_fn=upload_session,
        open_browser_fn=webbrowser.open,
        shutdown_fn=_request_server_shutdown,
        notify_fn=None,
    ):
        self.captures_root = Path(captures_root)
        self.server_url = server_url or server_url_from_env()
        self._upload_fn = upload_fn
        self._open_browser_fn = open_browser_fn
        self._shutdown_fn = shutdown_fn
        # Desktop balloon notifications default to the real tray icon's
        # .notify (Windows toast). Injectable so tests can assert on the
        # failure notification without a live icon. Called only from the
        # background auto-upload thread, never on the pystray menu thread.
        self._notify_fn = notify_fn or self._icon_notify
        self._recorder = None
        self._lock = threading.Lock()
        self._icon = pystray.Icon(
            "sopforge",
            IDLE_ICON,
            f"SOPForge v{__version__} (idle)",
            menu=pystray.Menu(
                pystray.MenuItem("Start/Stop recording", self.toggle_recording),
                pystray.MenuItem("Open SOPForge library", self.open_library),
                pystray.MenuItem("Configuration", self.open_config),
                pystray.MenuItem("Exit", self.exit),
            ),
        )
        self._hotkey_listener = keyboard.GlobalHotKeys({hotkey: self.toggle_recording})

    @property
    def is_recording(self):
        return self._recorder is not None

    def toggle_recording(self):
        # This runs on the pynput hotkey listener thread (and pystray's menu
        # thread). pynput STOPS the listener if a callback raises -- so an
        # unguarded exception here would silently kill Ctrl+Alt+R for the rest
        # of the process. Swallow, log, and notify instead.
        try:
            with self._lock:
                if self.is_recording:
                    self._stop_recording()
                else:
                    self._start_recording()
        except Exception:  # noqa: BLE001 - never let a toggle failure kill the hotkey listener
            logger.exception("recording toggle failed")
            self._notify_fn(
                "SOPForge couldn't start/stop the recording. See the log for details.",
                "SOPForge",
            )

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
        self._icon.title = f"SOPForge v{__version__} (recording)"

    def _stop_recording(self, upload=True):
        """Caller must hold self._lock. Returns the finished capture's output
        directory (or None if nothing was recording). `upload=False` finalizes
        the capture to disk WITHOUT spawning the auto-upload -- used by exit(),
        where uploading to a server we're about to shut down would just race the
        shutdown and be killed with the process."""
        if not self.is_recording:
            return None
        recorder, self._recorder = self._recorder, None
        try:
            recorder.stop()
        finally:
            self._icon.icon = IDLE_ICON
            self._icon.title = f"SOPForge v{__version__} (idle)"
        if not upload:
            return recorder.output_dir
        # Off the lock, on a background thread: uploading can take a few
        # seconds (screenshot transfer + queuing), and "stop recording"
        # must feel instant regardless of whether a server is even running.
        # upload_fn/server_url are bound to their CURRENT values here, at
        # spawn time -- not read from self inside the thread -- so
        # self_check()'s temporary no-op swap can never race a late-running
        # thread from a stop() that happened just before the swap (or just
        # after its restoration).
        threading.Thread(
            target=self._auto_upload,
            args=(recorder.output_dir, self._upload_fn, self.server_url),
            daemon=True,
        ).start()
        return recorder.output_dir

    def _icon_notify(self, message, title="SOPForge"):
        """Show a Windows tray balloon, best-effort. Some pystray backends
        don't implement notify(); a missing/failing notification must never
        take down the auto-upload thread, so this swallows everything."""
        try:
            self._icon.notify(message, title)
        except Exception:  # noqa: BLE001 - a notification is a convenience, not critical
            logger.info("tray notification not shown: %s", message)

    def _auto_upload(self, output_dir, upload_fn, server_url):
        """Best-effort: uploads via upload_fn and opens the browser straight
        to the review page on success. Never raises -- a failed upload just
        means the user opens the browser and uses the library page's upload
        form manually later; the capture is already safe on disk regardless.

        On failure, a tray notification tells the user the recording is safe
        on disk and where it is -- so a capture is never silently lost when
        the server isn't running (the "I recorded but don't see it" case)."""
        session_id = upload_fn(output_dir, server_url=server_url)
        if not session_id:
            self._notify_fn(
                f"Couldn't reach the SOPForge server. Your recording is saved at "
                f"{output_dir} -- start SOPForge, open the library, and use Upload.",
                "SOPForge: recording saved locally",
            )
            return
        try:
            self._open_browser_fn(f"{server_url}/ui/sessions/{session_id}")
        except Exception:  # noqa: BLE001 - the doc is generated either way; opening a tab is a convenience
            logger.warning("could not open browser to session %s", session_id, exc_info=True)

    def open_library(self):
        """Open the review UI / session library in the browser. The server's
        root and /ui both render the library, so opening server_url lands
        there. Best-effort: a failed open is logged, never raised into
        pystray's menu thread (an uncaught error there would kill it
        silently)."""
        try:
            self._open_browser_fn(self.server_url)
        except Exception:  # noqa: BLE001 - opening a browser tab is a convenience, not critical
            logger.warning("could not open library at %s", self.server_url, exc_info=True)

    def open_config(self):
        """Open the configuration page (choose AI provider/model per task).
        Best-effort, same as open_library."""
        try:
            self._open_browser_fn(f"{self.server_url.rstrip('/')}/ui/config")
        except Exception:  # noqa: BLE001 - opening a browser tab is a convenience, not critical
            logger.warning("could not open config at %s", self.server_url, exc_info=True)

    def exit(self):
        """Stops the recording (if any), then the headless server, then the
        tray icon -- so a single Exit closes both processes. The server stop
        is best-effort (see _request_server_shutdown); the icon always stops
        regardless.

        Exiting mid-recording finalizes the capture to disk but does NOT
        auto-upload: we're about to shut the server down, so an upload would
        race that shutdown and be killed with the process. Instead we notify
        (while the icon is still alive) where the capture is saved so the user
        can upload it next time -- the capture is never silently lost."""
        try:
            with self._lock:
                saved_dir = self._stop_recording(upload=False)
            if saved_dir is not None:
                self._notify_fn(
                    f"Recording saved at {saved_dir}. Start SOPForge later, open the "
                    "library, and use Upload.",
                    "SOPForge: recording saved locally",
                )
            self._shutdown_fn(self.server_url)
        finally:
            self._icon.stop()

    def run(self, on_ready=None):
        """Blocking: runs the tray icon's event loop until Exit is chosen.
        on_ready(), if given, fires once the icon is actually visible —
        used by scripts/verify_exe.py to measure cold-start-to-tray-visible
        timing (Phase 1 acceptance criterion 4) without needing to
        UI-automate the real system tray, which is unreliable in general."""
        self._hotkey_listener.start()
        self._hotkey_listener.wait()
        try:

            def _setup(icon):
                icon.visible = True
                if on_ready is not None:
                    on_ready()

            self._icon.run(setup=_setup)
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
        location. Auto-upload is disabled for the duration too — a
        self-check must never make a real network attempt or (worse) hand
        a throwaway diagnostic session to a genuinely running server."""
        self._hotkey_listener.start()
        self._hotkey_listener.wait()
        real_captures_root = self.captures_root
        real_upload_fn = self._upload_fn
        setup_error = []
        try:
            with tempfile.TemporaryDirectory(prefix="sopforge-selfcheck-") as tmp:
                self.captures_root = Path(tmp)
                self._upload_fn = lambda *args, **kwargs: None

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
            self._upload_fn = real_upload_fn
            self._hotkey_listener.stop()
            self._hotkey_listener.join()
        if setup_error:
            raise setup_error[0]
