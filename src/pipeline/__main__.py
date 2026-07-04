"""CLI entry point: `python -m pipeline` (or the frozen sopforge-server.exe)
runs the FastAPI app via uvicorn. Session data is written under a
configurable --sessions-root (defaults to ~/SOPForge/sessions)."""

import argparse
import sys
from pathlib import Path

import uvicorn

from pipeline.server import create_app

DEFAULT_SESSIONS_ROOT = Path.home() / "SOPForge" / "sessions"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sopforge-server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument("--sessions-root", type=Path, default=DEFAULT_SESSIONS_ROOT)
    args = parser.parse_args(argv)

    app = create_app(sessions_root=args.sessions_root)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
