# IBM Plex (vendored)

The review web UI's typefaces (Phase 6h, "Forge"): IBM Plex Sans (Regular,
Italic, Medium, SemiBold, Bold) and IBM Plex Mono (Regular, Medium,
SemiBold), WOFF2.

- Source: npm `@ibm/plex-sans@1.1.0` and `@ibm/plex-mono@2.5.0`,
  `fonts/complete/woff2/` (unmodified).
- License: SIL Open Font License 1.1 — `LICENSE` (identical in both packages).
- Served by `GET /ui/assets/fonts/{file}` (`pipeline/server.py`; the allowed
  file list is `FONT_FILES` in `pipeline/webui/pages.py`) and bundled into
  `sopforge-server.exe` by `sopforge-server.spec`, so the UI never loads a
  font from the network.
