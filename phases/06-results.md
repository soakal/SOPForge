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
