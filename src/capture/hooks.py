"""Click/type input hooks: pynput listeners that record event *metadata*
only. Typed content is never captured — a burst of keystrokes is summarized
as a fixed, content-free string (redaction-by-design, not a redaction pass
applied after the fact)."""

import threading
import time

import win32gui
from pynput import keyboard, mouse

TYPE_SUMMARY = "entered value in field (content not captured)"

DEFAULT_TYPE_FLUSH_IDLE = 1.5

_BUTTON_NAMES = {
    mouse.Button.left: "left",
    mouse.Button.right: "right",
    mouse.Button.middle: "middle",
}


def _button_name(button):
    return _BUTTON_NAMES.get(button, getattr(button, "name", "unknown"))


def _foreground_window_center():
    """Best-effort (x, y) at the center of the current foreground window, for
    anchoring a typing burst that was never preceded by a click (e.g. a
    terminal focused via Alt-Tab or the taskbar). win32gui is a hard capture
    dependency already (see uia.py), but the lookup itself can fail for
    reasons outside our control (no foreground window, a window that closed
    between the two calls) -- never raise out of a hook-thread callback."""
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        return ((left + right) // 2, (top + bottom) // 2)
    except Exception:  # noqa: BLE001
        return None


class InputRecorder:
    """Records clicks as individual events and contiguous keystroke bursts as
    a single summarized "type" event. A burst flushes on whichever comes
    first: an idle timeout (no keypress for `type_flush_idle` seconds), the
    next click, or stop() -- the idle timeout is what makes a burst with no
    following click (switch windows via keyboard, end the recording after a
    pause) still get a screenshot taken close to when the typing actually
    happened, instead of one taken whenever some later, unrelated event
    happens to flush it.

    `_on_press` runs on pynput's keyboard-listener thread, the idle timer
    fires on its own thread, and `_flush_typing` can additionally run from
    the mouse-listener thread or the caller's thread (stop()) -- so all
    typing-state reads/writes AND the resulting `on_event` emit happen while
    holding `_lock`. Emitting inside the lock (not after releasing it) is
    what guarantees a burst's "type" event can never lose an enqueue race
    against a click that lands right at the flush boundary: whichever of
    (idle-timer flush) or (click's flush-then-emit-click) acquires the lock
    first runs to completion -- guard, clear, enqueue -- before the other
    thread's flush attempt even gets to check `_typing`, so the loser always
    finds `_typing` already False and skips straight to enqueueing its own
    event after."""

    def __init__(self, on_event, type_flush_idle=DEFAULT_TYPE_FLUSH_IDLE):
        self.on_event = on_event
        self._type_flush_idle = type_flush_idle
        self._typing = False
        self._has_clicked = False
        self._last_pos = (0, 0)
        self._burst_start_ts = None
        self._burst_pos = (0, 0)
        self._last_press_ts = None
        self._idle_timer = None
        self._lock = threading.Lock()
        self._mouse_listener = None
        self._keyboard_listener = None

    def _on_click(self, x, y, button, pressed):
        if not pressed:
            return
        self._flush_typing()
        with self._lock:
            self._last_pos = (x, y)
            self._has_clicked = True
        self.on_event(
            {
                "action": "click",
                "button": _button_name(button),
                "x": x,
                "y": y,
                "ts": time.time(),
            }
        )

    def _on_press(self, key):
        now = time.time()
        with self._lock:
            self._last_press_ts = now
            if self._typing:
                return
            self._typing = True
            self._burst_start_ts = now
            # A prior click is the more precise anchor (the exact field the
            # user clicked into); only fall back to "whatever window has
            # focus" when typing started with no click at all this session
            # (e.g. a terminal focused via Alt-Tab or the taskbar).
            if self._has_clicked:
                self._burst_pos = self._last_pos
            else:
                self._burst_pos = _foreground_window_center() or self._last_pos
            self._arm_idle_timer_locked(self._type_flush_idle)

    def _arm_idle_timer_locked(self, delay):
        """Caller must hold `_lock`. One timer object lives per burst -- its
        callback re-arms itself for the remaining idle budget instead of a
        fresh Timer being created on every keypress, so a fast typist doesn't
        churn through dozens of timer threads for one burst."""
        self._idle_timer = threading.Timer(delay, self._on_idle_timeout)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _on_idle_timeout(self):
        with self._lock:
            if not self._typing:
                return
            idle_for = time.time() - self._last_press_ts
            remaining = self._type_flush_idle - idle_for
            if remaining > 0:
                self._arm_idle_timer_locked(remaining)
                return
        self._flush_typing()

    def _flush_typing(self):
        with self._lock:
            if not self._typing:
                return
            self._typing = False
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
            x, y = self._burst_pos
            ts = self._burst_start_ts
            self.on_event(
                {
                    "action": "type",
                    "text_summary": TYPE_SUMMARY,
                    "x": x,
                    "y": y,
                    "ts": ts,
                }
            )

    def start(self):
        self._mouse_listener = mouse.Listener(on_click=self._on_click)
        self._keyboard_listener = keyboard.Listener(on_press=self._on_press)
        self._mouse_listener.start()
        self._keyboard_listener.start()
        self._mouse_listener.wait()
        self._keyboard_listener.wait()

    def stop(self):
        """Stops and *joins* both listener threads (pynput's Listener is a
        threading.Thread subclass) FIRST, then flushes any typing burst that
        was in progress — so by the time stop() returns, no on_click/on_press
        callback can still be executing (a caller like Recorder.stop() that
        finalizes state right after this call is guaranteed there's no
        in-flight event that could still mutate it) AND no keystroke can have
        armed an idle timer stop() doesn't know about.

        Flushing first (the original order) left exactly that gap: a
        keystroke landing between the flush and the listeners actually being
        stopped would start a brand-new burst and arm a fresh idle timer that
        nothing then cancels. Recorder.stop() calls this method, then
        immediately enqueues its own stop sentinel on the assumption that no
        more events can be enqueued afterward — but that orphaned timer would
        still fire ~type_flush_idle seconds later and enqueue a "type" event
        into an already-drained, dead queue, silently dropping the final
        typing burst. Stopping the listeners first closes the race: pynput
        guarantees no callback is still executing or can run again once
        `.join()` returns, so any keystroke that snuck in during teardown has
        already fully completed (including arming its timer) by the time the
        final `_flush_typing()` below runs and catches it."""
        listeners = [
            listener
            for listener in (self._mouse_listener, self._keyboard_listener)
            if listener is not None
        ]
        for listener in listeners:
            listener.stop()
        for listener in listeners:
            listener.join()
        self._flush_typing()
