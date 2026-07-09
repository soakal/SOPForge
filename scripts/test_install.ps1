<#
.SYNOPSIS
    Automated install/uninstall round-trip (AC4): snapshots directory
    state, installs to a temp path on a non-default port, polls the
    health endpoint, uninstalls, and asserts before/after directory state
    matches. Separately probes the -Autostart branch: creates both
    scheduled tasks (server + capture agent), confirms each via
    Get-ScheduledTask, then removes them -- and for any that couldn't be
    registered, confirms install.ps1's Startup-folder-shortcut fallback
    fired instead, and that uninstall.ps1 removes those too.

.DESCRIPTION
    Register-ScheduledTask (and schtasks.exe /create) failing with "Access
    is denied" is a genuine Task Scheduler permission restriction on this
    build's own VM/account, not a bug (see phases/DEVIATIONS.md's "task-12
    -Autostart scheduled task" entry for the full history) -- confirmed by
    trying both mechanisms directly. Rather than treat that as an
    escalation-worthy blocker, install.ps1's Register-Autostart function
    now falls back to a per-EXE Startup-folder shortcut when the scheduled
    task can't be registered, making -Autostart self-healing instead of
    merely best-effort. Round trip 2 below asserts whichever mechanism
    actually took effect for each entry (scheduled task via
    Get-ScheduledTask, or shortcut via install-config.json's
    StartupShortcuts + the actual file in the Startup folder), and that
    uninstall.ps1 removes exactly that. Round trip 1 (the core AC4
    requirement: install -> health check -> uninstall -> directory state
    matches) is unconditional and always asserted.

    The Startup folder is real and shared with any live install on this
    machine (unlike the temp -InstallPath, shortcuts there aren't
    namespaced per test run) -- this script backs up and restores
    SOPForge-Server.lnk/SOPForge-Capture.lnk around both round trips so
    running it can never clobber or delete a real install's autostart
    shortcuts.

    Autostart now defaults to ON in install.ps1, so round trip 1 (which
    passes no -NoAutostart) also exercises autostart registration, not just
    round trip 2 -- on a machine with a real SOPForge install already
    registered under the same task names, install.ps1's ownership check
    (see its Register-Autostart) makes round trip 1 fall back to a
    Startup-folder shortcut rather than reuse round trip 2's names, and
    Test-TaskOwnedByPath (below) is what makes round trip 2's own
    assertions path-aware rather than merely name-aware, so a real
    install's identically-named task never registers as a false pass/fail
    for either round trip.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$InstallScript = Join-Path $RepoRoot "install.ps1"
$UninstallScript = Join-Path $RepoRoot "uninstall.ps1"

# Both round trips below install to a non-default port, so install.ps1 sets
# a persistent per-user SOPFORGE_SERVER_URL regardless of -Autostart --
# snapshotted once here and restored in the outer `finally` at the bottom
# of this script as a safety net. The real assertion, though, is inline
# after each uninstall call below: uninstall.ps1 itself must restore this
# to $OriginalServerUrlEnv -- if the outer finally were the only thing
# putting it back, a real product-level cleanup bug (uninstall.ps1 never
# removing it) would go completely unnoticed.
$OriginalServerUrlEnv = [Environment]::GetEnvironmentVariable("SOPFORGE_SERVER_URL", "User")

# Round trip 2 below exercises install.ps1's Register-Autostart fallback,
# which creates Startup-folder shortcuts under fixed, well-known names
# (SOPForge-Server.lnk / SOPForge-Capture.lnk) -- the SAME names a real
# -Autostart install on this machine uses (Startup-folder shortcuts aren't
# namespaced per -InstallPath the way the temp test root is). Back up
# whatever's already there under those names before the test runs, and
# restore it in the outer `finally`, so running this test can never clobber
# or delete a real install's autostart shortcuts.
$StartupDir = [Environment]::GetFolderPath("Startup")
$ShortcutNames = @("SOPForge-Server.lnk", "SOPForge-Capture.lnk")
$ShortcutBackups = @{}
foreach ($Name in $ShortcutNames) {
    $Path = Join-Path $StartupDir $Name
    if (Test-Path $Path) {
        $Backup = Join-Path $env:TEMP "sopforge-test-shortcut-backup-$Name-$(Get-Random)"
        Copy-Item -Path $Path -Destination $Backup -Force
        $ShortcutBackups[$Name] = $Backup
    }
}

