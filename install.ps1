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
    the -Autostart scheduled task for its launch arguments).

    -Autostart registers a per-user "SOPForge-Server" scheduled task
    (AtLogOn trigger, current user) that launches sopforge-server.exe with
    the chosen port and sessions root. This is best-effort: registering an
    ONLOGON-triggered scheduled task can be blocked by Task Scheduler
    permission policy on some machines/accounts even without elevation
    (confirmed via both Register-ScheduledTask and schtasks.exe on this
    build's own VM — see phases/DEVIATIONS.md's "task-12 -Autostart
    scheduled task" entry). Installing and running the server WITHOUT
    -Autostart never depends on this and always works; if -Autostart fails
    here, register the scheduled task manually (or grant the needed Task
    Scheduler rights) and re-run, or just launch sopforge-server.exe
    yourself / via a shortcut instead.
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

$TaskName = "SOPForge-Server"
$InstallConfig = [ordered]@{
    InstallPath  = $InstallPath
    Port         = $Port
    SessionsRoot = $SessionsRoot
    Autostart    = [bool]$Autostart
    TaskName     = $TaskName
}
$InstallConfig | ConvertTo-Json | Set-Content -Path (Join-Path $InstallPath "install-config.json") -Encoding utf8

if ($Autostart) {
    # Best-effort: the base install (files + config, above) already
    # succeeded regardless of what happens here, so a Task Scheduler
    # permission restriction on this branch must not fail the whole
    # install -- see this script's .DESCRIPTION and
    # phases/DEVIATIONS.md's "task-12 -Autostart scheduled task" entry.
    try {
        $ServerExe = Join-Path $ServerInstallPath "sopforge-server.exe"
        $Action = New-ScheduledTaskAction -Execute $ServerExe `
            -Argument "--port $Port --sessions-root `"$SessionsRoot`""
        $Trigger = New-ScheduledTaskTrigger -AtLogOn
        Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Force -ErrorAction Stop | Out-Null
        Write-Output "Registered autostart scheduled task '$TaskName'."
    } catch {
        Write-Warning "Could not register the '$TaskName' autostart scheduled task: $_"
        Write-Warning "SOPForge is installed and works without autostart -- launch sopforge-server.exe"
        Write-Warning "directly, or register the scheduled task manually / with elevated rights."
    }
}

Write-Output "Installed SOPForge to $InstallPath (port $Port)."
exit 0
