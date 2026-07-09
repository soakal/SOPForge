"""Single source of truth for the SOPForge version string, shared by both
the capture agent and the pipeline server (two separate top-level packages,
two separate frozen EXEs). Kept as a tiny top-level module -- not buried in
either package's __init__ -- so both can import it (src is on pathex in both
PyInstaller specs) without one package depending on the other.

Bump this on every user-facing release so the version shown in the tray
tooltip, the library page footer, `--version`, and GET /version all move
together. Keep pyproject.toml's version in sync with it."""

__version__ = "1.4.12"
