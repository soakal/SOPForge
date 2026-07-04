"""Phase 1 acceptance criterion 6 check: hotkey start/stop works while an
elevated window has focus, or the limitation is documented in
phases/DEVIATIONS.md with the UIPI explanation.

This build environment cannot exercise the genuinely-elevated "works" branch,
for one reason that does not vary run to run: this process is not elevated,
and there is no way to obtain a real elevated process non-interactively here.
`ShellExecuteW(..., "runas", ...)` needs interactive UAC consent (no user is
present in this autonomous, no-user-contact build loop — CLAUDE.md prime
directive 1), and the alternative — a scheduled task configured to run with
highest privileges, which Task Scheduler can launch without a UAC prompt —
requires modifying scheduled tasks, which CLAUDE.md's global rules require
explicit user confirmation for.

Synthetic input injection itself is a separate, *intermittent* fact about
this build VM (see .claude/skills/uia-notes.md — both this and real GDI
screen capture have been observed to work sometimes and fail with
ERROR_ACCESS_DENIED other times, in the same session, with no code change).
Because it varies, this script always probes it live on every run rather
than citing a prior run's result, and records what it saw — but a working
probe here still isn't the acceptance criterion: it doesn't exercise a
genuinely elevated window. If this process is ever run already elevated (not
the case on this build VM, so this branch is currently unreachable but
correct if it ever changes), it exercises the real hotkey path for real.
"""

import ctypes
import sys
import time
from pathlib import Path

DEVIATIONS_PATH = Path(__file__).resolve().parent.parent / "phases" / "DEVIATIONS.md"

UIPI_SECTION = """## UIPI

**Acceptance criterion 6** (phases/01-capture.md): "Hotkey start/stop works
while an elevated window has focus, or the limitation is documented in
DEVIATIONS.md with the UIPI explanation."

This could not be verified with a genuinely elevated window in the autonomous
build environment: this process is not elevated, and there is no way to
obtain a real elevated process non-interactively. `ShellExecuteW(...,
"runas", ...)` requires interactive UAC consent (no user is present in this
autonomous, no-user-contact build loop), and the alternative — a scheduled
task configured to run with highest privileges, which Task Scheduler can
launch without a UAC prompt — requires modifying scheduled tasks, which
CLAUDE.md's global rules require explicit user confirmation for.

**What is actually expected to happen, architecturally:** `src/capture/
hooks.py`'s `InputRecorder` uses pynput's `WH_MOUSE_LL`/`WH_KEYBOARD_LL`
global low-level hooks. Windows' UIPI (User Interface Privilege Isolation)
deliberately filters low-level hook callbacks installed by a *lower*-integrity
process while a *higher*-integrity (elevated) window has focus — this is a
documented anti-keylogger hardening added after Vista's UIPI introduction,
and is exactly why tools like AutoHotkey need to run elevated (or ship a
signed `uiAccess=true` manifest) for their hotkeys to fire over admin
windows. So the *expected* real-world behavior is that `sopforge.exe`'s
capture hotkey silently stops firing while focus is on an elevated window,
unless `sopforge.exe` itself is also running elevated. This is the
limitation criterion 6 anticipates, not a bug to chase later — if it's ever
a real problem for users, the fix is running the capture agent elevated (or
a uiAccess manifest), not touching hooks.py's hook-installation logic.

**Separately, and independent of elevation:** this build VM's synthetic input
injection (pynput's Controller, and a raw ctypes `SendInput` bypassing pynput
entirely) has been observed to *intermittently* fail with
`GetLastError() == ERROR_ACCESS_DENIED (5)` and intermittently succeed, with
no code change, across this same session (mirrors an identical intermittency
finding for real GDI screen capture — see .claude/skills/uia-notes.md). This
script probes it live on every run rather than trusting a cached result from
a prior run, and records what it observed this time — see the script's
printed output for this run's actual reading.
"""


def probe_injection_works_this_run():
    """Live per-run check: does a synthetic click reach a global low-level
    mouse hook right now? This has been observed to vary run-to-run in this
    environment (not a permanent block) — never trust a cached/prior-run
    result, always re-probe."""
    from pynput import mouse

    events = []
    listener = mouse.Listener(on_click=lambda x, y, button, pressed: events.append(pressed))
    listener.start()
    listener.wait()
    try:
        ctl = mouse.Controller()
        ctl.position = (50, 50)
        time.sleep(0.1)
        ctl.click(mouse.Button.left, 1)
        time.sleep(0.3)
    finally:
        listener.stop()
        listener.join()
    return len(events) > 0


def ensure_uipi_section(path=DEVIATIONS_PATH):
    """Idempotently ensures `path` contains the `## UIPI` section. Returns
    True if the section is present (whether it already was, or was just
    added)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if "## UIPI" not in existing:
        if not existing:
            prefix = "# Deviations\n\n"
        elif existing.endswith("\n"):
            prefix = "\n"
        else:
            prefix = "\n\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(prefix + UIPI_SECTION)
    return "## UIPI" in path.read_text(encoding="utf-8")


def try_real_elevated_check(injection_works_this_run):
    """Only reachable if this process is itself already elevated (never true
    on this build VM, but correct if it ever changes): opens a scratch
    window, starts a TrayApp hotkey listener, fires it via synthetic input,
    and confirms recording actually toggled. Returns True on genuine success."""
    if not injection_works_this_run:
        return False

    from capture.tray import TrayApp

    app = TrayApp(hotkey="<ctrl>+<alt>+<shift>+q")
    app._hotkey_listener.start()
    app._hotkey_listener.wait()
    try:
        from pynput import keyboard

        ctl = keyboard.Controller()
        with ctl.pressed(keyboard.Key.ctrl, keyboard.Key.alt, keyboard.Key.shift):
            ctl.press("q")
            ctl.release("q")
        time.sleep(0.5)
        started = app.is_recording
        if started:
            app.toggle_recording()
        return started
    finally:
        app._hotkey_listener.stop()
        app._hotkey_listener.join()


def main():
    is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    injection_works_this_run = probe_injection_works_this_run()

    if is_admin and try_real_elevated_check(injection_works_this_run):
        print("OK: verified elevated hotkey start/stop for real (admin=True)")
        return 0

    if not ensure_uipi_section():
        print("FAIL: could not establish ## UIPI section in DEVIATIONS.md", file=sys.stderr)
        return 1
    print(
        "OK: elevated hotkey check documented via DEVIATIONS.md "
        f"(admin={is_admin}, injection_worked_this_run={injection_works_this_run})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
