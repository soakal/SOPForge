# pretooluse.ps1 — SOPForge deny-list hook (Claude Code PreToolUse, matcher: Bash)
# Reads the tool-call JSON from stdin. Exit 0 = allow, exit 2 = block (stderr is
# fed back to the model as the reason). Keep this fast: no logging to disk, no I/O
# beyond stdin/stderr.
#
# Port of the NEXUS deny-list, extended for a VM where the loop will be poking at
# UIAutomation: protect explorer.exe, the registry outside HKCU test keys, and
# anything that could take down the interactive session the capture tests need.

#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

try {
    $payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $cmd = ''
    if ($payload.tool_input -and $payload.tool_input.PSObject.Properties['command']) {
        $cmd = [string]$payload.tool_input.command
    }
    if (-not $cmd) { exit 0 }

    # Each entry: regex (case-insensitive) + human reason.
    $denyList = @(
        @{ Pattern = 'rm\s+(-\w*\s+)*-\w*r\w*f|rm\s+-rf';                      Reason = 'recursive force delete' }
        @{ Pattern = 'Remove-Item\b.*-Recurse.*(C:\\Windows|C:\\Users\\[^\\]+\\(Documents|Desktop)|HKLM)'; Reason = 'recursive delete of protected path' }
        @{ Pattern = '\bformat(\.com)?\s+[a-z]:';                              Reason = 'disk format' }
        @{ Pattern = '\b(diskpart|bcdedit|bootrec)\b';                         Reason = 'boot/disk configuration' }
        @{ Pattern = 'taskkill\b.*(explorer\.exe|winlogon|csrss|lsass)';       Reason = 'killing a session-critical process breaks UIA testing' }
        @{ Pattern = 'Stop-Process\b.*-Name\s+.*(explorer|winlogon)';          Reason = 'killing a session-critical process breaks UIA testing' }
        @{ Pattern = '\breg(\.exe)?\s+(add|delete)\s+(?!"?HKCU\\Software\\SOPForgeTest)'; Reason = 'registry writes only allowed under HKCU\Software\SOPForgeTest' }
        @{ Pattern = 'Set-ItemProperty\b.*HKLM';                               Reason = 'HKLM registry write' }
        @{ Pattern = '\b(shutdown|Restart-Computer|Stop-Computer)\b';          Reason = 'reboot/shutdown kills the interactive session' }
        @{ Pattern = 'git\s+push\s+.*(--force|-f)\b';                          Reason = 'force push' }
        @{ Pattern = 'git\s+(reset\s+--hard\s+origin|clean\s+-\w*x)';          Reason = 'destructive git operation on tracked/ignored files' }
        @{ Pattern = '\bnetsh\s+advfirewall\b|\bsc(\.exe)?\s+delete\b';        Reason = 'firewall/service teardown' }
        @{ Pattern = 'Set-MpPreference|Add-MpPreference.*-Exclusion';          Reason = 'tampering with Defender' }
        @{ Pattern = '\bdel\s+/s\b|\brmdir\s+/s\b.*(C:\\Windows|C:\\Users)';   Reason = 'recursive cmd delete of protected path' }
        @{ Pattern = 'Invoke-(WebRequest|RestMethod)\b.*\|\s*(iex|Invoke-Expression)'; Reason = 'download-and-execute' }
        @{ Pattern = '\bcurl\b.*\|\s*(sh|bash|iex)\b';                         Reason = 'download-and-execute' }
    )

    foreach ($rule in $denyList) {
        if ($cmd -imatch $rule.Pattern) {
            [Console]::Error.WriteLine("BLOCKED by SOPForge deny-list: $($rule.Reason). Command: $cmd")
            exit 2
        }
    }
    exit 0
}
catch {
    # Fail closed on malformed input: a hook error should never silently allow.
    [Console]::Error.WriteLine("pretooluse.ps1 hook error: $($_.Exception.Message)")
    exit 2
}
