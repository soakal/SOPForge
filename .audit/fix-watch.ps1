# Wait for the running -Held fix loop; if it died before pushing, relaunch it in build mode.
# Committed items are skipped on resume, and fix-loop.ps1 now sleeps through usage limits itself.
Set-Location (Split-Path $PSScriptRoot -Parent)
$log = '.audit/fix/loop-held.log'
$p = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*fix-loop.ps1*-Held*' -and $_.ProcessId -ne $PID }
if ($p) { Wait-Process -Id $p.ProcessId -ErrorAction SilentlyContinue }
for ($n = 1; $n -le 5; $n++) {
    if ((Get-Content $log -Raw) -match 'audit-fixes -> audit-fixes|Everything up-to-date') { "watch: loop finished"; break }
    "watch: loop exited early, relaunch $n at $(Get-Date -Format HH:mm)"
    git stash push -q -u -m "fix-watch: half-done build $n" -- src tests config phases   # set aside, recoverable
    pwsh -NoProfile -File .audit/fix-loop.ps1 -Held -Phase build -RunTag h *>> $log
}
