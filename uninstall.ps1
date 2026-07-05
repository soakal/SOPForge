<#
.SYNOPSIS
    Removes an install.ps1 installation: the scheduled task(s) (if any), the
    persistent SOPFORGE_SERVER_URL env var (if this install set one), the
    capture/ and server/ EXE folders, and install-config.json.

.DESCRIPTION
    By default preserves <InstallPath>/sessions/ if it contains any real
    generated content (a user's captured/generated SOPs), removing only
    what install.ps1 actually created (the EXE folders, config,
    scheduled task(s), the env var) -- deleting a user's data isn't
    "uninstalling the app", and CLAUDE.md's rule against destructive
    actions without confirmation applies here too. Pass -RemoveData to
    also delete sessions/ (e.g. for a full clean wipe, or the automated
    round-trip test where no real session data is ever created).

    The SOPFORGE_SERVER_URL removal is conditional: only removed if its
    current value still matches exactly what THIS install wrote
    (install-config.json's ServerUrlEnvValue) -- never a value the user
    set themselves, or one a different install (e.g. at another port,
    installed afterward) now depends on.
#>
param(
    [string]$InstallPath = "$env:LOCALAPPDATA\SOPForge",
    [switch]$RemoveData
)

$ErrorActionPreference = "Stop"

$ConfigPath = Join-Path $InstallPath "install-config.json"
if (Test-Path $ConfigPath) {
    $Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
    # Both the current (ServerTaskName + CaptureTaskName) and the older
    # single-TaskName install-config.json schema are handled, so
    # uninstalling an install created by a previous SOPForge version still
    # cleans up correctly.
    $TaskNames = @($Config.ServerTaskName, $Config.CaptureTaskName, $Config.TaskName) | Where-Object { $_ }
    foreach ($TaskName in ($TaskNames | Select-Object -Unique)) {
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Output "Removed scheduled task '$TaskName'."
        }
    }

    # install.ps1 sets a persistent per-user SOPFORGE_SERVER_URL when a
    # non-default port is chosen (independent of -Autostart). Only remove
    # it if it still holds exactly the value THIS install wrote -- never a
    # value the user set themselves, or a different install (e.g. one at
    # another port, installed afterward) is now relying on.
    if ($Config.ServerUrlEnvValue) {
        $CurrentValue = [Environment]::GetEnvironmentVariable("SOPFORGE_SERVER_URL", "User")
        if ($CurrentValue -eq $Config.ServerUrlEnvValue) {
            [Environment]::SetEnvironmentVariable("SOPFORGE_SERVER_URL", $null, "User")
            Write-Output "Removed SOPFORGE_SERVER_URL environment variable ($CurrentValue)."
        }
    }

    # install.ps1 -Autostart falls back to a Startup-folder shortcut per EXE
    # when Register-ScheduledTask is blocked (see its Register-Autostart
    # function) -- remove exactly the ones it recorded creating, never any
    # other shortcut the user might have in that folder.
    foreach ($ShortcutName in @($Config.StartupShortcuts)) {
        if (-not $ShortcutName) { continue }
        $ShortcutPath = Join-Path ([Environment]::GetFolderPath("Startup")) $ShortcutName
        if (Test-Path $ShortcutPath) {
            Remove-Item -Path $ShortcutPath -Force
            Write-Output "Removed Startup-folder shortcut '$ShortcutName'."
        }
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
