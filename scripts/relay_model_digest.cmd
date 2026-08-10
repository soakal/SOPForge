@echo off
REM Pulls the latest digest commit from the cloud routine, then relays any
REM new digest file to Telegram. Run by a Windows Scheduled Task
REM ("SOPForge Model Digest Relay") on a repeating morning window.
REM NOTE: the digest routine lands its file on a digest/YYYY-MM-DD branch +
REM PR instead of committing straight to main (see DIGEST_INSTRUCTIONS.md),
REM so `git pull origin main` below legitimately brings down nothing on a
REM run where a PR is still open/unmerged. The Python script queries origin
REM directly for pending `digest/*` branches and prints a distinct notice
REM rather than a plain "nothing new to relay" in that case -- read its
REM output, don't just check the exit code.
cd /d "C:\Users\Brian\Documents\SOPForge"
git pull origin main
"C:\Users\Brian\Documents\Agentic os\nexus\venv\Scripts\python.exe" "scripts\relay_model_digest.py"