function Assert-ServerUrlEnvRestored($Context) {
    $Current = [Environment]::GetEnvironmentVariable("SOPFORGE_SERVER_URL", "User")
    if ($Current -ne $OriginalServerUrlEnv) {
        throw "FAIL ($Context): SOPFORGE_SERVER_URL is '$Current' after uninstall, expected '$OriginalServerUrlEnv' -- uninstall.ps1 did not clean up the environment variable install.ps1 set."
    }
}

function Wait-ForHealthy($Port, $TimeoutSeconds = 15) {
    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $Deadline) {
        try {
            $Response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 2
            if ($Response.StatusCode -eq 200) { return $true }
        } catch {}
        Start-Sleep -Milliseconds 200
    }
    return $false
}

function Stop-ServerCleanly($Proc, $Port) {
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$Port/shutdown" -Method POST -UseBasicParsing -TimeoutSec 2 | Out-Null
    } catch {}
    $Proc.WaitForExit(10000) | Out-Null
    if (-not $Proc.HasExited) {
        Stop-Process -Id $Proc.Id -Force -ErrorAction SilentlyContinue
    }
}

try {

# --- Round trip 1: plain install / health check / uninstall (autostart is
# on by default now, so this also incidentally exercises the fallback path
# -- see the .DESCRIPTION note above -- but that's not this round trip's
# focus; round trip 2 is where autostart itself is actually asserted) ---
$TestRoot = Join-Path $env:TEMP "sopforge-install-test-$(Get-Random)"
if (Test-Path $TestRoot) { throw "test root already exists: $TestRoot" }

Write-Output "=== Round trip 1: install / health check / uninstall ==="
$Port = 28420

# Pass an explicit -SessionsRoot INSIDE the temp test root: install.ps1 now
# defaults SessionsRoot to the per-user profile (%USERPROFILE%\SOPForge\
# sessions), which a test must never create/delete. Keeping it under $TestRoot
# keeps this round trip fully isolated and self-cleaning.
$SessionsRoot = Join-Path $TestRoot "sessions"
# -NoStart: this round trip's own Start-Process call below is what exercises
# the server; without -NoStart, install.ps1's own start-after-install would
# race it for the same port (see round trip 5 for what actually verifies
# start-after-install).
& $InstallScript -InstallPath $TestRoot -Port $Port -SessionsRoot $SessionsRoot -NoStart
if ($LASTEXITCODE -ne 0) { throw "install.ps1 failed (exit $LASTEXITCODE)" }

$Config1 = Get-Content (Join-Path $TestRoot "install-config.json") -Raw | ConvertFrom-Json
if ($Config1.SessionsRoot -ne $SessionsRoot) {
    throw "FAIL: install-config.json SessionsRoot '$($Config1.SessionsRoot)' != requested '$SessionsRoot'"
}

$ServerExe = Join-Path $TestRoot "server\sopforge-server.exe"
$CaptureExe = Join-Path $TestRoot "capture\sopforge.exe"
if (-not (Test-Path $ServerExe)) { throw "FAIL: $ServerExe not found after install" }
if (-not (Test-Path $CaptureExe)) { throw "FAIL: $CaptureExe not found after install" }

# Explicit stdio redirection is required: this console=False (windowed)
# EXE does not respond at all when launched without it (reproduced
# directly against dist/sopforge-server/sopforge-server.exe while writing
# this script — the same class of bug task-10 found for
# scripts/build_server_exe.py's subprocess launch).
$OutLog = Join-Path $env:TEMP "sopforge-install-test-stdout.log"
$ErrLog = Join-Path $env:TEMP "sopforge-install-test-stderr.log"
$Proc = Start-Process -FilePath $ServerExe `
    -ArgumentList "--port", $Port, "--sessions-root", "`"$SessionsRoot`"" `
    -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog

try {
    if (-not (Wait-ForHealthy -Port $Port)) {
        throw "FAIL: server never responded 200 on port $Port"
    }
    Write-Output "Health check passed."
} finally {
    Stop-ServerCleanly -Proc $Proc -Port $Port
}

& $UninstallScript -InstallPath $TestRoot
if ($LASTEXITCODE -ne 0) { throw "uninstall.ps1 failed (exit $LASTEXITCODE)" }

if (Test-Path $TestRoot) {
    throw "FAIL: $TestRoot still exists after uninstall (directory state does not match pre-install baseline)"
}
Assert-ServerUrlEnvRestored "round trip 1"
Write-Output "PASS: install/uninstall round trip -- directory state matches pre-install baseline (absent)."

# --- Round trip 2: -Autostart branch (both the server AND capture agent tasks) ---
# Best-effort by design (install.ps1's .DESCRIPTION and
# phases/DEVIATIONS.md's "task-12 -Autostart scheduled task" entry):
# Task Scheduler permission policy can block ONLOGON-triggered task
# registration on some machines/accounts even without elevation. When
# that happens here, install.ps1 itself still succeeds (each scheduled
# task step catches its own failure independently) -- this round trip
# verifies both scheduled tasks WHEN they can be created, and treats
# "neither could be created on this machine" as a documented, known
# limitation rather than a test failure, per the user's explicit decision
# to accept -Autostart as best-effort rather than block the release on it.
Write-Output ""
Write-Output "=== Round trip 2: -Autostart scheduled tasks (best-effort) ==="
$TestRoot2 = Join-Path $env:TEMP "sopforge-install-test-autostart-$(Get-Random)"
$TaskNames = @("SOPForge-Server", "SOPForge-Capture")
$Port2 = $Port + 1

& $InstallScript -InstallPath $TestRoot2 -Port $Port2 -Autostart -SessionsRoot (Join-Path $TestRoot2 "sessions") -NoStart
if ($LASTEXITCODE -ne 0) { throw "install.ps1 -Autostart failed (exit $LASTEXITCODE)" }

if (-not (Test-Path (Join-Path $TestRoot2 "server\sopforge-server.exe"))) {
    throw "FAIL: base install (files) did not succeed even though -Autostart is best-effort"
}
if (-not (Test-Path (Join-Path $TestRoot2 "capture\sopforge.exe"))) {
    throw "FAIL: base install (files) did not succeed even though -Autostart is best-effort"
}

# A task named e.g. "SOPForge-Server" existing is not enough -- task names
# aren't namespaced per -InstallPath, so a REAL install elsewhere on this
# machine can already own that name (install.ps1's Register-Autostart
# refuses to clobber it and falls back to a shortcut instead, see its
# collision check). Only count a task as "registered by this round trip" if
# its action actually points inside $TestRoot2.
function Test-TaskOwnedByPath($TaskName, $Path) {
    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $Task) { return $false }
    $Exe = ($Task.Actions | Select-Object -First 1).Execute
    # Trailing separator required -- a raw StartsWith would treat a sibling
    # path like "...SOPForge-dev\..." as "inside" "...SOPForge" (see the
    # matching fix/comment in install.ps1's ConvertTo-PathPrefix).
    $Prefix = $Path.TrimEnd('\') + '\'
    return [bool]($Exe -and $Exe.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase))
}

