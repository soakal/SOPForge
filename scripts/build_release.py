"""Packages an already-built dist/sopforge/ + dist/sopforge-server/ into a
single self-contained folder (and zip) that can be handed to someone else —
copied to a USB stick, shared over a network drive, whatever — without them
needing Python, PyInstaller, or a clone of this repo. install.ps1/uninstall.ps1
work unchanged inside it: they resolve dist/sopforge and dist/sopforge-server
relative to their own location ($PSScriptRoot), which this script preserves.

Usage: python scripts/build_release.py [--zip]
"""

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CAPTURE_DIST = REPO_ROOT / "dist" / "sopforge"
SERVER_DIST = REPO_ROOT / "dist" / "sopforge-server"
RELEASE_DIR = REPO_ROOT / "release" / "SOPForge"


def build_release_folder():
    if not CAPTURE_DIST.exists():
        raise SystemExit(f"Not built: {CAPTURE_DIST} -- run scripts/build_exe.py first.")
    if not SERVER_DIST.exists():
        raise SystemExit(f"Not built: {SERVER_DIST} -- run scripts/build_server_exe.py first.")

    if RELEASE_DIR.exists():
        shutil.rmtree(RELEASE_DIR)
    RELEASE_DIR.mkdir(parents=True)

    shutil.copytree(CAPTURE_DIST, RELEASE_DIR / "dist" / "sopforge")
    shutil.copytree(SERVER_DIST, RELEASE_DIR / "dist" / "sopforge-server")
    for name in ("install.ps1", "uninstall.ps1", "USER_MANUAL.md", "LICENSE"):
        shutil.copy2(REPO_ROOT / name, RELEASE_DIR / name)

    return RELEASE_DIR


def zip_release_folder():
    zip_path = RELEASE_DIR.parent / "SOPForge.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(RELEASE_DIR.rglob("*")):
            if path.is_file():
                zf.write(path, Path("SOPForge") / path.relative_to(RELEASE_DIR))
    return zip_path


def dir_size_mb(path):
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", action="store_true", help="also produce release/SOPForge.zip")
    args = parser.parse_args(argv)

    release_dir = build_release_folder()
    print(f"{release_dir}: {dir_size_mb(release_dir):.2f} MB")

    if args.zip:
        zip_path = zip_release_folder()
        print(f"{zip_path}: {zip_path.stat().st_size / (1024 * 1024):.2f} MB")

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
