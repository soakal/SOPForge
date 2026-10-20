# Audit fix pass: status (2026-10-20, 16:30 EDT)

## Where it stands

- Audit (Fable + Astra) done: `CONSENSUS.md` (18 agreed), `UNRESOLVED.md` (F-12).
- Fix plan agreed on 10 HIGH/MED items: `fix/PLAN.md`. Held for Brian: A-01, A-03, F-04, F-01
  (`fix/PLAN_UNRESOLVED.md`).
- Built by Sonnet, gated (ruff + full pytest), on branch `audit-fixes`.
- The audit ran on a stale local checkout (v1.6.1, `41b70ef`). The branch has since been **ported onto
  origin/main v1.8.x (`48f757c`)** by merge commit `f6edb89`:
  - F-06 reverted (upstream removed `use_vision`).
  - F-07's narrative-default switch dropped (upstream chose `qwen3.6:27b`); `scripts/verify_models.py` kept.
  - Gate on the merged tree: ruff clean, 717 passed / 5 skipped, nothing env-deselected.
- Re-verification on v1.8.x (run tag `p`):
  - Round 0 done (`fix/verify_*_p0.json`). Follow-up fixes committed: A-12 (preflight probe proxy),
    F-10 (DEVIATIONS entry), F-07 (manual default).
  - F-11 and the regressions fix failed the gate twice and were rolled back (`fix/BUILD_FAILURES.md`).
  - Round 1 was running when the laptop had to leave.
- `main` NOT merged yet. It waits for the re-verification to finish.

## Resume (laptop back online)

```powershell
git fetch; git switch audit-fixes; git pull
git log --oneline origin/main -1   # must still be 48f757c; if main moved, merge it into audit-fixes first
pwsh -NoProfile -File .audit/fix-loop.ps1 -Phase verify -DiffBase 48f757c896cd7159e8937836d408beb1251a4539 -RunTag p2
```

That runs verify, then at most 2 gated Sonnet fix rounds, then pushes `audit-fixes`. After it:
read `VERIFY.md`, run the gate on a test merge into main, merge `--no-ff`, and push main.

## Still Brian's call

1. The 4 held items (`fix/PLAN_UNRESOLVED.md`). A-01 would also close the rest of F-10.
2. Sign-off on the two F-10 entries in `phases/DEVIATIONS.md`.
3. OK to sign the EXEs (`scripts/sign_dist.ps1`). Rebuild, sign and reinstall are needed before the running app has any of this.
4. Phase 2 (interactive walkthrough) starts after the merge.
