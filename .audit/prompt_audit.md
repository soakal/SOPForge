You are {AGENT}, running a READ-ONLY, BLIND code audit of the SOPForge repo at commit {SHA}
(the current working tree; `git rev-parse HEAD` must equal that SHA).

This is an audit, not a build task. Ignore the "autonomous build loop" directives in CLAUDE.md /
AGENTS.md: do not modify, create, delete, commit, or format any file, and do not run builds or
installers. Read code, grep, run `git` read commands, and run tests only if your sandbox allows it.
Do not open anything under `.audit/` except `.audit/evidence/` (no other auditor's output exists
yet, and you must not look for it).

Shared evidence captured by the operator on the build host (cite these files as command output):
- `.audit/evidence/ollama_probe.txt`: the Ollama host 192.168.200.60:11434 was NOT reachable, so no
  model tag can be verified live. Every hardcoded tag is UNVERIFIED unless the repo itself contains
  a dated record of it being pulled/tested.
- `.audit/evidence/repo_visibility.txt`: GitHub visibility of soakal/SOPForge.

Checklist (cover every item; an item can produce zero, one, or several findings):
1. Pipeline wiring: is the [narrative] draft->critique->revise path dead code? Find the definition and
   every call site (server.py, render.py, anything else). State plainly whether it is reachable.
2. Narration placement bug (full transcript dumped into step 1): fixed or still present? Trace both
   the capture flow and the photo_build flow (pipeline/transcript.py).
3. Step-text stage: which model does it use (the brief says qwen3:14b; config may say otherwise.
   Verify, don't assume), and does that model actually receive screenshots? Quote the prompt/message
   construction. Note the vision/use_vision path separately.
4. Model tags: list every hardcoded model tag (code, config, tests, docs) and flag each as unverified
   per the evidence file. Flag tags that look nonexistent or inconsistent across files.
5. VRAM concurrency: can steps + narrative + vision (+ polish) models be resident at once and exceed
   40 GB, given max_concurrency settings and how stages are scheduled? Estimate sizes, show the
   scheduling code.
6. Fail-safe layers. For each of: manifest 1:1 step validation, round-trip check, template fallback,
   claim-coverage gate, sidecar review report, say whether it is present, wired into every build
   path, and tested (name the test).
7. Capture agent: DPI scaling (per-monitor DPI awareness, physical vs logical coords) and
   multi-monitor coordinate correctness (negative origins, virtual-screen offsets) of the click
   coordinates and element bounds written to manifest.json relative to the saved screenshot.
8. Dependency drift (requirements.txt vs pyproject vs imports), dead code, missing tests, security
   (repo visibility vs what's committed: hosts, IPs, names, secrets, real captures), and any network
   egress that would leave the LAN when cloud providers are off.

Rules:
- Every finding needs evidence: verbatim quoted code with file + line, or a command and its output.
  Findings without evidence are discarded.
- Prefix your ids with {PREFIX}- (e.g. {PREFIX}-01). Keep them stable; later rounds reference them.
- Severity: critical = wrong/unsafe document output or data leaving the LAN; high = a contract
  invariant not enforced or a likely production bug; med = correctness risk with a workaround or
  missing tests on a guarantee; low = hygiene.
- Report only real problems. A checklist item that is fine produces no finding (say nothing).
- proposed_fix: concrete and minimal (which function, what change).

Return only the structured JSON object.
