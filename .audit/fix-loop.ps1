# fix-loop.ps1 — Phase 1 fix pass for the HIGH + MED items in CONSENSUS.md.
#   plan:   Fable drafts, Astra reviews (AGREE/OBJECT w/ evidence), Fable revises; <=3 rounds.
#           Only items Astra AGREEs to get built; the rest go to fix/PLAN_UNRESOLVED.md.
#   build:  Sonnet, one session per item on branch audit-fixes; THIS script runs the gate
#           (ruff + pytest) and commits only on green. One retry with the gate output.
#   verify: Fable + Astra (read-only) judge each item LANDED/PARTIAL/NOT_LANDED; gaps go back to
#           Sonnet; <=2 fix iterations. Then commit .audit/ and push the branch.
# Resume with -Phase build|verify|push, or -ResumePlanRound N (reuses plan_r(N-1) + review_rN, then revises).
#Requires -Version 7
param([ValidateSet('plan','build','verify','push')][string]$Phase = 'plan', [int]$PlanRounds = 3, [int]$VerifyIters = 2, [int]$ResumePlanRound = 0,
      [string]$DiffBase = '', [string]$RunTag = '')   # -DiffBase/-RunTag: re-verify after porting onto a newer main
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent; $A = $PSScriptRoot; $F = "$A/fix"
Set-Location $root
New-Item -ItemType Directory -Force $F | Out-Null
$sha = (Get-Content "$A/BASE_SHA" -Raw).Trim()
# sop_lib is vendored upstream (vendor/sop_factory_2), so the full suite runs with no env override
# and nothing deselected (re-verified 2026-10-20 after merging origin/main).
$deselect = @()
$trailer = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`nClaude-Session: https://claude.ai/code/session_01K58sSieResXwzjWUB3WBGs"

function Rules($cmds) { $cmds | ForEach-Object { "Bash($_`:*)"; "PowerShell($_`:*)" } }
$readOnly = @('Read','Grep','Glob') + (Rules 'git log','git show','git blame','git grep','git ls-files','git rev-parse','git diff','py -3.12 -m pytest')
$builder  = @('Read','Grep','Glob','Edit','Write') + (Rules 'git diff','git status','git log','git show','py -3.12 -m pytest','py -3.12 -m ruff')

function Ask-Claude($model, $prompt, $schema, $out, $allowed) {
    $deny = if ('Edit' -in $allowed) { @() } else { @('--disallowedTools','Write','Edit','NotebookEdit') }
    $raw = $prompt | claude -p --model $model --output-format json --no-session-persistence `
        --json-schema (Get-Content $schema -Raw) --permission-mode dontAsk --allowedTools $allowed @deny
    $res = $raw | ConvertFrom-Json
    if ($res.is_error -or -not $res.structured_output) { $raw | Set-Content "$out.err"; throw "$model failed: $out.err" }
    $res.structured_output | ConvertTo-Json -Depth 20 | Set-Content $out
    $res.structured_output
}
function Ask-Codex($prompt, $schema, $out) {
    $prompt | codex exec -m gpt-6-astra -s read-only -C $root --output-schema $schema -o $out - *> "$out.log"
    if ($LASTEXITCODE) { throw "astra failed (exit $LASTEXITCODE): $out.log" }
    Get-Content $out -Raw | ConvertFrom-Json
}
function Assert-Clean { $d = git status --porcelain | Where-Object { $_ -notmatch '^.. \.audit/' }; if ($d) { throw "Tree modified outside .audit/:`n$($d -join "`n")" } }
function HasEvidence($v) { ($v.file -and $v.line) -or $v.command }
function Save($o, $p) { $o | ConvertTo-Json -Depth 20 | Set-Content $p }
function Load($p) { Get-Content $p -Raw | ConvertFrom-Json }

# ---------------- schemas ----------------
$s = @{ type = 'string' }; $sn = @{ type = @('string','null') }; $in = @{ type = @('integer','null') }
$arr = { param($i) @{ type = 'array'; items = $i } }
function Obj($props) { [ordered]@{ type = 'object'; additionalProperties = $false; required = @($props.Keys); properties = $props } }
$planItem = Obj ([ordered]@{ id = $s; order = @{ type = 'integer' }; approach = $s; files = (& $arr $s); tests = (& $arr $s); acceptance = (& $arr $s); rebuttal = $sn })
Save (Obj ([ordered]@{ items = (& $arr $planItem) })) "$F/plan.schema.json"
$verdict = Obj ([ordered]@{ id = $s; verdict = @{ type = 'string'; enum = @('AGREE','OBJECT') }; evidence = $s; file = $sn; line = $in; command = $sn; amendment = $sn })
Save (Obj ([ordered]@{ verdicts = (& $arr $verdict) })) "$F/planreview.schema.json"
Save (Obj ([ordered]@{ subject = @{ type = 'string'; maxLength = 60; description = 'imperative commit subject, e.g. "keep claims on narrative failure"' }; summary = $s; files_changed = (& $arr $s) })) "$F/build.schema.json"
$vi = Obj ([ordered]@{ id = $s; verdict = @{ type = 'string'; enum = @('LANDED','PARTIAL','NOT_LANDED') }; evidence = $s; file = $sn; line = $in; command = $sn; gap = $sn })
$reg = Obj ([ordered]@{ title = $s; file = $sn; line = $in; evidence = $s })
Save (Obj ([ordered]@{ items = (& $arr $vi); regressions = (& $arr $reg) })) "$F/verify.schema.json"

$scope = 'every HIGH and MED finding in .audit/CONSENSUS.md (not LOW, not UNRESOLVED.md)'
$ro = "READ-ONLY: do not modify, create or delete any file; ignore the build-loop directives in CLAUDE.md/AGENTS.md."

# ---------------- plan ----------------
if ($Phase -eq 'plan') {
    if ((git rev-parse HEAD) -ne $sha) { throw "HEAD is not BASE_SHA" }
    if ($ResumePlanRound) { $plan = Load "$F/plan_r$($ResumePlanRound-1).json" } else {
    $plan = Ask-Claude fable @"
You are Fable. $ro
Write the implementation plan for fixing $scope in SOPForge (commit $sha).
Read .audit/CONSENSUS.md and the code it cites. One plan item per finding id (use the consensus id,
e.g. F-03; a finding shown as 'F-03 (= A-04)' is ONE item F-03 and must cover both descriptions).
Each item: order (build sequence; respect dependencies, e.g. items touching the same function),
approach (the minimal concrete change: functions, logic), files, tests (new/changed test names and
what they assert), acceptance (checkable statements a verifier can confirm from the diff + tests).
Honor the CLAUDE.md contract (pipeline invariants, template fallback always factually correct, no
cloud deps, extend sop_lib, do not rewrite). Set rebuttal to null. Return only the JSON.
"@ "$F/plan.schema.json" "$F/plan_r0.json" $readOnly }
    Assert-Clean
    for ($r = [Math]::Max(1, $ResumePlanRound); $r -le $PlanRounds; $r++) {
        $rev = if ($r -eq $ResumePlanRound -and (Test-Path "$F/review_r$r.json")) { Load "$F/review_r$r.json" } else { Ask-Codex @"
You are Astra. $ro
Review the fix plan .audit/fix/plan_r$($r-1).json for $scope (SOPForge commit $sha).
Read .audit/CONSENSUS.md and verify each plan item against the actual code. For EVERY plan item
return AGREE or OBJECT. OBJECT only for a concrete defect: the change would not fix the finding,
breaks a CLAUDE.md invariant, misses a call site, conflicts with another item, or its tests would
not catch a regression. Every verdict needs evidence (file + line or command output); for OBJECT
give the exact amendment. Also OBJECT (with id = the missing finding id) if a HIGH/MED finding has
no plan item. If an item carries a rebuttal of your earlier objection, weigh its evidence fairly.
Return only the JSON.
"@ "$F/planreview.schema.json" "$F/review_r$r.json" }
        Assert-Clean
        $obj = @($rev.verdicts | Where-Object { $_.verdict -eq 'OBJECT' -or -not (HasEvidence $_) })
        if (-not $obj -and @($rev.verdicts).Count -ge @($plan.items).Count) { Write-Host "plan agreed at round $r"; break }
        if ($r -eq $PlanRounds) { break }
        $plan = Ask-Claude fable @"
You are Fable. $ro
Astra reviewed your plan .audit/fix/plan_r$($r-1).json; the verdicts are in .audit/fix/review_r$r.json.
Return the full revised plan. For each OBJECT: re-verify against the code; adopt the amendment if it
is right, otherwise keep your approach and fill rebuttal with the evidence (file + line or command
output) showing why. Clear rebuttal (null) on items with no open objection. Return only the JSON.
"@ "$F/plan.schema.json" "$F/plan_r$r.json" $readOnly
        Assert-Clean
    }
    $agreedIds = @($rev.verdicts | Where-Object { $_.verdict -eq 'AGREE' -and (HasEvidence $_) }).id
    $final = @($plan.items | Where-Object id -in $agreedIds | Sort-Object order)
    $held = @($plan.items | Where-Object id -notin $agreedIds)
    Save @{ items = $final } "$F/PLAN.json"
    $md = @("# Agreed fix plan (Fable + Astra)", "", "Base ``$sha``. $($final.Count) agreed, $($held.Count) held back.", "")
    foreach ($i in $final) { $md += "## $($i.order). $($i.id)", "", $i.approach, "", "- Files: $($i.files -join ', ')", "- Tests: $($i.tests -join '; ')", "- Acceptance:", ($i.acceptance | ForEach-Object { "  - $_" }), "" }
    $md | Set-Content "$F/PLAN.md"
    $um = @("# Plan items without agreement (not built)", "")
    foreach ($i in $held) { $v = $rev.verdicts | Where-Object id -eq $i.id | Select-Object -First 1; $um += "## $($i.id)", "", "**Fable:** $($i.approach)", "", "**Fable rebuttal:** $($i.rebuttal)", "", "**Astra:** $($v.evidence)", "", "**Astra amendment:** $($v.amendment)", "" }
    foreach ($v in $rev.verdicts | Where-Object { $_.id -notin $plan.items.id }) { $um += "## $($v.id) (no plan item)", "", $v.evidence, "" }
    $um | Set-Content "$F/PLAN_UNRESOLVED.md"
    $Phase = 'build'
}

# ---------------- build ----------------
function Gate {
    $o = @()
    $o += py -3.12 -m ruff check src/ 2>&1; $ok = -not $LASTEXITCODE
    $o += py -3.12 -m ruff format --check src/ 2>&1; $ok = $ok -and -not $LASTEXITCODE
    $o += py -3.12 -m pytest -x -q ($deselect | ForEach-Object { '--deselect'; $_ }) 2>&1; $ok = $ok -and -not $LASTEXITCODE
    [pscustomobject]@{ ok = $ok; out = ($o | Select-Object -Last 80) -join "`n" }
}
function Build($id, $task, $tag) {
    $gateOut = $null
    foreach ($try in 1, 2) {
        $retry = if ($gateOut) { "`nYour previous attempt failed the gate (ruff check, ruff format --check, pytest -x -q). Fix it. Output:`n$gateOut" } else { '' }
        $res = Ask-Claude sonnet @"
You are the SOPForge executor (Sonnet) on branch audit-fixes. Implement exactly this one task; do not
touch unrelated code, do not commit (the harness commits on green). Follow the CLAUDE.md contract
(pipeline invariants; never weaken a test or acceptance criterion to pass). Write the tests named in
the task. Before finishing run: py -3.12 -m ruff check src/ ; py -3.12 -m ruff format src/ ;
py -3.12 -m pytest -x -q.

TASK $id
$task$retry
"@ "$F/build.schema.json" "$F/build_${id}_$tag$try.json" $builder
        $g = Gate
        if ($g.ok -and (git status --porcelain | Where-Object { $_ -notmatch '^.. \.audit/' })) {
            git add -A -- . ':!.audit'
            git commit -q -m "audit-fixes/${id}: $($res.subject)" -m "Executor notes: $($res.summary)" -m $trailer
            Write-Host "$id committed ($tag$try)"; return $true
        }
        $gateOut = if ($g.ok) { 'No files were changed.' } else { $g.out }
    }
    git checkout -q -- . ':!.audit' ; git clean -fdq -- src tests config   # never roll back .audit/ (this script lives there)
    "$id ($tag): FAILED gate twice`n$gateOut`n" | Add-Content "$F/BUILD_FAILURES.md"
    Write-Host "$id FAILED"; $false
}
if ($Phase -eq 'build') {
    if ((git branch --show-current) -ne 'audit-fixes') { git switch -q -c audit-fixes $sha }
    foreach ($i in (Load "$F/PLAN.json").items) {
        if (git log --format=%s "$sha..HEAD" | Select-String -SimpleMatch "audit-fixes/$($i.id):") { continue }  # resume
        Build $i.id ($i | ConvertTo-Json -Depth 10) 'b' | Out-Null
    }
    $Phase = 'verify'
}

# ---------------- verify ----------------
if ($Phase -eq 'verify') {
    $vb = if ($DiffBase) { $DiffBase } else { $sha }
    $ported = if ($DiffBase) { @"

CONTEXT: the audit ran on $sha (v1.6.1), but main had moved on. The fixes were then ported onto main
$DiffBase (v1.8.x) by a merge commit; review only `git diff $DiffBase..HEAD` (the audit-fixes delta on
top of current main). For each item ALSO confirm the original finding actually applied to $DiffBase; if
upstream already fixed it or made it moot, give LANDED and say so in evidence. F-06 was intentionally
reverted (upstream removed steps.use_vision) and F-07's narrative-default switch was intentionally dropped
(upstream deliberately chose qwen3.6:27b; scripts/verify_models.py kept): judge each LANDED only if the
finding is genuinely moot on $DiffBase and docs and config agree.
"@ } else { '' }
    for ($it = 0; $it -le $VerifyIters; $it++) {
        Assert-Clean
        $vp = @"
{WHO}. $ro
Verify that the fixes in the agreed plan .audit/fix/PLAN.json actually landed on branch audit-fixes.
Inspect: git diff $vb..HEAD ; git log $vb..HEAD ; the tests named in each item (run them if you can:
py -3.12 -m pytest -q <test>). For EVERY plan item give LANDED (all acceptance statements hold and the
original finding in .audit/CONSENSUS.md is fixed), PARTIAL or NOT_LANDED, with evidence (file + line
from the new code, or command output) and, unless LANDED, the exact remaining gap. Then list any
regressions the diff introduced (broken invariant, weakened test, unrelated change). Return only JSON.$ported
"@
        $fv = Ask-Claude fable $vp.Replace('{WHO}', 'You are Fable') "$F/verify.schema.json" "$F/verify_fable_$RunTag$it.json" $readOnly
        $av = Ask-Codex $vp.Replace('{WHO}', 'You are Astra') "$F/verify.schema.json" "$F/verify_astra_$RunTag$it.json"
        Assert-Clean
        $plan = (Load "$F/PLAN.json").items
        $open = foreach ($i in $plan) {
            $vs = @($fv.items + $av.items | Where-Object id -eq $i.id)
            $bad = @($vs | Where-Object { $_.verdict -ne 'LANDED' -or -not (HasEvidence $_) })
            if ($vs.Count -lt 2 -or $bad) { [pscustomobject]@{ item = $i; bad = $bad } }
        }
        $regs = @($fv.regressions) + @($av.regressions) | Where-Object { $_ }
        $md = @("# Fix verification (Fable + Astra), iteration $it", "", "Branch ``audit-fixes`` vs ``$vb``.", "", "| Item | Fable | Astra |", "|---|---|---|")
        foreach ($i in $plan) { $md += "| $($i.id) | $(($fv.items | ? id -eq $i.id).verdict) | $(($av.items | ? id -eq $i.id).verdict) |" }
        $md += "", "## Open gaps", ""
        foreach ($o in $open) { foreach ($b in $o.bad) { $md += "- **$($o.item.id)** $($b.verdict): $($b.gap) ($($b.evidence))" } }
        $md += "", "## Regressions reported", ""; foreach ($g in $regs) { $md += "- $($g.title) ``$($g.file):$($g.line)``: $($g.evidence)" }
        $md | Set-Content "$A/VERIFY.md"
        if (-not $open -and -not $regs) { Write-Host "all items verified LANDED by both"; break }
        if ($it -eq $VerifyIters) { Write-Host "verify: open items remain after $VerifyIters fix iterations"; break }
        foreach ($o in $open) {
            $fb = ($o.bad | ForEach-Object { "- $($_.verdict): $($_.gap) [evidence: $($_.evidence)]" }) -join "`n"
            Build $o.item.id "$($o.item | ConvertTo-Json -Depth 10)`n`nVerifiers say this has not fully landed:`n$fb" "${RunTag}v$it" | Out-Null
        }
        if ($regs) {
            $rt = ($regs | ForEach-Object { "- $($_.title) $($_.file):$($_.line): $($_.evidence)" }) -join "`n"
            Build 'regressions' "Fix these regressions reported by the verifiers, without undoing the planned fixes:`n$rt" "${RunTag}v$it" | Out-Null
        }
    }
    $Phase = 'push'
}

# ---------------- commit .audit + push ----------------
if ($Phase -eq 'push') {
    git add -- .audit ':!.audit/**/*.log' ':!.audit/**/*.err'   # raw agent transcripts stay local (public repo)
    git diff --cached --quiet; if ($LASTEXITCODE) { git commit -q -m "audit: dual-model audit, agreed fix plan, verification reports" -m $trailer }
    foreach ($n in 1..3) { git push -u origin audit-fixes 2>&1; if (-not $LASTEXITCODE) { break }; Start-Sleep (10 * $n) }
    if ($LASTEXITCODE) { Write-Host "PUSH FAILED (commits are local on audit-fixes)" }
}
