# SOPForge

Self-hosted workflow capture → SOP generation. Windows capture agent records clicks,
UIA element metadata, and screenshots; a local pipeline (Ollama-backed) turns the
capture into validated docx/pdf/html/md SOPs. Nothing leaves your network.

Built autonomously — see CLAUDE.md for the contract, phases/ for acceptance criteria.
See [USER_MANUAL.md](USER_MANUAL.md) for how to actually run it. Private —
see [LICENSE](LICENSE).

## Kick off the build
```powershell
# 1. Verify auth + models inside claude: /status and /model (need sonnet-5 + fable-5)

# 2. Launch:
.\run-loop.ps1              # all phases
.\run-loop.ps1 -Phase 1     # phase 1 only
# Stop anytime:  ni STOP
```

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
