"""Phase 1 spike (throwaway, not part of src/capture): resolve the UIA element
at a known screen point in Notepad (Win32), Chrome (Chromium), and VS Code
(Electron). Prints per-app JSON; exits 0 only if all three yield a non-empty
name or control_type. Findings get appended to .claude/skills/uia-notes.md.

Windows 11's notepad.exe is a redirector into a packaged app, and Chrome/VS Code
are multi-process, so tracking the launched window by PID (pywinauto's normal
Application.start()) is unreliable. Instead: snapshot the desktop window list,
launch via subprocess, and poll for a new window matching a title predicate.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

from pywinauto import Desktop

VSCODE_PATH = r"C:\Users\Brian\AppData\Local\Programs\Microsoft VS Code\Code.exe"
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
NOTES_PATH = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "uia-notes.md"


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


def kill_window(win):
    """Kill only the process that owns this specific window (by PID, never by
    image name) — never touch other running instances of the same app."""
    pid = win.element_info.process_id
    subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True)


def resolve_at(x, y):
    try:
        elem = Desktop(backend="uia").from_point(x, y)
        info = elem.element_info
        return {
            "name": info.name or "",
            "control_type": info.control_type or "",
            "automation_id": info.automation_id or "",
            "framework": info.framework_id or "",
            "class_name": info.class_name or "",
        }
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def spike_notepad():
    before = snapshot()
    subprocess.Popen(["notepad.exe"])
    win = None
    try:
        win = wait_new_window(before, lambda t: t.endswith("Notepad"), timeout=10)
        win.set_focus()
        rect = win.rectangle()
        x, y = rect.left + 250, rect.top + 200
        return resolve_at(x, y)
    finally:
        if win is not None:
            kill_window(win)


def spike_chrome():
    before = snapshot()
    subprocess.Popen([CHROME_PATH, "--new-window", "--no-first-run", "about:blank"])
    win = None
    try:
        win = wait_new_window(
            before, lambda t: "Chrome" in t or t == "New Tab", timeout=15
        )
        win.set_focus()
        rect = win.rectangle()
        x, y = rect.left + 120, rect.top + 45  # tab strip / address bar band
        return resolve_at(x, y)
    finally:
        if win is not None:
            kill_window(win)


def spike_vscode():
    before = snapshot()
    subprocess.Popen([VSCODE_PATH, "--new-window"])
    win = None
    try:
        win = wait_new_window(
            before, lambda t: "Visual Studio Code" in t, timeout=20
        )
        win.set_focus()
        rect = win.rectangle()
        x, y = rect.left + 150, rect.top + 40  # title/menu band
        return resolve_at(x, y)
    finally:
        if win is not None:
            kill_window(win)


def main():
    results = {
        "notepad": spike_notepad(),
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
                f"automation_id={r['automation_id']!r} name={r['name']!r}"
            )
    notes.append("")
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with NOTES_PATH.open("a", encoding="utf-8") as f:
        f.write("\n".join(notes) + "\n")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
