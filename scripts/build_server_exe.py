"""Builds sopforge-server.exe via PyInstaller (sopforge-server.spec) and
verifies it: launches the frozen EXE, polls GET / until HTTP 200 with real
UI markup served from the bundle, measures first-launch vs steady-state
timing (same AV-scan-cost distinction as scripts/verify_exe.py — see its
module docstring / phases/DEVIATIONS.md's "Criterion 4 packaging mode"
entry), asserts steady-state stays under a threshold, and confirms the
process exits cleanly via POST /shutdown (AC3) — a console=False
(windowed-subsystem) PyInstaller build does not reliably receive Windows
console control events (CTRL_BREAK_EVENT/CTRL_C_EVENT) the way a
console-subsystem process does, confirmed empirically while building this
script, so an HTTP-triggered stop is the mechanism that actually works.

Usage: python scripts/build_server_exe.py [--assert-start SECONDS]
       [--assert-size MB] [--skip-build]
"""

import argparse
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "sopforge-server.spec"
DIST_DIR = REPO_ROOT / "dist" / "sopforge-server"
DIST_EXE = DIST_DIR / "sopforge-server.exe"
RESULTS_PATH = REPO_ROOT / "phases" / "03-results.md"

READY_TIMEOUT = 15.0
POLL_INTERVAL = 0.1
EXIT_TIMEOUT = 10.0
STEADY_STATE_RUNS = 3
DEFAULT_START_THRESHOLD = 5.0

# Same UPX location Phase 1's scripts/build_exe.py looks for.
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
        return None
    if (_KNOWN_UPX_DIR / "upx.exe").exists():
        return str(_KNOWN_UPX_DIR)
    return None


def build():
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", str(SPEC_PATH)]
    upx_dir = _upx_dir()
    if upx_dir:
        cmd += ["--upx-dir", upx_dir]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)
    # Self-signs both dist/ EXEs -- see scripts/sign_dist.ps1's docstring for
    # why a self-signed cert is enough for this local-only tool.
    subprocess.run(
        ["powershell", "-File", str(REPO_ROOT / "scripts" / "sign_dist.ps1")],
        cwd=REPO_ROOT,
        check=True,
    )


def dir_size_mb(path):
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def measure_one_launch(sessions_root):
    """Returns (elapsed_seconds, exit_returncode) for a single launch:
    time from process start to GET / returning 200 with real UI markup,
    then a graceful stop via POST /shutdown and a clean exit."""
    port = _find_free_port()
    start = time.time()
    proc = subprocess.Popen(
        [str(DIST_EXE), "--port", str(port), "--sessions-root", str(sessions_root)],
        # Explicit redirection, not inherited handles: a console=False
        # (windowed) PyInstaller EXE launched with unredirected stdio
        # reliably hung here (connections timed out, process never
        # crashed or exited) when the parent's own stdout/stderr handles
        # weren't a plain Windows console (e.g. a shell's pipe-based
        # terminal) — reproduced directly, not assumed. Always redirecting
        # sidesteps whatever the parent shell's handles look like.
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    deadline = time.time() + READY_TIMEOUT
    response = None
    while time.time() < deadline:
        try:
            response = httpx.get(f"http://127.0.0.1:{port}/", timeout=1.0)
            if response.status_code == 200:
                break
        except httpx.TransportError:
            pass
        time.sleep(POLL_INTERVAL)
    else:
        proc.kill()
        proc.wait(timeout=EXIT_TIMEOUT)
        raise RuntimeError(f"server never responded 200 within {READY_TIMEOUT}s")
    elapsed = time.time() - start

    if response is None or "<!doctype html>" not in response.text.lower():
        proc.kill()
        proc.wait(timeout=EXIT_TIMEOUT)
        raise RuntimeError("GET / did not return UI markup")

    try:
        httpx.post(f"http://127.0.0.1:{port}/shutdown", timeout=2.0)
    except httpx.TransportError:
        pass  # the process exiting mid-response is the expected outcome
    try:
        proc.wait(timeout=EXIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError("process did not exit within timeout after POST /shutdown")

    return elapsed, proc.returncode


def measure_first_and_steady_state(sessions_root, steady_state_runs=STEADY_STATE_RUNS):
    """Returns (first_launch_seconds, steady_state_seconds_list, returncodes)."""
    first_elapsed, first_returncode = measure_one_launch(sessions_root)
    steady_elapsed = []
    returncodes = [first_returncode]
    for _ in range(steady_state_runs):
        elapsed, returncode = measure_one_launch(sessions_root)
        steady_elapsed.append(elapsed)
        returncodes.append(returncode)
    return first_elapsed, steady_elapsed, returncodes


def write_results(first_elapsed, steady_elapsed, returncodes, threshold, results_path=None):
    path = results_path or RESULTS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if not existing or existing.endswith("\n\n"):
        prefix = ""
    elif existing.endswith("\n"):
        prefix = "\n"
    else:
        prefix = "\n\n"

    steady_avg = sum(steady_elapsed) / len(steady_elapsed)
    steady_str = ", ".join(f"{s:.3f}s" for s in steady_elapsed)
    lines = [
        prefix + "## AC3: sopforge-server.exe cold-start timing and clean exit",
        "",
        f"- First launch after build: {first_elapsed:.3f}s (one-time AV-scan cost, "
        "same mechanism as Phase 1's sopforge.exe — see phases/DEVIATIONS.md)",
        f"- Steady-state launches ({len(steady_elapsed)} repeats): {steady_str} "
        f"(average {steady_avg:.3f}s, threshold {threshold}s)",
        f"- Clean exit return codes: {returncodes}",
        "",
    ]
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return steady_avg


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--assert-start",
        type=float,
        default=None,
        help="fail if the steady-state warm-start average is >= this many seconds",
    )
    parser.add_argument(
        "--assert-size",
        type=float,
        default=None,
        help="fail if the built app's total footprint is >= this many MB",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="only verify an already-built dist/sopforge-server/",
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

    with tempfile.TemporaryDirectory(prefix="sopforge-server-verify-") as tmp:
        sessions_root = Path(tmp) / "sessions"
        first_elapsed, steady_elapsed, returncodes = measure_first_and_steady_state(sessions_root)

    threshold = args.assert_start if args.assert_start is not None else DEFAULT_START_THRESHOLD
    steady_avg = write_results(first_elapsed, steady_elapsed, returncodes, threshold)

    ok = steady_avg < threshold and all(rc == 0 for rc in returncodes)
    print(
        f"{'PASS' if ok else 'FAIL'}: first launch {first_elapsed:.3f}s, "
        f"steady-state average {steady_avg:.3f}s (threshold {threshold}s), "
        f"exit codes {returncodes}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
