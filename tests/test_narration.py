"""Narration is optional: this build VM has zero waveIn devices (confirmed
via `has_audio_input()`), so the clean-skip path is what actually runs here;
the real-recording path is skipped rather than faked, since a fake WAV would
not prove the MCI wiring works — it needs a machine with a microphone."""

import time
import wave

import pytest

from capture.narration import NarrationRecorder, has_audio_input


def test_no_device_clean_skip(tmp_path):
    if has_audio_input():
        pytest.skip("audio input device present; covered by the real-recording test")
    rec = NarrationRecorder()
    assert rec.start() is False
    result = rec.stop(tmp_path / "narration.wav")
    assert result is None


def test_records_valid_wav_when_device_present(tmp_path):
    if not has_audio_input():
        pytest.skip("no audio input device on this machine")
    rec = NarrationRecorder()
    assert rec.start() is True
    time.sleep(1.0)
    out = tmp_path / "narration.wav"
    result = rec.stop(out)
    assert result == str(out)
    with wave.open(str(out), "rb") as w:
        assert w.getnchannels() >= 1
        assert w.getframerate() > 0
