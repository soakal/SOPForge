"""Phase 1 spike (throwaway, not part of src/capture): resolve the UIA element
at a known screen point in Notepad++ (Win32), Chrome (Chromium), and VS Code
(Electron). Prints per-app JSON; exits 0 only if all three yield a non-empty
name or control_type. Findings get appended to .claude/skills/uia-notes.md.

Safety: every launch is forced into its own isolated process (Notepad++
`-multiInst`, Chrome/VS Code with a throwaway `--user-data-dir`) so this script
can never attach to — and therefore never force-kill — a window the user
already had open. Windows 11's built-in Notepad is intentionally NOT used here:
it shares one process across tabs/windows, so a naive PID-based cleanup can
force-close windows the script didn't launch (this bit us once already — see
uia-notes.md). Even so, kill_window() double-checks the PID was not alive
before this script started, as a second layer of defense.
"""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import win32gui
from pywinauto import Desktop

VSCODE_PATH = r"C:\Users\Brian\AppData\Local\Programs\Microsoft VS Code\Code.exe"
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
NPP_PATH = r"C:\Program Files\Notepad++\notepad++.exe"
NOTES_PATH = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "uia-notes.md"


def running_pids(image_name):
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


def window_key(w):
    return (w.element_info.handle,)


def snapshot():
    return {window_key(w) for w in Desktop(backend="uia").windows()}


def wait_new_window(before, title_predicate, timeout=15.0, poll=0.4):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for w in Desktop(backend="uia").windows():
            if window_key(w) in before:
                continue
            try:
                title = w.window_text()
            except Exception:  # noqa: BLE001
                continue
            if title_predicate(title):
                return w
        time.sleep(poll)
    raise RuntimeError(f"no matching new window after {timeout}s")


def bring_to_foreground(win, timeout=8.0, poll=0.3):
    """set_focus() alone doesn't guarantee the window is topmost at its own
    screen coordinates (Windows focus-stealing prevention can leave another
    window visually on top) — poll until GetForegroundWindow actually matches,
    so from_point() below can't accidentally resolve a different window. Spike
    script: best-effort only, warns rather than raising if it never confirms,
    since a false-negative here just means the click point may resolve the
    wrong window (visible in the printed JSON) rather than corrupting cleanup."""
    handle = win.element_info.handle
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            win32gui.SetForegroundWindow(handle)
        except Exception:  # noqa: BLE001
            pass
        try:
            win.set_focus()
        except Exception:  # noqa: BLE001
            pass
        if win32gui.GetForegroundWindow() == handle:
            return
        time.sleep(poll)
    print(
        f"WARNING: window {handle} never confirmed foreground within {timeout}s",
        file=sys.stderr,
    )


def resolve_at(x, y):
    try:
        elem = Desktop(backend="uia").from_point(x, y)
        info = elem.element_info
        rect = info.rectangle
        return {
            "name": info.name or "",
            "control_type": info.control_type or "",
            "automation_id": info.automation_id or "",
            "framework": info.framework_id or "",
            "class_name": info.class_name or "",
            "bounding_rect": [rect.left, rect.top, rect.right, rect.bottom],
        }
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def kill_window(win, image_name, pids_before):
    """Kill only the process that owns this window, and only if that PID did
    not exist before this script launched anything (never touch a pre-existing
    instance, e.g. one the user already had open)."""
    pid = str(win.element_info.process_id)
    if pid in pids_before:
        print(
            f"WARNING: {image_name} window resolved to pre-existing PID {pid} — "
            "not killing, closing the window instead.",
            file=sys.stderr,
        )
        try:
            win.close()
        except Exception:  # noqa: BLE001
            pass
        return
    subprocess.run(["taskkill", "/PID", pid, "/F", "/T"], capture_output=True)


def spike_notepadpp():
    pids_before = running_pids("notepad++.exe")
    before = snapshot()
    subprocess.Popen([NPP_PATH, "-multiInst", "-nosession"])
    win = None
    try:
        win = wait_new_window(before, lambda t: "Notepad++" in t, timeout=10)
        bring_to_foreground(win)
        rect = win.rectangle()
        x, y = rect.left + 250, rect.top + 200
        return resolve_at(x, y)
    finally:
        if win is not None:
            kill_window(win, "notepad++.exe", pids_before)


def spike_chrome():
    pids_before = running_pids("chrome.exe")
    before = snapshot()
    with tempfile.TemporaryDirectory(
        prefix="sopforge-spike-chrome-", ignore_cleanup_errors=True
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
        win = None
        try:
            win = wait_new_window(before, lambda t: "Chrome" in t or t == "New Tab", timeout=15)
            bring_to_foreground(win)
            rect = win.rectangle()
            x, y = rect.left + 120, rect.top + 45  # tab strip / address bar band
            return resolve_at(x, y)
        finally:
            if win is not None:
                kill_window(win, "chrome.exe", pids_before)
            time.sleep(1)  # let the child release the temp profile dir before cleanup


def spike_vscode():
    pids_before = running_pids("Code.exe")
    before = snapshot()
    with tempfile.TemporaryDirectory(
        prefix="sopforge-spike-vscode-", ignore_cleanup_errors=True
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
        win = None
        try:
            win = wait_new_window(before, lambda t: "Visual Studio Code" in t, timeout=20)
            bring_to_foreground(win)
            rect = win.rectangle()
            x, y = rect.left + 150, rect.top + 40  # title/menu band
            return resolve_at(x, y)
        finally:
            if win is not None:
                kill_window(win, "Code.exe", pids_before)
            time.sleep(1)


def main():
    results = {
        "notepadpp": spike_notepadpp(),
        "chrome": spike_chrome(),
        "vscode": spike_vscode(),
    }
    print(json.dumps(results, indent=2))

    ok = all(bool(r.get("name") or r.get("control_type")) for r in results.values())

    notes = ["## UIA spike findings (Phase 1, scripts/spike_uia.py)", ""]
    for app_name, r in results.items():
        if "error" in r:
            notes.append(f"- **{app_name}**: resolution FAILED — {r['error']}")
        else:
            notes.append(
                f"- **{app_name}**: framework={r['framework']!r} "
                f"class={r['class_name']!r} control_type={r['control_type']!r} "
                f"automation_id={r['automation_id']!r} name={r['name']!r} "
                f"bounding_rect={r['bounding_rect']!r}"
            )
    notes.append("")
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with NOTES_PATH.open("a", encoding="utf-8") as f:
        f.write("\n".join(notes) + "\n")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
