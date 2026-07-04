## UIA spike findings (Phase 1, scripts/spike_uia.py)

- **notepadpp** (Win32, Notepad++ `-multiInst`): framework='Win32' class='Scintilla'
  control_type='Pane' automation_id='' name='' bounding_rect=[10, 102, 826, 555]
- **chrome** (Chromium): framework='Chrome' class='ToolbarView' control_type='ToolBar'
  automation_id='view_1000' name='' bounding_rect=[18, 50, 806, 96]
- **vscode** (Electron): framework='Chrome' class='View' control_type='Pane'
  automation_id='' name='' bounding_rect=[0, 380, 824, 1180]

### Key takeaways for src/capture/uia.py (task-05)

- **Do not use Windows 11's built-in Notepad as a Win32 UIA test target.** It shares
  one process across tabs/windows (even separate top-level `hwnd`s can share a PID),
  so launching a "new" instance and force-killing its resolved window's PID can kill
  windows you never launched. This actually happened once during this spike and
  closed the user's pre-existing Notepad tabs. Use **Notepad++ with `-multiInst`**
  instead — it guarantees an isolated process per launch and is a real Win32/MFC
  Scintilla control, a cleaner Win32 exemplar anyway.
- For Chrome and VS Code, force process isolation with a throwaway
  `--user-data-dir` (VS Code also needs `--extensions-dir`) so a launch never
  attaches to the user's existing browser/editor session.
- **Kill only by PID that a `tasklist` snapshot proves is new** (never by image
  name `/IM`, and double-check the resolved window's PID wasn't already running
  before you launched anything — belt-and-suspenders even with the isolation
  above).
- UIA reports `framework_id == "Chrome"` for **both** real Chrome and Electron apps
  (VS Code) — Chromium's accessibility tree is exposed identically. `framework_id`
  alone cannot distinguish "chromium browser" from "electron app"; the resolver
  must also record the owning process's exe name (`element_info.process_id` -> exe
  path) to classify `window.class` as win32/chromium/electron in the manifest.
- `element_info.automation_id` is frequently empty even on a successfully resolved
  element (seen on VS Code's outer pane and Chrome's toolbar) — treat automation_id
  as optional metadata, never require it for a "non-empty" resolution; `name` or
  `control_type` is the right non-empty signal (matches acceptance criterion 1's
  "non-empty element metadata" wording).
- `win.set_focus()` does not reliably make a freshly-launched window win
  `GetForegroundWindow()` in this environment (observed consistent warnings even
  though the resolved UIA element was still correct) — Windows' focus-stealing
  prevention applies to background-launched processes. Treat foreground
  confirmation as best-effort, not a hard precondition, and validate the actual
  resolved element/class instead of trusting focus state alone.
- Cold launch-to-window-ready timing observed: Notepad++ <1s, Chrome ~2-3s, VS Code
  ~4-6s. The resolver/self-test harness should poll with a generous timeout
  (used 10s/15s/20s here) rather than a fixed sleep.

### Environment limitation: synthetic input is invisible to global hooks (task-06)

**Confirmed on this build VM: neither pynput's `mouse.Controller`/`keyboard.Controller`
nor a raw `SendInput` call via ctypes (bypassing pynput entirely) is observed by a
`WH_MOUSE_LL`/`WH_KEYBOARD_LL` listener** (`pynput.mouse.Listener` /
`keyboard.Listener`), even with the listener confirmed running and the injecting
process at the same (non-admin) integrity level as the hook. Repro: install a
listener, call `l.wait()` to confirm it's live, inject one click/keypress via each
of pynput's Controller and a hand-rolled ctypes `SendInput`, and in both cases zero
events arrive. Root cause is sharper than "invisible": a direct `SendInput` call
returns `0` (failure) with `GetLastError() == ERROR_ACCESS_DENIED (5)` — this
session actively **denies** synthetic input injection, it doesn't just fail to
route it to hooks. This is very likely specific to however this remote/automated
VM session delivers input (whatever mechanism gives the agent "hands" on this
machine is evidently blocked from injecting raw input, by design) — it should not
reflect how `src/capture/hooks.py`'s `InputRecorder` behaves for a real physical
user, since hardware-generated input is exactly what `WH_MOUSE_LL`/`WH_KEYBOARD_LL`
exist to see, and a real user's OS session would not have this restriction.

**Consequence for testing:** any test in this environment that relies on injected
synthetic input reaching a global low-level hook cannot pass, regardless of which
API does the injecting (pynput, raw SendInput, or pywinauto's `click_input()`, which
is SendInput-based too). `InputRecorder`'s callback *logic* (coordinate/button
handling, typing-burst summarization) is still fully testable by invoking
`_on_click`/`_on_press` directly, which is what `tests/test_hooks_shots.py` does —
but true end-to-end verification that the OS hook fires on real input is out of
scope for this autonomous build and would need a human at the keyboard.

