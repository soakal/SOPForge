# PAUSED 2026-10-21 14:25 EDT: held-items fix pass + production-ready pass

Paused by Brian. Nothing is running (loop, watcher and the Claude session timer are all stopped).

## Where things stand

**On `main` (pushed, `dca29e2`):** the first 10 audit fixes (A-12, F-02, F-03, F-05, F-06, F-07, F-09, F-10, F-11,
F-16). Gate green: ruff clean, 720 passed / 5 skipped. See `STATUS.md` for what is still open from that pass.

**On `audit-fixes` (ahead of main, NOT merged):** the 4 previously-held findings. Fable and Astra agreed on a plan
for all four in round 4 (`fix/PLAN_held.md`, `fix/review_held_r4.json`); Sonnet is building them one at a time.

| Item | State |
|---|---|
| A-03 narrative grounding | committed (`a43036d`), gated green |
| A-01 stricter step round-trip gate | committed (`f3a973b`), gated green. Its first attempt failed only on the flaky `test_tray.py::test_exit_stops_icon_even_if_recording_was_active` ("Class already exists"). The commit message notes a recorder test flake too. |
| F-04 verbatim narration / placed_text coverage | NOT built. Failed the gate 3 times. It changes what ~8 existing `tests/pipeline/test_server.py` tests expect (narration no longer polished), and Sonnet lost shell access once mid-task. |
| F-01 photo-mode narrative + consistency | NOT built. Failed once, then was mid-build when paused. Half-done work is saved in `git stash@{0}` ("F-01 half-built ... ungated"). It has NOT passed lint or tests. |

Verify (Fable + Astra) of this pass has not run yet. The one attempt crashed when Astra hit its Codex usage limit.

## To resume

```powershell
git switch audit-fixes; git pull
# optional: git stash list   (stash@{0} is the unfinished F-01 build; the loop rebuilds F-01 from scratch anyway)
pwsh -NoProfile -File .audit/fix-loop.ps1 -Held -Phase build -RunTag h *> .audit/fix/loop-held.log     # PowerShell syntax
```

From Git Bash use `> .audit/fix/loop-held.log 2>&1` instead of `*>` (Bash expands `*`). The loop skips committed items
(A-01, A-03), rebuilds F-04 and F-01, then runs Fable + Astra verify, up to 2 fix rounds, then pushes `audit-fixes`.
It now sleeps through usage limits by itself (`Wait-Limit` in `fix-loop.ps1`) instead of dying. Optional watchdog:
`pwsh -NoProfile -File .audit/fix-watch.ps1` relaunches the loop if it exits before pushing.
Auto-mode will not let Claude launch the loop; run it yourself with `!` or in a separate PowerShell window.

If F-04 keeps failing, consider shipping without it: merge A-01 + A-03 (+ F-01 if it lands) and leave F-04 for
Phase 2. F-04's design is in `fix/PLAN_held.md`; the failing tests are listed in `fix/BUILD_FAILURES.md`.

## Then: the production-ready pass (not started)

1. Merge `audit-fixes` into `main`, ruff + `pytest -q`.
2. `pytest -m ui` (needs playwright + chromium).
3. Rebuild both EXEs: `py -3.12 -m PyInstaller --noconfirm --clean sopforge-server.spec` and `sopforge.spec`.
4. `pytest -m exe`, `py -3.12 scripts/verify_exe.py`, `--version` and `--self-check` smoke runs.
5. Independent review of the held-items diff (A-01, A-03, F-04, F-01): they are safety checks on what the LLM writes.
6. `scripts/sign_dist.ps1` to sign both EXEs. Brian approved SIGNING ONLY: no install, no release. It also adds the
   `CN=SOPForge` cert to this PC's Trusted Root/TrustedPublisher; other PCs still show the prompt unless they import
   `scripts/sopforge-signing-cert.cer`; SentinelOne still needs a console exclusion.
7. Push `main`, update `STATUS.md`.

## Gotchas learned this session

- **Notepad++ pops up** during the full test run: `tests/test_recorder.py` and `tests/test_uia_resolver.py` (the
  `scratch_window` fixture) and `capture --self-check` launch it. A local, git-ignored `conftest.py` at the repo root
  skips the `scratch_window` tests unless `SOPFORGE_GUI=1`. Run them once on purpose before release:
  `$env:SOPFORGE_GUI=1; py -3.12 -m pytest -q tests/test_recorder.py tests/test_uia_resolver.py`, and run the
  self-check once, and warn whoever is at the keyboard first.
- `test_recorder.py::test_hook_callback_returns_instantly...` (0.2 s wall-clock bound) and
  `test_tray.py::test_exit_stops_icon_...` are flaky under full-suite load; both pass alone.
- Still open from the first pass (see `STATUS.md`): F-10 deviation sign-off in `phases/DEVIATIONS.md`; F-07 needs
  Ollama on 192.168.200.60 up to run `py -3.12 scripts/verify_models.py`; EXE rebuild + sign + reinstall.
- Usage limits hit Fable (resets on the hour shown in the error), Codex/Astra and Sonnet separately.