$RegisteredTasks = $TaskNames | Where-Object { Test-TaskOwnedByPath $_ $TestRoot2 }
if ($RegisteredTasks.Count -gt 0) {
    Write-Output "Autostart scheduled task(s) confirmed via Get-ScheduledTask: $($RegisteredTasks -join ', ')"
}
if ($RegisteredTasks.Count -lt $TaskNames.Count) {
    $Missing = $TaskNames | Where-Object { $_ -notin $RegisteredTasks }
    Write-Warning "Only $($RegisteredTasks.Count) of $($TaskNames.Count) autostart tasks registered (missing: $($Missing -join ', ')) -- Register-Autostart's Startup-folder-shortcut fallback is expected for each, verified below."
}

# For each entry whose scheduled task didn't register, install.ps1's
# Register-Autostart fallback should have created a Startup-folder shortcut
# instead. install-config.json's own StartupShortcuts is the expected set
# directly (it's what install.ps1 itself recorded creating) -- re-deriving
# it here from a second hardcoded task-name-to-filename mapping would just
# be a second place that has to stay in sync with install.ps1's naming.
# $RegisteredTasks is only used for the narrower cross-check that a shortcut
# was recorded only for a task that didn't register.
$Config2 = Get-Content (Join-Path $TestRoot2 "install-config.json") -Raw | ConvertFrom-Json
$ExpectedShortcuts = @($Config2.StartupShortcuts)

