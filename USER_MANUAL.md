# SOPForge User Manual

Private software — see [LICENSE](LICENSE). Built by CwiAI.

This manual covers what's actually usable today (Phase 1 capture agent + Phase 2
generation pipeline, both complete). Phase 3 (exports beyond docx, review web UI,
one-click packaging/installer) is not built yet — see the Limitations section.

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
3. The result is assembled into a **docx** (using the SOP Factory 2 / VRSI
   formatting engine), plus Markdown and HTML.

Nothing is sent to the cloud by default. An Anthropic API routing option exists
per config section but is off unless you turn it on.

---

## 2. Prerequisites

- Windows 11 (the capture agent uses Windows-only APIs — UI Automation, low-level
  input hooks, GDI screen capture).
- Python 3.12 (`py -3.12`).
- The SOP Factory 2 docx engine, cloned to `C:\Users\Brian\Documents\SOP_Factory_2`:
  ```powershell
  gh repo clone soakal/SOP-Factory C:\Users\Brian\Documents\SOP_Factory_2
  ```
  (Private repo — you need `gh auth login` with access to `soakal/SOP-Factory`
  first.) If you clone it somewhere else, set the `SOPFORGE_SOP_FACTORY_2_DIR`
  environment variable to that path.
- (Optional, for LLM-phrased steps and narration) An Ollama server reachable at
  the endpoint in `config/models.toml` (default `http://192.168.200.60:11434/v1`),
  with the `qwen3:14b` and `qwen3:32b` models pulled. Everything works without
  this — steps just render via the deterministic template fallback instead.
- (Optional, for narration/transcription) faster-whisper downloads its own model
  weights from Hugging Face on first use of a given model size; needs internet
  access to `huggingface.co` the first time only.

### Install dependencies

```powershell
cd C:\Users\Brian\Documents\SOPForge
py -3.12 -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
.\.venv\Scripts\pip.exe install -e .
```

---

## 3. Recording a session (capture agent)

Run the tray app:

```powershell
.\.venv\Scripts\python.exe -m capture
```

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

You can sanity-check a manifest against the schema at any time:
```powershell
.\.venv\Scripts\python.exe -c "from pipeline.manifest import load_manifest; load_manifest(r'<path>\manifest.json')"
```
(raises if anything is missing or malformed; prints nothing and exits 0 if valid.)

---

## 4. Generating a SOP from a captured session

