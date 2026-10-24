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

## AC3 (6c, phase grouping) — PASS

- `src/pipeline/grouping.py`: the LLM (the `[steps]` model; no new config)
  proposes only phase names + start step numbers, so the partition is
  contiguous and in order by construction; `validate_phases` re-checks exact
  coverage. Bad JSON, a bad partition, or an LLM error fall back to
  `group_by_window` (one-step detours folded back in), with no retry. Captures
  under 4 steps stay ungrouped with no LLM call.
- Phases saved to `phases.json` at generation; every export (including the
  no-LLM step-edit re-export) loads them and re-validates against the current
  manifest, falling back to window grouping if a step was hidden since.
- All five formats + TOC render one section per phase; with no phases the
  output is byte-identical to before (golden docx unchanged, and an explicit
  test). Step anchors (`id=step-…`) survive for the review page.
- Duplicate screenshots: omitted only on an exact repeat (same window, same
  control, <0.2% of pixels changed); listed as `omitted_screenshot_steps`.
  Narrowed from the first draft because annotated images are already cropped
  per control (see DEVIATIONS.md).
- `tests/pipeline/test_grouping.py` (16 tests, one hypothesis property):
  validation rejects drop/duplicate/reorder/non-contiguous/blank/empty/long;
  proposal parsing; LLM accepted, rejected, raising, absent; window fallback;
  stale `phases.json`; duplicate detection incl. moved marker kept; all
  formats; end to end through the server incl. phases surviving a step edit.
- Found by the tests and fixed before commit: the first detour rule folded
  the first step of a new window into the previous phase, and a mean-pixel
  duplicate check treated a moved click marker as a repeat.
- Not yet done: editing phase names and restoring an omitted screenshot from
  the review page; both belong with the 6h UI work.

## AC4 (6d, walkthrough export) — PASS

- `src/pipeline/export_walkthrough.py`: `walkthrough.html` written with every
  export and served at `GET /sessions/{id}/walkthrough.html`. One
  self-contained page (screenshots inlined), engine ported from SOP Factory's
  `walkthrough_template.html`: Back/Next, arrow keys, Enter/Space on the
  hotspot, clickable phase chips, progress bar, resume position, `?shot=N`.
- Hotspots come from the manifest: the element's `bounding_rect` mapped into
  the annotated crop via the same `compute_crop_box` call that made the crop;
  a square around the click when there is no usable rect; none for photo-mode
  steps. An omitted duplicate screenshot reuses the previous step's picture.
- Review markers and narration show on each step, same as the document.
- `tests/pipeline/test_export_walkthrough.py` (8): hotspot math vs.
  `compute_crop_box`, click-centred fallback, photo/unknown frame, one entry per
  step in order, omitted-screenshot reuse, no external references, `node
  --check`, and a `ui`-marked playwright test that clicks Start, the hotspot,
  a phase chip and ArrowRight and loads `?shot=3` (passes here with
  `SOPFORGE_TEST_CHROMIUM` pointing at the container's chromium).
  `test_server_exports.py::test_walkthrough_endpoint` covers the route.
- Found by the tests and fixed: step text embedded as JSON escaped only `</`,
  so a title containing `<script` reached the HTML parser; all `<`, `>`, `&`
  are now `\u`-escaped.
