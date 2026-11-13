# Phase 7 — SOP Factory Parity 2

## Why

A two-model audit (Fable + Codex Astra, 2026-11-13, read-only, SOPForge
v1.11.1 vs `SOP Factory` as it sits) found that SOPForge has every SOP
Factory output type, but not its authoring breadth: the engine helpers
SOP Factory's `build_*.py` scripts lean on (`code_block`, `scenario_table`,
`placeholder`, `system_only`, rich bullets, multi-row revision history,
the SPL prerequisites/callout/verdict layouts) were vendored but never
reachable, the walkthrough only knew screenshot + hotspot, and a PDF/DOCX
could not be a source.

## Deliverables

- **7a — Engine sync.** Re-vendor `sop_lib.py` (title-page version stamp),
  adapted so a missing `VERSION` file can't break import; SOPForge stamps
  `SOPForge v<version>`.
- **7b — Authored content** (`pipeline/authoring.py`, `authoring.json` per
  session, edited as JSON on the review page, saved → re-export with no LLM
  call). Per step: extra bullets, copyable commands, warning, scenario
  table, `system_only` / path flag, image width, SPL callouts + verdict.
  Blocks between steps (optional new section, heading, prose, bullets,
  commands, warning, table, image placeholder). Document: prerequisites,
  revision history rows + current-row comment, second title line +
  connector, release date, walkthrough paths and `{{param}}` parameters.
  Inline markup `**bold**`, `` `code` ``.
- **7c — VRSI layout.** docx and PDF follow SOP Factory's convention:
  numbered `N.  Section` headings, lettered `A)  …` subsections (steps and
  headed blocks), as 6c already specified. One outline
  (`assembler.document_outline`) drives the docx, the PDF and both TOCs.
- **7d — All formats.** Authored content reaches docx, pdf, md, html,
  single-file html, md bundle, walkthrough and SPL deck.
- **7e — Walkthrough.** VRSI logo + `doc no · Rev · version` header,
  full-window layout, Enter advances, Copy buttons on commands, live
  parameters (persisted), alternative paths (chooser, `?path=`), mock
  terminal / note screens for authored blocks, browser frame for browser
  screenshots.
- **7f — SPL.** Prerequisites slide, numbered image callouts, correct /
  incorrect badges, warnings and commands in the notes line, headed blocks
  as extra numbered entries.
- **7g — Sources.** Photo build accepts `.pdf` / `.docx`; their embedded
  images become steps (pypdf, BSD — not PyMuPDF, which is AGPL); images
  < 5 KB skipped; every upload is EXIF-rotated.

## Out of scope (with reason)

- `[WEB]` gap-image download (`prep_active.py`): fetches from the internet
  at build time — violates prime directive 4 (local-first).
- Merging clicks / dropping mistake sequences: the manifest is ground truth
  (prime directive 2); stray steps are already removable on the
  steps-review page.
- SOP Factory's `PROMPT.txt` chat workflow and `promptbuild.py` image pack:
  helpers for a human driving an LLM by hand, replaced here by the pipeline.
- One-off widgets inside individual SOP Factory outputs (the password
  checker in `Password_Policy…_v1.0.html`): document content, not engine
  capability. Parameter substitution — the reusable part — is 7e.
- Strict SPL `TextOverflow`: deliberately reported, not raised (6f / AC6).
- SPL preview PNGs, near-white background removal: low value, off by
  default upstream.

## Acceptance criteria (record in phases/07-results.md)

1. No authoring → md, html, single html, md bundle, walkthrough step data
   and SPL deck unchanged; docx/pdf differ only by the 7c layout and the
   version stamp. All Phase 2–6 invariant tests pass.
2. With authoring, each authored element appears in every format that can
   show it; `set(doc.step_ids) == set(manifest.step_ids)`, order preserved
   (blocks are never steps).
3. Invalid authoring JSON is refused with a field-named reason and the
   typed text kept; a stale file (a step since hidden) drops only what no
   longer applies.
4. Walkthrough: no external network references; script parses; path and
   parameter state survive reload; `?shot=N&path=…` works.
5. A PDF and a DOCX with embedded images build a photo-mode document with
   one step per image ≥ 5 KB; an EXIF-rotated JPEG lands upright.
6. ruff + full pytest green; EXEs rebuild.
