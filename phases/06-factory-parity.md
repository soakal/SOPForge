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
- **6b — Inline review markers.** Steps with an uncaptured typed value, and
  real (non-photo) steps with empty UIA metadata, render an inline red
  `[REVIEW NEEDED — …]` marker under the step in all five formats (docx, pdf,
  html, single-file html, md), derived from the manifest after polish so no LLM
  pass can drop it. The sidecar gains `uncaptured_value_steps`. Scoped down from
  the first draft: bold UI names already render deterministically from
  `element.name` (generation.py strips LLM `**bold**` on purpose), and capture
  never records typed content, so there is no typed text to put in a
  `code_block`.
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
- **6e — Labelled redaction.** The existing OCR/password redaction hits are
  covered with a box labelled by detected type (`email`, `IP address`,
  `password`) instead of only blurred, applied before crop/annotate so every
  export gets identical pixels.
- **6f — SPL deck.** `.pptx` export via a ported `spl_lib`. Text fit is measured
  in PowerPoint when it is installed; otherwise estimated with Pillow font
  metrics plus a safety margin. The deck always builds.
- **6g — Packaging.** Per-doc metadata inferred, never required: title from the
  capture, author from the Windows username, doc number auto-generated; all
  editable in the review UI. Exports download as `<Name>_v<rev>.<ext>`.
- **6h — UI redesign ("Forge").** Rebuild `src/pipeline/webui/` pages: graphite
  sidebar (Library, New SOP, Review queue, Configuration; Ollama status +
  version in the footer), warm off-white surface, ember-orange accent, IBM Plex
  Sans + Mono bundled under `assets/fonts/` (OFL, offline, in the PyInstaller
  spec). Library as a table (thumbnail, doc no., steps, status pill, available
  exports, updated) with four stat tiles (procedures, need review, steps
  captured, steps AI-written = steps not on template fallback, from the sidecar
  reports). One drop zone replaces both upload forms and auto-detects capture
  (`manifest.json` + PNGs) vs. screenshots + transcript. Light and dark themes.
  Plain HTML/CSS/JS, no build step, no CDN.

## Rule: zero new configuration

Phase 6 adds no new config keys, settings, or toggles. Every feature is on by
default, picks its behaviour from what is available (Ollama reachable,
PowerPoint installed), and degrades quietly instead of asking.

## Acceptance criteria (record in phases/06-results.md)

1. 6a: in the golden-fixture docx, every heading paragraph and every bullet
   immediately followed by an image carries `keep_with_next`; `code_block`
   exists on `SOPBuilder`. All pre-existing pipeline tests pass.
2. 6b: in the golden fixture, the uncaptured `type` step and the empty-metadata
   step each show their `[REVIEW NEEDED` marker in all five formats; photo-mode
   synthetic steps show none; an edited `text_summary` clears the marker; the
   sidecar lists `uncaptured_value_steps`; the step bullet and markers keep
   with the step's image.
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
6. 6f: with PowerPoint absent, the golden fixture still produces a pptx whose
   estimated text fit passes; with PowerPoint present, `save()` measures in
   PowerPoint and raises `TextOverflow` rather than shipping cut-off text.
7. 6g: download filenames derive from the title, with filesystem-unsafe
   characters removed; a doc builds with no metadata entered.
8. 6h: every existing UI route still renders and every existing UI test
   passes; served pages make no external network requests; Plex fonts load
   from the frozen bundle; both drop-zone paths reach a built document in the
   playwright smoke test; `design:accessibility-review` finds no WCAG AA
   contrast failures.
9. No new keys in `config/models.toml` or any settings page (diff check).

## Invariant notes

6c changes document *structure*, so it is recorded in `phases/DEVIATIONS.md`
before implementation. It is not a weakening: the LLM still never decides what
the steps are. It may only propose section boundaries, and code validates full
coverage deterministically.
