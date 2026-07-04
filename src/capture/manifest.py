"""Session/manifest builder: assembles capture steps into a schema-valid manifest.json.

Step IDs and ordering are assigned here (never trusted from the caller) so the
1:1 step-mapping invariant (CLAUDE.md) holds from the moment a manifest exists.
"""

import json
import os
import tempfile
from pathlib import Path

SCHEMA_VERSION = "1.0"


class ManifestBuilder:
    def __init__(
        self,
        session_id,
        *,
        title="",
        started_utc=None,
        machine="",
        os_build="",
        narration_wav=None,
    ):
        self.session_id = session_id
        self.title = title
        self.started_utc = started_utc
        self.ended_utc = None
        self.machine = machine
        self.os_build = os_build
        self.narration_wav = narration_wav
        self._steps = []

    def add_step(
        self,
        *,
        ts_utc,
        action,
        screen,
        screenshot,
        window,
        element,
        button=None,
        text_summary=None,
        redactions=None,
        screenshot_placeholder=False,
    ):
        """Append a step; returns its assigned step-NNN id. Steps are numbered
        by append order — the caller never chooses the id."""
        step_id = f"step-{len(self._steps) + 1:03d}"
        step = {
            "id": step_id,
            "ts_utc": ts_utc,
            "action": action,
            "screen": screen,
            "screenshot": screenshot,
            "screenshot_placeholder": screenshot_placeholder,
            "window": window,
            "element": element,
            "redactions": redactions or [],
        }
        if action == "click":
            if button is None:
                raise ValueError("click step requires button")
            step["button"] = button
        elif action == "type":
            if text_summary is None:
                raise ValueError("type step requires text_summary")
            step["text_summary"] = text_summary
        else:
            raise ValueError(f"unknown action {action!r}")
        self._steps.append(step)
        return step_id

    def finish(self, ended_utc):
        self.ended_utc = ended_utc

    def step_ids(self):
        return [s["id"] for s in self._steps]

    def to_dict(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "session": {
                "id": self.session_id,
                "title": self.title,
                "started_utc": self.started_utc,
                "ended_utc": self.ended_utc,
                "machine": self.machine,
                "os_build": self.os_build,
                "narration_wav": self.narration_wav,
            },
            "steps": list(self._steps),
        }

    def write(self, path):
        """Atomic write: build the full JSON in a temp file next to the
        destination, then os.replace() so a crash mid-write never leaves a
        truncated manifest.json behind."""
        path = Path(path)
        payload = json.dumps(self.to_dict(), indent=2)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".manifest-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp_name, path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
            raise
        return path