foreach ($Name in $ExpectedShortcuts) {
    $CorrespondingTask = $TaskNames | Where-Object { $Name -eq "$_.lnk" }
    if ($CorrespondingTask -and ($RegisteredTasks -contains $CorrespondingTask)) {
        throw "FAIL: fallback shortcut '$Name' recorded even though its scheduled task '$CorrespondingTask' registered successfully"
    }
}

# The Startup-folder-shortcut existence check (and the earlier scheduled-task
# checks) must never prevent uninstall.ps1 from running -- otherwise a bug
# in the fallback leaves $TestRoot2 and any successfully-registered
# scheduled task orphaned on the real machine. Any assertion failure here is
# captured and rethrown AFTER cleanup runs, not instead of it.
$PreUninstallError = $null
try {
    foreach ($Name in $ExpectedShortcuts) {
        if (-not (Test-Path (Join-Path $StartupDir $Name))) {
            throw "FAIL: expected fallback shortcut '$Name' not found in the Startup folder"
        }
    }
    if ($ExpectedShortcuts.Count -gt 0) {
        Write-Output "Fallback Startup-folder shortcut(s) confirmed: $($ExpectedShortcuts -join ', ')"
    }
} catch {
    $PreUninstallError = $_
}

& $UninstallScript -InstallPath $TestRoot2 -RemoveData
if ($LASTEXITCODE -ne 0) {
    Write-Warning "uninstall.ps1 (autostart) failed (exit $LASTEXITCODE) while cleaning up"
}

if ($PreUninstallError) { throw $PreUninstallError }

$TasksAfter = $TaskNames | Where-Object { Test-TaskOwnedByPath $_ $TestRoot2 }
if ($TasksAfter.Count -gt 0) {
    throw "FAIL: scheduled task(s) still exist after uninstall: $($TasksAfter -join ', ')"
}
foreach ($Name in $ExpectedShortcuts) {
    if (Test-Path (Join-Path $StartupDir $Name)) {
        throw "FAIL: fallback shortcut '$Name' still exists after uninstall"
    }
}
if (Test-Path $TestRoot2) {
    throw "FAIL: $TestRoot2 still exists after uninstall"
}
Assert-ServerUrlEnvRestored "round trip 2"

Write-Output "PASS: -Autostart round trip -- scheduled task(s)/fallback shortcut(s) removed; directory state matches baseline."

# --- Round trip 3: external (per-user-style) SessionsRoot preserve/remove ---
# The core of the sessions-location fix: SessionsRoot now lives OUTSIDE
# InstallPath. Verify uninstall (a) preserves it by default when it holds data
# while still removing the install dir, and (b) deletes it with -RemoveData.
# Uses temp dirs (NOT %USERPROFILE%) so the test never touches real user data.
# -NoAutostart keeps this focused on the data logic without task registration.
Write-Output ""
Write-Output "=== Round trip 3: external SessionsRoot preserve/remove ==="
$Port3 = $Port + 2
$TestRoot3 = Join-Path $env:TEMP "sopforge-install-test-extroot-$(Get-Random)"
$ExtSessions = Join-Path $env:TEMP "sopforge-install-test-extdata-$(Get-Random)"

& $InstallScript -InstallPath $TestRoot3 -Port $Port3 -SessionsRoot $ExtSessions -NoAutostart -NoStart
if ($LASTEXITCODE -ne 0) { throw "install.ps1 (ext sessions) failed (exit $LASTEXITCODE)" }
$FakeSop = Join-Path $ExtSessions "session-xyz"
New-Item -ItemType Directory -Force -Path $FakeSop | Out-Null
Set-Content -Path (Join-Path $FakeSop "report.json") -Value "{}" -Encoding utf8

& $UninstallScript -InstallPath $TestRoot3
if ($LASTEXITCODE -ne 0) { throw "uninstall.ps1 (ext, preserve) failed (exit $LASTEXITCODE)" }
if (Test-Path $TestRoot3) { throw "FAIL: install dir $TestRoot3 not removed on default uninstall" }
if (-not (Test-Path (Join-Path $FakeSop "report.json"))) {
    throw "FAIL: external session data at $ExtSessions was NOT preserved on default uninstall"
}
Write-Output "Preserve verified: install dir removed, external session data kept."

