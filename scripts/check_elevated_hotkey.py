"""Phase 1 acceptance criterion 6 check: hotkey start/stop works while an
elevated window has focus, or the limitation is documented in
phases/DEVIATIONS.md with the UIPI explanation.

This build environment cannot exercise the "works" branch, for two
independent reasons already established in earlier tasks:

1. Synthetic OS-level input injection is denied outright here — pynput's
   Controller AND a hand-rolled ctypes SendInput (bypassing pynput entirely)
   both fail with GetLastError()==ERROR_ACCESS_DENIED (see
   .claude/skills/uia-notes.md) — so no automation in this session can press
   a real key combo at all, elevated focus or not.
2. This process is not elevated, and there is no way to obtain a real
   elevated process non-interactively here: ShellExecuteW(..., "runas", ...)
   needs interactive UAC consent (no user present in this autonomous,
   no-user-contact build loop — CLAUDE.md prime directive 1), and the
   alternative (a scheduled task configured to run with highest privileges,
   which Task Scheduler can launch without a UAC prompt) requires modifying
   scheduled tasks, which CLAUDE.md's global rules require explicit user
   confirmation for.

So this script documents the limitation in phases/DEVIATIONS.md under a
`## UIPI` section (writing it once, idempotently) and exits 0, per this
task's own acceptance path.
"""

import ctypes
import sys
from pathlib import Path

DEVIATIONS_PATH = Path(__file__).resolve().parent.parent / "phases" / "DEVIATIONS.md"

UIPI_SECTION = """## UIPI

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
"""


def ensure_uipi_section(path=DEVIATIONS_PATH):
    """Idempotently ensures `path` contains the `## UIPI` section. Returns
    True if the section is present (whether it already was, or was just
    added)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if "## UIPI" not in existing:
        header = "" if existing else "# Deviations\n\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(header + UIPI_SECTION)
    return "## UIPI" in path.read_text(encoding="utf-8")


def main():
    is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    if not ensure_uipi_section():
        print("FAIL: could not establish ## UIPI section in DEVIATIONS.md", file=sys.stderr)
        return 1
    print(f"OK: elevated hotkey check documented via DEVIATIONS.md (admin={is_admin})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
