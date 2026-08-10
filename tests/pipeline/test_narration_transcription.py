"""Optional narration recording -> local transcription wiring:
POST /sessions accepting a narration_wav_file, and _maybe_transcribe_narration
(server.py) turning it into transcript.json for the *existing, unchanged*
align_transcript_to_steps/_apply_transcript path when [transcription].enabled
is on. The ingestion-level tests (narration.wav written to session_dir) never
need generation to reach "done" and so are environment-agnostic; the
end-to-end transcription tests (like every other transcript test in
test_server.py) need the session to reach "done", which requires the external
sop_lib docx engine (see CLAUDE.md/README) -- not available in every dev
sandbox, same pre-existing constraint as test_server.py's own transcript
tests."""

import shutil
import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from pipeline.config import default_config_path, load_models_config, save_models_config
from pipeline.manifest import load_manifest
from pipeline.server import create_app

from _stub_llm import stub_llm_client_factory

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


class _StubTranscriber:
    def __init__(self, segments=None, raises=None):
        self._segments = segments if segments is not None else []
        self._raises = raises

    def transcribe(self, audio_path):
        if self._raises is not None:
            raise self._raises
        return self._segments


def _make_client(tmp_path, transcription_enabled=False, transcriber_factory=None):
    cfg = tmp_path / "models.toml"
    shutil.copyfile(default_config_path(), cfg)
    if transcription_enabled:
        models_cfg = load_models_config(cfg)
        models_cfg.transcription.enabled = True
        save_models_config(models_cfg, cfg)
    app = create_app(
        sessions_root=tmp_path / "sessions",
        llm_client_factory=stub_llm_client_factory,
        narrative_llm_client_factory=stub_llm_client_factory,
        config_path=cfg,
        transcriber_factory=transcriber_factory,
    )
    return TestClient(app)


def _manifest_and_files(tmp_path, fixture="sample-manifest.json"):
    manifest_path = FIXTURES / fixture
    manifest = load_manifest(manifest_path)
    shots_dir = tmp_path / "shots"
    shots_dir.mkdir(exist_ok=True)
    files = []
    for step in manifest.steps:
        p = shots_dir / step.screenshot
        Image.new("RGB", (1920, 1080), (255, 255, 255)).save(p)
        files.append(("files", (step.screenshot, p.open("rb"), "image/png")))
    return manifest_path.read_text(encoding="utf-8"), files


