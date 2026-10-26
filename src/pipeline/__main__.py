"""CLI entry point: `python -m pipeline` (or the frozen sopforge-server.exe)
runs the FastAPI app via uvicorn. Session data is written under a
configurable --sessions-root (defaults to ~/SOPForge/sessions)."""

import argparse
import os
import sys
from pathlib import Path

import uvicorn

from pipeline import __version__
from pipeline.config import load_user_api_keys
from pipeline.server import create_app

DEFAULT_SESSIONS_ROOT = Path.home() / "SOPForge" / "sessions"


def _documents_dir():
    """The user's real Documents folder (it may be redirected, e.g. to
    OneDrive), falling back to ~/Documents."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            return Path(os.path.expandvars(winreg.QueryValueEx(key, "Personal")[0]))
    except (ImportError, OSError):
        return Path.home() / "Documents"


def main(argv=None):
    # The server ships as a console=False (windowed) PyInstaller EXE so no
    # console window flashes up when the tray/autostart launches it. Such a
    # process has sys.stdout / sys.stderr == None unless the launcher
    # redirected them, and uvicorn's default log formatter calls
    # sys.stdout.isatty() at startup -> AttributeError on None. Give it a
    # sink so logging configures cleanly however the EXE was launched
    # (Start-Process, double-click, autostart shortcut, or a console).
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    parser = argparse.ArgumentParser(prog="sopforge-server")
    parser.add_argument("--version", action="version", version=f"sopforge-server {__version__}")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument("--sessions-root", type=Path, default=DEFAULT_SESSIONS_ROOT)
    # Each finished doc is also copied to <outputs-root>/<Title_vX>/.
    parser.add_argument("--outputs-root", type=Path, default=None)
    args = parser.parse_args(argv)

    load_user_api_keys()
    app = create_app(
        sessions_root=args.sessions_root,
        bind_host=args.host,
        outputs_root=args.outputs_root or _documents_dir() / "SOPForge",
    )
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
