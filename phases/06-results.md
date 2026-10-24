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

## 6h — Forge UI redesign

Rebuilt every page in `src/pipeline/webui/pages.py` (library, review queue,
steps review, processing, session/review, configuration) on the Forge chrome:
graphite sidebar (Library, New SOP, Review queue + count, Configuration; LLM
status + version in the footer), warm off-white surface, ember accent, IBM
Plex Sans + Mono, light + dark via `prefers-color-scheme`. Screenshots of each
page in both themes: `phases/06-screenshots/6h/`.

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
| Every existing UI route still renders; every existing UI test passes | PASS. No route or form field removed/renamed. `pytest` (Linux container, pinned ruff 0.15.20 clean): **691 passed**, 6 skipped; the 7 failures/errors are the pre-existing Windows-only set (`winsdk` OCR ×5 in `test_redaction.py`, live UIA, elevated-hotkey probe), identical to the pre-change baseline on `factory-parity` @ `6f49217` (680 passed + same 7). New: `tests/pipeline/test_webui_forge.py` (11). |
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
manifest.session.id)` — blank (—) when no prefix is configured, until 6g
auto-generates one.

### AC9 — PASS

`git diff factory-parity -- config/ src/pipeline/config.py` is empty: no new
keys in `config/models.toml` or the config model. The configuration page
posts exactly the 23 fields it posted before (`test_config_page_posts_exactly_the_existing_fields`;
also diffed against the old renderer's output). No new settings or toggles:
theme follows the OS, the review queue is a URL filter (`?view=review`), and
the sidebar status reuses `/ui/config/test`.
