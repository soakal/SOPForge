"""Session orchestration: wires click/type hooks, screenshot capture, UIA
resolution, manifest building, and redaction into one
`captures/<session-id>/` output directory (CLAUDE.md's fixed architecture)."""

import datetime as dt
import logging
import queue
import threading
import uuid
from pathlib import Path

from capture.hooks import InputRecorder
from capture.manifest import ManifestBuilder
from capture.redact import OcrUnavailableError, blur_regions, is_password_field, load_config
from capture.redact import redact_screenshot_tagged as _redact_screenshot_tagged
from capture.shots import ScreenshotWriter
from capture.uia import resolve_at

logger = logging.getLogger(__name__)

_STOP_SENTINEL = object()


def new_session_id():
    return f"{dt.datetime.now(dt.timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"


def _utc_iso(ts):
    return (
        dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        + "Z"
    )


def _now_iso():
    return _utc_iso(dt.datetime.now(dt.timezone.utc).timestamp())


class Recorder:
    """start()/stop() around one capture session.

    InputRecorder's on_click/on_press callbacks run *directly on pynput's
    WH_MOUSE_LL/WH_KEYBOARD_LL hook threads*. Windows enforces a low-level
    hook timeout (LowLevelHooksTimeout, a few hundred ms by default) and will
    silently detach a hook that blocks past it — so the callback must return
    almost instantly. resolve_at()/screenshot/redaction can legitimately take
    several seconds (some UIA controls alone take ~4s — see
    .claude/skills/uia-notes.md), which would blow that budget many times
    over if run inline. So the hook callback (`_enqueue_event`) only ever
    does a queue.Queue.put() — fast and thread-safe — and a single dedicated
    worker thread (`_drain_queue`) does the actual slow pipeline
    (`_process_event`) one event at a time, preserving arrival order without
    needing a lock (only one thread ever calls _process_event in normal
    operation; it still takes a lock for defense in depth since tests and
    selftest.py call _process_event directly for deterministic behavior)."""

    def __init__(self, captures_root, session_id=None, machine="", os_build="", redact_config=None):
        self.session_id = session_id or new_session_id()
        self.output_dir = Path(captures_root) / self.session_id
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._redact_config = redact_config or load_config()
        self._builder = ManifestBuilder(
            self.session_id, started_utc=_now_iso(), machine=machine, os_build=os_build
        )
        self._shots = ScreenshotWriter(self.output_dir)
        self._input = InputRecorder(on_event=self._enqueue_event)
        self._lock = threading.Lock()
        self._queue = queue.Queue()
        self._worker = None

    def start(self):
        self._worker = threading.Thread(target=self._drain_queue, daemon=True)
        self._worker.start()
        self._input.start()

    def stop(self):
        # Listener threads are joined inside InputRecorder.stop() (hooks.py),
        # so no more events can be enqueued after this line returns — the
        # sentinel below is guaranteed to be the last item in the queue.
        self._input.stop()
        self._queue.put(_STOP_SENTINEL)
        self._worker.join()
        with self._lock:
            self._builder.finish(_now_iso())
            return self._builder.write(self.output_dir / "manifest.json")

    def _enqueue_event(self, event):
        """The actual pynput hook callback — must stay fast (see class
        docstring). Never process an event inline here."""
        self._queue.put(event)

    def _drain_queue(self):
        while True:
            event = self._queue.get()
            if event is _STOP_SENTINEL:
                return
            self._process_event(event)

    def _redact(self, screenshot_path, element):
        """Returns the manifest `redactions` list (region+reason) for the
        regions actually blurred."""
        try:
            return _redact_screenshot_tagged(
                screenshot_path, element=element, config=self._redact_config
            )
        except OcrUnavailableError:
            # OCR pattern-matching (email/IPv4) is unavailable this run, but
            # the password-field heuristic doesn't depend on OCR at all —
            # still apply that half rather than shipping a fully unredacted
            # shot. Logged (not silent) since email/IPv4 coverage is skipped.
            logger.warning("OCR unavailable; email/IPv4 redaction skipped for %s", screenshot_path)
            if element is not None and is_password_field(element, self._redact_config):
                rect = element.get("bounding_rect")
                if rect:
                    applied = blur_regions(screenshot_path, [tuple(rect)])
                    return [{"region": list(r), "reason": "password_heuristic"} for r in applied]
            return []

    def _process_event(self, event):
        """The actual slow pipeline: UIA resolution, screenshot, redaction,
        manifest append. Runs on the queue-draining worker thread in normal
        operation; tests and selftest.py call this directly for
        deterministic, synchronous behavior."""
        with self._lock:
            x, y = event["x"], event["y"]
            element, window = resolve_at(x, y)
            filename, monitor_idx, is_placeholder = self._shots.capture(x, y)
            redactions = self._redact(self.output_dir / filename, element)

            kwargs = {
                "ts_utc": _utc_iso(event["ts"]),
                "action": event["action"],
                "screen": {"x": x, "y": y, "monitor": monitor_idx},
                "screenshot": filename,
                "screenshot_placeholder": is_placeholder,
                "window": window,
                "element": element,
                "redactions": redactions,
            }
            if event["action"] == "click":
                kwargs["button"] = event["button"]
            else:
                kwargs["text_summary"] = event["text_summary"]
            self._builder.add_step(**kwargs)
