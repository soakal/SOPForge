"""Tray app tests. self_check() is exercised for real (it's the same code
path `python -m capture --self-check` runs) — it briefly creates and tears
down a real Win32 tray icon and hotkey listener, but records into a temp
directory rather than the default captures_root."""

import subprocess
import sys

import pytest

from capture.tray import TrayApp


def test_self_check_initializes_and_tears_down_cleanly(tmp_path):
    app = TrayApp(captures_root=tmp_path, hotkey="<ctrl>+<alt>+<shift>+z")
    app.self_check()  # must return, not hang or raise
    assert app.captures_root == tmp_path  # restored after the check
    assert not app.is_recording


def test_self_check_leaves_no_session_under_captures_root(tmp_path):
    app = TrayApp(captures_root=tmp_path, hotkey="<ctrl>+<alt>+<shift>+z")
    app.self_check()
    assert list(tmp_path.iterdir()) == []  # recorded into a throwaway temp dir instead


def test_cli_self_check_exits_zero_with_no_stray_output():
    result = subprocess.run(
        [sys.executable, "-m", "capture", "--self-check"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert result.stderr == ""


def test_self_check_reraises_setup_failure_instead_of_hanging(tmp_path, monkeypatch):
    """A crash inside setup() must still stop the icon (pystray's setup runs
    on its own thread with no exception handling of its own) and surface to
    the caller — this test times out via pytest-timeout-free means: if the
    fix regresses, self_check() below would hang instead of raising."""
    app = TrayApp(captures_root=tmp_path, hotkey="<ctrl>+<alt>+<shift>+x")

    def boom():
        raise RuntimeError("simulated recorder start failure")

    monkeypatch.setattr(app, "_start_recording", boom)

    with pytest.raises(RuntimeError, match="simulated recorder start failure"):
        app.self_check()

    assert app.captures_root == tmp_path  # still restored despite the failure
    assert not app.is_recording


def test_start_recording_twice_is_idempotent(tmp_path):
    app = TrayApp(captures_root=tmp_path, hotkey="<ctrl>+<alt>+<shift>+y")
    with app._lock:
        app._start_recording()
        first = app._recorder
        app._start_recording()  # already recording: must not replace it
        assert app._recorder is first
        app._stop_recording()
    assert not app.is_recording


def test_stop_recording_when_idle_is_a_noop(tmp_path):
    app = TrayApp(captures_root=tmp_path, hotkey="<ctrl>+<alt>+<shift>+w")
    with app._lock:
        app._stop_recording()  # never started: must not raise
    assert not app.is_recording
