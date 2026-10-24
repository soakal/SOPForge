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

## AC5 (6e, labelled redaction) — PASS (Linux, OCR stubbed); real OCR path pending Windows run

- `src/capture/redact.py`: OCR pattern hits and password fields are now covered
  with a solid graphite box labelled by type (`email`, `IP address`,
  `password`; other pattern names shown as-is) instead of a Gaussian blur. A
  solid cover leaves nothing of the text to reconstruct, which a blur of short
  text can. OCR word boxes are padded 2 px for anti-aliased edges; UIA control
  rects are not. Labels are dropped (box stays) when they don't fit.
- Still applied once, in place, on the raw screenshot at capture time, so every
  export (and the walkthrough) gets the same pixels. Manifest `reason` values
  are unchanged (`pattern`, `password_heuristic`): no schema change.
- `tests/test_redaction_cover.py` (5, OCR stubbed so they run off Windows): no
  pixel of the seeded secret colour survives, label drawn, neighbouring text
  untouched, exact password rect, tiny box unlabelled, clamping and degenerate
  boxes. `tests/test_redaction.py` (real Windows OCR) renamed blur → cover; its
  assertions still hold by construction but have not been run on Windows yet.

## AC6 (6f, SPL deck) — PASS (estimate path); PowerPoint path pending Windows run

- `vendor/sop_factory_2/spl_lib.py` + `Single Point Lesson Template V4.potm`
  vendored unmodified from sop-factory-local `e4a00e5`; bundled in
  `sopforge-server.spec` (datas + `pptx`/`win32com.client` hidden imports);
  `python-pptx==1.0.2` pinned.
- `src/pipeline/export_spl.py`: `spl.pptx` written with every export, served at
  `GET /sessions/{id}/spl.pptx`. Title slide + phase-labelled step slides
  ("(cont.)" on later ones), up to 2 screenshots each, SOP step numbers
  continued via `startAt`, review markers on the red notes line.
- Fit: PowerPoint (`spl_lib.fit_text`, COM initialised per worker thread) when
  `PowerPoint.Application` is registered; otherwise, or if COM fails, a
  font-metric estimate (Calibri when installed, else the wider DejaVu Sans)
  shrinks 1pt at a time to the 12pt floor. Slides are planned with the same
  estimate so each fits at ≥12pt. Step text is never truncated; anything that
  still overflows goes to the report as `spl_text_overflow`, and a deck
  failure as `spl_error`, without blocking the other exports.
- `tests/pipeline/test_export_spl.py` (8) + `test_server_exports.py::test_spl_endpoint`.
  Rendered a 9-step sample through LibreOffice and checked the slides by eye.
- Not verified here: the PowerPoint COM path, and how long PowerPoint takes
  on each step-edit re-export (SOP Factory runs it once per build).

## AC7 (6g, metadata and packaging) — PASS

- `src/pipeline/doc_meta.py`: title (capture/auto title), author (configured,
  else the Windows user, else "SOPForge"), doc number (always present,
  `SOP-XXXX` when no prefix is configured), revision 1.0, optional subtitle.
  Overrides in the session's `metadata.json`; the title goes to the manifest
  so the library agrees. `POST /ui/sessions/{id}/metadata` validates (revision
  like 1.0, ≤120 chars) and re-exports with no LLM call. Resolved values are
  in `report["document"]` for the review page.
- Docx title page and PDF cover use the subtitle; author, doc number and
  revision reach docx, PDF and the SPL deck.
- Downloads: `<Title_Words>_v<rev>` + `.docx` / `.pdf` / `.html` /
  `_Walkthrough.html` / `_SPL.pptx` / `_Markdown.zip`.
- `tests/pipeline/test_doc_meta.py` (7, incl. an end-to-end edit through the
  server); download-name assertions in `test_server_exports.py` updated.

## Model review (2026-10-24)

- Fixed: the Anthropic provider read `content[0].text`, but current Claude
  models return a thinking block first, so every Anthropic call fell back to
  the template; `max_tokens` 1024 → 16000; refusals and empty replies raise.
  No code sends temperature/top_p/top_k (rejected by the API since
  2026-10-21).
- Claude IDs moved to the current generation (Sonnet/Opus/Haiku 5.5, Fable
  5.1); planner/reviewer → `claude-fable-5-1`; `run-loop.ps1 -Model` pins the
  executor (`claude-opus-5-5`).
- Local: `qwen3.6:27b` confirmed as an official Ollama tag. Candidates
  `qwen3.6:27b` (steps) and `qwen3-vl:8b` (vision) added to suggestions;
  shipped defaults unchanged until measured on the Ollama host.

