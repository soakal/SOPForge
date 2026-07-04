"""Auto-upload: after a recording stops, POST the manifest + screenshots to
a running sopforge-server so the generated doc appears with zero manual
steps -- no browser upload form, no hunting for the capture folder.

Best-effort and non-blocking by design: if the server isn't running, or
the upload fails for any reason, the capture is already safely on disk
(manifest.json + screenshots, written by Recorder.stop() before this ever
runs) and can be uploaded later through the library page's upload form.
This must never raise -- a failed auto-upload is a missed convenience, not
a lost capture."""

import json
import logging
import os
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

DEFAULT_SERVER_URL = "http://127.0.0.1:8420"


def server_url_from_env():
    return os.environ.get("SOPFORGE_SERVER_URL", DEFAULT_SERVER_URL)


def upload_session(output_dir, server_url=None, timeout=10.0, transport=None):
    """Uploads output_dir's manifest.json + the screenshots it references
    to server_url's POST /sessions. Returns the new session_id on success,
    None on any failure (server unreachable, timeout, bad response,
    missing manifest) -- never raises. transport is an injectable httpx
    transport, used only by tests (see tests/test_upload.py) so they never
    make a real network call."""
    server_url = (server_url or server_url_from_env()).rstrip("/")
    output_dir = Path(output_dir)
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        return None

    try:
        manifest_json = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(manifest_json)
    except (OSError, json.JSONDecodeError):
        logger.warning("could not read manifest at %s for auto-upload", manifest_path)
        return None

    opened = []
    try:
        screenshot_names = {step["screenshot"] for step in manifest.get("steps", [])}
        files = []
        for name in screenshot_names:
            path = output_dir / name
            if not path.exists():
                continue
            fh = path.open("rb")
            opened.append(fh)
            files.append(("files", (name, fh, "image/png")))

        with httpx.Client(transport=transport, timeout=timeout) as client:
            resp = client.post(
                f"{server_url}/sessions",
                data={"manifest_json": manifest_json},
                files=files,
            )
        resp.raise_for_status()
        return resp.json()["session_id"]
    except Exception:  # noqa: BLE001 - best-effort; the capture is already safe on disk
        logger.warning(
            "auto-upload to %s failed; capture remains at %s", server_url, output_dir, exc_info=True
        )
        return None
    finally:
        for fh in opened:
            fh.close()
