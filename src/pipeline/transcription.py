"""Local speech-to-text via faster-whisper, behind a thin interface so tests
can inject a stub model instead of downloading real weights. The real model
is only constructed lazily, on first actual use — never at import time, and
never in the default (non-opt-in) test suite."""


class Transcriber:
    def __init__(self, model=None, model_size="base", device="cpu", compute_type="int8"):
        self._model = model
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type

    def _get_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self._model_size, device=self._device, compute_type=self._compute_type
            )
        return self._model

    def transcribe(self, audio_path):
        """Returns [{"text", "start", "end"}, ...] in chronological order."""
        model = self._get_model()
        segments, _info = model.transcribe(str(audio_path))
        return [{"text": seg.text.strip(), "start": seg.start, "end": seg.end} for seg in segments]
