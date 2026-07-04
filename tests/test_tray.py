"""Tray app tests. self_check() is exercised for real (it's the same code
path `python -m capture --self-check` runs) — it briefly creates and tears
down a real Win32 tray icon and hotkey listener, but records into a temp
directory rather than the default captures_root."""

import subprocess
import sys

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
