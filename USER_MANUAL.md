# SOPForge User Manual

Private software — see [LICENSE](LICENSE). Built by CwiAI.

SOPForge is complete: the capture agent, the generation pipeline, exports,
the review web UI, and packaged installers all work as described below.

---

## 1. What SOPForge does

SOPForge turns a recorded Windows workflow into a polished Standard Operating
Procedure document, entirely on your own machine — **the whole point is that
you record and stop, and the doc just appears; no manual steps in between:**

1. A **capture agent** (system tray app) records mouse clicks, keystrokes-as-typed-
   field summaries, screenshots, and UI Automation metadata (what window, what
   control, what element name) while you perform a task.
2. The instant you stop recording, it's **automatically sent to the pipeline
   server** (if one's running) — no browser, no file picker, nothing to
   upload by hand.
3. A **generation pipeline** turns that recording into step-by-step instructions,
   optionally phrased by a local LLM (via Ollama or Anthropic) with a guaranteed
   factual fallback if the LLM is unavailable or produces something inaccurate.
4. The result is assembled into **docx** (using the SOP Factory 2 / VRSI
   formatting engine), **PDF**, a **self-contained single-file HTML**, and an
   **Obsidian-compatible Markdown bundle** (`.md` + `images/`).
5. **Your browser opens automatically** to the finished doc's review page —
   preview it, check the sidecar report, download whatever format you need.

If the server isn't running when you stop recording, nothing is lost — the
capture stays safely on disk and you can upload it manually later through the
review web UI's upload form (§5), or via the API (§4).

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
works fine either way.

#### If `-Autostart` fails: two manual ways to start the server automatically

**Option 1 — Startup folder shortcut (simplest, usually works even when the
scheduled task doesn't, since it isn't subject to the same Task Scheduler
restriction):**
```powershell
$startup = [Environment]::GetFolderPath("Startup")
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut("$startup\SOPForge-Server.lnk")
$shortcut.TargetPath = "$env:LOCALAPPDATA\SOPForge\server\sopforge-server.exe"
$shortcut.Arguments = "--port 8420 --sessions-root `"$env:LOCALAPPDATA\SOPForge\sessions`""
$shortcut.Save()
```
This runs the server whenever you log in. To remove it later, just delete
`SOPForge-Server.lnk` from that Startup folder (or open it with
`explorer (Get-Item $startup).FullName`).

**Option 2 — register the scheduled task yourself** through the Task
Scheduler GUI (`taskschd.msc`), if your account's restriction only blocks
the *unattended*/scripted registration path and not an interactive one:
1. Task Scheduler → Create Task (not "Create Basic Task").
2. General tab: name it "SOPForge-Server"; under "Security options" choose
   "Run only when user is logged on."
3. Triggers tab → New → "At log on" → your user account.
4. Actions tab → New → Program/script: the full path to
   `sopforge-server.exe` (e.g. `%LOCALAPPDATA%\SOPForge\server\sopforge-server.exe`);
   Add arguments: `--port 8420 --sessions-root "%LOCALAPPDATA%\SOPForge\sessions"`.
5. OK, then right-click the new task → Run, to confirm it starts the server
   (check `http://127.0.0.1:8420/` in a browser).

Either way, this only needs doing once per machine.

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

### Distributing to someone else

Once both EXEs are built, package everything a recipient needs — no Python,
no PyInstaller, no repo clone required on their end — into one folder (and
zip) with:

```powershell
.\.venv\Scripts\python.exe scripts\build_release.py --zip
```

This produces `release\SOPForge\` (and `release\SOPForge.zip`) containing
both built EXEs, `install.ps1`/`uninstall.ps1`, `USER_MANUAL.md`, and
`LICENSE` — the recipient just unzips it and runs `install.ps1` from inside,
exactly as described above. `release/` is gitignored; rerun this after every
rebuild you want to hand off.

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
- (Optional, for LLM-phrased steps) Either an Ollama server reachable at the
  endpoint in `config/models.toml` (default `http://192.168.200.60:11434/v1`,
  with the `qwen3:14b` model pulled), or Anthropic routing configured with
  `ANTHROPIC_API_KEY` set (§7). Everything works without either — steps just
  render via the deterministic template fallback instead (§4/§6).
