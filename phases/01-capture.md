# Phase 1 — Capture Agent

Windows tray EXE that records a workflow: screenshot on every click, UIA element
metadata per click, optional mic narration track, redaction pass, manifest output
shaped for the Phase 2 pipeline.

## Deliverables

- `src/capture/` package: tray app (start/stop hotkey, recording indicator),
  click hook (pynput), screenshot on click (mss), UIA element resolution
  (pywin32/comtypes UIAutomation), optional WAV narration recording.
- Redaction: post-capture pass over PNGs, blur regions whose OCR/UIA text matches
  configurable regexes (email, IPv4, password-field heuristic). Config in
  `config/redaction.toml`.
- Output: `captures/<session-id>/NNN.png` + `manifest.json` conforming to
  `fixtures/sample-manifest.json` schema (validate with jsonschema in tests).
- Self-test harness: pywinauto script that drives Notepad, Chrome, and VS Code
  (Electron) through a fixed click sequence and asserts on the resulting manifest.
- PyInstaller spec producing `sopforge.exe`.

## Acceptance criteria (record results in phases/01-results.md)

1. Self-test harness: ≥90% of scripted clicks yield non-empty element metadata
   across all three target apps (Notepad/Win32, Chrome, VS Code/Electron).
   Per-app breakdown recorded.
2. Manifest from a self-test run validates against the schema; step ordering
   matches the scripted click order exactly.
3. Redaction test suite green: seeded test images with known email/IP text come
   out with those regions blurred (pixel-diff assertion on the region).
4. `sopforge.exe`: builds clean, <40MB, cold start to tray icon <2s
   (measured, number recorded), no console window, exits cleanly from tray menu.
5. A capture session with zero UIA metadata (worst case) still produces a valid
   manifest — elements empty, screenshots present. Pipeline must be able to run
   template mode from it.
6. Hotkey start/stop works while an elevated window has focus, or the limitation
   is documented in DEVIATIONS.md with the UIPI explanation.

## Known risk (attack first)

UIA element resolution differs per app class. Spike this before any tray/packaging
work: a throwaway script that resolves the element under the cursor in all three
apps. Record findings in `.claude/skills/uia-notes.md`.
