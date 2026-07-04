"""Screenshot-on-event: one mss grab per event, written as sequential
NNN.png in the session's output directory, tagged with the monitor index
the event's screen coordinates fall on."""

from pathlib import Path

import mss
import mss.tools


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
        self._count += 1
        filename = f"{self._count:03d}.png"
        path = self.output_dir / filename
        monitor_idx = self.monitor_for_point(x, y)
        with mss.mss() as sct:
            monitor = sct.monitors[monitor_idx]
            shot = sct.grab(monitor)
            mss.tools.to_png(shot.rgb, shot.size, output=str(path))
        return filename, monitor_idx
