---
description: Run the autonomous build loop for a phase (or all phases)
argument-hint: [phase number 1-3, or "all"]
---

You are the executor in an autonomous build loop for SOPForge. Read CLAUDE.md now and
obey it absolutely — especially the no-user-contact rule.

Target: phase $ARGUMENTS (if "all" or empty, start at the lowest phase whose
acceptance criteria in `phases/` are not yet green, and continue through phase 3).

## Loop

1. **Plan.** Invoke the `planner` subagent with: the target phase file, CLAUDE.md, and
   `git log --oneline -20`. It returns a numbered task list where every task has (a) a
   concrete deliverable and (b) a verification command that proves it. Write the list
   to `phases/NN-tasks.md` and commit it.

2. **Execute tasks in order.** For each task:
   - Implement it. Run the verification pipeline from CLAUDE.md.
   - On green: commit (`phase-NN/task-MM: ...`), push, mark the task done in
     `phases/NN-tasks.md`.
   - On red: fix and retry. After 3 failed attempts on the same task, go to step 4.

3. **Review gate.** After each commit, invoke the `reviewer` subagent with
   `git diff HEAD~1 --stat` plus the changed hunks (`git diff HEAD~1`), the task text,
   and the relevant CLAUDE.md sections. Verdicts:
   - PASS → next task.
   - FAIL → apply the critique, re-verify, re-commit, re-review. 3 FAILs on one task →
     step 4.

4. **Replan.** Invoke `planner` again with the failure history for this task. It may
   split, reorder, or rewrite tasks. 3 consecutive replans that still fail → STOP and
   write a plain-language escalation summary for the user (this is the only permitted
   user contact).

5. **Phase gate.** When all tasks are done, verify every acceptance criterion in
   `phases/NN-*.md` explicitly — run each check, record output in
   `phases/NN-results.md`, commit. All green → next phase (if target was "all"),
   otherwise write the completion summary.

## Completion summary (end of run only)

One short report: phases completed, acceptance results file paths, how to launch the
built EXEs, and anything in DEVIATIONS.md. Nothing else — no walkthrough of the work.

## Context hygiene

You will run for many hours. After each phase gate, and whenever context feels heavy,
rely on the committed task/results files as your memory — they, plus git log, must
always be sufficient to resume cold. Never keep essential state only in conversation.
