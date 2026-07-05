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
    # function). Prefer the names it recorded in StartupShortcuts, but older
    # install-config.json versions didn't record them -- so also try the
    # deterministic "<TaskName>.lnk" names install.ps1 always uses. Guard
    # every candidate by checking the shortcut's target actually points inside
    # this InstallPath, so an unrelated .lnk the user happens to have under the
    # same name is never removed.
    $StartupDir = [Environment]::GetFolderPath("Startup")
    $ShortcutNames = @(@($Config.StartupShortcuts) + ($TaskNames | ForEach-Object { "$_.lnk" }) |
        Where-Object { $_ } | Select-Object -Unique)
    $WShell = New-Object -ComObject WScript.Shell
    foreach ($ShortcutName in $ShortcutNames) {
        $ShortcutPath = Join-Path $StartupDir $ShortcutName
        if (-not (Test-Path $ShortcutPath)) { continue }
        $Target = $WShell.CreateShortcut($ShortcutPath).TargetPath
        if ($Target -and $Target.StartsWith($InstallPath, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -Path $ShortcutPath -Force
            Write-Output "Removed Startup-folder shortcut '$ShortcutName'."
        }
    }
}

# Stop any SOPForge EXE still running from this InstallPath before deleting the
# folder -- a running process holds a lock on its own .exe, which would make the
# Remove-Item below silently fail (it uses -ErrorAction SilentlyContinue) and
# leave server/ or capture/ behind. Match on executable path so a sopforge
# process launched from somewhere else (e.g. a dev build under the repo's dist/)
# is never touched.
Get-CimInstance Win32_Process -Filter "Name='sopforge-server.exe' OR Name='sopforge.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($InstallPath, [StringComparison]::OrdinalIgnoreCase) } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Output "Stopped running '$($_.Name)' (PID $($_.ProcessId))."
    }
Start-Sleep -Milliseconds 500

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
