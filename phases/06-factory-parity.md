# Phase 6 — SOP Factory Parity

## Why

SOPForge already uses the SOP Factory 2 engine (`vendor/sop_factory_2/sop_lib.py`),
but only as a transcriber: every captured step becomes its own `heading2` +
bullet + screenshot (`docx_assembler.py`). The hand-driven SOP Factory workflow
(`soakal/sop-factory-local`: `sop-builder`, `sop-walkthrough`, `spl-deck` skills
and `build_*_sop.py` scripts) produces better documents because of the layer
above the engine: steps grouped into workflow phases, one screenshot per UI
state, bold UI names, warnings, `[REVIEW NEEDED]` markers, a click-through
walkthrough generated from the same step list, and redacted crops.

This phase ports that layer into SOPForge without weakening any Phase 2
invariant. SOP Factory local is a **read-only reference**: nothing in this phase
writes to it. Code is copied into this repo, never imported from that folder.

## Deliverables

- **6a — Engine sync.** Re-vendor `sop_lib.py` from SOP Factory local
  (`template/sop_lib.py`). Adds `keep_with_next` on headings, bullets before an
  image, and image paragraphs, plus `code_block()`. The template docx is
  content-identical (only zip metadata differs) and is not re-copied.
- **6b — Richer step rendering.** `**bold**` UI names in step text render via
  `bullet_rich`; text typed in `type` steps renders via `code_block`; steps with
  uncaptured values render an inline `[REVIEW NEEDED — value not captured]`
  marker in the document (not only in the sidecar report).
- **6c — Phase grouping.** Steps are grouped into lettered phases
  (`heading2("A)  Sign in")`, steps as bullets under them). The LLM may
  *propose* groups; code accepts a proposal only if it is a contiguous,
  order-preserving partition covering every step ID exactly once, otherwise falls
  back to deterministic grouping by window title. One screenshot per distinct
  screen within a phase; omitted screenshots are listed in the sidecar report.
  Phase names are editable in the review UI.
- **6d — Walkthrough export.** A seventh export, `walkthrough.html`: single
  self-contained file, engine ported from SOP Factory's
  `walkthrough_template.html` (step navigation, keyboard, clickable phase chips,
  `?shot=N`). Each step shows its real screenshot with a clickable hotspot over
  `element.bounding_rect`. Built from the same `step_results` as the docx.
- **6e — Labelled redaction.** Cover-box redactions with a generic label
  ("Your Company") alongside the existing blur, applied before crop/annotate so
  every export gets identical pixels.
- **6f — SPL deck (optional).** `.pptx` export via a ported `spl_lib`, only when
  PowerPoint is detected; otherwise the export is absent, never broken.
- **6g — Packaging.** Per-doc metadata (title, subtitle, doc number, revision,
  author) set in the review UI; exports download as `<Name>_v<rev>.<ext>` named
  after the procedure.

## Acceptance criteria (record in phases/06-results.md)

1. 6a: in the golden-fixture docx, every heading paragraph and every bullet
   immediately followed by an image carries `keep_with_next`; `code_block`
   exists on `SOPBuilder`. All pre-existing pipeline tests pass.
2. 6b: a fixture step with `**Save**` renders a bold run "Save"; a `type` step
   renders a Consolas shaded paragraph; a step with empty captured text renders
   the `[REVIEW NEEDED` marker in the docx body and is listed in the sidecar.
3. 6c: `set(doc.step_ids) == set(manifest.step_ids)` and order is preserved for
   every grouping path (LLM-accepted, LLM-rejected → fallback, fallback only).
   Property test (hypothesis): any proposed grouping that is non-contiguous,
   reorders, drops or duplicates an ID is rejected. Omitted screenshots appear
   in the sidecar report.
4. 6d: `walkthrough.html` from the golden fixture has one step per manifest step
   in order, no external network references (same assertion as Phase 3 #1), and
   its `<script>` parses (`node --check` when node is available, otherwise
   skipped with reason). Hotspot rects equal the manifest bounding rects scaled
   to the displayed crop.
5. 6e: a labelled redaction region in the fixture is fully covered in every
   exported image (pixel check), and the label text is not OCR'd from the source.
6. 6f: with PowerPoint absent the server starts, the pptx export is not offered,
   and nothing errors.
7. 6g: download filenames derive from the title, with filesystem-unsafe
   characters removed.

## Invariant notes

6c changes document *structure*, so it is recorded in `phases/DEVIATIONS.md`
before implementation. It is not a weakening: the LLM still never decides what
the steps are. It may only propose section boundaries, and code validates full
coverage deterministically.