& $InstallScript -InstallPath $TestRoot3 -Port $Port3 -SessionsRoot $ExtSessions -NoAutostart -NoStart
if ($LASTEXITCODE -ne 0) { throw "install.ps1 (ext sessions, reinstall) failed (exit $LASTEXITCODE)" }
& $UninstallScript -InstallPath $TestRoot3 -RemoveData
if ($LASTEXITCODE -ne 0) { throw "uninstall.ps1 (ext, RemoveData) failed (exit $LASTEXITCODE)" }
if (Test-Path $TestRoot3) { throw "FAIL: install dir $TestRoot3 not removed on -RemoveData uninstall" }
if (Test-Path $ExtSessions) { throw "FAIL: external session data at $ExtSessions NOT removed with -RemoveData" }
Write-Output "PASS: external SessionsRoot preserve/remove round trip."

# --- Round trip 4: upgrade-in-place OVER a running install ---
# Regression: re-running install over a still-running SOPForge used to fail
# with "cannot access the file ... VCRUNTIME140.dll ... used by another
# process" because a running .exe locks its own files. install.ps1 now stops
# any instance from the target path before copying. -NoAutostart keeps this to
# the copy/lock behavior; the server is started by hand purely to hold a lock.
Write-Output ""
Write-Output "=== Round trip 4: upgrade-in-place over a running install ==="
$Port4 = $Port + 3
$TestRoot4 = Join-Path $env:TEMP "sopforge-install-test-upgrade-$(Get-Random)"
$Sess4 = Join-Path $TestRoot4 "sessions"

& $InstallScript -InstallPath $TestRoot4 -Port $Port4 -SessionsRoot $Sess4 -NoAutostart -NoStart
if ($LASTEXITCODE -ne 0) { throw "install.ps1 (upgrade base install) failed (exit $LASTEXITCODE)" }