def _wait_for_terminal_status(client, session_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/sessions/{session_id}/status").json()
        if status["status"] in ("done", "error"):
            return status
        time.sleep(0.05)
    raise AssertionError(f"session {session_id} never reached a terminal status")


def test_create_session_with_narration_wav_writes_it_to_session_dir(tmp_path):
    """Ingestion-only: proves the WAV is saved to session_dir/narration.wav
    regardless of whether generation itself can complete in this
    environment (see module docstring)."""
    client = _make_client(tmp_path)
    manifest_json, files = _manifest_and_files(tmp_path)
    files = [*files, ("narration_wav_file", ("narration.wav", b"RIFF....WAVEfmt ", "audio/wav"))]
    resp = client.post(
        "/sessions", data={"manifest_json": manifest_json, "stage": "1"}, files=files
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]
    wav_path = tmp_path / "sessions" / session_id / "narration.wav"
    assert wav_path.read_bytes() == b"RIFF....WAVEfmt "


def test_create_session_without_narration_wav_leaves_no_narration_file(tmp_path):
    """Regression proof: today's only real path (no narration_wav_file at
    all) must be byte-identical -- no narration.wav ever appears."""
    client = _make_client(tmp_path)
    manifest_json, files = _manifest_and_files(tmp_path)
    resp = client.post(
        "/sessions", data={"manifest_json": manifest_json, "stage": "1"}, files=files
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]
    session_dir = tmp_path / "sessions" / session_id
    assert session_dir.exists()
    assert not (session_dir / "narration.wav").exists()


def test_ui_upload_with_narration_wav_writes_it_to_session_dir(tmp_path):
    client = _make_client(tmp_path)
    manifest_json, files = _manifest_and_files(tmp_path)
    files = [
        ("manifest_file", ("manifest.json", manifest_json, "application/json")),
        *files,
        ("narration_wav_file", ("narration.wav", b"RIFF....WAVEfmt ", "audio/wav")),
    ]
    resp = client.post("/ui/upload", files=files, follow_redirects=False)
    assert resp.status_code == 303
    session_id = resp.headers["location"].rsplit("/", 1)[-1]
    wav_path = tmp_path / "sessions" / session_id / "narration.wav"
    assert wav_path.read_bytes() == b"RIFF....WAVEfmt "


def _create_with_wav(client, tmp_path, fixture="sample-manifest.json"):
    manifest_json, files = _manifest_and_files(tmp_path, fixture)
    files = [*files, ("narration_wav_file", ("narration.wav", b"RIFF....WAVEfmt ", "audio/wav"))]
    resp = client.post("/sessions", data={"manifest_json": manifest_json}, files=files)
    return resp.json()["session_id"]


def test_transcription_disabled_by_default_ignores_uploaded_wav(tmp_path):
    """[transcription].enabled defaults False -- a narration.wav sitting in
    the session dir must never be transcribed (no transcript.json), so a
    session that happens to have a WAV behaves exactly like one that
    doesn't unless the operator explicitly opted in on /ui/config too."""
    calls = []
    client = _make_client(
        tmp_path,
        transcription_enabled=False,
        transcriber_factory=lambda cfg: calls.append(cfg) or _StubTranscriber(),
    )
    session_id = _create_with_wav(client, tmp_path)
    _wait_for_terminal_status(client, session_id)
    session_dir = tmp_path / "sessions" / session_id
    assert (session_dir / "narration.wav").exists()
    assert not (session_dir / "transcript.json").exists()
    assert not (session_dir / "transcript.md").exists()
    assert calls == []


def test_transcription_enabled_writes_transcript_json_and_report_note(tmp_path):
    manifest_json, _ = _manifest_and_files(tmp_path)
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    segments = [
        {"text": seg_text, "start": float(i)}
        for i, seg_text in enumerate(f"narration for {s.id}" for s in manifest.steps)
    ]
    client = _make_client(
        tmp_path,
        transcription_enabled=True,
        transcriber_factory=lambda cfg: _StubTranscriber(segments=segments),
    )
    session_id = _create_with_wav(client, tmp_path)
    status = _wait_for_terminal_status(client, session_id)
    session_dir = tmp_path / "sessions" / session_id
    assert (session_dir / "transcript.json").exists()
    md = (session_dir / "transcript.md").read_text(encoding="utf-8")
    assert "narration for" in md
    assert "0.0s" in md
    if status["status"] == "done":
        report = client.get(f"/sessions/{session_id}/report").json()
        assert "narration_transcription" in report


def test_transcription_failure_is_graceful(tmp_path):
    """A stub simulating a missing model/unavailable hardware must never
    fail the whole generation job -- the doc still ships from steps alone,
    matching CLAUDE.md's "template fallback, never a retry loop" spirit for
    this best-effort add-on."""
    client = _make_client(
        tmp_path,
        transcription_enabled=True,
        transcriber_factory=lambda cfg: _StubTranscriber(
            raises=RuntimeError("simulated: no whisper model available")
        ),
    )
    session_id = _create_with_wav(client, tmp_path)
    status = _wait_for_terminal_status(client, session_id)
    session_dir = tmp_path / "sessions" / session_id
    assert not (session_dir / "transcript.json").exists()
    assert not (session_dir / "transcript.md").exists()
    if status["status"] == "done":
        report = client.get(f"/sessions/{session_id}/report").json()
        assert "could not be transcribed" in report.get("narration_transcription", "")


def test_explicit_transcript_wins_over_narration_wav(tmp_path):
    """An uploaded human transcript must never be overwritten/ignored in
    favor of a derived one, even with transcription enabled."""
    calls = []
    client = _make_client(
        tmp_path,
        transcription_enabled=True,
        transcriber_factory=lambda cfg: calls.append(cfg) or _StubTranscriber(),
    )
    manifest_json, files = _manifest_and_files(tmp_path)
    files = [
        *files,
        ("narration_wav_file", ("narration.wav", b"RIFF....WAVEfmt ", "audio/wav")),
        ("transcript_file", ("transcript.txt", "Step one narration.\n", "text/plain")),
    ]
    resp = client.post("/sessions", data={"manifest_json": manifest_json}, files=files)
    session_id = resp.json()["session_id"]
    _wait_for_terminal_status(client, session_id)
    session_dir = tmp_path / "sessions" / session_id
    assert (session_dir / "transcript.txt").exists()
    assert not (session_dir / "transcript.json").exists()
    assert not (session_dir / "transcript.md").exists()
    assert calls == []  # the transcriber must never even have been constructed
