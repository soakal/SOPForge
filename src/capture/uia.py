"""UIA element resolution: element-from-point with a per-call timeout and
window-class classification (win32/chromium/electron). Never raises — any
failure degrades to the schema's empty-metadata shape, because a capture
session must keep recording even when a single resolve goes wrong (Phase 1
acceptance criterion 5: zero-UIA-metadata sessions still produce a valid
manifest).

Classification quirk (see .claude/skills/uia-notes.md): UIA reports
framework_id == "Chrome" for both real Chromium browsers and Electron apps —
the owning process's exe name is the only reliable discriminator.
"""

import ctypes
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

EMPTY_ELEMENT = {
    "name": "",
    "control_type": "",
    "automation_id": "",
    "framework": "",
    "bounding_rect": None,
}
EMPTY_WINDOW = {"title": "", "process": "", "class": ""}

KNOWN_BROWSER_EXES = {
    "chrome.exe",
    "msedge.exe",
    "brave.exe",
    "firefox.exe",
    "opera.exe",
    "vivaldi.exe",
}

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def process_exe_name(pid):
    """Owning exe filename for a PID, via QueryFullProcessImageNameW (needs no
    more than PROCESS_QUERY_LIMITED_INFORMATION, unlike the pywin32 module-list
    APIs) — returns "" on any failure (protected process, already exited, etc)."""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = ctypes.c_ulong(260)
        buf = ctypes.create_unicode_buffer(size.value)
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        if not ok:
            return ""
        return Path(buf.value).name
    except OSError:
        return ""
    finally:
        kernel32.CloseHandle(handle)


def classify_window(framework_id, exe_name):
    exe_lower = (exe_name or "").lower()
    if exe_lower in KNOWN_BROWSER_EXES:
        return "chromium"
    if framework_id == "Chrome":
        return "electron"
    if framework_id == "Win32":
        return "win32"
    return ""


def _resolve_at_uncapped(x, y):
    from pywinauto import Desktop

    elem = Desktop(backend="uia").from_point(x, y)
    info = elem.element_info
    rect = info.rectangle
    bounding_rect = [rect.left, rect.top, rect.right, rect.bottom] if rect is not None else None
    element = {
        "name": info.name or "",
        "control_type": info.control_type or "",
        "automation_id": info.automation_id or "",
        "framework": info.framework_id or "",
        "bounding_rect": bounding_rect,
    }

    exe_name = process_exe_name(info.process_id)
    try:
        title = elem.top_level_parent().window_text()
    except Exception:  # noqa: BLE001 - title is best-effort
        title = ""
    window = {
        "title": title,
        "process": exe_name,
        "class": classify_window(element["framework"], exe_name),
    }
    return element, window


def resolve_at(x, y, timeout=5.0):
    """Resolve the UIA element at a screen point. Always returns
    (element_dict, window_dict); degrades to the all-empty shape on any error
    or if resolution takes longer than `timeout` seconds.

    Default raised from 2.0s to 5.0s (see .claude/skills/uia-notes.md): some
    controls — observed with Notepad++'s toolbar buttons — genuinely take
    ~4s to resolve via UIA. A capture tool isn't latency-sensitive the way an
    input handler is (a few seconds of background processing lag per click
    is an acceptable trade-off), so timing out at 2s was silently discarding
    metadata UIA could have supplied correctly one heartbeat later — directly
    undermining the tool's reason to exist. Never raise this back down
    without re-checking that finding.

    Uses shutdown(wait=False) rather than the executor as a context manager:
    __exit__ would block until the worker thread finishes, which defeats the
    point of a timeout if UIA genuinely hangs — a stuck call is abandoned to
    finish in the background instead of holding up the caller."""
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(_resolve_at_uncapped, x, y)
    try:
        return future.result(timeout=timeout)
    except Exception:  # noqa: BLE001 - never raise, including on timeout
        return dict(EMPTY_ELEMENT), dict(EMPTY_WINDOW)
    finally:
        pool.shutdown(wait=False)
