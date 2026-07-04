## Criterion 1: self-test harness element-metadata coverage

- **notepadpp**: 4/5 (80.0%) non-empty element metadata
- **chrome**: 5/5 (100.0%) non-empty element metadata
- **vscode**: 5/5 (100.0%) non-empty element metadata

**Overall: 14/15 (93.3%)** — threshold 90%

## Criterion 4: EXE cold-start timing and clean exit

- First launch after build: 1.167s (one-time cost — see scripts/verify_exe.py's module docstring; matches an OS-level scan of a binary it hasn't seen before, not app or packaging behavior)
- Steady-state launches (3 repeats): 1.150s, 1.089s, 1.164s (average 1.134s, threshold 2.0s)
- Clean exit return codes: [0, 0, 0, 0]

