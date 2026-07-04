"""Builds dist/sopforge.exe via PyInstaller (sopforge.spec) and optionally
asserts its size stays under a threshold.

Usage: python scripts/build_exe.py [--assert-size MB] [--skip-build]
"""

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "sopforge.spec"
DIST_EXE = REPO_ROOT / "dist" / "sopforge.exe"


def build():
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", str(SPEC_PATH)],
        cwd=REPO_ROOT,
        check=True,
    )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--assert-size", type=float, default=None, help="fail if the built EXE is >= this many MB"
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="only check the size of an already-built dist/sopforge.exe",
    )
    args = parser.parse_args(argv)

    if not args.skip_build:
        build()

    if not DIST_EXE.exists():
        print(f"FAIL: {DIST_EXE} does not exist", file=sys.stderr)
        return 1

    size_mb = DIST_EXE.stat().st_size / (1024 * 1024)
    print(f"{DIST_EXE}: {size_mb:.2f} MB")

    if args.assert_size is not None and size_mb >= args.assert_size:
        print(f"FAIL: {size_mb:.2f} MB >= {args.assert_size} MB threshold", file=sys.stderr)
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
