"""Interactive test: requires a real desktop session (UIA needs one — see
CLAUDE.md environment facts). Uses the shared `scratch_window` fixture
(tests/conftest.py) for a safe, isolated Notepad++ target."""

import time

import capture.uia as uia_module
from capture.uia import resolve_at


def test_resolves_live_notepadpp_control(scratch_window):
    rect = scratch_window.rectangle()
    x, y = rect.left + 250, rect.top + 200
    element, window = resolve_at(x, y)

    assert element["control_type"] or element["name"]
    assert window["process"].lower() == "notepad++.exe"
    assert window["class"] == "win32"


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
