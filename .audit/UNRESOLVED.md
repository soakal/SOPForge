# SOPForge audit — UNRESOLVED (for Brian)

Base: `41b70ef4fb45c82a5242d27c063f52099df8e72f`. Each item: the owner's claim, then the peer's latest evidenced verdict (or none).

## [LOW] F-12 (fable) — A click outside every monitor rect is silently attributed to monitor 1 and translated by monitor 1's origin; only reachable if hook and mss coordinate frames disagree

Location: `src/capture/shots.py:36`

```
shots.py:25-36 `monitor_for_point`: docstring 'falls back to 1 if the point isn't inside any known monitor rect'; loop over `sct.monitors[1:]` then `return 1`; :56-57 `monitor = sct.monitors[monitor_idx]; origin = (monitor["left"], monitor["top"])`; recorder.py:143 subtracts that origin. Peer A-09/F-12 dispute is partly right: Windows confines the cursor to the monitor union, so with pynput and mss in the same DPI context this branch is unreachable; it fires only under the F-11 frame-mismatch precondition, which I could not demonstrate. Retained as low because the fallback is silent (no log, no manifest flag) when it does fire -- the peer's own point that clamping could conceal the coordinate defect argues for making it visible, not for leaving it silent.
```

**Proposed fix:** Log a warning and set a manifest flag when `monitor_for_point` falls through to 1 so a frame mismatch becomes visible rather than silently mis-placing the marker; fix the frame itself per F-11 rather than clamping to a different monitor.

**astra DISPUTE** at `src/capture/shots.py:25`:

```
Reverified :26-28 explicitly documents fallback to monitor 1 for a point outside known monitor rectangles, and :30-36 implements that contract with `return 1`. recorder.py:143 correctly translates relative to the selected origin. The peer now concedes that the alleged coordinate-frame precondition was not demonstrated. A silent documented fallback alone does not establish a capture defect; adding diagnostics would be useful hardening.
```