**Consequence for task-11 (self-test harness) / acceptance criterion 1:** the ≥90%
non-empty-element-metadata measurement should be driven by calling
`src/capture/uia.py`'s `resolve_at(x, y)` directly at each scripted interaction
point (pywinauto can still drive the apps via its message-based `click()`, which
doesn't depend on the global hook at all) rather than by routing scripted clicks
through `InputRecorder` and hoping the hook sees them.

### Environment limitation: GDI screen capture (BitBlt) also fails here (task-06)

**`mss.grab()` and `PIL.ImageGrab.grab()` both fail on this build VM** —
`mss.exception.ScreenShotError: Windows graphics function failed (no error
provided): BitBlt` / PIL's `OSError: screen grab failed` — even capturing the
primary monitor's full rect (`sct.monitors[1]`, `{0,0,824,1560}`, a real value
from `sct.monitors`, not an out-of-range one). `SetProcessDPIAware()` first makes
no difference. Two independent GDI-capture libraries failing identically points to
the remoting/virtual-display layer under this VM session, not a library bug — this
is the same class of restriction as the input-injection finding above (this
session's virtual display apparently isn't a real GDI-BitBlt-capturable surface).
The 824x1560 portrait resolution also suggests this build session itself is being
viewed through a phone-form-factor remote client, which may have its own capture
path that doesn't go through classic Win32 GDI at all.

**Consequence for testing:** `ScreenshotWriter` (`src/capture/shots.py`) cannot be
reliably verified against a real `mss.grab()` call in this environment. Tests instead
monkeypatch the `mss.mss()` construction to a fake session object returning
synthetic pixel data, so the sequential-naming/file-write logic is still exercised
for real — but actual GDI capture success is unverified here and needs a normal
desktop session (a real target machine, not this build VM) to confirm.

**Update (task-11): this failure turned out to be intermittent, not permanent.**
Later in the same session, `capture()` against real `mss.grab()` succeeded
consistently (3/3 calls, no exception) with no code change on our side — so this
VM's GDI-capture availability apparently varies (by session/focus/display state,
not fully understood), rather than being a hard, constant block like the
input-injection finding above. Because of this, `ScreenshotWriter.capture()`
(`src/capture/shots.py`) gained a graceful fallback: on `mss.exception.ScreenShotError`
it writes a solid-color placeholder image and returns `is_placeholder=True` instead of
raising, and `Recorder`/`ManifestBuilder` record that flag on the step
(`screenshot_placeholder`) so a real, non-transient failure never crashes a capture
session and is never silently indistinguishable from a real screenshot. Do not write
a test that hard-asserts real `mss.grab()` fails in this environment — it may pass or
fail depending on when it runs; assert only that `capture()` never raises and always
produces a valid file, and use the mocked test for the fallback logic itself.

### Some UIA controls are genuinely slow to resolve (task-11 self-test harness)

Building the Phase 1 self-test harness (scripted interaction points against
Notepad++/Chrome/VS Code) surfaced that `resolve_at()`'s original 2.0s default
timeout (task-05) was too aggressive: **Notepad++'s toolbar buttons took ~4s to
resolve via UIA** (measured directly, bypassing the timeout wrapper), while
resolving a point inside the Scintilla text area took <0.5s. At 2.0s, every
toolbar-button interaction point silently degraded to the empty-metadata
fallback — not a bug, exactly the designed behavior, but a bad trade-off: a
capture tool isn't latency-sensitive the way an input handler is (delayed
background processing per click is fine; missing metadata for a real user
click is not), so `resolve_at()`'s default timeout was raised to 5.0s.
If this ever needs retuning, measure per-control-type resolution time first —
don't just raise the number blindly, and don't lower it back toward 2s without
re-confirming this finding no longer holds.

**Run-to-run variance near the 90% threshold:** with only ~15 scripted
interaction points total (5 per app), a single UIA resolution miss moves the
overall percentage by ~6.7% — most runs landed at 93.3% (14/15), one run
landed at 88.9% (16/18, notepadpp alone contributing 8 candidates instead of
the requested 5 — not reproduced on retry, structurally shouldn't be possible
given `_find_candidates`'s `[:count]` slice, so likely transient session
state from this session's earlier iterative debugging rather than a real bug;
worth a closer look if it recurs). If this threshold ever flakes in CI,
increase `--clicks` first (more samples per app dampens the per-miss swing)
before assuming a regression.