- (Optional, for narration/transcription) faster-whisper downloads its own model
  weights from Hugging Face on first use of a given model size.

---

## 3. Recording a session (capture agent)

Have the pipeline server running first (§4) if you want the "record, stop,
doc appears" experience — it isn't required to record, just to get the
automatic hand-off.

Run the tray app (`dist\sopforge\sopforge.exe`, or from source: `.\.venv\Scripts\python.exe -m capture`):

- A gray circular tray icon appears once the app is ready.
- Press **Ctrl+Alt+R** (or right-click the tray icon → "Start/Stop recording")
  to start recording. The icon turns red while recording.
- Perform the workflow you want documented: click through the UI, type into
  fields (SOPForge records *that* you typed and a redacted summary, never your
  actual keystrokes' content), switch windows as needed.
- Press **Ctrl+Alt+R** again (or the tray menu) to stop. The icon returns to gray.
- **That's it.** If a pipeline server is running (default: `http://127.0.0.1:8420`),
  the capture is uploaded automatically and your browser opens straight to the
  finished doc's review page once it's done. Nothing to click, nothing to upload.
- Right-click → **Exit** closes the tray app.

If no server was running, or the upload failed for any reason, nothing is
lost — each session is also always written to
`%USERPROFILE%\SOPForge\captures\<timestamp>\`:
- `manifest.json` — the ordered list of recorded steps (this is ground truth;
  nothing downstream can add, drop, or reorder a step from this file).
- Numbered screenshots, one per step.

Upload it later through the review web UI's upload form (§5) once the server
is running, or via the API (§4).

By default the capture agent looks for the server at `http://127.0.0.1:8420`.
To point it elsewhere (a different port, or a server on another machine), set
the `SOPFORGE_SERVER_URL` environment variable before launching
`sopforge.exe`, e.g. `$env:SOPFORGE_SERVER_URL = "http://127.0.0.1:9000"`.

---

## 4. Running the pipeline server

This is the piece that makes recording fully hands-off (§3) — set it up with
`-Autostart` (§2) so it's always running and you never think about it again.

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

**The live server now generates step text via the LLM configured in
`config/models.toml`'s `[steps]` section** (Ollama by default, or Anthropic
if you've set `anthropic = true` — see §7). Each step is one generation
attempt, round-trip-checked against the manifest's own facts — if the
configured endpoint is unreachable, the API key is missing, or the reply
doesn't hold up, that one step falls back to the template automatically.
Nothing ever retries, and a single broken/unreachable LLM can never take
down doc generation. Narration/claim-coverage (audio transcripts, `[verify]`
blockquotes) is still not wired into the live server — there's no transcript
upload endpoint yet, so `verify_claims` in the sidecar report (§6) is always
empty for now.

---

## 5. Using the review web UI

Open **http://127.0.0.1:<port>/** (or `/ui`):

- **Library page**: every past session, searchable by title/date substring,
  plus an **upload form** for the fallback path — if the server wasn't
  running when you stopped a recording (§3), pick that capture's
  `manifest.json` and its screenshots here, hit Upload, and it lands you on
  that session's processing page. No `curl`/API calls needed (§4's API
  walkthrough still works too, e.g. for scripting).
- **Session page**: a back-to-library link, the session's real title and
  date, an iframe preview of the generated doc, the sidecar report as three
  color-coded sections (see §6), a **Re-render** button, a **Delete**
  button (removes the session's files, library entry, and everything —
  irreversible, no undo), a **Downloads** list
  (docx/pdf/single-file-html/markdown-zip), and a read-only panel showing
  the current `config/models.toml`.

No JavaScript is required — the search box, upload form, re-render button,
and delete button are all plain HTML forms.

