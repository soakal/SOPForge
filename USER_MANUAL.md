# SOPForge User Manual

Private software — see [LICENSE](LICENSE). Built by CwiAI.

SOPForge is complete: the capture agent, the generation pipeline, exports,
the review web UI, and packaged installers all work as described below.

---

## 1. What SOPForge does

SOPForge turns a recorded Windows workflow into a polished Standard Operating
Procedure document, entirely on your own machine:

1. A **capture agent** (system tray app) records mouse clicks, keystrokes-as-typed-
   field summaries, screenshots, and UI Automation metadata (what window, what
   control, what element name) while you perform a task.
2. A **generation pipeline** turns that recording into step-by-step instructions,
   optionally phrased by a local LLM (via Ollama) with a guaranteed factual
   fallback if the LLM is unavailable or produces something inaccurate.
3. The result is assembled into **docx** (using the SOP Factory 2 / VRSI
   formatting engine), **PDF**, a **self-contained single-file HTML**, and an
   **Obsidian-compatible Markdown bundle** (`.md` + `images/`).
4. A **review web UI** lets you search past sessions, preview the generated
   doc, see a red/yellow/green sidecar report, re-render, and download every
   format.

Nothing is sent to the cloud by default. An Anthropic API routing option exists
per config section but is off unless you turn it on.

---

## 2. Installing

### Option A — packaged EXEs (recommended)

If `dist/sopforge/` and `dist/sopforge-server/` are already built:

```powershell
.\install.ps1                                    # installs to %LOCALAPPDATA%\SOPForge
.\install.ps1 -InstallPath "D:\SOPForge" -Port 8420 -Autostart
```

This copies both EXEs, creates a `sessions/` folder, and (with `-Autostart`)
registers a per-user scheduled task that launches the server at logon.
`-Autostart` is **best-effort** — some machines/accounts restrict
`AtLogOn`-triggered scheduled task registration even without elevation; if
that happens you'll see a warning, but SOPForge itself still installs and
works fine. Launch `sopforge-server.exe` yourself in that case, or register
the task manually.

```powershell
.\uninstall.ps1                                  # removes what install.ps1 created
.\uninstall.ps1 -RemoveData                      # also deletes sessions/ (your generated SOPs)
```

By default, `uninstall.ps1` preserves `sessions/` if it has any real content
in it — uninstalling the app doesn't delete your generated documents unless
you explicitly ask it to.

Building the EXEs from source (once per machine/rebuild):
```powershell
.\.venv\Scripts\python.exe scripts\build_exe.py                  # sopforge.exe (capture agent)
.\.venv\Scripts\python.exe scripts\build_server_exe.py           # sopforge-server.exe (pipeline server)
```
Both scripts wrap PyInstaller with the right UPX settings and print the
built size; `build_server_exe.py` also launches the built EXE to verify it
actually starts and responds before reporting success.

### Option B — run from source (development)

See §9 below.

### Prerequisites (either option)

- Windows 11 (the capture agent uses Windows-only APIs — UI Automation, low-level
  input hooks, GDI screen capture).
- The SOP Factory 2 docx engine, cloned to `C:\Users\Brian\Documents\SOP_Factory_2`
  (only needed for building `sopforge-server.exe` yourself, or running from source —
  the packaged EXE bundles the two files it actually needs):
  ```powershell
  gh repo clone soakal/SOP-Factory C:\Users\Brian\Documents\SOP_Factory_2
  ```
  (Private repo — `gh auth login` with access to `soakal/SOP-Factory` first.)
  If cloned elsewhere, set `SOPFORGE_SOP_FACTORY_2_DIR` to that path.
- (Optional, for LLM-phrased steps and narration) An Ollama server reachable at
  the endpoint in `config/models.toml` (default `http://192.168.200.60:11434/v1`),
  with the `qwen3:14b` and `qwen3:32b` models pulled. Everything works without
  this — steps just render via the deterministic template fallback instead, and
  the live server currently always runs in this template-only mode (see §6).
- (Optional, for narration/transcription) faster-whisper downloads its own model
  weights from Hugging Face on first use of a given model size.

---

## 3. Recording a session (capture agent)

Run the tray app (`dist\sopforge\sopforge.exe`, or from source: `.\.venv\Scripts\python.exe -m capture`):

- A gray circular tray icon appears once the app is ready.
- Press **Ctrl+Alt+R** (or right-click the tray icon → "Start/Stop recording")
  to start recording. The icon turns red while recording.
