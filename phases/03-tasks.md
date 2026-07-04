# Phase 3 — Task list

Rationale: All export/server/UI logic is built and unit-tested from `fixtures/`
alone first (tasks 01–08), with the riskiest library unknown (PDF, no renderer
currently installed) attacked immediately; everything environment-fragile — live
browser (AC2), PyInstaller build+run (AC3/AC5), installer round-trip (AC4) — is
isolated at the end (tasks 09–12), each independently runnable so a fragile
failure never blocks the fixture-verified core.

Environment facts noted at plan time:
- **No PDF renderer exists on this machine** (weasyprint absent from system
  py3.12 and `.venv`; not in `requirements.txt`). weasyprint needs GTK native
  DLLs on Windows and is PyInstaller-hostile; Word COM automation is
  machine-fragile. Task-01 therefore mandates a pure-Python renderer (`fpdf2`,
  pinned), with `pypdf` as a test-only dep for text extraction. Do not
  substitute a GTK- or COM-dependent path without a DEVIATIONS.md entry.
- **playwright is not installed** (checked system py3.12 and `.venv`) and needs
  a ~120MB chromium download plus first-launch AV scanning on this VM. Phase 1
  DEVIATIONS.md records intermittent synthetic-input/GDI failures and
  AV-inflated first launches on this exact host — headless chromium here is
  unproven, so task-09 must probe live (headless mode, generous first-launch
  timeout) rather than trust defaults. Phase 3 needs no input injection or GDI
  capture, so those specific Phase 1 intermittents do not otherwise apply.
- **The AC3 start budget (under 5s) is AV-sensitive.** Phase 1 measured a
  one-time AV first-scan cost dominating cold start (DEVIATIONS.md). Task-10
  must record both first-launch and warm-launch timings and gate on the same
  measurement discipline Phase 1 used — probe live, do not assume.
- **AC5 packaging hazard:** the docx engine is imported via `sys.path` from
  `C:\Users\Brian\Documents\SOP_Factory_2` (a working project with real client
  data, never vendored into this repo). A frozen EXE cannot see that path, so
  the build must bundle *only* the engine module(s) (`template/sop_lib.py` and
  its template assets) as PyInstaller datas at build time — bundling into
  `dist/` is not committing to the repo; `dist/` stays gitignored. Task-08
  de-risks this with a frozen-aware import shim before any build is attempted.
- **Scheduled-task autostart may require elevation.** `schtasks /create` for
  the current user is normally unelevated, but ONLOGON triggers can be blocked
  by policy. Task-12 verifies the non-autostart path headlessly and probes the
  autostart branch live; if elevation blocks it, record in DEVIATIONS.md and
  escalate rather than silently skip.

- [x] task-01: PDF export (`src/pipeline/export_pdf.py`) using pure-Python `fpdf2` (pin in `requirements.txt`; `pypdf` pinned as test-only extractor) — golden fixture manifest → PDF with one section per step, annotated screenshots embedded, `[verify]` blockquotes rendered; test asserts the `%PDF` header, page count ≥ step count, and pypdf-extracted text contains every step title and every `[verify]` claim (AC1 part 1) — verify: `pytest -q tests/pipeline/test_export_pdf.py`
- [x] task-02: Self-contained single-file HTML export (`src/pipeline/export_html.py`) — all images inlined as base64 data URIs, CSS inline, no script/link/img/font reference to any external resource; test scans the markup and asserts zero `http(s)://` or protocol-relative refs and that every `src`/`href` is `data:` or a fragment anchor (AC1 part 2) — verify: `pytest -q tests/pipeline/test_export_html_single.py`
- [x] task-03: Markdown export with relative image links (`src/pipeline/export_md.py`) — Obsidian-compatible bundle `<slug>.md` + `images/NNN.png`; test parses every image link out of the emitted markdown and asserts each target file exists relative to the `.md`, and that no link is absolute or a URI (AC1 part 3) — verify: `pytest -q tests/pipeline/test_export_md_bundle.py`
- [x] task-04: AC1 rollup + export endpoints — one test renders all four formats (docx via existing `assemble_docx`, pdf, single-file html, md bundle) from the golden fixture; server gains `GET /sessions/{id}/doc.docx` (closes the known missing-docx-route gap), `/doc.pdf`, `/doc.single.html`, `/export.md.zip`, each with correct content-type and content-disposition — verify: `pytest -q tests/pipeline/test_export_all_formats.py tests/pipeline/test_server_exports.py`
- [x] task-05: Background job runner (`src/pipeline/jobs.py`) — thread worker with per-session status lifecycle queued → processing → done | error, replacing the status plumbing that always reports done; `POST /sessions` returns immediately, `POST /sessions/{id}/rerender` re-runs generation+exports against the current `config/models.toml`; tests inject slow and exploding stub pipelines to observe an intermediate non-done status, terminal done, and captured error detail — verify: `pytest -q tests/pipeline/test_jobs.py`
- [x] task-06: SOP library store + read endpoints — persistent JSON index (title, session date, formats rendered, sidecar summary counts) updated on every completed job; `GET /library?q=` searches by title/date substring; `GET /config` returns parsed `config/models.toml` for read-only display — verify: `pytest -q tests/pipeline/test_library.py`
- [x] task-07: Review web UI (plain HTML/JS under `src/pipeline/webui/`, no build step, no Node) — library page with search box, per-session page with doc preview (`doc.html` iframe), sidecar report as red/yellow/green per section (red = template-fallback step, yellow = `[verify]` claim or empty-UIA-metadata step, green = clean), re-render button wired to `POST .../rerender`, read-only config panel from `GET /config`; DOM-asserted via TestClient + HTML parser, no browser — verify: `pytest -q tests/pipeline/test_webui_pages.py`
- [x] task-08: Frozen-bundle resource resolution (AC3 de-risk, AC5 prerequisite) — single `resource_path()` helper (`sys._MEIPASS`-aware) adopted by the webui static file mounting, a SOP Factory 2 import shim (dev: existing `sys.path` to `C:\Users\Brian\Documents\SOP_Factory_2`; frozen: engine modules bundled inside the EXE at build time, never committed to this repo), AND `src/pipeline/config.py`'s `DEFAULT_CONFIG_PATH` (currently `Path(__file__).resolve().parent.parent.parent / "config" / "models.toml"`, which will not resolve under a frozen build's `_internal` layout — task-06 review flagged this as a real gap in `GET /config`, not yet fixed); unit tests monkeypatch `sys.frozen`/`sys._MEIPASS` to prove all three call sites resolve in both branches — verify: `pytest -q tests/pipeline/test_frozen_paths.py`
- [x] task-09: Playwright UI smoke against the localhost dev server (AC2) — pin `playwright`, install headless chromium; test uploads a fixture session built from `fixtures/review-report-manifest.json` + `fixtures/review-report-transcript.json`, polls status to done, asserts the report page shows exactly the expected 3 flags (step-003 fallback red, step-002 empty-metadata yellow, claim-002 `[verify]` yellow), and downloads a valid docx; isolated behind a `ui` marker so the core suite stays browser-free — verify: `pytest -q -m ui tests/pipeline/test_ui_smoke.py`
- [x] task-10: `sopforge-server.spec` + `scripts/build_server_exe.py` (extend the Phase 1 one-folder+UPX pattern) — bundles webui assets, SOP Factory 2 engine modules/template, and the default `config/models.toml` as datas; verify script launches the frozen EXE, polls `GET /` to HTTP 200 with UI markup served from the bundle, asserts warm start under 5s while recording first-launch (AV-scan) timing separately per Phase 1 precedent, then asserts the process exits cleanly on stop (AC3) — verify: `python scripts/build_server_exe.py --assert-start 5`
- [x] task-11: End-to-end through the built EXE, not the dev server (AC5) — launch `dist/sopforge-server/sopforge-server.exe`, POST the golden fixture session over real HTTP, poll to done, download `doc.docx`, byte-compare `word/document.xml` against `fixtures/golden-document.xml` using the Phase 2 golden normalizer — verify: `pytest -q tests/pipeline/test_exe_e2e.py`
- [ ] task-12: `install.ps1` / `uninstall.ps1` + automated round-trip (AC4) — install to a parameterized path: create folders, copy both EXEs from `dist/`, write default config with configurable port, optional `-Autostart` scheduled task (current user); uninstall removes exactly what install created and nothing else; `scripts/test_install.ps1` snapshots directory state, installs to a temp path on a non-default port, polls the health endpoint, uninstalls, and asserts before/after directory state matches (autostart branch: create, then `schtasks /query`, then delete; on elevation failure record in DEVIATIONS.md and escalate, never silently pass) — verify: `powershell -ExecutionPolicy Bypass -File scripts/test_install.ps1`
