"""Session orchestration: wires click/type hooks, screenshot capture, UIA
resolution, manifest building, and redaction into one
`captures/<session-id>/` output directory (CLAUDE.md's fixed architecture)."""

import datetime as dt
import uuid
from pathlib import Path

from capture.hooks import InputRecorder
from capture.manifest import ManifestBuilder
from capture.redact import OcrUnavailableError, blur_regions, is_password_field, load_config
from capture.redact import redact_screenshot as _redact_screenshot
from capture.shots import ScreenshotWriter
from capture.uia import resolve_at


def new_session_id():
    return f"{dt.datetime.now(dt.timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"


def _utc_iso(ts):
    return (
        dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        + "Z"
    )


class Recorder:
    """start()/stop() around one capture session. Each click/type event from
    InputRecorder triggers, in order: UIA resolution at the event's screen
    point, a screenshot, a redaction pass over that screenshot, and a step
    appended to the session's manifest."""

    def __init__(self, captures_root, session_id=None, machine="", os_build="", redact_config=None):
        self.session_id = session_id or new_session_id()
        self.output_dir = Path(captures_root) / self.session_id
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._redact_config = redact_config or load_config()
        self._builder = ManifestBuilder(
            self.session_id,
            started_utc=_utc_iso(dt.datetime.now(dt.timezone.utc).timestamp()),
            machine=machine,
            os_build=os_build,
        )
        self._shots = ScreenshotWriter(self.output_dir)
        self._input = InputRecorder(on_event=self._on_input_event)

    def start(self):
        self._input.start()

    def stop(self):
        self._input.stop()
        self._builder.finish(_utc_iso(dt.datetime.now(dt.timezone.utc).timestamp()))
        return self._builder.write(self.output_dir / "manifest.json")

    def _redact(self, screenshot_path, element):
        try:
            _redact_screenshot(screenshot_path, element=element, config=self._redact_config)
        except OcrUnavailableError:
            # OCR pattern-matching (email/IPv4) is unavailable, but the
            # password-field heuristic doesn't depend on OCR at all — still
            # apply that half rather than shipping a fully unredacted shot.
            if element is not None and is_password_field(element, self._redact_config):
                rect = element.get("bounding_rect")
                if rect:
                    blur_regions(screenshot_path, [tuple(rect)])

    def _on_input_event(self, event):
        x, y = event["x"], event["y"]
        element, window = resolve_at(x, y)
        filename, monitor_idx = self._shots.capture(x, y)
        self._redact(self.output_dir / filename, element)

        kwargs = {
            "ts_utc": _utc_iso(event["ts"]),
            "action": event["action"],
            "screen": {"x": x, "y": y, "monitor": monitor_idx},
            "screenshot": filename,
            "window": window,
            "element": element,
        }
        if event["action"] == "click":
            kwargs["button"] = event["button"]
        else:
            kwargs["text_summary"] = event["text_summary"]
        self._builder.add_step(**kwargs)
