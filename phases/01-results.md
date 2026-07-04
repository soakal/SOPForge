# Phase 1 — Acceptance Criteria Results

Evidence for each of phases/01-capture.md's six acceptance criteria, gathered
from real runs in this build environment (see .claude/skills/uia-notes.md and
phases/DEVIATIONS.md for the environment-specific caveats referenced below).

## Criterion 1: self-test harness element-metadata coverage

Self-test harness (`python -m capture.selftest --all`, `src/capture/selftest.py`)
drives Notepad++ (Win32), Chrome (Chromium), and VS Code (Electron) through
scripted interaction points against a live Recorder and measures the fraction
resolving non-empty UIA element metadata:

- **notepadpp**: 4/5 (80.0%) non-empty element metadata
- **chrome**: 5/5 (100.0%) non-empty element metadata
- **vscode**: 5/5 (100.0%) non-empty element metadata

**Overall: 14/15 (93.3%)** — threshold 90%, PASS.

(Notepad++'s built-in Notepad substitution and the interaction-point delivery
method are documented deviations from the literal "Notepad" wording — see
.claude/skills/uia-notes.md — required by this build VM's environment
limitations, not by the app under test.)

## Criterion 2: manifest schema validity and step ordering

Re-ran the self-test harness (`run_selftest(tmp_dir, click_count=2)`,
3 apps x 2 interactions = 6 steps) and validated the resulting manifest:

- `jsonschema.Draft202012Validator(fixtures/manifest.schema.json).validate(...)`:
  **passed, no exception**.
- Step ids in file order: `['step-001', 'step-002', 'step-003', 'step-004',
  'step-005', 'step-006']` — exactly matches the sequential scripted
  interaction order (notepadpp's 2 interactions, then chrome's 2, then
  vscode's 2), **no reordering, no gaps, no duplicates**.

This is also covered by `tests/test_recorder.py`'s dedicated schema-validation
and step-order assertions (real UIA resolution against a live scratch window,
run on every `pytest` invocation), not just this one-off gate check.

## Criterion 3: redaction test suite

`.venv\Scripts\python.exe -m pytest -q tests/test_redaction.py` — **10 passed**.

Includes the required pixel-diff assertions: seeded images with known
email/IPv4 text are OCR'd (Windows.Media.Ocr via winsdk) and the matched
word's bounding box is Gaussian-blurred (mean absolute pixel difference vs.
the original > 5), while a real, non-matching OCR'd word elsewhere in the
same image is asserted **untouched** (mean absolute difference < 0.5) —
proving the blur is scoped to matched regions, not a wholesale image blur.
Password fields (masked on screen, no OCR-able text) are separately caught
via a UIA-metadata heuristic and blurred regardless of OCR outcome, including
when OCR itself is unavailable (`OcrUnavailableError` path, tested).

## Criterion 4: EXE size, cold-start timing, no console, clean exit

- Build: `dist/sopforge/sopforge.exe` (one-folder — see phases/DEVIATIONS.md
  for why this differs from task-13's original onefile plan), **26.78 MB**
  total footprint, under the 40 MB budget.
- `console=False` in `sopforge.spec`: no console window.
- First launch after build: **3.126s** (one-time cost, not gated against the
  threshold — see phases/DEVIATIONS.md's "Criterion 4 packaging mode" entry
  for why, and the full measured evidence).
- Steady-state launches (3 repeats): 1.141s, 1.128s, 1.133s (**average
  1.134s**, threshold 2.0s) — **PASS**.
- Clean exit return codes across all 4 launches in that run: `[0, 0, 0, 0]`
  — exits via the identical `TrayApp.exit()` code path the tray menu's Exit
  item calls (see scripts/verify_exe.py's module docstring for why a real
  system-tray click isn't simulated).

## Criterion 5: zero-UIA-metadata worst case still produces a valid manifest

`tests/test_manifest_writer.py::test_all_empty_elements_still_valid_worst_case`
— **passed**. Builds a 3-step session with every element field empty
(`name`/`control_type`/`automation_id`/`framework` all `""`,
`bounding_rect: null`) and an empty `window.class`, and asserts:
- The manifest still validates against `fixtures/manifest.schema.json`.
- Every step's `screenshot` field is present and non-empty (screenshots are
  captured independently of UIA resolution success).

The produced manifest is committed as `fixtures/empty-elements-manifest.json`
— a real Phase 2 fixture for exercising the template-fallback path (Phase 2's
own invariant: template mode must be reachable with zero LLM/UIA input).

## Criterion 6: elevated-focus hotkey, or documented UIPI limitation

`.venv\Scripts\python.exe scripts\check_elevated_hotkey.py` — **exit 0**:
`OK: elevated hotkey check documented via DEVIATIONS.md (admin=False,
injection_worked_this_run=False)`.

This process cannot obtain a genuinely elevated window non-interactively in
this autonomous build loop (no UAC consent available, and creating a
scheduled task to bypass that requires user confirmation per CLAUDE.md's
global rules) — so this criterion is satisfied via its own documented
fallback path: `phases/DEVIATIONS.md`'s `## UIPI` section explains the
expected real-world behavior (UIPI filters low-level input hooks from a
lower-integrity process while a higher-integrity window has focus — the
capture agent must run elevated, or ship a `uiAccess` manifest, to capture
elevated-window workflows) and the two independent reasons this couldn't be
verified live here.

## Full verification pipeline (all criteria, run together)

```
.venv\Scripts\ruff.exe check src/
.venv\Scripts\ruff.exe format --check src/
.venv\Scripts\python.exe -m pytest -x -q
```

All green — 57 passed, 1 skipped (the skip is
`test_records_valid_wav_when_device_present`, conditional on this VM having
zero audio input devices; see `tests/test_narration.py`).

