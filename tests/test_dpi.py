import os
import subprocess
import sys
from pathlib import Path

import pytest

from capture import dpi


class _User32:
    def __init__(self, calls, context=1, aware=0, raises=False):
        self.calls, self.context, self.aware, self.raises = calls, context, aware, raises
        self.point = self.flag = None

    def SetProcessDpiAwarenessContext(self, arg):  # noqa: N802
        self.calls.append(("context", arg))
        if self.raises:
            raise AttributeError("SetProcessDpiAwarenessContext")
        return self.context

    def SetProcessDPIAware(self):  # noqa: N802
        self.calls.append(("aware", None))
        return self.aware

    def MonitorFromPoint(self, point, flag):  # noqa: N802
        self.point, self.flag = (point.x, point.y), flag
        return 7


class _Shcore:
    def __init__(self, calls, hr=0, dpi=96):
        self.calls, self.hr, self.dpi = calls, hr, dpi

    def SetProcessDpiAwareness(self, arg):  # noqa: N802
        self.calls.append(("shcore", arg))
        return self.hr

    def GetDpiForMonitor(self, hmon, kind, px, py):  # noqa: N802
        assert hmon == 7 and kind == dpi.MDT_EFFECTIVE_DPI
        if self.dpi == "raise":
            raise OSError
        px._obj.value = self.dpi
        py._obj.value = self.dpi
        return self.hr


def test_context_v2_success_selects_per_monitor_v2():
    calls = []
    assert dpi.set_process_dpi_awareness(_User32(calls), _Shcore(calls)) == "per_monitor_v2"
    assert calls == [("context", -4)]


@pytest.mark.parametrize(
    "ctx,raises,hr,aware,expected,order",
    [
        (0, False, 0, 0, "per_monitor", ["context", "shcore"]),
        (0, False, 0x80070005, 0, "per_monitor", ["context", "shcore"]),
        (0, True, 0x80070057, 1, "system", ["context", "shcore", "aware"]),
        (0, False, 0x80070057, 0, "unavailable", ["context", "shcore", "aware"]),
    ],
)
def test_fallback_sequence_and_return_code_handling(ctx, raises, hr, aware, expected, order):
    calls = []
    user32 = _User32(calls, context=ctx, aware=aware, raises=raises)
    assert dpi.set_process_dpi_awareness(user32, _Shcore(calls, hr=hr)) == expected
    assert [c[0] for c in calls] == order


def test_winapi_missing_v2_still_returns_handles_for_fallbacks(monkeypatch):
    class _Fn:
        pass

    class _Lib:
        def __init__(self, **fns):
            for k, v in fns.items():
                setattr(self, k, v)

    user32 = _Lib(MonitorFromPoint=_Fn(), SetProcessDPIAware=lambda: 1)  # no v2 context API
    shcore = _Lib(GetDpiForMonitor=_Fn(), SetProcessDpiAwareness=lambda _a: 0x80070057)
    monkeypatch.setattr(dpi.sys, "platform", "win32")
    monkeypatch.setattr(dpi.ctypes, "windll", _Lib(user32=user32, shcore=shcore), raising=False)
    assert dpi._winapi() == (user32, shcore)
    assert dpi.set_process_dpi_awareness() == "system"


def test_monitor_scale_from_effective_dpi():
    calls = []
    user32 = _User32(calls)
    assert dpi.monitor_scale(10, 20, user32, _Shcore(calls, dpi=96)) == 1.0
    assert user32.point == (10, 20) and user32.flag == 2
    assert dpi.monitor_scale(10, 20, user32, _Shcore(calls, dpi=144)) == 1.5
    assert dpi.monitor_scale(10, 20, user32, _Shcore(calls, hr=0x80070057, dpi=144)) == 1.0
    assert dpi.monitor_scale(10, 20, user32, _Shcore(calls, dpi="raise")) == 1.0


@pytest.mark.skipif(sys.platform != "win32", reason="win32 only")
def test_fresh_process_sets_per_monitor_awareness_before_any_hook_import():
    code = (
        "import sys, ctypes, capture\n"
        "for m in ('pynput', 'mss', 'pywinauto', 'comtypes'):\n"
        "    assert m not in sys.modules, m\n"
        "u = ctypes.windll.user32\n"
        "try:\n"
        "    print(u.GetAwarenessFromDpiAwarenessContext(u.GetThreadDpiAwarenessContext()))\n"
        "except AttributeError:\n"
        "    print(2 if u.IsProcessDPIAware() else 0)\n"
    )
    out = subprocess.run(
        ["py", "-3.12", "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src")},
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "2"


@pytest.mark.skipif(sys.platform != "win32", reason="win32 only")
def test_set_process_dpi_awareness_is_idempotent():
    assert dpi.set_process_dpi_awareness() in {
        "per_monitor_v2",
        "per_monitor",
        "system",
        "unavailable",
    }
