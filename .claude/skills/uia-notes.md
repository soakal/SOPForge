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
