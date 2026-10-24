# Phase 6 — Results

## AC1 (6a, engine sync) — PASS

- `vendor/sop_factory_2/sop_lib.py` re-vendored from `soakal/sop-factory-local`
  `template/sop_lib.py` at `e4a00e5`. Template docx content-identical (only zip
  metadata differs), not re-copied.
- `tests/pipeline/test_docx_keep_with_next.py`: the paragraph before every step
  image, `Procedure` and `Revision History` carry `keep_with_next`;
  `SOPBuilder.code_block` exists. All 3 fail against the previous vendored copy
  (run with `SOPFORGE_SOP_FACTORY_2_DIR` pointed at it) and pass against the new one.
- `fixtures/golden-document.xml` regenerated. Verified before writing: with
  `<w:keepNext/>` stripped, old and new normalized XML are byte-identical (0 → 9
  `keepNext` elements, no other change).
- Verification on the Linux CI container: `ruff check` / `ruff format --check`
  (pinned 0.15.20) clean; pytest 655 passed. 7 failures/errors are Windows-only
  (`winsdk` OCR in `test_redaction.py`, live UIA, elevated-hotkey probe) and fail
  identically on the pre-change baseline. PyInstaller build not applicable
  (phases 1 & 3 only).

## AC2 (6b, inline review markers) — PASS

- `src/pipeline/review_notes.py`: `step_review_notes(step)` returns the
  markers from the manifest alone; called by docx, pdf, html, single-file html
  and md renderers after polish. Photo-mode steps detected by
  `photo_build.is_synthetic_step` (pinned against `synthetic_manifest_dict` and
  both real fixtures) and skipped.
- `tests/pipeline/test_review_notes.py` (8 tests): per-step markers, edited
  summary clears the marker, synthetic steps unmarked, all five formats,
  sidecar `uncaptured_value_steps`.
- Found and fixed while verifying: the engine keeps only the paragraph directly
  before an image with it, so a step bullet followed by a marker or narration
  could split from its screenshot (the narration case predates 6b).
  `test_whole_step_block_keeps_with_its_image` fails without the fix and
  passes with it.
- Golden XML regenerated; diff by paragraph is exactly the 2 added marker
  paragraphs, nothing removed.
- Verification: pinned ruff clean; pytest 664 passed, same 7 Windows-only
  failures/errors as the baseline.
