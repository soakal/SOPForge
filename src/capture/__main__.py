"""CLI entry point: `python -m capture` runs the tray app; `--self-check`
initializes tray+hotkey+recorder and exits 0 without blocking or requiring
user input, for headless verification."""

import argparse
import sys

from capture.tray import TrayApp


def main(argv=None):
    parser = argparse.ArgumentParser(prog="capture")
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
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
