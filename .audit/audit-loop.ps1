# audit-loop.ps1 — Phase 1 dual-model audit: blind r0, then up to 3 reconciliation rounds,
# then CONSENSUS.md + UNRESOLVED.md. Read-only on the repo; re-run with -StartRound N to resume.
#   Fable = Claude Code `--model fable` (claude-fable-5-1)   Astra = Codex `-m gpt-6-astra`
#Requires -Version 7
param([int]$StartRound = 0, [int]$MaxRounds = 3)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$A = $PSScriptRoot
Set-Location $root
$sha = (Get-Content "$A/BASE_SHA" -Raw).Trim()
if ((git rev-parse HEAD) -ne $sha) { throw "HEAD is not BASE_SHA $sha" }

# Read-only enforcement for Claude: no write tools, allowlisted read commands, everything else
# auto-denied (dontAsk). Codex: read-only sandbox. Both: git-status check after every run.
$claudeAllowed = @('Read','Grep','Glob') + (@('git log','git show','git blame','git grep','git ls-files','git rev-parse','git diff','py -3.12 -m pytest','pip show') |
    ForEach-Object { "Bash($_`:*)"; "PowerShell($_`:*)" })
$env:SOPFORGE_SOP_FACTORY_2_DIR = "$root/dist/sopforge-server/_internal/sop_factory_2"

function Invoke-Agent($who, $prompt, $schema, $out) {
    $sb = if ($who -eq 'fable') {
        { param($root, $prompt, $schema, $out, $allowed)
          Set-Location $root
          $raw = $prompt | claude -p --model fable --output-format json --no-session-persistence `
              --json-schema (Get-Content $schema -Raw) --permission-mode dontAsk `
              --allowedTools $allowed --disallowedTools Write Edit NotebookEdit
          $res = $raw | ConvertFrom-Json
          if ($res.is_error -or -not $res.structured_output) { $raw | Set-Content "$out.err"; throw "fable failed: $out.err" }
          $res.structured_output | ConvertTo-Json -Depth 20 | Set-Content $out }
    } else {
        { param($root, $prompt, $schema, $out)
          Set-Location $root
          $prompt | codex exec -m gpt-6-astra -s read-only -C $root --output-schema $schema -o $out - *> "$out.log"
          if ($LASTEXITCODE) { throw "astra failed (exit $LASTEXITCODE): $out.log" }
          Get-Content $out -Raw | ConvertFrom-Json | Out-Null }   # must be valid JSON
    }
    Start-Job -ScriptBlock $sb -ArgumentList $root, $prompt, $schema, $out, $claudeAllowed
}