## 6h — Forge UI redesign

Rebuilt every page in `src/pipeline/webui/pages.py` (library, review queue,
steps review, processing, session/review, configuration) on the Forge chrome:
graphite sidebar (Library, New SOP, Review queue + count, Configuration; LLM
status + version in the footer), warm off-white surface, ember accent, IBM
Plex Sans + Mono, light + dark via `prefers-color-scheme`. Screenshots of each
page in both themes (full-page captures, refreshed at the PR's final head): `phases/06-screenshots/6h/`.

- **Fonts**: `assets/fonts/ibm-plex/` (5 Sans + 3 Mono WOFF2 from npm
  `@ibm/plex-sans@1.1.0` / `@ibm/plex-mono@2.5.0`, OFL `LICENSE`, README with
  provenance). Served by `GET /ui/assets/fonts/{file}` (allow-list =
  `pages.FONT_FILES`, read through `resource_path()`), bundled by
  `sopforge-server.spec` datas.
- **Library**: table (thumbnail, doc no., steps, status pill, export links,
  updated) + four tiles. Rows/tiles come from `webui/library_view.py`, which
  only reads files a session already has (report.json, steps.json, the
  exports) plus the job status the server tracks. In-flight sessions
  (staged/queued/processing/failed) are listed too, first.
  - *Steps AI-written* = steps not on fallback. Source: each session's
    `steps.json` `used_fallback` flag; sessions without steps.json fall back
    to `report.json`'s `template_fallback_steps` + `caption_fallback_steps`.
    steps.json is preferred because report.json can't tell a photo-mode step
    whose text came verbatim from the transcript (vision off) from an
    AI-captioned one — counting from report.json alone would call those
    AI-written. Ratio is over finished documents only.
  - *Need review* / sidebar count / review queue (`/ui?view=review`): staged
    sessions (step checklist pending), finished documents whose report flags
    anything (fallback steps, empty-metadata steps, `[verify]` claims), and
    failed builds.
  - Search also matches doc number (UI only; `GET /library` unchanged).
  - The 6b/6c report keys are surfaced on the session page: `uncaptured_value_steps`
    as a "Values not captured" card (counted toward need-review) and
    `omitted_screenshot_steps` as a read-only note. Both are cards, not
    `<section data-status>`, so the three-section UI-smoke contract holds.
  - 6c review-page edits: a **Phases** card lists each phase with its
    steps and a rename form (`POST /ui/sessions/{id}/phases/{index}`; whitespace
    collapsed, blank or > `MAX_PHASE_NAME_LEN` → 400, unknown index/session →
    404). An **Omitted screenshots** card has a "Show screenshot" button per
    omitted step (`POST /ui/sessions/{id}/steps/{step_id}/keep-screenshot`;
    unknown step → 404). Both rewrite `phases.json` via `phases_to_json(...,
    "user", keep_screenshots=...)`, which preserves whichever of name/keep-list
    the call didn't change, then run the existing no-LLM re-export. A session
    without phases.json (screenshot build, pre-6c) gets 409. No config keys.
  - 6g: a **Document details** card (title, subtitle, document no., author,
    revision) prefilled from `report["document"]` posts to the existing
    `/ui/sessions/{id}/metadata`; blank resets a field to its inferred value.
    The library's Doc no. column now shows that same resolved number. Numbers
    are sequential and assigned at first export (`doc_no.txt`); an unexported
    session shows its saved number or "—", and a page view never consumes a
    counter value.
  - 6f's SPL deck (`spl.pptx`) is the SPL chip and an "SPL deck" download,
    shown only when the file exists; `spl_text_overflow` renders as a review
    note listing the over-long boxes, `spl_error` as a "deck not built" note.
  - 6d's walkthrough (`walkthrough.html`) is the WALK chip in the Exports
    column and a "walkthrough" download on the session page, shown only when
    the file exists (sessions generated before 6d have none).
- **Drop zone**: one zone (drag-drop, Browse files, Choose folder; folders are
  walked). `manifest.json` present → `POST /ui/upload` (manifest_file, files,
  transcript_file, narration_wav_file); absent → `POST /ui/build` (title,
  images in natural filename order, transcript_file). Shows the detected mode
  and file breakdown before submit. The two classic forms remain in
  `<noscript>`, so both routes still work as plain form posts.
- **Sidebar LLM status**: one `POST /ui/config/test` probe of the `[steps]`
  section (existing route), cached per tab for 60 s; renders "Ollama
  connected / model missing / unreachable" (or the configured provider's
  name). Async — never blocks page render.
