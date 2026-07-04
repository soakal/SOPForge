"""Interactive test: requires a real desktop session (UIA needs one — see
CLAUDE.md environment facts). Launches Notepad++ in an isolated process
(`-multiInst`) and kills only that process, never a pre-existing instance —
see .claude/skills/uia-notes.md for why that safety matters."""

import subprocess
import time

import capture.uia as uia_module
from capture.uia import resolve_at

NPP_PATH = r"C:\Program Files\Notepad++\notepad++.exe"


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


def launch_notepadpp():
    from pywinauto import Desktop

    pids_before = running_pids("notepad++.exe")
    before = {w.element_info.handle for w in Desktop(backend="uia").windows()}
    proc = subprocess.Popen([NPP_PATH, "-multiInst", "-nosession"])

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
                return w, pids_before
        time.sleep(0.3)
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/F", "/T"], capture_output=True)
    raise RuntimeError("Notepad++ window never appeared")


def kill_if_new(win, pids_before):
    pid = str(win.element_info.process_id)
    if pid in pids_before:
        win.close()
        return
    subprocess.run(["taskkill", "/PID", pid, "/F", "/T"], capture_output=True)


def test_resolves_live_notepadpp_control():
    win, pids_before = launch_notepadpp()
    try:
        rect = win.rectangle()
        x, y = rect.left + 250, rect.top + 200
        element, window = resolve_at(x, y)

        assert element["control_type"] or element["name"]
        assert window["process"].lower() == "notepad++.exe"
        assert window["class"] == "win32"
    finally:
        kill_if_new(win, pids_before)


def test_resolution_error_degrades_to_empty_safely(monkeypatch):
    def boom(x, y):
        raise RuntimeError("simulated UIA failure")

    monkeypatch.setattr(uia_module, "_resolve_at_uncapped", boom)
    element, window = resolve_at(0, 0)
    assert element["name"] == ""
    assert element["control_type"] == ""
    assert element["bounding_rect"] is None
    assert window["title"] == ""
    assert window["class"] == ""


def test_slow_resolution_times_out_to_empty_safely(monkeypatch):
    def slow(x, y):
        time.sleep(2.0)
        return dict(uia_module.EMPTY_ELEMENT), dict(uia_module.EMPTY_WINDOW)

    monkeypatch.setattr(uia_module, "_resolve_at_uncapped", slow)
    element, window = resolve_at(0, 0, timeout=0.2)
    assert element == uia_module.EMPTY_ELEMENT
    assert window == uia_module.EMPTY_WINDOW
