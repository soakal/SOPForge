"""Per-monitor DPI awareness + per-monitor scale lookup (ctypes only, never
raises). The WinAPI handles are injectable so the logic is testable off-Windows.

Must run before pynput/mss/pywinauto are imported (see capture/__init__.py) so
hook coordinates, screenshots and UIA rects share one physical-pixel frame."""

import ctypes
import sys
from ctypes import wintypes

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
PROCESS_PER_MONITOR_DPI_AWARE = 2
MONITOR_DEFAULTTONEAREST = 2
MDT_EFFECTIVE_DPI = 0
S_OK = 0
E_ACCESSDENIED = 0x80070005


def _winapi():
    """(user32, shcore) from ctypes.windll; (None, None) off-Windows or on failure."""
    if sys.platform != "win32":
        return None, None
    try:
        return ctypes.windll.user32, ctypes.windll.shcore
    except (AttributeError, OSError):
        return None, None


def set_process_dpi_awareness(user32=None, shcore=None):
    """Best available awareness: 'per_monitor_v2' | 'per_monitor' | 'system' | 'unavailable'."""
    if user32 is None and shcore is None:
        user32, shcore = _winapi()
    try:
        if user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return "per_monitor_v2"
    except (AttributeError, OSError):
        pass
    try:
        hr = shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
        if (hr & 0xFFFFFFFF) in (S_OK, E_ACCESSDENIED):
            return "per_monitor"
    except (AttributeError, OSError):
        pass
    try:
        if user32.SetProcessDPIAware():
            return "system"
    except (AttributeError, OSError):
        pass
    return "unavailable"


def monitor_scale(x, y, user32=None, shcore=None):
    """Effective DPI / 96 of the monitor nearest (x, y); 1.0 on any failure."""
    if user32 is None and shcore is None:
        user32, shcore = _winapi()
    try:
        hmon = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
        dpi_x, dpi_y = ctypes.c_uint(), ctypes.c_uint()
        hr = shcore.GetDpiForMonitor(
            hmon, MDT_EFFECTIVE_DPI, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
        )
        if (hr & 0xFFFFFFFF) != S_OK or not dpi_x.value:
            return 1.0
        return dpi_x.value / 96.0
    except Exception:  # noqa: BLE001
        return 1.0