Sessions survive a server restart: a session's manifest is saved to its own
folder on disk, and the server rebuilds its session list from disk at
startup — restarting (or a crash) never makes a past session's docs
inaccessible.

---

## 6. Reading the sidecar review report

Every generated doc ships with a report (`GET /sessions/{id}/report`, the
`/review` page, or the colored sections on the `/ui/sessions/{id}` page)
listing three things a reviewer should check:

- **Template-fallback steps** (red if non-empty) — steps where the LLM's
  phrasing didn't hold up, or the configured LLM was unreachable/errored, so
  the plain template sentence was used instead. Not wrong, just less fluent
  — worth a glance to see if they're worth polishing by hand or if your LLM
  endpoint needs attention.
- **Verify claims** (yellow if non-empty) — narration claims (from an audio
  transcript) that couldn't be matched to anything in the generated
  narrative text. Appear in the doc as `> [verify] (claim-id): <original
  claim text>` blockquotes — nothing from a recording is ever silently
  dropped. Always empty today, since narration isn't wired into the live
  server yet (no transcript upload endpoint exists).
- **Empty-metadata steps** (yellow if non-empty) — steps where no UI
  Automation element info was captured at all. These render using screen
  coordinates instead of an element name — still factual, just less specific.

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

`[steps]` controls how each step's text is generated (§4); `[narrative]`
controls the not-yet-wired narration pipeline. `endpoint`/`model` point at
an Ollama (or any OpenAI-compatible chat-completions) server by default.

View the currently-active config at `GET /config` or the `/ui/sessions/{id}`
page's config panel. Edits to `config/models.toml` take effect on the next
session generated or re-rendered — no server restart needed.

### Using Anthropic instead of Ollama

Set `anthropic = true` on a section to route it to Anthropic's API instead:

```toml
[steps]
endpoint = "unused-when-anthropic-is-true"
model = "claude-sonnet-5"       # or claude-haiku-4-5-20251001, etc.
anthropic = true
```

1. `endpoint` is ignored once `anthropic = true` — Anthropic's API address
   is fixed, not configurable per-section.
2. `model` must be a real Anthropic model name.
3. Set the **`ANTHROPIC_API_KEY`** environment variable before launching
   `sopforge-server.exe` (or `python -m pipeline`). The key is read only
   from this environment variable — never from a config file, and never
   committed to the repo. In PowerShell:
   ```powershell
   $env:ANTHROPIC_API_KEY = "sk-ant-..."
   .\dist\sopforge-server\sopforge-server.exe --port 8420
   ```
   To make it stick across launches without setting it every time, set it
   as a persistent user environment variable instead (Windows Settings →
   System → About → Advanced system settings → Environment Variables), or
   set it in the same terminal session before running the scheduled task /
   shortcut that launches the server.
4. If `anthropic = true` and `ANTHROPIC_API_KEY` isn't set, every step on
   that section falls back to the template automatically (§4/§6's
   "Template-fallback steps" turns red) — it fails loudly in the server's
   logs but never breaks doc generation.

---

## 8. Known limitations

- Narration/transcription (audio transcripts, claim-coverage, `[verify]`
  blockquotes) is built and unit-tested but not wired into the live server —
  there's no transcript upload endpoint. Step generation itself (§4) is now
  LLM-backed (Ollama or Anthropic, per `config/models.toml`).
- A configured LLM endpoint that's unreachable adds real latency per step
  (a short connect-timeout wait before falling back), not just an instant
  fallback — a misconfigured/down endpoint will make generation slower, not
  incorrect.
- `-Autostart` scheduled-task registration is best-effort; some
  machines/accounts restrict it (see §2 and `phases/DEVIATIONS.md`). The
  server always works fine launched manually regardless.
- `POST /sessions` processes each session on a single background worker
  thread — sessions queue and run one at a time, not in parallel.
- Auto-upload (§3) only fires if the server is reachable at the moment you
  stop recording — it doesn't retry later or queue for when the server comes
  back up. If it fails, use the library page's upload form (§5) once the
  server is running.

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
