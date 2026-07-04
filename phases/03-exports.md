# Phase 3 — Exports, Review UI, Packaging

## Deliverables

- Exports beyond docx: self-contained HTML (inline images, single file), PDF,
  Markdown with relative image links (Obsidian-compatible).
- Review web UI on the FastAPI server: session list, doc preview, sidecar report
  as red/yellow/green per section, one-click re-render after config change.
- SOP library: sessions and rendered docs listed, searchable by title/date.
- `config/models.toml` surfaced in the UI (read-only display is enough for v1).
- Packaging: `sopforge-server.exe` PyInstaller build; single-line install script
  (`install.ps1`: create folders, drop EXEs, optional scheduled-task autostart for
  the server); matching `uninstall.ps1`.

## Acceptance criteria (record in phases/03-results.md)

1. All four export formats render from the golden fixture; HTML opens as a single
   file with no network requests (assert no external refs in the markup); Markdown
   links resolve against its image folder.
2. UI smoke test (playwright against localhost): upload fixture session → status
   reaches done → report page shows the expected 3 flags → docx downloads.
3. `sopforge-server.exe` builds clean, serves the UI from the frozen bundle
   (static assets resolved via `sys._MEIPASS`), starts <5s.
4. install.ps1 on a clean path: install → server responds on configured port →
   uninstall removes everything it created (assert directory state before/after).
5. End-to-end: fixture session through the built EXE (not the dev server) produces
   the same golden docx as Phase 2's test.
