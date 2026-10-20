"""Capture-side user settings: currently just whether to record narration
audio alongside a capture session. A small per-user TOML file
(~/SOPForge/capture_settings.toml), read with stdlib tomllib (same
technique redact.py already uses for its bundled config) and written with a
hand-rolled serializer -- there's exactly one field, so pulling in the
pipeline's pydantic/models.toml machinery would be overkill.

A missing or corrupt settings file always resolves to the default
(narration recording on) rather than raising -- an optional preference
must never be able to crash the tray on startup."""

import os
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SETTINGS_PATH = Path.home() / "SOPForge" / "capture_settings.toml"


@dataclass
class CaptureSettings:
    record_narration: bool = True


def load_settings(path=None):
    path = Path(path) if path is not None else DEFAULT_SETTINGS_PATH
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return CaptureSettings()
    return CaptureSettings(record_narration=bool(data.get("record_narration", True)))


def save_settings(settings, path=None):
    """Atomic write (temp file + os.replace), matching manifest.py's
    pattern, so a crash mid-write never leaves a corrupt settings file that
    would fall back to defaults forever."""
    path = Path(path) if path is not None else DEFAULT_SETTINGS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        "# SOPForge capture settings.\n"
        f"record_narration = {'true' if settings.record_narration else 'false'}\n"
    )
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".capture_settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise
