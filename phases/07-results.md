# Phase 7 results (2026-11-13)

Gate: `ruff check src/ tests/` clean, `ruff format --check src/` clean,
`pytest` 865 passed / 8 skipped (opt-in `ui`/`exe` markers deselected).

| AC | Evidence |
|---|---|
| 1 no-authoring unchanged | `test_export_authoring_md_html.py` (byte-identical md/html/single/bundle), `test_export_walkthrough.py` (None == EMPTY entries), `test_export_spl.py::test_no_authoring_is_identical_plan`; docx/pdf layout change recorded in DEVIATIONS.md, golden regenerated (heading text only) |
| 2 authored content in every format, steps unchanged | `test_authoring.py::test_authoring_route_reaches_every_format`, `test_docx_pdf_authoring.py`, `test_export_spl.py::test_authoring_adds_prereqs_callouts_notes_and_blocks`, `test_authoring.py::test_outline_keeps_every_step_once_in_order_and_letters_per_section` |
| 3 invalid refused / stale tolerated | `test_authoring.py` (parametrized field errors, 400 keeps typed text, stale file) |
| 4 walkthrough | `test_export_walkthrough.py` (no network refs, `node --check`, browser test: params, path, Enter, `?shot`) |
| 5 PDF/DOCX sources, EXIF | `test_source_extract.py` |
| 6 | this gate; EXE rebuild below |

Also fixed: tests read the real `~/SOPForge/models.toml` (polish on → live
LLM calls, session-wait timeouts). `tests/conftest.py` now isolates it.
