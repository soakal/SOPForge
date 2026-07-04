"""Optional narration: WAV recording via the classic MCI `waveaudio` driver
(winmm.dll, via ctypes — no extra dependency beyond stdlib+pywin32 already in
requirements.txt). Degrades cleanly when no audio input device exists — the
manifest's `narration_wav` field stays None rather than the capture session
failing outright over an optional feature."""

import ctypes

_winmm = ctypes.windll.winmm


def has_audio_input():
    return _winmm.waveInGetNumDevs() > 0


def _mci(command):
    buf = ctypes.create_unicode_buffer(256)
    err = _winmm.mciSendStringW(command, buf, len(buf), None)
    if err != 0:
        raise OSError(f"MCI command failed (error {err}): {command!r}")
    return buf.value


class NarrationRecorder:
    """start()/stop(path) around one MCI waveaudio recording session. If no
    audio input device is present, start() returns False and does nothing;
    stop() then returns None instead of raising, so the caller can set
    manifest.narration_wav = None and continue the capture session."""

    ALIAS = "sopforge_narration"

    def __init__(self):
        self._active = False

    def start(self):
        if self._active:
            return True  # already recording: no-op, not a second `open`
        if not has_audio_input():
            return False
        _mci(f"open new type waveaudio alias {self.ALIAS}")
        try:
            _mci(f"record {self.ALIAS}")
        except OSError:
            # Device present but record failed (busy, OS privacy block, ...)
            # — never leave the alias open, or every future start() fails.
            _mci(f"close {self.ALIAS}")
            raise
        self._active = True
        return True

    def stop(self, out_path):
        if not self._active:
            return None
        self._active = False
        try:
            _mci(f"stop {self.ALIAS}")
            _mci(f'save {self.ALIAS} "{out_path}"')
        finally:
            _mci(f"close {self.ALIAS}")
        return str(out_path)
