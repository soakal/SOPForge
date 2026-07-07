"""CLI entry point: `python -m capture` runs the tray app; `--self-check`
initializes tray+hotkey+recorder and exits 0 without blocking or requiring
user input, for headless verification."""

import argparse
import sys
import threading

from capture import __version__
from capture.tray import TrayApp


def _stdin_exit_watcher(app):
    """For automated smoke testing (scripts/verify_exe.py) only: exits via
    the exact same TrayApp.exit() the tray menu's Exit item calls, triggered
    by a line on stdin instead of a system-tray mouse click — UI-automating
    a real system tray icon is unreliable in general, this exercises the
    identical code path a real Exit click would run. Harmless for a normal
    interactive launch: stdin isn't redirected there, so this just blocks
    (or raises, caught below) until the process exits some other way."""
    try:
        for line in sys.stdin:
            if line.strip() == "EXIT":
                app.exit()
                return
    except Exception:  # noqa: BLE001
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(prog="capture")
    parser.add_argument("--version", action="version", version=f"sopforge {__version__}")
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="initialize tray+hotkey+recorder, then exit 0 without blocking",
    )
    args = parser.parse_args(argv)

    app = TrayApp()
    if args.self_check:
        app.self_check()
        return 0

    threading.Thread(target=_stdin_exit_watcher, args=(app,), daemon=True).start()
    app.run(on_ready=lambda: print("TRAY_READY", flush=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
