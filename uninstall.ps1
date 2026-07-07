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

.NOTES
    -InstallPath defaults to Program Files (matching install.ps1's default),
    which is machine-wide and requires administrator rights to modify. If
    this process isn't already elevated and $InstallPath actually needs it,
    this relaunches itself elevated (triggering a UAC prompt) -- an
    -InstallPath the current user can already write to (e.g. under
    %LOCALAPPDATA%) skips that prompt entirely.
#>
param(
    [string]$InstallPath = "$env:ProgramFiles\SOPForge",
    [switch]$RemoveData
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
# Applied everywhere below that decides whether to touch a task/shortcut/
# process based on its path being "ours" (scheduled tasks, Startup-folder
# shortcuts, running processes) -- all three shared the same bug.
function ConvertTo-PathPrefix([string]$Path) {
    return $Path.TrimEnd('\') + '\'
}

# Nothing to elevate for if there's nothing installed at this path yet.
if ((Test-Path $InstallPath) -and -not (Test-IsElevated)) {
    $ProbeFile = Join-Path $InstallPath ".sopforge-write-test"
    $NeedsElevation = $false
    try {
        New-Item -ItemType File -Path $ProbeFile -Force -ErrorAction Stop | Out-Null
        Remove-Item -Path $ProbeFile -Force -ErrorAction SilentlyContinue
    } catch {
        $NeedsElevation = $true
    }
    if ($NeedsElevation) {
        Write-Output "'$InstallPath' requires administrator rights -- relaunching elevated (a UAC prompt will appear)..."
        $ElevatedArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-InstallPath", "`"$InstallPath`"")
        if ($RemoveData) { $ElevatedArgs += "-RemoveData" }
        try {
            $ElevatedProc = Start-Process -FilePath "powershell.exe" -ArgumentList $ElevatedArgs -Verb RunAs -Wait -PassThru -ErrorAction Stop
            exit $ElevatedProc.ExitCode
        } catch {
            Write-Warning "Elevation was declined or failed to start: $_"
            exit 1
        }
    }
}

$ConfigPath = Join-Path $InstallPath "install-config.json"
if (Test-Path $ConfigPath) {
    $Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
    # Both the current (ServerTaskName + CaptureTaskName) and the older
    # single-TaskName install-config.json schema are handled, so
    # uninstalling an install created by a previous SOPForge version still
    # cleans up correctly.
    $TaskNames = @($Config.ServerTaskName, $Config.CaptureTaskName, $Config.TaskName) | Where-Object { $_ }
    # Task names ("SOPForge-Server"/"SOPForge-Capture") are fixed, not
    # namespaced per -InstallPath -- a task by this name could belong to a
    # DIFFERENT SOPForge install (e.g. this -InstallPath is stale/a test
    # path, but a real install elsewhere registered the same task name).
    # Only remove it if its action actually points inside THIS -InstallPath,
    # mirroring the ownership check already applied to Startup-folder
    # shortcuts below.
    foreach ($TaskName in ($TaskNames | Select-Object -Unique)) {
        $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $Task) { continue }
        $TaskExe = ($Task.Actions | Select-Object -First 1).Execute
        if ($TaskExe -and -not $TaskExe.StartsWith((ConvertTo-PathPrefix $InstallPath), [StringComparison]::OrdinalIgnoreCase)) {
            Write-Output "Skipping scheduled task '$TaskName' -- it belongs to a different install ($TaskExe), not $InstallPath."
            continue
        }
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Output "Removed scheduled task '$TaskName'."
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
        if ($Target -and $Target.StartsWith((ConvertTo-PathPrefix $InstallPath), [StringComparison]::OrdinalIgnoreCase)) {
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
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith((ConvertTo-PathPrefix $InstallPath), [StringComparison]::OrdinalIgnoreCase) } |
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