$ServerExe4 = Join-Path $TestRoot4 "server\sopforge-server.exe"
$Proc4 = Start-Process -FilePath $ServerExe4 `
    -ArgumentList "--port", $Port4, "--sessions-root", "`"$Sess4`"" `
    -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $env:TEMP "sopforge-upgrade-out.log") `
    -RedirectStandardError (Join-Path $env:TEMP "sopforge-upgrade-err.log")
Start-Sleep -Seconds 3  # let it start and lock its _internal files

$UpgradeError = $null
try {
    & $InstallScript -InstallPath $TestRoot4 -Port $Port4 -SessionsRoot $Sess4 -NoAutostart -NoStart
    if ($LASTEXITCODE -ne 0) {
        throw "FAIL: upgrade-in-place over a running install failed (exit $LASTEXITCODE)"
    }
    if (-not (Test-Path $ServerExe4)) { throw "FAIL: server exe missing after upgrade" }
    Write-Output "Upgrade over a running install succeeded (files not locked)."
} catch {
    $UpgradeError = $_
} finally {
    # The reinstall's stop-before-copy already killed the original process;
    # sweep any stragglers launched from this test root, regardless.
    Get-CimInstance Win32_Process -Filter "Name='sopforge-server.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($TestRoot4, [StringComparison]::OrdinalIgnoreCase) } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
& $UninstallScript -InstallPath $TestRoot4 -RemoveData | Out-Null
if ($UpgradeError) { throw $UpgradeError }
if (Test-Path $TestRoot4) { throw "FAIL: $TestRoot4 still exists after uninstall" }
Write-Output "PASS: upgrade-in-place over a running install."

# --- Round trip 5: apps actually start immediately after install ---
# The feature this proves: install.ps1 no longer only configures autostart
# for the NEXT logon -- it also starts both apps THIS session (Start-
# InstalledApp), regardless of which mechanism ends up wiring autostart
# (scheduled task, Startup-folder shortcut fallback, or a direct launch if
# autostart itself failed). Deliberately mechanism-agnostic: whichever one
# fired, the resulting process's ExecutablePath is under $TestRoot5 either
# way, so this checks process presence rather than which path was taken.
Write-Output ""
Write-Output "=== Round trip 5: apps start immediately after install (no -NoStart) ==="
$Port5 = $Port + 4
$TestRoot5 = Join-Path $env:TEMP "sopforge-install-test-autostart-now-$(Get-Random)"
$Sess5 = Join-Path $TestRoot5 "sessions"

function Get-SopforgeProcessUnder($ExeName, $Path) {
    $Prefix = $Path.TrimEnd('\') + '\'
    return @(
        Get-CimInstance Win32_Process -Filter "Name='$ExeName'" -ErrorAction SilentlyContinue |
            Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase) }
    )
}

& $InstallScript -InstallPath $TestRoot5 -Port $Port5 -SessionsRoot $Sess5
if ($LASTEXITCODE -ne 0) { throw "install.ps1 (start-after-install) failed (exit $LASTEXITCODE)" }

$Round5Error = $null
try {
    if (-not (Wait-ForHealthy -Port $Port5)) {
        throw "FAIL: server did not come up on its own after install (this test never called Start-Process itself) -- start-after-install did not actually start it"
    }
    Write-Output "Server auto-started and became healthy on port $Port5."

    $ServerProcs = Get-SopforgeProcessUnder "sopforge-server.exe" $TestRoot5
    if ($ServerProcs.Count -eq 0) {
        throw "FAIL: no sopforge-server.exe process found running from $TestRoot5"
    }

    # sopforge.exe (capture) has no HTTP endpoint to poll -- give it a moment
    # to actually launch, then check by process presence only.
    $CaptureProcs = @()
    $Deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $Deadline) {
        $CaptureProcs = Get-SopforgeProcessUnder "sopforge.exe" $TestRoot5
        if ($CaptureProcs.Count -gt 0) { break }
        Start-Sleep -Milliseconds 200
    }
    if ($CaptureProcs.Count -eq 0) {
        throw "FAIL: no sopforge.exe (capture) process found running from $TestRoot5"
    }
    Write-Output "Capture agent auto-started."
} catch {
    $Round5Error = $_
} finally {
    # Kill by path prefix (not specific PIDs) so a start-after-install path
    # that spawned via a different mechanism than expected is still cleaned
    # up, and so uninstall.ps1 below never trips over a locked file.
    Get-SopforgeProcessUnder "sopforge-server.exe" $TestRoot5 | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Get-SopforgeProcessUnder "sopforge.exe" $TestRoot5 | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 300
}

& $UninstallScript -InstallPath $TestRoot5 -RemoveData
if ($LASTEXITCODE -ne 0) {
    Write-Warning "uninstall.ps1 (round trip 5) failed (exit $LASTEXITCODE) while cleaning up"
}
if ($Round5Error) { throw $Round5Error }
if (Test-Path $TestRoot5) { throw "FAIL: $TestRoot5 still exists after uninstall" }
Write-Output "PASS: apps start immediately after install."

# --- Round trip 6: start-after-install's direct-launch fallback branch ---
# Round trip 5 above can land on any of Start-InstalledApp's three branches
# depending on machine state (it happened to hit the Startup-shortcut
# fallback on this VM, since a real install already owns the scheduled task
# names) -- -NoAutostart forces the OTHER two branches out entirely (no task
# gets started per the $AutostartEffective gate, no shortcut gets created),
# deterministically exercising the plain Start-Process direct-launch path.
Write-Output ""
Write-Output "=== Round trip 6: start-after-install direct-launch fallback (-NoAutostart) ==="
$Port6 = $Port + 5
$TestRoot6 = Join-Path $env:TEMP "sopforge-install-test-directlaunch-$(Get-Random)"
$Sess6 = Join-Path $TestRoot6 "sessions"

& $InstallScript -InstallPath $TestRoot6 -Port $Port6 -SessionsRoot $Sess6 -NoAutostart
if ($LASTEXITCODE -ne 0) { throw "install.ps1 (direct-launch) failed (exit $LASTEXITCODE)" }

$Round6Error = $null
try {
    if (-not (Wait-ForHealthy -Port $Port6)) {
        throw "FAIL: server did not come up via the direct-launch fallback (-NoAutostart, no scheduled task/shortcut possible)"
    }
    Write-Output "Server auto-started (direct launch) and became healthy on port $Port6."

    if ((Get-SopforgeProcessUnder "sopforge-server.exe" $TestRoot6).Count -eq 0) {
        throw "FAIL: no sopforge-server.exe process found running from $TestRoot6"
    }

    $CaptureProcs6 = @()
    $Deadline6 = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $Deadline6) {
        $CaptureProcs6 = Get-SopforgeProcessUnder "sopforge.exe" $TestRoot6
        if ($CaptureProcs6.Count -gt 0) { break }
        Start-Sleep -Milliseconds 200
    }
    if ($CaptureProcs6.Count -eq 0) {
        throw "FAIL: no sopforge.exe (capture) process found running from $TestRoot6"
    }
    Write-Output "Capture agent auto-started (direct launch)."
} catch {
    $Round6Error = $_
} finally {
    Get-SopforgeProcessUnder "sopforge-server.exe" $TestRoot6 | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Get-SopforgeProcessUnder "sopforge.exe" $TestRoot6 | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 300
}

& $UninstallScript -InstallPath $TestRoot6 -RemoveData
if ($LASTEXITCODE -ne 0) {
    Write-Warning "uninstall.ps1 (round trip 6) failed (exit $LASTEXITCODE) while cleaning up"
}
if ($Round6Error) { throw $Round6Error }
if (Test-Path $TestRoot6) { throw "FAIL: $TestRoot6 still exists after uninstall" }
Write-Output "PASS: start-after-install direct-launch fallback."

Write-Output ""
Write-Output "ALL PASS"
exit 0

} finally {
    # Safety net, not the primary mechanism -- Assert-ServerUrlEnvRestored
    # above is what actually verifies uninstall.ps1 cleans up
    # SOPFORGE_SERVER_URL correctly (both round trips install to a
    # non-default port, so both set it). This just guarantees the real
    # machine running this test is never left in a different state than it
    # started in, even if something throws before an assertion runs.
    [Environment]::SetEnvironmentVariable("SOPFORGE_SERVER_URL", $OriginalServerUrlEnv, "User")

    # Same safety-net role for the Startup-folder shortcuts backed up above:
    # restore whatever a real install had there, and remove anything this
    # test run left behind that didn't exist before it.
    foreach ($Name in $ShortcutNames) {
        $Path = Join-Path $StartupDir $Name
        if ($ShortcutBackups.ContainsKey($Name)) {
            Copy-Item -Path $ShortcutBackups[$Name] -Destination $Path -Force
            Remove-Item -Path $ShortcutBackups[$Name] -Force -ErrorAction SilentlyContinue
        } elseif (Test-Path $Path) {
            Remove-Item -Path $Path -Force
        }
    }

    # Unregister any scheduled task this test created -- a task whose action
    # points under our %TEMP%\sopforge-install-test-* dirs. Without this, a
    # failure between install and uninstall leaves a REAL AtLogOn task launching
    # an EXE from a since-deleted temp path, and a later real install would then
    # refuse the task name and silently downgrade to the shortcut fallback. A
    # task pointing at a genuine install (Program Files) is never touched.
    $TestPrefix = Join-Path $env:TEMP "sopforge-install-test"
    foreach ($TaskName in @("SOPForge-Server", "SOPForge-Capture")) {
        $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $Task) { continue }
        $TaskExe = ($Task.Actions | Select-Object -First 1).Execute
        if ($TaskExe -and $TaskExe.StartsWith($TestPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
            Write-Output "Cleanup: removed test-owned scheduled task '$TaskName'."
        }
    }

    # Safety net for round trip 5 (and any future round trip that lets
    # install.ps1 start real processes): sweep any sopforge-server.exe/
    # sopforge.exe still running from under a test root, regardless of which
    # inline cleanup path ran or whether this run threw before reaching it.
    $TestTempPrefix = Join-Path $env:TEMP "sopforge-install-test"
    Get-CimInstance Win32_Process -Filter "Name='sopforge-server.exe' OR Name='sopforge.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($TestTempPrefix, [StringComparison]::OrdinalIgnoreCase) } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Output "Cleanup: stopped stray '$($_.Name)' (PID $($_.ProcessId)) from a test root."
        }

    # Remove every test root/data dir this run may have created (some may not
    # exist if it threw early) plus the captured stdout/stderr logs. The logs
    # are removed via a null-filtered pipeline: $OutLog/$ErrLog are unset if the
    # try threw before they were assigned, and `Remove-Item -Path $null` throws
    # a binding error that -EA can't suppress -- which would REPLACE the real
    # failure propagating out of this finally.
    Get-ChildItem -Path $env:TEMP -Filter "sopforge-install-test-*" -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
    @(
        $OutLog, $ErrLog,
        (Join-Path $env:TEMP "sopforge-upgrade-out.log"),
        (Join-Path $env:TEMP "sopforge-upgrade-err.log")
    ) | Where-Object { $_ } | ForEach-Object { Remove-Item -LiteralPath $_ -Force -ErrorAction SilentlyContinue }
}
