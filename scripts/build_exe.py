"""Builds the sopforge app via PyInstaller (sopforge.spec) and optionally
asserts its total footprint stays under a size threshold.

One-folder build (see sopforge.spec for why, vs the original onefile plan):
dist/sopforge/sopforge.exe is the entry point, but most of the footprint
lives in its sibling files in that same folder, so the size check measures
the whole dist/sopforge/ directory, not just the small stub EXE.

Usage: python scripts/build_exe.py [--assert-size MB] [--skip-build]
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "sopforge.spec"
DIST_DIR = REPO_ROOT / "dist" / "sopforge"
DIST_EXE = DIST_DIR / "sopforge.exe"

# UPX compresses the one-folder build's DLLs in place (decompressed at load
# time, not extracted to a temp dir like onefile) — needed to get the
# one-folder build's total footprint under the 40MB threshold while keeping
# onefile's cold-start penalty avoided. Falls back to an unset --upx-dir
# (PyInstaller then only finds `upx` if it's on PATH) if not found here.
_KNOWN_UPX_DIR = (
    Path.home()
    / "AppData"
    / "Local"
    / "Microsoft"
    / "WinGet"
    / "Packages"
    / "UPX.UPX_Microsoft.Winget.Source_8wekyb3d8bbwe"
    / "upx-5.2.0-win64"
)


def _upx_dir():
    if shutil.which("upx"):
        return None  # already on PATH, no need for --upx-dir
    if (_KNOWN_UPX_DIR / "upx.exe").exists():
        return str(_KNOWN_UPX_DIR)
    return None


def build():
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", str(SPEC_PATH)]
    upx_dir = _upx_dir()
    if upx_dir:
        cmd += ["--upx-dir", upx_dir]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def dir_size_mb(path):
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--assert-size",
        type=float,
        default=None,
        help="fail if the built app's total footprint is >= this many MB",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="only check the size of an already-built dist/sopforge/",
    )
    args = parser.parse_args(argv)

    if not args.skip_build:
        build()

    if not DIST_EXE.exists():
        print(f"FAIL: {DIST_EXE} does not exist", file=sys.stderr)
        return 1

    size_mb = dir_size_mb(DIST_DIR)
    print(f"{DIST_DIR}: {size_mb:.2f} MB total")

    if args.assert_size is not None and size_mb >= args.assert_size:
        print(f"FAIL: {size_mb:.2f} MB >= {args.assert_size} MB threshold", file=sys.stderr)
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