- **Deviation from the mockup**: buttons and the count badge use `#c2410c`,
  not the mockup's `#e8590c` — white text on `#e8590c` is 3.58:1 and fails AA.
  `#e8590c` is kept for non-text accents (active-nav bar, focus ring, logo,
  drop-zone hover).
- **Skills**: `frontend-design`, `design:accessibility-review` and
  `design:design-critique` are not available in this session (searched the
  skill catalog). Substitutes below.

### AC8 — PASS

| Criterion | Result |
|---|---|
| Every existing UI route still renders; every existing UI test passes | PASS. No route or form field removed/renamed. `pytest` (Linux container, pinned ruff 0.15.20 clean): **734 passed** (after merging `factory-parity` @ `61cb566`), 6 skipped; the 7 failures/errors are the pre-existing Windows-only set (`winsdk` OCR ×5 in `test_redaction.py`, live UIA, elevated-hotkey probe), identical to the pre-change baseline on `factory-parity` @ `6f49217` (680 passed + same 7). New: `tests/pipeline/test_webui_forge.py` (19). |
| Playwright `-m ui` | **5 passed** (2 existing + 3 new). `test_ui_smoke_upload_to_docx_download` was already failing on `factory-parity` before this change: F-02 (`28287b5`) made "Verify claims" yellow and updated the unit-test twin (`test_session_page_colors_sidecar_sections_correctly`) but not this browser test. Its assertion now matches the unit test (`yellow`); nothing else in it changed. `_launch()` honours `PLAYWRIGHT_CHROMIUM_EXECUTABLE` for containers whose browser revision differs from the pip package. |
| Served pages make no external network requests | PASS. Browser-level: every request recorded on `/ui`, `/ui?view=review`, a session page, `/ui/config`, and through both drop-zone flows is same-origin (`_assert_local_only`). Markup-level: `test_pages_reference_no_external_resources` (no absolute `src`/`href`/`url()`, no `@import`, no CDN). |
| Plex fonts load from the frozen bundle | PASS. `test_font_route_reads_from_the_frozen_bundle` (sys.frozen + `_MEIPASS` monkeypatched → bytes come from the bundle dir); `test_server_spec_bundles_the_plex_fonts`; in-browser `document.fonts.check()` true for Plex Sans 600 and Plex Mono on all four page types, body font resolves to `"IBM Plex Sans"`, every font response 200. A real PyInstaller build is not runnable on this Linux container (spec is Windows-targeted; CLAUDE.md runs it for phases 1 & 3 only). |
| Both drop-zone paths reach a built document (playwright) | PASS. `test_ui_drop_zone_capture_path_reaches_a_built_document` (file picker, manifest + PNGs → steps review → docx downloads). `test_ui_drop_zone_screenshot_path_reaches_a_built_document` (a real `drop` event carrying 2 PNGs + transcript, natural-order check, title → steps review → docx; library title matches). |
| WCAG AA contrast (`design:accessibility-review` substitute) | PASS. Automated audit in Chromium over every visible text node + input placeholders (effective background composited through ancestors; 4.5:1, 3:1 for large text) on library, review queue, steps review (all checked + one unchecked), processing, capture session page, photo session page, config, config-saved banner, drop-zone plan and drop-zone error, light and dark: **0 failures** (first run found dark-mode placeholders at 3.63:1 → fixed with `::placeholder{color:var(--muted)}`). Unchecked step cards no longer use text opacity (would drop body text below AA) — dashed card + dimmed thumbnail + strikethrough instead. |

**Design critique (substitute for `design:design-critique`)** — issues found
in the screenshots and fixed: full-page sidebar stopped at the first
viewport on long pages (fixed with a column background); config inputs had
two different widths in one card; the recommended-models table overflowed a
half-width card (now full width); steps review rendered `· ()` for empty UIA
fields (now omitted, or "No window or element metadata captured"); at 390 px
the grid column grew to the table's width and scrolled the whole page (now
`minmax(0,1fr)`, table scrolls inside its own wrapper); doc numbers stay on
one line (`white-space:nowrap`, the wrap visible in the mockup). Known and
left: the per-session doc number is the existing `doc_number(prefix,
manifest.session.id)`; superseded by 6g's resolved number (see above).

### AC9 — PASS

`git diff factory-parity -- config/ src/pipeline/config.py` is empty: no new
keys in `config/models.toml` or the config model. The configuration page
posts exactly the 23 fields it posted before (`test_config_page_posts_exactly_the_existing_fields`;
also diffed against the old renderer's output). No new settings or toggles:
theme follows the OS, the review queue is a URL filter (`?view=review`), and
the sidebar status reuses `/ui/config/test`.
