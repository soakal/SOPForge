# Deviations

## UIPI

**Acceptance criterion 6** (phases/01-capture.md): "Hotkey start/stop works
while an elevated window has focus, or the limitation is documented in
DEVIATIONS.md with the UIPI explanation."

This could not be verified with a real elevated window plus a real hotkey
keypress in the autonomous build environment, for two independent reasons:

1. **Synthetic input injection is denied outright in this build VM.**
   pynput's `mouse.Controller`/`keyboard.Controller`, and a hand-rolled
   `ctypes.windll.user32.SendInput` call bypassing pynput entirely, both
   fail — `SendInput` returns 0 with `GetLastError() == ERROR_ACCESS_DENIED
   (5)`. No automation in this session can press a real key combo,
   regardless of which window has focus or its integrity level. See
   `.claude/skills/uia-notes.md` for the full repro.
2. **This process is not elevated**, and there is no way to obtain a real
   elevated process non-interactively: `ShellExecuteW(..., "runas", ...)`
   requires interactive UAC consent (no user is present in this autonomous,
   no-user-contact build loop), and the alternative — a scheduled task
   configured to run with highest privileges, which Task Scheduler can
   launch without a UAC prompt — requires modifying scheduled tasks, which
   CLAUDE.md's global rules require explicit user confirmation for.

**What is true architecturally, independent of this environment:**
`src/capture/hooks.py`'s `InputRecorder` uses pynput's `WH_MOUSE_LL` /
`WH_KEYBOARD_LL` global low-level hooks. These intercept input at the raw
input-queue level, before window-message routing and UIPI's message
filtering apply — this is exactly why global hotkey/keylogging libraries
built on them observe input system-wide regardless of which window
currently has focus or its integrity level. No UIPI-specific handling was
added to hooks.py/tray.py because none should be needed architecturally; if
a real target machine ever shows the hotkey failing to fire while an
elevated window has focus, that is a genuine bug to investigate there, not
an expected limitation baked in here.
