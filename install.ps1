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

    Each task is registered independently (one failing doesn't block the
    other) and is best-effort: registering an AtLogOn-triggered scheduled
    task can be blocked by Task Scheduler permission policy on some
    machines/accounts even without elevation (confirmed via both
    Register-ScheduledTask and schtasks.exe on this build's own VM — see
    phases/DEVIATIONS.md's "task-12 -Autostart scheduled task" entry).
    Installing and running SOPForge WITHOUT -Autostart never depends on
    this and always works; if -Autostart fails here, register the
    scheduled task(s) manually (or grant the needed Task Scheduler rights)
    and re-run, or just launch the EXEs yourself / via a shortcut instead
    (see USER_MANUAL.md's manual-autostart walkthrough).

.NOTES
    If PowerShell's execution policy blocks double-clicking this script
    directly, use install.bat instead (wraps this with
    -ExecutionPolicy Bypass) -- a common Windows 11 default-policy issue,
    not specific to this script.
#>
param(
    [string]$InstallPath = "$env:LOCALAPPDATA\SOPForge",
    [int]$Port = 8420,
    [switch]$Autostart
)

$ErrorActionPreference = "Stop"

$RepoRoot = $PSScriptRoot
$CaptureDist = Join-Path $RepoRoot "dist\sopforge"
$ServerDist = Join-Path $RepoRoot "dist\sopforge-server"

if (-not (Test-Path $CaptureDist)) {
    throw "Not built: $CaptureDist -- run 'python scripts/build_exe.py' first."
}
if (-not (Test-Path $ServerDist)) {
    throw "Not built: $ServerDist -- run 'python scripts/build_server_exe.py' first."
}

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
$InstallConfig = [ordered]@{
    InstallPath      = $InstallPath
    Port             = $Port
    SessionsRoot     = $SessionsRoot
    Autostart        = [bool]$Autostart
    ServerTaskName   = $ServerTaskName
    CaptureTaskName  = $CaptureTaskName
    ServerUrlEnvValue = $ServerUrlEnvValue
}
$InstallConfig | ConvertTo-Json | Set-Content -Path (Join-Path $InstallPath "install-config.json") -Encoding utf8

if ($Autostart) {
    # Each task is registered independently -- the base install (files +
    # config, above) already succeeded regardless of what happens here, and
    # one task's Task Scheduler permission restriction must not block the
    # other from being attempted. See this script's .DESCRIPTION and
    # phases/DEVIATIONS.md's "task-12 -Autostart scheduled task" entry.
    try {
        $ServerExe = Join-Path $ServerInstallPath "sopforge-server.exe"
        $ServerAction = New-ScheduledTaskAction -Execute $ServerExe `
            -Argument "--port $Port --sessions-root `"$SessionsRoot`""
        $ServerTrigger = New-ScheduledTaskTrigger -AtLogOn
        Register-ScheduledTask -TaskName $ServerTaskName -Action $ServerAction -Trigger $ServerTrigger -Force -ErrorAction Stop | Out-Null
        Write-Output "Registered autostart scheduled task '$ServerTaskName'."
    } catch {
        Write-Warning "Could not register the '$ServerTaskName' autostart scheduled task: $_"
        Write-Warning "SOPForge is installed and works without autostart -- launch sopforge-server.exe"
        Write-Warning "directly, or register the scheduled task manually / with elevated rights."
    }

    try {
        $CaptureExe = Join-Path $CaptureInstallPath "sopforge.exe"
        $CaptureAction = New-ScheduledTaskAction -Execute $CaptureExe
        $CaptureTrigger = New-ScheduledTaskTrigger -AtLogOn
        Register-ScheduledTask -TaskName $CaptureTaskName -Action $CaptureAction -Trigger $CaptureTrigger -Force -ErrorAction Stop | Out-Null
        Write-Output "Registered autostart scheduled task '$CaptureTaskName'."
    } catch {
        Write-Warning "Could not register the '$CaptureTaskName' autostart scheduled task: $_"
        Write-Warning "SOPForge is installed and works without autostart -- launch sopforge.exe"
        Write-Warning "directly, or register the scheduled task manually / with elevated rights."
    }
}

Write-Output "Installed SOPForge to $InstallPath (port $Port)."
exit 0
