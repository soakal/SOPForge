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
    (AtLogOn trigger, current user, no elevation required for a
    non-elevated logon trigger) that launches sopforge-server.exe with the
    chosen port and sessions root.
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
    $ServerExe = Join-Path $ServerInstallPath "sopforge-server.exe"
    $Action = New-ScheduledTaskAction -Execute $ServerExe `
        -Argument "--port $Port --sessions-root `"$SessionsRoot`""
    $Trigger = New-ScheduledTaskTrigger -AtLogOn
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Force | Out-Null
    Write-Output "Registered autostart scheduled task '$TaskName'."
}

Write-Output "Installed SOPForge to $InstallPath (port $Port)."
exit 0
