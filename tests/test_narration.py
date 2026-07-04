"""Narration is optional: this build VM has zero waveIn devices (confirmed
via `has_audio_input()`), so the clean-skip path is what actually runs here;
the real-recording path is skipped rather than faked, since a fake WAV would
not prove the MCI wiring works — it needs a machine with a microphone."""

import time
import wave

import pytest

import capture.narration as narration_module
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


def test_double_start_is_a_noop_not_a_second_open(monkeypatch):
    monkeypatch.setattr(narration_module, "has_audio_input", lambda: True)
    calls = []
    monkeypatch.setattr(narration_module, "_mci", lambda cmd: calls.append(cmd))

    rec = NarrationRecorder()
    assert rec.start() is True
    assert rec.start() is True  # already active: no-op, no second "open"

    open_calls = [c for c in calls if c.startswith("open")]
    assert len(open_calls) == 1


def test_record_failure_closes_alias_and_reraises(monkeypatch):
    monkeypatch.setattr(narration_module, "has_audio_input", lambda: True)
    calls = []

    def fake_mci(cmd):
        calls.append(cmd)
        if cmd.startswith("record"):
            raise OSError("simulated: device busy")
        return ""

    monkeypatch.setattr(narration_module, "_mci", fake_mci)

    rec = NarrationRecorder()
    with pytest.raises(OSError):
        rec.start()

    assert any(c.startswith("close") for c in calls)
    assert rec._active is False
    # A later start() must be able to re-open the same alias, not error on
    # "alias already in use" from the leaked previous session.
    monkeypatch.setattr(narration_module, "_mci", lambda cmd: calls.append(cmd))
    assert rec.start() is True
