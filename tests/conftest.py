"""Shared test helper for interactive UIA tests: launch a disposable,
isolated Notepad++ instance (`-multiInst`) as a safe click/type target, and
guarantee cleanup never touches a window this fixture didn't launch.

Windows 11's built-in Notepad shares one process across tabs/windows, so a
naive resolve-then-kill can force-close windows the test never launched —
this happened once during Phase 1 development and closed the user's real,
already-open Notepad tabs. Every interactive test that needs a scratch GUI
window must go through this fixture instead of rolling its own launch/kill.
"""

import subprocess
import time

import pytest

NPP_PATH = r"C:\Program Files\Notepad++\notepad++.exe"


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


def _kill_if_new(win, pids_before):
    pid = str(win.element_info.process_id)
    if pid in pids_before:
        win.close()
        return
    subprocess.run(["taskkill", "/PID", pid, "/F", "/T"], capture_output=True)


@pytest.fixture
def scratch_window():
    """Yields a live, focused Notepad++ window scoped to this test only."""
    from pywinauto import Desktop

    pids_before = _running_pids("notepad++.exe")
    before = {w.element_info.handle for w in Desktop(backend="uia").windows()}
    proc = subprocess.Popen([NPP_PATH, "-multiInst", "-nosession"])

    win = None
    deadline = time.time() + 10.0
    while time.time() < deadline:
        for w in Desktop(backend="uia").windows():
            if w.element_info.handle in before:
                continue
            try:
                title = w.window_text()
            except Exception:  # noqa: BLE001
                continue
            if "Notepad++" in title:
                win = w
                break
        if win is not None:
            break
        time.sleep(0.3)

    if win is None:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/F", "/T"], capture_output=True)
        raise RuntimeError("Notepad++ window never appeared")

    try:
        yield win
    finally:
        _kill_if_new(win, pids_before)