function Assert-ReadOnly {
    $dirty = git status --porcelain | Where-Object { $_ -notmatch '^\?\? \.audit/|^.. \.audit/' }
    if ($dirty) { throw "Repo modified outside .audit/ — aborting:`n$($dirty -join "`n")" }
}

function Fill($tpl, $map) { foreach ($k in $map.Keys) { $tpl = $tpl.Replace("{$k}", $map[$k]) }; $tpl }
function Load($f) { if (Test-Path "$A/$f") { Get-Content "$A/$f" -Raw | ConvertFrom-Json } }
function HasEvidence($v) { ($v.file -and $v.line) -or $v.command }
$peerOf = @{ fable = 'astra'; astra = 'fable' }
$prefix = @{ fable = 'F'; astra = 'A' }

function Test-Consensus($r) {
    $issues = foreach ($me in 'fable','astra') {
        $cur = Load "${me}_r$r.json"; $prev = Load "${me}_r$($r-1).json"
        $peerPrev = Load "$($peerOf[$me])_r$($r-1).json"
        $valid = @($cur.responses | Where-Object { HasEvidence $_ })
        foreach ($v in $valid | Where-Object verdict -eq 'DISPUTE') { "$me disputes $($v.peer_id)" }
        foreach ($p in $peerPrev.findings) { if ($p.id -notin $valid.peer_id) { "${me}: no valid response to $($p.id)" } }
        foreach ($f in $cur.findings) { if ($f.id -notin $prev.findings.id) { "$me adds $($f.id)" } }
    }
    $issues | Set-Content "$A/round_$r.open.txt"
    -not $issues
}

# ---- round 0: blind, parallel; written outside the repo until both finish ----
$tmp = Join-Path ([IO.Path]::GetTempPath()) "sopforge-audit-$sha"
if ($StartRound -le 0) {
    New-Item -ItemType Directory -Force $tmp | Out-Null
    $tpl = Get-Content "$A/prompt_audit.md" -Raw
    $jobs = foreach ($who in 'fable','astra') {
        Invoke-Agent $who (Fill $tpl @{ AGENT = $who; SHA = $sha; PREFIX = $prefix[$who] }) "$A/schema/r0.schema.json" "$tmp/${who}_r0.json"
    }
    $jobs | Wait-Job | Receive-Job
    Assert-ReadOnly
    foreach ($who in 'fable','astra') { Move-Item -Force "$tmp/${who}_r0.json" "$A/${who}_r0.json" }
    $StartRound = 1
}

# ---- reconciliation rounds ----
$last = $StartRound - 1
for ($r = $StartRound; $r -le $MaxRounds; $r++) {
    $tpl = Get-Content "$A/prompt_review.md" -Raw
    $jobs = foreach ($who in 'fable','astra') {
        $p = Fill $tpl @{ AGENT = $who; R = "$r"; SHA = $sha; PREFIX = $prefix[$who]
                          SELF = "${who}_r$($r-1).json"; PEER = "$($peerOf[$who])_r$($r-1).json" }
        Invoke-Agent $who $p "$A/schema/review.schema.json" "$A/${who}_r$r.json"
    }
    $jobs | Wait-Job | Receive-Job
    Assert-ReadOnly
    $last = $r
    if (Test-Consensus $r) { Write-Host "consensus at round $r"; break }
    Write-Host "round $r open items:"; Get-Content "$A/round_$r.open.txt"
}

# ---- CONSENSUS.md / UNRESOLVED.md ----
# A finding is agreed iff it is in its owner's final list and the peer's LATEST evidenced verdict
# on it is CONFIRM. Latest DISPUTE or no verdict at all -> UNRESOLVED.
$rank = @{ critical = 0; high = 1; med = 2; low = 3 }
$verdicts = @{}; $dupes = @{}
for ($r = 1; $r -le $last; $r++) {
    foreach ($who in 'fable','astra') {
        foreach ($v in (Load "${who}_r$r.json").responses | Where-Object { HasEvidence $_ }) {
            $verdicts[$v.peer_id] = $v | Add-Member -PassThru -Force by $who
            if ($v.verdict -eq 'CONFIRM' -and $v.duplicate_of) { $dupes[$v.peer_id] = $v.duplicate_of }
        }
    }
}
$final = foreach ($who in 'fable','astra') { (Load "${who}_r$last.json").findings | Add-Member -PassThru -Force owner $who }
$agreed = @(); $open = @()
foreach ($f in $final) {
    $v = $verdicts[$f.id]
    if ($v -and $v.verdict -eq 'CONFIRM') { $agreed += $f } else { $open += [pscustomobject]@{ f = $f; v = $v } }
}
# Collapse duplicates: drop an Astra item that Fable confirmed as the same as an agreed Fable item.
$agreedIds = $agreed.id
$agreed = $agreed | Where-Object { -not ($_.owner -eq 'astra' -and $dupes[$_.id] -in $agreedIds) } |
    Sort-Object { $rank[$_.severity] }, id
$same = @{}; foreach ($k in $dupes.Keys) { if ($k -like 'A-*') { $same[$dupes[$k]] = $k } }

function Loc($x) { if ($x.file) { "``$($x.file)$(if ($x.line) { ":$($x.line)" })``" } elseif ($x.command) { "``$($x.command)``" } else { '(none)' } }
$md = @("# SOPForge audit — CONSENSUS", "", "Base: ``$sha`` · rounds: $last · agreed: $($agreed.Count) · unresolved: $($open.Count)", "")
foreach ($f in $agreed) {
    $also = if ($same[$f.id]) { " (= $($same[$f.id]))" } else { '' }
    $md += "## [$($f.severity.ToUpper())] $($f.id)$also — $($f.title)", "", "Location: $(Loc $f)$(if ($f.checklist_item) { " · checklist #$($f.checklist_item)" })", "",
           '```', $f.evidence.Trim(), '```', "", "**Fix:** $($f.proposed_fix)", ""
}
$md | Set-Content "$A/CONSENSUS.md"
$um = @("# SOPForge audit — UNRESOLVED (for Brian)", "", "Base: ``$sha``. Each item: the owner's claim, then the peer's latest evidenced verdict (or none).", "")
foreach ($o in $open | Sort-Object { $rank[$_.f.severity] }) {
    $f = $o.f; $v = $o.v
    $um += "## [$($f.severity.ToUpper())] $($f.id) ($($f.owner)) — $($f.title)", "", "Location: $(Loc $f)", "", '```', $f.evidence.Trim(), '```', "", "**Proposed fix:** $($f.proposed_fix)", ""
    $um += if ($v) { "**$($v.by) $($v.verdict)** at $(Loc $v):", "", '```', $v.evidence.Trim(), '```', "" } else { "_No evidenced peer verdict (added in the final round, or the peer's response was rejected for missing evidence)._", "" }
}
$um | Set-Content "$A/UNRESOLVED.md"
Write-Host "wrote CONSENSUS.md ($($agreed.Count)) and UNRESOLVED.md ($($open.Count))"
