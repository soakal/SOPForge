"""Screenshot-on-event: one mss grab per event, written as sequential
NNN.png in the session's output directory, tagged with the monitor index
the event's screen coordinates fall on. If real GDI screen capture fails
(some virtualized/remoted sessions block BitBlt entirely — see
.claude/skills/uia-notes.md), a placeholder image is written instead of
crashing the whole capture session over one screenshot."""

import logging
from pathlib import Path

import mss
import mss.exception
import mss.tools
from PIL import Image

logger = logging.getLogger(__name__)


class ScreenshotWriter:
    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._count = 0

    def monitor_for_point(self, x, y):
        """1-based monitor index (mss.monitors[0] is the virtual union of all
        monitors, so real monitors start at index 1); falls back to 1 if the
        point isn't inside any known monitor rect."""
        with mss.mss() as sct:
            for idx, mon in enumerate(sct.monitors[1:], start=1):
                if (
                    mon["left"] <= x < mon["left"] + mon["width"]
                    and mon["top"] <= y < mon["top"] + mon["height"]
                ):
                    return idx
        return 1

    def capture(self, x, y):
        """Returns (filename, monitor_idx, is_placeholder). is_placeholder
        is True when real GDI capture failed and a solid-color placeholder
        was written instead — the caller must record this on the manifest
        step so a downstream review report (Phase 2) can flag it rather than
        it being silently indistinguishable from a real screenshot."""
        self._count += 1
        filename = f"{self._count:03d}.png"
        path = self.output_dir / filename
        monitor_idx = self.monitor_for_point(x, y)
        is_placeholder = False
        with mss.mss() as sct:
            monitor = sct.monitors[monitor_idx]
            try:
                shot = sct.grab(monitor)
                mss.tools.to_png(shot.rgb, shot.size, output=str(path))
            except mss.exception.ScreenShotError:
                logger.warning(
                    "GDI screen capture failed; writing placeholder image for %s",
                    filename,
                )
                self._write_placeholder(path, monitor["width"], monitor["height"])
                is_placeholder = True
        return filename, monitor_idx, is_placeholder

    @staticmethod
    def _write_placeholder(path, width, height):
        Image.new("RGB", (max(width, 1), max(height, 1)), (64, 64, 64)).save(path)
