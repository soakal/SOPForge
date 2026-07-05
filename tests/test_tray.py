"""Tray app tests. self_check() is exercised for real (it's the same code
path `python -m capture --self-check` runs) — it briefly creates and tears
down a real Win32 tray icon and hotkey listener, but records into a temp
directory rather than the default captures_root.

Every test that stops a recording injects a no-op (or recording) upload_fn
-- without it, _stop_recording's background auto-upload thread would
attempt a real network call to http://127.0.0.1:8420, which is slow,
flaky in CI, and — if a real sopforge-server happens to be running on this
machine — could hand it a throwaway test session for real."""

import subprocess
import sys
import threading

import pytest

from capture.tray import TrayApp


def _noop_upload(*args, **kwargs):
    return None


def test_self_check_initializes_and_tears_down_cleanly(tmp_path):
    app = TrayApp(captures_root=tmp_path, hotkey="<ctrl>+<alt>+<shift>+z")
    app.self_check()  # must return, not hang or raise
    assert app.captures_root == tmp_path  # restored after the check
    assert not app.is_recording


def test_self_check_leaves_no_session_under_captures_root(tmp_path):
    app = TrayApp(captures_root=tmp_path, hotkey="<ctrl>+<alt>+<shift>+z")
    app.self_check()
    assert list(tmp_path.iterdir()) == []  # recorded into a throwaway temp dir instead


def test_self_check_never_touches_the_network(tmp_path):
    """self_check's upload_fn override must actually take effect -- a
    self-check is a headless diagnostic, not a real recording, and must
    never attempt to upload anywhere. The real (injected) upload_fn must
    also be restored afterward, not left swapped for the no-op."""
    calls = []

    def real_upload_fn(*a, **k):
        calls.append(a)
        return "should-never-be-used"

    app = TrayApp(
        captures_root=tmp_path,
        hotkey="<ctrl>+<alt>+<shift>+v",
        upload_fn=real_upload_fn,
    )
    app.self_check()
    assert calls == []
    assert app._upload_fn is real_upload_fn


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
    app = TrayApp(captures_root=tmp_path, hotkey="<ctrl>+<alt>+<shift>+y", upload_fn=_noop_upload)
    with app._lock:
        app._start_recording()
        first = app._recorder
        app._start_recording()  # already recording: must not replace it
        assert app._recorder is first
        app._stop_recording()
    assert not app.is_recording


def test_stop_recording_when_idle_is_a_noop(tmp_path):
    app = TrayApp(captures_root=tmp_path, hotkey="<ctrl>+<alt>+<shift>+w", upload_fn=_noop_upload)
    with app._lock:
        app._stop_recording()  # never started: must not raise
    assert not app.is_recording


def test_stop_recording_triggers_auto_upload_with_the_session_output_dir(tmp_path):
    done = threading.Event()
    calls = []

    def fake_upload(output_dir, server_url=None):
        calls.append((output_dir, server_url))
        done.set()
        return None

    app = TrayApp(
        captures_root=tmp_path,
        hotkey="<ctrl>+<alt>+<shift>+u",
        server_url="http://example-server",
        upload_fn=fake_upload,
    )
    with app._lock:
        app._start_recording()
        recorder = app._recorder
        app._stop_recording()

    assert done.wait(timeout=5), "auto-upload was never attempted"
    assert calls == [(recorder.output_dir, "http://example-server")]


def test_stop_recording_opens_browser_on_successful_upload(tmp_path):
    done = threading.Event()
    opened = []

    def fake_upload(output_dir, server_url=None):
        return "new-session-id"

    def fake_open_browser(url):
        opened.append(url)
        done.set()

    app = TrayApp(
        captures_root=tmp_path,
        hotkey="<ctrl>+<alt>+<shift>+t",
        server_url="http://example-server",
        upload_fn=fake_upload,
        open_browser_fn=fake_open_browser,
    )
    with app._lock:
        app._start_recording()
        app._stop_recording()

    assert done.wait(timeout=5), "browser was never opened"
    assert opened == ["http://example-server/ui/sessions/new-session-id"]


def test_stop_recording_does_not_open_browser_when_upload_fails(tmp_path):
    done = threading.Event()
    opened = []

    def fake_upload(output_dir, server_url=None):
        done.set()
        return None  # upload failed / server unreachable

    def fake_open_browser(url):
        opened.append(url)

    app = TrayApp(
        captures_root=tmp_path,
        hotkey="<ctrl>+<alt>+<shift>+s",
        upload_fn=fake_upload,
        open_browser_fn=fake_open_browser,
    )
    with app._lock:
        app._start_recording()
        app._stop_recording()

    assert done.wait(timeout=5), "upload was never attempted"
    assert opened == []


def test_default_server_url_comes_from_env(monkeypatch):
    monkeypatch.setenv("SOPFORGE_SERVER_URL", "http://from-env:1234")
    app = TrayApp(hotkey="<ctrl>+<alt>+<shift>+r")
    assert app.server_url == "http://from-env:1234"


class _FakeIcon:
    """Stand-in for the pystray icon so exit() can be exercised without a real
    system-tray icon (pystray's own .stop() on a never-run icon is unreliable)."""

    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


def test_open_library_opens_the_server_url(tmp_path):
    opened = []
    app = TrayApp(
        captures_root=tmp_path,
        hotkey="<ctrl>+<alt>+<shift>+l",
        server_url="http://example-server",
        upload_fn=_noop_upload,
        open_browser_fn=lambda url: opened.append(url),
    )
    app.open_library()
    assert opened == ["http://example-server"]


def test_open_library_swallows_browser_errors(tmp_path):
    def boom(url):
        raise RuntimeError("no browser available")

    app = TrayApp(
        captures_root=tmp_path,
        hotkey="<ctrl>+<alt>+<shift>+o",
        upload_fn=_noop_upload,
        open_browser_fn=boom,
    )
    app.open_library()  # must not raise into pystray's menu thread


def test_exit_stops_server_then_icon(tmp_path):
    calls = []
    app = TrayApp(
        captures_root=tmp_path,
        hotkey="<ctrl>+<alt>+<shift>+e",
        server_url="http://example-server",
        upload_fn=_noop_upload,
        shutdown_fn=lambda url: calls.append(("shutdown", url)),
    )
    app._icon = _FakeIcon()
    app.exit()
    assert calls == [("shutdown", "http://example-server")]
    assert app._icon.stopped
    assert not app.is_recording


def test_exit_stops_icon_even_if_recording_was_active(tmp_path):
    app = TrayApp(
        captures_root=tmp_path,
        hotkey="<ctrl>+<alt>+<shift>+q",
        upload_fn=_noop_upload,
        shutdown_fn=lambda url: None,
    )
    app._icon = _FakeIcon()
    with app._lock:
        app._start_recording()
    app.exit()
    assert app._icon.stopped
    assert not app.is_recording  # exit stopped the active recording


def test_request_server_shutdown_is_best_effort_when_server_unreachable():
    from capture.tray import _request_server_shutdown

    # Nothing is listening on this port -> connection refused, must be swallowed.
    _request_server_shutdown("http://127.0.0.1:59999", timeout=0.5)  # must not raise
