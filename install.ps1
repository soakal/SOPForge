<#
.SYNOPSIS
    Installs SOPForge (both the capture agent and the pipeline server) from
    already-built dist/ EXEs (scripts/build_exe.py and
    scripts/build_server_exe.py) into a target directory.

.DESCRIPTION
    Creates <InstallPath>/capture/ (sopforge.exe) and <InstallPath>/server/
    (sopforge-server.exe) as copies of dist/sopforge/ and
    dist/sopforge-server/. Session data (generated SOPs) is stored under the
    per-user profile at -SessionsRoot (default %USERPROFILE%\SOPForge\sessions),
    NOT under <InstallPath> -- the server autostart task runs unelevated and
    could not write into a Program Files install dir. An install-config.json
    records what was installed, including the resolved SessionsRoot (read back
    by uninstall.ps1 so it removes exactly what this script created, and by
    the -Autostart scheduled tasks for their launch arguments).

    -Autostart registers TWO per-user scheduled tasks (AtLogOn trigger,
    current user): "SOPForge-Server" (launches sopforge-server.exe with the
    chosen port and sessions root) and "SOPForge-Capture" (launches
    sopforge.exe, the tray capture agent) -- so after a reboot or logon,
    both the server and the always-on recording hotkey are already running
    with zero manual steps, matching capture.upload's auto-upload feature
    (which only helps if the server is actually running).

    Independent of -Autostart: if a non-default -Port is chosen, a
    persistent per-user SOPFORGE_SERVER_URL environment variable is set so
    the capture agent's auto-upload targets the right port regardless of
    how sopforge.exe is eventually launched (this install's own autostart
    task, a manual shortcut, or by hand). uninstall.ps1 removes this
    variable again, but only if its current value still matches what this
    install wrote (recorded in install-config.json) -- never a value the
    user or a different install set.

    Each entry is registered independently (one failing doesn't block the
    other): registering an AtLogOn-triggered scheduled task can be blocked
    by Task Scheduler permission policy on some machines/accounts even
    without elevation (confirmed via both Register-ScheduledTask and
    schtasks.exe on this build's own VM — see phases/DEVIATIONS.md's
    "task-12 -Autostart scheduled task" entry). When that happens, -Autostart
    automatically falls back to a Startup-folder shortcut for that EXE
    instead (Register-Autostart, below) -- a plain per-user shortcut in
    shell:startup isn't subject to the same Task Scheduler restriction, so
    this makes -Autostart self-healing on machines where scheduled tasks are
    blocked, with no manual step required (this codifies what was previously
    USER_MANUAL.md's manual-autostart walkthrough). Any Startup-folder
    shortcuts actually created are recorded in install-config.json's
    StartupShortcuts so uninstall.ps1 removes exactly those and nothing the
    user added themselves; re-running install.ps1 at the same -InstallPath
    also uses that record to safely refresh a shortcut it made before (never
    a pre-existing file it didn't create) and to clean up a shortcut a
    previous run needed but this run no longer does (e.g. Task Scheduler
    access was restored in between). Installing and running SOPForge
    WITHOUT -Autostart never depends on any of this and always works.

    Autostart defaults to ON (both scheduled tasks registered/attempted) --
    pass -NoAutostart to install without it. The plain -Autostart switch is
    still accepted for backward compatibility but is a no-op (autostart is
    already the default); it does not need to be combined with anything.

.NOTES
    If PowerShell's execution policy blocks double-clicking this script
    directly, use install.bat instead (wraps this with
    -ExecutionPolicy Bypass) -- a common Windows 11 default-policy issue,
    not specific to this script.

    The default -InstallPath (Program Files) is machine-wide and requires
    administrator rights to write to. If this process isn't already
    elevated, it relaunches itself elevated (triggering a UAC prompt) --
    see the elevation check below. Passing an -InstallPath the current user
    can already write to (e.g. under %LOCALAPPDATA%) skips that prompt
    entirely, since no elevation is actually needed for it.
#>
param(
    [string]$InstallPath = "$env:ProgramFiles\SOPForge",
    [int]$Port = 8420,
    # Where generated SOPs are stored. Defaults to the per-user profile so the
    # unelevated server can write to it (a Program Files InstallPath cannot be
    # written by the unelevated autostart task). Captured from the INVOKING
    # user's %USERPROFILE% and threaded through the elevated relaunch below, so
    # even an over-the-shoulder (different-admin) UAC still targets the logged-in
    # user's profile, not the admin's.
    [string]$SessionsRoot = "$env:USERPROFILE\SOPForge\sessions",
    [switch]$Autostart,
    [switch]$NoAutostart,
    # Internal: skips starting the apps immediately after install (see
    # Start-InstalledApp below). Not documented for end users -- it exists so
    # scripts/test_install.ps1's process-free round trips can opt out of
    # spawning real sopforge.exe/sopforge-server.exe instances.
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"

function Test-IsElevated {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($Identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# A raw StartsWith($InstallPath) is not enough to prove a path is INSIDE
# $InstallPath -- "C:\Program Files\SOPForge-dev\..." starts with the string
# "C:\Program Files\SOPForge" even though it belongs to a sibling install.
# Appending a trailing separator before comparing closes that gap: a real
# child path is always "$InstallPath\something", never a same-prefix sibling.
function ConvertTo-PathPrefix([string]$Path) {
    return $Path.TrimEnd('\') + '\'
}

# Checked before the elevation prompt below so a not-yet-built dist/ fails
# fast with a clear message instead of making the user click through a UAC
# prompt for an install that was going to fail anyway.
$RepoRoot = $PSScriptRoot
$CaptureDist = Join-Path $RepoRoot "dist\sopforge"
$ServerDist = Join-Path $RepoRoot "dist\sopforge-server"

if (-not (Test-Path $CaptureDist)) {
    throw "Not built: $CaptureDist -- run 'python scripts/build_exe.py' first."
}
if (-not (Test-Path $ServerDist)) {
    throw "Not built: $ServerDist -- run 'python scripts/build_server_exe.py' first."
}

# Only elevate if actually needed: probe write access to the target
# -InstallPath rather than assuming Program Files always requires it, so an
# explicit user-writable -InstallPath (e.g. %LOCALAPPDATA%\SOPForge, as
# before this default changed) never triggers an unnecessary UAC prompt.
if (-not (Test-IsElevated)) {
    $NeedsElevation = $false
    try {
        New-Item -ItemType Directory -Force -Path $InstallPath -ErrorAction Stop | Out-Null
        $ProbeFile = Join-Path $InstallPath ".sopforge-write-test"
        New-Item -ItemType File -Path $ProbeFile -Force -ErrorAction Stop | Out-Null
        Remove-Item -Path $ProbeFile -Force -ErrorAction SilentlyContinue
    } catch {
        $NeedsElevation = $true
    }
    if ($NeedsElevation) {
        Write-Output "'$InstallPath' requires administrator rights -- relaunching elevated (a UAC prompt will appear)..."
        # Pass the invoking user's resolved -SessionsRoot through so the
        # elevated run doesn't recompute it from the (possibly different) admin
        # account's %USERPROFILE%.
        $ElevatedArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-InstallPath", "`"$InstallPath`"", "-Port", "$Port", "-SessionsRoot", "`"$SessionsRoot`"")
        if ($NoAutostart) { $ElevatedArgs += "-NoAutostart" }
        if ($NoStart) { $ElevatedArgs += "-NoStart" }
        try {
            $ElevatedProc = Start-Process -FilePath "powershell.exe" -ArgumentList $ElevatedArgs -Verb RunAs -Wait -PassThru -ErrorAction Stop
            exit $ElevatedProc.ExitCode
        } catch {
            Write-Warning "Elevation was declined or failed to start: $_"
            exit 1
        }
    }
}

# Autostart is on by default; -NoAutostart opts out. -Autostart is accepted
# for backward compatibility but is a no-op since it's already the default.
$AutostartEffective = -not $NoAutostart

# Normalize to full paths BEFORE any path comparison or the install-config
# record: a raw-string compare (the migration below) or containment check
# (uninstall.ps1) would otherwise mishandle "<InstallPath>/sessions" (forward
# slashes), a trailing-backslash form, or a "..\" form -- the class of bug that
# could migrate data into itself and then delete it, or delete session data
# uninstall claimed to preserve. Recording the normalized SessionsRoot makes
# uninstall's containment check reliable too.
$InstallPath  = [System.IO.Path]::GetFullPath($InstallPath)
$SessionsRoot = [System.IO.Path]::GetFullPath($SessionsRoot)

New-Item -ItemType Directory -Force -Path $InstallPath | Out-Null

$CaptureInstallPath = Join-Path $InstallPath "capture"
$ServerInstallPath = Join-Path $InstallPath "server"

New-Item -ItemType Directory -Force -Path $CaptureInstallPath | Out-Null
New-Item -ItemType Directory -Force -Path $ServerInstallPath | Out-Null
New-Item -ItemType Directory -Force -Path $SessionsRoot | Out-Null

# Stop any SOPForge EXE already running from THIS install path before touching
# its files. A running .exe holds a lock on its own files (e.g.
# capture\_internal\VCRUNTIME140.dll), so an upgrade-in-place otherwise fails
# with "cannot access the file ... because it is being used by another process"
# -- exactly what a recipient hits re-running install.bat while autostart has
# the previous version running. Match on executable path (trailing separator
# via ConvertTo-PathPrefix) so a sopforge process from a DIFFERENT install or a
# dev build under a repo dist/ is never touched. Done BEFORE the session
# migration below too, so a still-running old server can't lock files it moves.
$RunningSopforge = @(
    Get-CimInstance Win32_Process -Filter "Name='sopforge-server.exe' OR Name='sopforge.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith((ConvertTo-PathPrefix $InstallPath), [StringComparison]::OrdinalIgnoreCase) }
)
foreach ($proc in $RunningSopforge) {
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Output "Stopped running '$($proc.Name)' (PID $($proc.ProcessId)) to upgrade in place."
}
if ($RunningSopforge.Count -gt 0) {
    # Stop-Process returns before the OS tears the process down and releases its
    # file locks; wait for actual exit (bounded) rather than a fixed sleep, so
    # the copy below can't lose the race and abort the install mid-upgrade.
    Wait-Process -Id ($RunningSopforge.ProcessId) -Timeout 15 -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 300
}

# Migrate session data from an older install that (incorrectly) kept it under
# <InstallPath>\sessions -- where the unelevated server couldn't write, the bug
# this location move fixes. Moving it into $SessionsRoot on upgrade means prior
# SOPs aren't orphaned, and uninstall (which now tracks the recorded
# $SessionsRoot) never deletes real data it stopped knowing about.
$LegacySessions = [System.IO.Path]::GetFullPath((Join-Path $InstallPath "sessions"))
# Migrate ONLY when legacy is a genuinely different directory from the sessions
# root, and neither contains the other -- otherwise moving files "into itself"
# errors (silently, -EA SilentlyContinue) and the delete below would then wipe
# un-moved data. Both paths are already GetFullPath-normalized above.
$SameDir = $LegacySessions.TrimEnd('\') -ieq $SessionsRoot.TrimEnd('\')
$Nested = $SessionsRoot.StartsWith((ConvertTo-PathPrefix $LegacySessions), [StringComparison]::OrdinalIgnoreCase) -or
          $LegacySessions.StartsWith((ConvertTo-PathPrefix $SessionsRoot), [StringComparison]::OrdinalIgnoreCase)
if ((Test-Path -LiteralPath $LegacySessions) -and -not $SameDir -and -not $Nested) {
    Get-ChildItem -Force -LiteralPath $LegacySessions -ErrorAction SilentlyContinue | ForEach-Object {
        Move-Item -LiteralPath $_.FullName -Destination $SessionsRoot -Force -ErrorAction SilentlyContinue
    }
    # Remove the legacy dir ONLY if migration actually emptied it -- never delete
    # data a name-collision or a transient lock left un-moved. Leave it and warn.
    if (-not (Get-ChildItem -Force -LiteralPath $LegacySessions -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $LegacySessions -Recurse -Force -ErrorAction SilentlyContinue
        Write-Output "Migrated prior session data from $LegacySessions to $SessionsRoot."
    } else {
        Write-Warning "Some legacy session data in $LegacySessions could not be migrated (a name collision or a locked file) and was LEFT IN PLACE. Move it into $SessionsRoot by hand."
    }
}

# Copy the EXEs in, retrying briefly: even after the old processes exit, AV or
# the OS can hold a transient lock on a just-released image for a moment, and
# this script runs under ErrorActionPreference=Stop, where one failed copy
# would abort the whole install mid-upgrade.
function Copy-DistWithRetry([string]$Source, [string]$Destination) {
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            Copy-Item -Path $Source -Destination $Destination -Recurse -Force -ErrorAction Stop
            return
        } catch {
            if ($attempt -eq 5) { throw }
            Start-Sleep -Milliseconds 600
        }
    }
}
Copy-DistWithRetry (Join-Path $CaptureDist "*") $CaptureInstallPath
Copy-DistWithRetry (Join-Path $ServerDist "*") $ServerInstallPath

# A non-default port means the capture agent's auto-upload (which defaults
# to http://127.0.0.1:8420) needs to be told where the server actually is,
# regardless of -Autostart -- sopforge.exe might be launched by hand, via a
# shortcut, or later autostart setup, not just by this run. A persistent
# per-user environment variable covers all of those. Recorded in
# install-config.json (the value actually written, if any) so uninstall.ps1
# can remove exactly this and nothing else -- it must never blow away some
# unrelated value the user set themselves, or one from an install at a
# different port that this install didn't create.
$ServerUrlEnvValue = $null
if ($Port -ne 8420) {
    $ServerUrlEnvValue = "http://127.0.0.1:$Port"
    [Environment]::SetEnvironmentVariable("SOPFORGE_SERVER_URL", $ServerUrlEnvValue, "User")
}

$ServerTaskName = "SOPForge-Server"
$CaptureTaskName = "SOPForge-Capture"

# If this -InstallPath was installed before, its previous install-config.json
# (about to be overwritten below) records which Startup-folder shortcuts, if
# any, THAT run created. Read it before overwriting so Register-Autostart can
# tell "a shortcut we made ourselves last time, safe to refresh" apart from
# "some other file that happens to have this name, do not touch" -- and so
# leftover shortcuts from a run that no longer needs them (this run's
# scheduled task registered fine) get removed instead of orphaned.
$PriorConfigPath = Join-Path $InstallPath "install-config.json"
$PriorStartupShortcuts = @()
if (Test-Path $PriorConfigPath) {
    try {
        $PriorStartupShortcuts = @((Get-Content $PriorConfigPath -Raw | ConvertFrom-Json).StartupShortcuts)
    } catch {
        $PriorStartupShortcuts = @()
    }
}

function Register-Autostart {
    <#
    Tries Register-ScheduledTask first; if that's blocked (confirmed on some
    machines/accounts even without elevation -- see phases/DEVIATIONS.md's
    "task-12 -Autostart scheduled task" entry, and USER_MANUAL.md Sec 2's
    "Option 1" manual workaround this codifies), falls back to a Startup-folder
    shortcut instead, since that isn't subject to the same Task Scheduler
    restriction. Returns the Startup-folder shortcut filename if that fallback
    was used, or $null if the scheduled task succeeded (or both failed).

    The shortcut filename is derived from $TaskName, not a separately
    hand-typed literal, so it can't drift out of sync with it. Before
    creating it, checks for a pre-existing file at that path: overwriting is
    only safe when $PriorStartupShortcuts (this same -InstallPath's last run)
    already recorded creating it -- anything else there is left untouched
    since SOPForge didn't create it.

    Task names ("SOPForge-Server"/"SOPForge-Capture") are fixed, not
    namespaced per -InstallPath, so a second SOPForge install at a
    different location (or this same one, at a different path) could
    otherwise silently overwrite (Register-ScheduledTask -Force) or later
    delete a DIFFERENT install's autostart task. Before registering, this
    checks whether a task by this name already exists and points at an EXE
    outside this run's -InstallPath -- if so, it's a different install's
    task, so this run falls back to a Startup-folder shortcut instead of
    clobbering it (uninstall.ps1 applies the same ownership check before
    ever unregistering a task).
    #>
    param(
        [string]$TaskName,
        [string]$Exe,
        [string]$Arguments,
        [string]$OwnInstallPath
    )
    $ShortcutName = "$TaskName.lnk"

    function New-StartupShortcutFallback {
        try {
            $StartupDir = [Environment]::GetFolderPath("Startup")
            $ShortcutPath = Join-Path $StartupDir $ShortcutName
            if ((Test-Path $ShortcutPath) -and ($PriorStartupShortcuts -notcontains $ShortcutName)) {
                throw "A file already exists at '$ShortcutPath' that this install didn't create -- refusing to overwrite it."
            }
            $Shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($ShortcutPath)
            $Shortcut.TargetPath = $Exe
            if ($Arguments) { $Shortcut.Arguments = $Arguments }
            $Shortcut.Save()
            # Write-Host, not Write-Output -- see the note above; only
            # $ShortcutName must reach the caller as this function's return.
            Write-Host "Created Startup-folder shortcut '$ShortcutName' instead."
            return $ShortcutName
        } catch {
            Write-Warning "Could not create a Startup-folder shortcut either: $_"
            Write-Warning "SOPForge is installed and works without autostart -- launch $Exe manually,"
            Write-Warning "or register the '$TaskName' scheduled task yourself (see USER_MANUAL.md Sec 2)."
            return $null
        }
    }

    $ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($ExistingTask) {
        $ExistingExe = ($ExistingTask.Actions | Select-Object -First 1).Execute
        if ($ExistingExe -and -not $ExistingExe.StartsWith((ConvertTo-PathPrefix $OwnInstallPath), [StringComparison]::OrdinalIgnoreCase)) {
            Write-Warning "A scheduled task named '$TaskName' already exists for a different SOPForge install ($ExistingExe) -- not overwriting it."
            Write-Warning "Falling back to a Startup-folder shortcut instead."
            return New-StartupShortcutFallback
        }
    }

    try {
        $Action = if ($Arguments) { New-ScheduledTaskAction -Execute $Exe -Argument $Arguments } else { New-ScheduledTaskAction -Execute $Exe }
        $Trigger = New-ScheduledTaskTrigger -AtLogOn
        Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Force -ErrorAction Stop | Out-Null
        # Write-Host (host stream), NOT Write-Output: this function's return
        # value IS its success-stream output, so a Write-Output here would be
        # returned to the caller alongside the real return value and land in
        # install-config.json's StartupShortcuts.
        Write-Host "Registered autostart scheduled task '$TaskName'."
        return $null
    } catch {
        Write-Warning "Could not register the '$TaskName' autostart scheduled task: $_"
        Write-Warning "Falling back to a Startup-folder shortcut instead."
        return New-StartupShortcutFallback
    }
}

# Computed unconditionally (not just under -Autostart) since Start-InstalledApp
# below needs them regardless of whether autostart registration ran/succeeded.
$ServerExe = Join-Path $ServerInstallPath "sopforge-server.exe"
$ServerArgs = "--port $Port --sessions-root `"$SessionsRoot`""
$CaptureExe = Join-Path $CaptureInstallPath "sopforge.exe"

$StartupShortcuts = @()
# Tracks, per task name, whether Register-Autostart actually registered a
# FRESH scheduled task THIS run (Register-ScheduledTask -Force rewrites the
# action with this run's current -Port/-SessionsRoot -- so this is true only
# when that call succeeded). A task by the right name existing at the right
# install path is NOT enough to trust starting it via Start-ScheduledTask
# later: if registration failed this run and fell back to a shortcut, a
# task from a PRIOR run at this same -InstallPath can still be sitting there
# with STALE arguments, and Start-InstalledApp must not start THAT one.
$TaskRegisteredThisRun = @{}
if ($AutostartEffective) {
    # Each entry is attempted independently -- the base install (files +
    # config, above) already succeeded regardless of what happens here, and
    # one entry's Task Scheduler permission restriction must not block the
    # other from being attempted.
    $ServerShortcut = Register-Autostart -TaskName $ServerTaskName -Exe $ServerExe `
        -Arguments $ServerArgs -OwnInstallPath $InstallPath
    $TaskRegisteredThisRun[$ServerTaskName] = -not $ServerShortcut
    if ($ServerShortcut) { $StartupShortcuts += $ServerShortcut }

    $CaptureShortcut = Register-Autostart -TaskName $CaptureTaskName -Exe $CaptureExe -Arguments $null -OwnInstallPath $InstallPath
    $TaskRegisteredThisRun[$CaptureTaskName] = -not $CaptureShortcut
    if ($CaptureShortcut) { $StartupShortcuts += $CaptureShortcut }
}

# Any shortcut a PREVIOUS run at this -InstallPath created that this run no
# longer needs (its scheduled task registered fine this time) is now
# orphaned -- remove it here rather than leaving it to autostart forever
# with no install-config.json record for a future uninstall.ps1 to find.
$StartupDirForCleanup = [Environment]::GetFolderPath("Startup")
foreach ($StaleName in ($PriorStartupShortcuts | Where-Object { $_ -and $StartupShortcuts -notcontains $_ })) {
    $StalePath = Join-Path $StartupDirForCleanup $StaleName
    if (Test-Path $StalePath) {
        Remove-Item -Path $StalePath -Force
        Write-Output "Removed now-unneeded Startup-folder shortcut '$StaleName' from a previous install."
    }
}

$InstallConfig = [ordered]@{
    InstallPath       = $InstallPath
    Port              = $Port
    SessionsRoot      = $SessionsRoot
    Autostart         = [bool]$AutostartEffective
    ServerTaskName    = $ServerTaskName
    CaptureTaskName   = $CaptureTaskName
    ServerUrlEnvValue = $ServerUrlEnvValue
    StartupShortcuts  = $StartupShortcuts
}
$InstallConfig | ConvertTo-Json | Set-Content -Path (Join-Path $InstallPath "install-config.json") -Encoding utf8

Write-Output "Installed SOPForge to $InstallPath (port $Port)."

function Test-TaskOwnedByThisInstall([string]$TaskName, [string]$OwnInstallPath) {
    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $Task) { return $false }
    $Exe = ($Task.Actions | Select-Object -First 1).Execute
    return [bool]($Exe -and $Exe.StartsWith((ConvertTo-PathPrefix $OwnInstallPath), [StringComparison]::OrdinalIgnoreCase))
}

function Start-InstalledApp {
    <#
    Starts one app immediately, in addition to whatever autostart got
    configured above for future logons -- so a fresh install is usable right
    away instead of only working after the next reboot/sign-in. Branches on
    however this entry's autostart actually ended up wired: a scheduled task
    owned by this install (Start-ScheduledTask -- the same path logon
    autostart itself uses, runs unelevated as the task principal), this
    run's own Startup-folder shortcut fallback (launched via explorer.exe so
    it de-elevates out of this UAC-elevated installer, same as double-
    clicking it would), or -- if neither exists, e.g. -NoAutostart was
    passed -- a direct process launch as a last resort. Never throws: a
    failure here must not fail an otherwise-successful install, only warn.
    #>
    param(
        [string]$TaskName,
        [string]$Exe,
        [string]$Arguments,
        [string]$ShortcutName
    )
    try {
        # Only trust a same-name/same-path task when THIS run's own
        # Register-Autostart call actually registered it fresh --
        # Register-ScheduledTask -Force always rewrites the action with this
        # run's current -Port/-SessionsRoot, so a task existing at the right
        # path is NOT enough by itself: if registration failed this run
        # (Task Scheduler policy block) and fell back to a shortcut, a task
        # from a PRIOR run at this same -InstallPath can still be sitting
        # there with STALE arguments (an old port/sessions root) -- starting
        # THAT one would silently bring up the wrong config. $TaskRegistered
        # ThisRun is set only when Register-Autostart's own return value
        # said registration succeeded; Test-TaskOwnedByThisInstall is kept
        # as a second, independent check (it re-queries live Task Scheduler
        # state) so a total registration+fallback failure -- where
        # Register-Autostart also returns falsy but nothing was actually
        # created -- can't be misread as "safe to start".
        if ($TaskRegisteredThisRun[$TaskName] -and (Test-TaskOwnedByThisInstall -TaskName $TaskName -OwnInstallPath $InstallPath)) {
            Start-ScheduledTask -TaskName $TaskName
            Write-Output "Started '$TaskName'."
            return
        }
        if ($StartupShortcuts -contains $ShortcutName) {
            $ShortcutPath = Join-Path ([Environment]::GetFolderPath("Startup")) $ShortcutName
            Start-Process -FilePath "explorer.exe" -ArgumentList "`"$ShortcutPath`""
            Write-Output "Started '$TaskName' via its Startup-folder shortcut."
            return
        }
        if (Test-IsElevated) {
            # De-elevate via a temporary shortcut launched through
            # explorer.exe -- explorer.exe always runs at the user's own
            # integrity level even when this installer process is
            # UAC-elevated, the same technique the Startup-shortcut branch
            # above already relies on. Without this, a plain Start-Process
            # here would run the app with the elevated admin token -- e.g.
            # creating session directories the user's own later unelevated
            # launches can't write into (the exact upload-500s failure mode
            # the sessions-root design otherwise prevents).
            $TempShortcutPath = Join-Path $env:TEMP "sopforge-start-$([guid]::NewGuid().ToString('N').Substring(0, 8)).lnk"
            try {
                $TempShortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($TempShortcutPath)
                $TempShortcut.TargetPath = $Exe
                if ($Arguments) { $TempShortcut.Arguments = $Arguments }
                $TempShortcut.Save()
                Start-Process -FilePath "explorer.exe" -ArgumentList "`"$TempShortcutPath`""
                Write-Output "Started '$TaskName' directly (de-elevated)."
            } finally {
                # Give explorer.exe a moment to read the shortcut before
                # deleting it.
                Start-Sleep -Milliseconds 500
                Remove-Item -Path $TempShortcutPath -Force -ErrorAction SilentlyContinue
            }
        } else {
            if ($Arguments) {
                Start-Process -FilePath $Exe -ArgumentList $Arguments | Out-Null
            } else {
                Start-Process -FilePath $Exe | Out-Null
            }
            Write-Output "Started '$TaskName' directly."
        }
    } catch {
        Write-Warning "Could not start '$TaskName' after install: $_"
        Write-Warning "It will still start automatically at next logon (if autostart is enabled), or launch $Exe by hand."
    }
}

function Wait-ForHealthy([int]$Port, [int]$TimeoutSeconds = 8) {
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

if (-not $NoStart) {
    Start-InstalledApp -TaskName $ServerTaskName -Exe $ServerExe -Arguments $ServerArgs -ShortcutName "$ServerTaskName.lnk"
    # A real health check, not a blind sleep: on an upgrade-in-place, the
    # server was just killed a few hundred ms ago (see the stop-before-copy
    # block above) and a socket that hasn't fully released yet makes the new
    # instance fail to bind and exit silently (CLAUDE.md's "Operational
    # procedures" documents exactly this). Polling for an actual 200 catches
    # that instead of reporting "Started" regardless, and also gives an
    # immediate first recording's auto-upload (capture.upload) a real head
    # start rather than a fixed guess -- still best-effort, not a hard
    # dependency (capture only calls the server on session stop, with its
    # own 10s timeout, so a slow/failed server here doesn't block Capture).
    $HealthCheckTimeoutSeconds = 8
    if (Wait-ForHealthy -Port $Port -TimeoutSeconds $HealthCheckTimeoutSeconds) {
        Write-Output "Server is responding on port $Port."
    } else {
        Write-Warning "Server did not respond on port $Port within ${HealthCheckTimeoutSeconds}s -- it may have failed to bind (see CLAUDE.md's port-rebind note) or is still starting. Check Task Manager, or start it manually: $ServerExe $ServerArgs"
    }
    Start-InstalledApp -TaskName $CaptureTaskName -Exe $CaptureExe -Arguments $null -ShortcutName "$CaptureTaskName.lnk"
}

exit 0
