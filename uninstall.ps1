<#
.SYNOPSIS
    Removes an install.ps1 installation: the scheduled task (if any), the
    capture/ and server/ EXE folders, and install-config.json.

.DESCRIPTION
    By default preserves <InstallPath>/sessions/ if it contains any real
    generated content (a user's captured/generated SOPs), removing only
    what install.ps1 actually created (the EXE folders, config,
    scheduled task) -- deleting a user's data isn't "uninstalling the
    app", and CLAUDE.md's rule against destructive actions without
    confirmation applies here too. Pass -RemoveData to also delete
    sessions/ (e.g. for a full clean wipe, or the automated round-trip
    test where no real session data is ever created).
#>
param(
    [string]$InstallPath = "$env:LOCALAPPDATA\SOPForge",
    [switch]$RemoveData
)

$ErrorActionPreference = "Stop"

$ConfigPath = Join-Path $InstallPath "install-config.json"
if (Test-Path $ConfigPath) {
    $Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
    $TaskName = $Config.TaskName
    if ($TaskName -and (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Output "Removed scheduled task '$TaskName'."
    }
}

if (-not (Test-Path $InstallPath)) {
    Write-Output "$InstallPath does not exist; nothing to remove."
    exit 0
}

$SessionsRoot = Join-Path $InstallPath "sessions"
$HasData = (Test-Path $SessionsRoot) -and
    ((Get-ChildItem $SessionsRoot -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object).Count -gt 0)

if ($HasData -and -not $RemoveData) {
    Write-Output "Preserving non-empty session data at $SessionsRoot (pass -RemoveData to also delete it)."
    Remove-Item -Path (Join-Path $InstallPath "capture") -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -Path (Join-Path $InstallPath "server") -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -Path $ConfigPath -Force -ErrorAction SilentlyContinue
    Write-Output "Removed capture/, server/, and install-config.json from $InstallPath."
} else {
    Remove-Item -Path $InstallPath -Recurse -Force
    Write-Output "Removed $InstallPath."
}
exit 0
