## UIA spike findings (Phase 1, scripts/spike_uia.py)

- **notepad**: framework='Win32' class='RichEditD2DPT' control_type='Document' automation_id='' name='Text editor'
- **chrome**: framework='Chrome' class='ToolbarView' control_type='ToolBar' automation_id='view_1000' name=''
- **vscode**: framework='Chrome' class='View' control_type='Pane' automation_id='' name=''

### Key takeaways for src/capture/uia.py (task-05)

- Windows 11's `notepad.exe` in PATH is a redirector into the packaged Notepad app —
  the PID from `subprocess.Popen`/`CreateProcess` is short-lived and does not own the
  eventual window. Never track a launched app by PID; instead diff the desktop window
  list before/after launch and match on window title/class. Same caution applies to
  any packaged (MSIX) app.
- UIA reports `framework_id == "Chrome"` for **both** real Chrome and Electron apps
  (VS Code) — Chromium's accessibility tree is exposed identically. `framework_id`
  alone cannot distinguish "chromium browser" from "electron app"; the resolver must
  also record the owning process's exe name (`element_info.process_id` -> exe path)
  and/or window class (`Chrome_WidgetWin_1` for both, so exe name is the only
  reliable discriminator) to classify `window.class` as win32/chromium/electron in
  the manifest.
- `element_info.automation_id` is frequently empty even on a successfully resolved
  element (seen on VS Code's outer pane and Chrome's toolbar) — treat automation_id
  as optional metadata, never require it for a "non-empty" resolution; `name` or
  `control_type` is the right non-empty signal (matches acceptance criterion 1's
  "non-empty element metadata" wording).
- Cold launch-to-window-ready timing observed: Notepad <1s, Chrome ~2-3s, VS Code
  ~4-6s. The resolver/self-test harness should poll with a generous timeout
  (used 10s/15s/20s here) rather than a fixed sleep.
- Process cleanup: kill only by the resolved window's own PID
  (`taskkill /PID <pid> /F /T`), never by image name (`/IM`) — the dev machine may
  have other instances of the same exe already open with unsaved user data.
