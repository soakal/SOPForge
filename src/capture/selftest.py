"""Self-test harness (Phase 1 acceptance criteria 1-2): drives Notepad++
(Win32), Chrome (Chromium), and VS Code (Electron) through a fixed sequence
of scripted interaction points against a live Recorder, and measures the
fraction of interactions that resolve non-empty UIA element metadata.

Uses Notepad++ instead of Windows 11's built-in Notepad — see
.claude/skills/uia-notes.md: built-in Notepad shares one process across
windows/tabs, so this harness's process-isolated launch/kill pattern would be
unsafe against it. Each app launches in an isolated process
(`-multiInst` / throwaway `--user-data-dir`) so this harness can never attach
to or kill a window the user already had open.

Also per uia-notes.md: this build environment denies synthetic OS-level
input injection outright, so interaction points are fed directly into
Recorder's `on_event` entry point (real UIA resolution against real live
windows, same as the recorder.py integration test) rather than via injected
clicks a global hook would need to observe.
"""

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from pywinauto import Desktop

from capture.recorder import Recorder

NPP_PATH = r"C:\Program Files\Notepad++\notepad++.exe"
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
VSCODE_PATH = r"C:\Users\Brian\AppData\Local\Programs\Microsoft VS Code\Code.exe"

_WINDOW_CHROME_NAMES = {"minimize", "maximize", "close", "restore"}
RESULTS_PATH = Path(__file__).resolve().parent.parent.parent / "phases" / "01-results.md"


def _running_pids(image_name):
    out = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
    ).stdout
    pids = set()
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == image_name.lower():
            pids.add(parts[1])
    return pids


