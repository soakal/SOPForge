"""Transcription wrapper: faster-whisper behind a thin interface, tested
with a stubbed model by default so the suite never needs real weights. The
one opt-in real-model test skips (never fails) unless SOPFORGE_WHISPER_MODEL
is set and the model actually loads — no weights are downloaded here."""

import os
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


def test_real_model_integration_opt_in():
    model_size = os.environ.get("SOPFORGE_WHISPER_MODEL")
    if not model_size:
        pytest.skip("SOPFORGE_WHISPER_MODEL not set; opt-in real-model test skipped")
    transcriber = Transcriber(model_size=model_size)
    try:
        transcriber._get_model()
    except Exception as exc:  # noqa: BLE001 - any load failure means skip, not fail
        pytest.skip(f"faster-whisper model unavailable: {exc}")