The generation pipeline is a FastAPI app (`src/pipeline/server.py`). There's no
packaged launcher yet (that's Phase 3) — run it directly with uvicorn in the
meantime:

```powershell
.\.venv\Scripts\python.exe -c "
import uvicorn
from pathlib import Path
from pipeline.server import create_app

app = create_app(sessions_root=Path.home() / 'SOPForge' / 'sessions')
uvicorn.run(app, host='127.0.0.1', port=8000)
"
```

Then, from another terminal (or a script/Postman/etc.), upload a captured
session's manifest and its screenshots:

```powershell
$manifestJson = Get-Content -Raw "$env:USERPROFILE\SOPForge\captures\<timestamp>\manifest.json"
curl.exe -X POST http://127.0.0.1:8000/sessions `
  -F "manifest_json=$manifestJson" `
  -F "files=@$env:USERPROFILE\SOPForge\captures\<timestamp>\001.png" `
  -F "files=@$env:USERPROFILE\SOPForge\captures\<timestamp>\002.png"
  # ... one -F "files=@...png" per screenshot in the manifest
```

The response is `{"session_id": "...", "status": "done"}` (processing is
synchronous today — the request doesn't return until the doc is ready).

### Endpoints

| Method | Path | Returns |
|---|---|---|
| `POST` | `/sessions` | Creates and fully processes a session. `multipart/form-data`: `manifest_json` (the manifest file's raw text) + one `files` part per screenshot, named exactly as the manifest's `screenshot` field for that step. |
| `GET` | `/sessions/{id}/status` | `{"status": "done"}` |
| `GET` | `/sessions/{id}/report` | The sidecar review report (JSON) — see §5. |
| `GET` | `/sessions/{id}/doc.md` | The generated Markdown document. |
| `GET` | `/sessions/{id}/doc.html` | The generated HTML document (self-contained styling, images referenced by relative path). |
| `GET` | `/sessions/{id}/review` | A plain-HTML page rendering the sidecar report for a human reviewer. |

There is currently **no docx download endpoint on the server** — docx assembly
(`pipeline.docx_assembler.assemble_docx`) is wired in and fully tested, but not
yet exposed as a server route. Call it directly for now:

```python
from pipeline.manifest import load_manifest
from pipeline.render import render_steps_template_mode
from pipeline.docx_assembler import assemble_docx

manifest = load_manifest(r"<path>\manifest.json")
step_results, _ = render_steps_template_mode(
    manifest, screenshot_dir=r"<path>", annotated_dir=r"<path>\annotated"
)
assemble_docx(manifest, step_results, r"<path>\annotated", r"<path>\output.docx")
```

### What "template mode" means

Every step's wording can come from one of two places:
- **LLM-phrased** (if `config/models.toml`'s endpoint is reachable): a locally-run
  model writes a natural sentence, which is then checked against the manifest's
  own facts (action, element name, window title) — if it doesn't hold up, or the
  LLM call fails for any reason, that one step silently falls back to...
- **Template fallback**: a plain, always-correct sentence built by direct string
  substitution from the manifest ("Click the 'Save' Button in the 'Answer File
  Editor' window."). This never requires the LLM and is never wrong, by
  construction — it just isn't as fluent to read.

The current server always uses template mode. Nothing is lost either way — the
sidecar report (§5) tells you exactly which steps used which path once LLM mode
is wired into the server (a Phase 3 follow-on).

---

## 5. Reading the sidecar review report

Every generated doc ships with a report (`GET /sessions/{id}/report`, or the
human-readable `/review` page) listing three things a reviewer should check:

- **`template_fallback_steps`** — steps where the LLM's phrasing didn't hold up
  (or wasn't attempted) and the plain template sentence was used instead. Not
  wrong, just less fluent — read these to see if they're worth polishing by hand.
- **`verify_claims`** — narration claims (from an audio transcript, if one was
  provided) that couldn't be matched to anything in the generated narrative text.
  These appear in the doc itself as `> [verify] (claim-id): <original claim
  text>` blockquotes — nothing from the recording is ever silently dropped.
- **`empty_metadata_steps`** — steps where no UI Automation element info was
  captured at all (the app/control didn't expose one, or capture couldn't
  resolve it in time). These render using screen coordinates instead of an
  element name — still factual, just less specific.

An empty report across all three means every step and every narration claim
made it into the doc with full information and no fallback.

---

## 6. Configuration

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

Anthropic routing per section exists as a config flag but has no client
implementation yet (`LLMClient.chat()` raises `NotImplementedError` if
`anthropic = true` — this is deliberate, so a misconfigured section fails loudly
instead of silently talking to the wrong endpoint).

---

## 7. Limitations (what's not built yet — Phase 3)

- No PDF export, no single-file self-contained HTML export, no Obsidian-style
  relative-link Markdown packaging — only the plain md/html/docx covered above.
- No review web UI beyond the single static `/review` report page — no session
  list, no doc preview, no red/yellow/green status coloring, no re-render button.
- No SOP library / search across past sessions.
- No packaged `sopforge-server.exe` or `sopforge.exe` — everything runs from a
  Python virtualenv as shown above. No `install.ps1`/`uninstall.ps1` yet.
- No docx download route on the server (call `assemble_docx` directly, per §4).
- The server processes each `POST /sessions` synchronously — large sessions will
  make that request take a while; `GET /status` is plumbing for a future async
  version, not currently meaningful (it's always `"done"` by the time you see
  the session id at all).

---

## 8. Development

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # full test suite
.\.venv\Scripts\python.exe -m ruff check src/ tests/
.\.venv\Scripts\python.exe -m ruff format src/ tests/
```

Opt-in tests that need real external things (skip cleanly otherwise):
- `SOPFORGE_OLLAMA_URL=http://<host>:<port>/v1` — exercises a real Ollama call.
- `SOPFORGE_WHISPER_MODEL=tiny` — downloads and runs a real faster-whisper model.

See `CLAUDE.md` for the full build contract and `phases/*-results.md` for
acceptance-criteria evidence per phase.
