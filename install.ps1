<#
.SYNOPSIS
    Installs SOPForge (both the capture agent and the pipeline server) from
    already-built dist/ EXEs (scripts/build_exe.py and
    scripts/build_server_exe.py) into a target directory.

.DESCRIPTION
    Creates <InstallPath>/capture/ (sopforge.exe) and <InstallPath>/server/
    (sopforge-server.exe) as copies of dist/sopforge/ and
    dist/sopforge-server/, plus an empty <InstallPath>/sessions/ directory
    and an install-config.json recording what was installed (read back by
    uninstall.ps1 so it removes exactly what this script created, and by
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
    [switch]$Autostart,
    [switch]$NoAutostart
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
        $ElevatedArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-InstallPath", "`"$InstallPath`"", "-Port", "$Port")
        if ($NoAutostart) { $ElevatedArgs += "-NoAutostart" }
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

New-Item -ItemType Directory -Force -Path $InstallPath | Out-Null

$CaptureInstallPath = Join-Path $InstallPath "capture"
$ServerInstallPath = Join-Path $InstallPath "server"
$SessionsRoot = Join-Path $InstallPath "sessions"

New-Item -ItemType Directory -Force -Path $CaptureInstallPath | Out-Null
New-Item -ItemType Directory -Force -Path $ServerInstallPath | Out-Null
New-Item -ItemType Directory -Force -Path $SessionsRoot | Out-Null

Copy-Item -Path (Join-Path $CaptureDist "*") -Destination $CaptureInstallPath -Recurse -Force
Copy-Item -Path (Join-Path $ServerDist "*") -Destination $ServerInstallPath -Recurse -Force

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

$StartupShortcuts = @()
if ($AutostartEffective) {
    # Each entry is attempted independently -- the base install (files +
    # config, above) already succeeded regardless of what happens here, and
    # one entry's Task Scheduler permission restriction must not block the
    # other from being attempted.
    $ServerExe = Join-Path $ServerInstallPath "sopforge-server.exe"
    $ServerShortcut = Register-Autostart -TaskName $ServerTaskName -Exe $ServerExe `
        -Arguments "--port $Port --sessions-root `"$SessionsRoot`"" -OwnInstallPath $InstallPath
    if ($ServerShortcut) { $StartupShortcuts += $ServerShortcut }

    $CaptureExe = Join-Path $CaptureInstallPath "sopforge.exe"
    $CaptureShortcut = Register-Autostart -TaskName $CaptureTaskName -Exe $CaptureExe -Arguments $null -OwnInstallPath $InstallPath
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
exit 0
