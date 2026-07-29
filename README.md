# SOPForge

Self-hosted workflow capture → SOP generation. A Windows capture agent records
clicks, UIA element metadata, and screenshots; a local pipeline (Ollama-backed)
turns the capture into validated **docx / pdf / html / md** SOPs, reviewed in a
modern local web UI. You can add a narration **transcript** (`.txt`/`.md`) that
gets placed under the matching step — or skip the capture entirely and **build
a document straight from screenshots + a transcript**. Nothing leaves your
network.

Built autonomously — see CLAUDE.md for the contract, phases/ for acceptance
criteria. Private — see [LICENSE](LICENSE).

## Install (packaged)

Download **`SOPForge.zip`** from the repo's
[Releases](https://github.com/soakal/SOPForge/releases) page, unzip, and
**double-click `install.bat`**. It installs both EXEs and, with autostart on
by default, brings the capture tray + server up at logon. Record with
**Ctrl+Alt+R**; the SOP appears in the review UI at `http://127.0.0.1:8420/ui`.
Choose your AI from the tray → **Configuration** page (Ollama local by default,
or OpenRouter / OpenAI / Anthropic — keys read from env vars, never stored).

Use `install.bat` rather than running `install.ps1` directly — double-clicking
a `.ps1` often just opens it in a text editor, or errors "running scripts is
disabled on this system" (a Windows 11 default execution-policy thing, not
specific to this script); `install.bat` runs `install.ps1` with that policy
bypassed for you, and forwards any arguments (`install.bat -Port 9000
-NoAutostart`). The default install path, `%ProgramFiles%\SOPForge`, needs
administrator rights — **a UAC prompt will appear; accept it** to continue.
To skip that entirely, install somewhere you already own, e.g.
`install.bat -InstallPath "%LOCALAPPDATA%\SOPForge"`.

**Signing/EDR note:** the EXEs are self-signed, trusted only on the machine
that built them — on any other machine Windows will show "unknown publisher"
and endpoint security (e.g. SentinelOne) may flag or block them, since the
capture agent installs global keyboard/mouse hooks (which looks like a
keylogger). Import `scripts/sopforge-signing-cert.cer` into Trusted Root, or
have it allowlisted, to clear that.

Full walkthrough (recording, config, transcripts, distribution): see
[USER_MANUAL.md](USER_MANUAL.md).

To build the release bundle yourself: `py -3.12 scripts/build_release.py --zip`
→ `release/SOPForge.zip`. Deploy/version/signing procedure: see CLAUDE.md's
"Operational procedures".

## Kick off the build (autonomous dev loop)
```powershell
# 1. Verify auth + models inside claude: /status and /model (need sonnet-5 + fable-5)

# 2. Launch:
.\run-loop.ps1              # all phases
.\run-loop.ps1 -Phase 1     # phase 1 only
# Stop anytime:  ni STOP
```

## Multi-GPU / parallel generation

An Ollama host with multiple GPUs already auto-splits a model's layers across
them when it doesn't fit on one card — that's about *fitting* a model, not
*speed*. The actual speedup lever is `[steps] max_concurrency` in
`config/models.toml` (or the Steps card's "Max concurrency" field on
**Configuration**): it controls how many step-generation LLM calls SOPForge
dispatches at once. Raising it only helps if the Ollama **server itself** is
tuned for concurrent requests — set `OLLAMA_NUM_PARALLEL` (and optionally
`OLLAMA_SCHED_SPREAD=1` to spread load across GPUs) on the Ollama host; that's
server-side configuration this app can't set remotely. Against an untuned,
single-slot Ollama server, raising `max_concurrency` just queues requests and
risks a queued step's own per-request timeout expiring into a template
fallback it didn't need — so it defaults to `1` (strictly sequential).

## SOP Factory 2 dependency

The docx assembler (Phase 2, task-15) extends the existing `SOPBuilder` engine
from the private repo `soakal/SOP-Factory`, expected at
`C:\Users\Brian\Documents\SOP_Factory_2` (`gh repo clone soakal/SOP-Factory
SOP_Factory_2`). It is **imported at runtime via `sys.path`
(`src/pipeline/docx_assembler.py`), never copied into this repo** — that
directory is a full working project (active jobs, per-client archives with real
photos/documents, its own git history, install scripts), not a clean library, so
vendoring it wholesale would leak proprietary business content into SOPForge's
history. Override the path with the `SOPFORGE_SOP_FACTORY_2_DIR` env var if it's
cloned somewhere else.