def _wait_new_window(before, title_predicate, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for w in Desktop(backend="uia").windows():
            if w.element_info.handle in before:
                continue
            try:
                title = w.window_text()
            except Exception:  # noqa: BLE001
                continue
            if title_predicate(title):
                return w
        time.sleep(0.3)
    raise RuntimeError("window never appeared")


def _kill_if_new(win, pids_before):
    pid = str(win.element_info.process_id)
    if pid in pids_before:
        win.close()
        return
    subprocess.run(["taskkill", "/PID", pid, "/F", "/T"], capture_output=True)


def _find_candidates(win, count, timeout=15.0):
    """Polls until enough non-window-chrome descendants with a real
    (non-empty) rectangle appear, then returns up to `count` of them spread
    evenly across the list for spatial diversity — (descendant, rect) pairs,
    not yet clicked or resolved."""
    deadline = time.time() + timeout
    candidates = []
    while time.time() < deadline:
        try:
            descendants = win.descendants()
        except Exception:  # noqa: BLE001
            descendants = []
        candidates = []
        for d in descendants:
            try:
                name = (d.window_text() or "").strip().lower()
                rect = d.rectangle()
            except Exception:  # noqa: BLE001
                continue
            if name in _WINDOW_CHROME_NAMES:
                continue
            if rect.width() <= 0 or rect.height() <= 0:
                continue
            candidates.append((d, rect))
        if len(candidates) >= count:
            break
        time.sleep(0.5)

    if not candidates:
        return []
    step = max(1, len(candidates) // count)
    return candidates[::step][:count]


def _interact_and_feed(win, recorder, count):
    """Clicks each chosen candidate and immediately resolves+records that
    same point, one at a time — interleaved, not click-everything-then-
    resolve-everything, since an earlier click can change the window layout
    (a dialog, a focus change) and shift what a later resolve_at() would see
    at an earlier point's coordinates if resolution were deferred."""
    for descendant, rect in _find_candidates(win, count):
        try:
            descendant.click()  # message-based; best-effort, failure is fine
        except Exception:  # noqa: BLE001
            pass
        x = rect.left + rect.width() // 2
        y = rect.top + rect.height() // 2
        recorder._on_input_event(
            {"action": "click", "button": "left", "x": x, "y": y, "ts": time.time()}
        )


def run_notepadpp(recorder, click_count):
    pids_before = _running_pids("notepad++.exe")
    before = {w.element_info.handle for w in Desktop(backend="uia").windows()}
    subprocess.Popen([NPP_PATH, "-multiInst", "-nosession"])
    win = _wait_new_window(before, lambda t: "Notepad++" in t, timeout=10)
    try:
        _interact_and_feed(win, recorder, click_count)
    finally:
        _kill_if_new(win, pids_before)


def run_chrome(recorder, click_count):
    pids_before = _running_pids("chrome.exe")
    before = {w.element_info.handle for w in Desktop(backend="uia").windows()}
    with tempfile.TemporaryDirectory(
        prefix="sopforge-selftest-chrome-", ignore_cleanup_errors=True
    ) as profile_dir:
        subprocess.Popen(
            [
                CHROME_PATH,
                f"--user-data-dir={profile_dir}",
                "--new-window",
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
            ]
        )
        win = _wait_new_window(before, lambda t: "Chrome" in t or t == "New Tab", timeout=15)
        try:
            _interact_and_feed(win, recorder, click_count)
        finally:
            _kill_if_new(win, pids_before)
            time.sleep(1)


def run_vscode(recorder, click_count):
    pids_before = _running_pids("Code.exe")
    before = {w.element_info.handle for w in Desktop(backend="uia").windows()}
    with tempfile.TemporaryDirectory(
        prefix="sopforge-selftest-vscode-", ignore_cleanup_errors=True
    ) as data_dir:
        extensions_dir = str(Path(data_dir) / "extensions")
        subprocess.Popen(
            [
                VSCODE_PATH,
                "--new-window",
                f"--user-data-dir={data_dir}",
                f"--extensions-dir={extensions_dir}",
            ]
        )
        win = _wait_new_window(before, lambda t: "Visual Studio Code" in t, timeout=20)
        try:
            _interact_and_feed(win, recorder, click_count)
        finally:
            _kill_if_new(win, pids_before)
            time.sleep(1)


APPS = {
    "notepadpp": run_notepadpp,
    "chrome": run_chrome,
    "vscode": run_vscode,
}


def run_selftest(captures_root, click_count=5, apps=None):
    """Drives each named app through `click_count` scripted interaction
    points against one Recorder session. Returns (manifest_path, per_app)
    where per_app maps app name -> (non_empty_count, total_count)."""
    apps = apps if apps is not None else list(APPS)
    recorder = Recorder(captures_root, machine="selftest")
    recorder.start()
    per_app_ranges = {}
    try:
        for name in apps:
            start = len(recorder._builder.step_ids())
            APPS[name](recorder, click_count)
            end = len(recorder._builder.step_ids())
            per_app_ranges[name] = (start, end)
    finally:
        manifest_path = recorder.stop()

    steps = json.loads(manifest_path.read_text(encoding="utf-8"))["steps"]
    per_app = {}
    for name, (start, end) in per_app_ranges.items():
        app_steps = steps[start:end]
        non_empty = sum(
            1 for s in app_steps if s["element"]["name"] or s["element"]["control_type"]
        )
        per_app[name] = (non_empty, len(app_steps))
    return manifest_path, per_app


def write_results(per_app, threshold=0.9, results_path=None):
    total_non_empty = sum(n for n, _ in per_app.values())
    total = sum(t for _, t in per_app.values())
    overall = (total_non_empty / total) if total else 0.0

    lines = [
        "## Criterion 1: self-test harness element-metadata coverage",
        "",
    ]
    for name, (n, t) in per_app.items():
        pct = (n / t * 100) if t else 0.0
        lines.append(f"- **{name}**: {n}/{t} ({pct:.1f}%) non-empty element metadata")
    lines.append("")
    lines.append(
        f"**Overall: {total_non_empty}/{total} ({overall * 100:.1f}%)** "
        f"— threshold {threshold * 100:.0f}%"
    )
    lines.append("")

    path = results_path or RESULTS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return overall


def main(argv=None):
    parser = argparse.ArgumentParser(prog="capture.selftest")
    parser.add_argument(
        "--all", action="store_true", help="run all three target apps (Notepad++/Chrome/VS Code)"
    )
    parser.add_argument("--clicks", type=int, default=5, help="scripted interaction points per app")
    parser.add_argument("--threshold", type=float, default=0.9)
    args = parser.parse_args(argv)

    if not args.all:
        parser.error("--all is currently the only supported mode")

    with tempfile.TemporaryDirectory(
        prefix="sopforge-selftest-session-", ignore_cleanup_errors=True
    ) as captures_root:
        _manifest_path, per_app = run_selftest(captures_root, click_count=args.clicks)

    overall = write_results(per_app, threshold=args.threshold)

    if overall < args.threshold:
        print(
            f"FAIL: {overall * 100:.1f}% < {args.threshold * 100:.0f}% threshold",
            file=sys.stderr,
        )
        return 1
    print(f"PASS: {overall * 100:.1f}% >= {args.threshold * 100:.0f}% threshold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
