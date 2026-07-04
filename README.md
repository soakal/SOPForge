# SOPForge

Self-hosted workflow capture → SOP generation. Windows capture agent records clicks,
UIA element metadata, and screenshots; a local pipeline (Ollama-backed) turns the
capture into validated docx/pdf/html/md SOPs. Nothing leaves your network.

Built autonomously — see CLAUDE.md for the contract, phases/ for acceptance criteria.

## Kick off the build
```powershell
# 1. Copy the SOP Factory 2 engine in as the Phase 2 baseline:
robocopy "C:\Users\Brian\Documents\SOP_Factory_2" .\src\pipeline /E /XD Active Output __pycache__
git add -A; git commit -m "baseline: SOP Factory 2 engine"; git push -u origin main

# 2. Verify auth + models inside claude: /status and /model (need sonnet-5 + fable-5)

# 3. Launch:
.\run-loop.ps1              # all phases
.\run-loop.ps1 -Phase 1     # phase 1 only
# Stop anytime:  ni STOP
```
