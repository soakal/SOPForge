"""SOPForge capture agent: click hooks, UIA metadata, screenshots, manifest output."""

from capture.dpi import set_process_dpi_awareness
from sopforge_version import __version__

# Must run before capture.tray imports pynput (see .claude/skills/uia-notes.md).
set_process_dpi_awareness()

__all__ = ["__version__"]