- Perform the workflow you want documented: click through the UI, type into
  fields (SOPForge records *that* you typed and a redacted summary, never your
  actual keystrokes' content), switch windows as needed.
- Press **Ctrl+Alt+R** again (or the tray menu) to stop. The icon returns to gray.
- Right-click → **Exit** closes the tray app.

Each session is written to `%USERPROFILE%\SOPForge\captures\<timestamp>\`:
- `manifest.json` — the ordered list of recorded steps (this is ground truth;
  nothing downstream can add, drop, or reorder a step from this file).
- Numbered screenshots, one per step.

---

## 4. Running the pipeline server

If installed via `install.ps1`:
```powershell
& "$env:LOCALAPPDATA\SOPForge\server\sopforge-server.exe" --port 8420 --sessions-root "$env:LOCALAPPDATA\SOPForge\sessions"
```
(or just launch it directly — `--port`/`--sessions-root` default to `8420` and
`~/SOPForge/sessions` if omitted.) Then open **http://127.0.0.1:8420/** in a
browser — that's the review web UI (§5).

To stop it: `POST /shutdown` (what the tray/installer tooling uses), or close
its process. There's no console window since it runs windowed, so closing it
via Task Manager or `Stop-Process` both work too.

### Uploading a session via the API

```powershell
$manifestJson = Get-Content -Raw "$env:USERPROFILE\SOPForge\captures\<timestamp>\manifest.json"
curl.exe -X POST http://127.0.0.1:8420/sessions `
  -F "manifest_json=$manifestJson" `
  -F "files=@$env:USERPROFILE\SOPForge\captures\<timestamp>\001.png" `
  -F "files=@$env:USERPROFILE\SOPForge\captures\<timestamp>\002.png"
  # ... one -F "files=@...png" per screenshot in the manifest
```

The response is `{"session_id": "...", "status": "queued"}`. Generation runs
in the background — poll `GET /sessions/{id}/status` until `"status": "done"`
(or `"error"`, with an `"error"` detail message) before fetching any doc.

### Endpoints

| Method | Path | Returns |
|---|---|---|
| `POST` | `/sessions` | Uploads + queues a session for processing. `multipart/form-data`: `manifest_json` + one `files` part per screenshot, named exactly as the manifest's `screenshot` field. Returns immediately with `status: "queued"`. |
| `POST` | `/sessions/{id}/rerender` | Re-runs generation + all exports for an already-uploaded session. |
| `GET` | `/sessions/{id}/status` | `{"status": "queued"\|"processing"\|"done"\|"error", ["error": "..."]}` |
| `GET` | `/sessions/{id}/report` | The sidecar review report (JSON) — see §6. 409 until done. |
| `GET` | `/sessions/{id}/doc.md` | Markdown, relative image paths. |
| `GET` | `/sessions/{id}/doc.html` | HTML, relative image paths (served alongside it, so it previews correctly). |
| `GET` | `/sessions/{id}/doc.docx` | The assembled docx (VRSI/SOP Factory 2 formatting). |
| `GET` | `/sessions/{id}/doc.pdf` | PDF, one page per step. |
| `GET` | `/sessions/{id}/doc.single.html` | Self-contained single-file HTML (images inlined as base64) — safe to email or move anywhere. |
| `GET` | `/sessions/{id}/export.md.zip` | The Markdown bundle (`.md` + `images/`) zipped up. |
| `GET` | `/sessions/{id}/review` | A plain-HTML page rendering just the sidecar report. |
| `GET` | `/library?q=` | Search past sessions by title/date substring. |
| `GET` | `/config` | Read-only view of the parsed `config/models.toml`. |
| `GET` | `/` or `/ui` | The review web UI's library page (see §5). |
| `GET` | `/ui/sessions/{id}` | The review web UI's per-session page. |

### What "template mode" means

Every step's wording can come from one of two places:
- **LLM-phrased**: a locally-run model writes a natural sentence, checked
  against the manifest's own facts (action, element name, window title) — if
  it doesn't hold up, or the LLM call fails for any reason, that step falls
  back to...
- **Template fallback**: a plain, always-correct sentence built by direct
  string substitution from the manifest ("Click the 'Save' Button in the
  'Answer File Editor' window."). Never requires the LLM, never wrong by
  construction.

**The live server currently always runs in template mode** — the LLM
client/generation orchestrator and narration/claim-coverage pipeline are
built and fully unit-tested, but wiring them into `POST /sessions`' live
generation path hasn't been done yet. Nothing is lost either way: template
mode is always factually correct, just less fluent to read.

---

## 5. Using the review web UI

Open **http://127.0.0.1:<port>/** (or `/ui`):

- **Library page**: every past session, searchable by title/date substring.
  Click a session to open its review page.
- **Session page**: an iframe preview of the generated doc, the sidecar
  report as three color-coded sections (see §6), a **Re-render** button, a
  **Downloads** list (docx/pdf/single-file-html/markdown-zip), and a
  read-only panel showing the current `config/models.toml`.

No JavaScript is required — the search box and re-render button are plain
HTML forms.

---

## 6. Reading the sidecar review report

Every generated doc ships with a report (`GET /sessions/{id}/report`, the
`/review` page, or the colored sections on the `/ui/sessions/{id}` page)
listing three things a reviewer should check:

- **Template-fallback steps** (red if non-empty) — steps where the LLM's
  phrasing didn't hold up (or wasn't attempted) and the plain template
  sentence was used instead. Always empty today, since the live server runs
  template-mode only (§4).
- **Verify claims** (yellow if non-empty) — narration claims (from an audio
  transcript) that couldn't be matched to anything in the generated
  narrative text. Appear in the doc as `> [verify] (claim-id): <original
  claim text>` blockquotes — nothing from a recording is ever silently
  dropped. Always empty today, since narration isn't wired into the live
  server either.
- **Empty-metadata steps** (yellow if non-empty) — steps where no UI
  Automation element info was captured at all. These render using screen
  coordinates instead of an element name — still factual, just less specific.
  This is the one category that reflects real data through the live server.

An all-green report means every step made it into the doc with full
information and no fallback.

---

## 7. Configuration

`config/models.toml`:

```toml
[steps]
endpoint = "http://192.168.200.60:11434/v1"   # Ollama OpenAI-compatible endpoint
model = "qwen3:14b"
anthropic = false                              # true routes this section to Anthropic instead

[narrative]
endpoint = "http://192.168.200.60:11434/v1"
model = "qwen3:32b"
passes = 3                                      # draft -> critique -> revise round count
anthropic = false
```

View the currently-active config at `GET /config` or the `/ui/sessions/{id}`
page's config panel. Anthropic routing per section exists as a config flag
but has no client implementation yet (`LLMClient.chat()` raises
`NotImplementedError` if `anthropic = true`, deliberately — a misconfigured
section fails loudly instead of silently talking to the wrong endpoint).

---

## 8. Known limitations

- LLM-phrased steps and narration/transcription are built and unit-tested but
  not wired into the live server's generation path — see §4/§6.
- `-Autostart` scheduled-task registration is best-effort; some
  machines/accounts restrict it (see §2 and `phases/DEVIATIONS.md`). The
  server always works fine launched manually regardless.
- `POST /sessions` processes each session on a single background worker
  thread — sessions queue and run one at a time, not in parallel.

---

## 9. Development (running from source)

```powershell
cd C:\Users\Brian\Documents\SOPForge
py -3.12 -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\pip.exe install -e .
```

Run the server directly instead of the packaged EXE:
```powershell
.\.venv\Scripts\python.exe -m pipeline --port 8420
```

Run the capture agent:
```powershell
.\.venv\Scripts\python.exe -m capture
```

Test suite:
```powershell
.\.venv\Scripts\python.exe -m pytest -q          # full suite (excludes browser/EXE-dependent tests)
.\.venv\Scripts\python.exe -m pytest -q -m ui tests/pipeline/test_ui_smoke.py     # needs playwright + chromium
.\.venv\Scripts\python.exe -m pytest -q -m exe tests/pipeline/test_exe_e2e.py    # needs a pre-built dist/sopforge-server/
.\.venv\Scripts\python.exe -m ruff check src/ tests/ scripts/
.\.venv\Scripts\python.exe -m ruff format src/ tests/ scripts/
```

Opt-in tests that need real external things (skip cleanly otherwise):
- `SOPFORGE_OLLAMA_URL=http://<host>:<port>/v1` — exercises a real Ollama call.
- `SOPFORGE_WHISPER_MODEL=tiny` — downloads and runs a real faster-whisper model.

See `CLAUDE.md` for the full build contract and `phases/*-results.md` for
acceptance-criteria evidence per phase.
