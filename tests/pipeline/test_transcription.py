"""Transcription wrapper: faster-whisper behind a thin interface, tested
with a stubbed model by default so the suite never needs real weights. The
one opt-in real-model test skips (never fails) unless SOPFORGE_WHISPER_MODEL
is set and the model actually loads — no weights are downloaded here."""

import os
import wave
from types import SimpleNamespace

import pytest

from pipeline.transcription import Transcriber


class _StubModel:
    def __init__(self, segments):
        self._segments = segments

    def transcribe(self, audio_path, **kwargs):
        return (self._segments, SimpleNamespace(language="en"))


def test_transcribe_returns_segments_with_text_and_timestamps():
    stub_segments = [
        SimpleNamespace(text=" First segment. ", start=0.0, end=2.0),
        SimpleNamespace(text=" Second segment. ", start=2.0, end=4.5),
    ]
    transcriber = Transcriber(model=_StubModel(stub_segments))
    result = transcriber.transcribe("fake.wav")
    assert result == [
        {"text": "First segment.", "start": 0.0, "end": 2.0},
        {"text": "Second segment.", "start": 2.0, "end": 4.5},
    ]


def test_transcribe_strips_whitespace_from_segment_text():
    stub_segments = [SimpleNamespace(text="   padded text   ", start=0.0, end=1.0)]
    transcriber = Transcriber(model=_StubModel(stub_segments))
    result = transcriber.transcribe("fake.wav")
    assert result[0]["text"] == "padded text"


def test_empty_segments_produce_empty_result():
    transcriber = Transcriber(model=_StubModel([]))
    assert transcriber.transcribe("fake.wav") == []


def _write_silent_wav(path, duration_seconds=1.0, sample_rate=16000):
    n_frames = int(duration_seconds * sample_rate)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * n_frames)


def test_real_model_integration_opt_in(tmp_path):
    model_size = os.environ.get("SOPFORGE_WHISPER_MODEL")
    if not model_size:
        pytest.skip("SOPFORGE_WHISPER_MODEL not set; opt-in real-model test skipped")

    transcriber = Transcriber(model_size=model_size)
    try:
        transcriber._get_model()
    except (OSError, RuntimeError, ValueError) as exc:
        # Model/weights unavailable (no network, bad model name/cache, etc.)
        # — skip, don't fail. A TypeError/AttributeError here would be a
        # real bug in Transcriber itself and must propagate, not be hidden
        # behind this opt-in test's skip.
        pytest.skip(f"faster-whisper model unavailable: {exc}")

    wav_path = tmp_path / "silence.wav"
    _write_silent_wav(wav_path)
    result = transcriber.transcribe(wav_path)
    assert isinstance(result, list)
    for segment in result:
        assert set(segment) == {"text", "start", "end"}
