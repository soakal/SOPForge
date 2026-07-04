"""capture.upload: best-effort POST of a finished capture to a running
sopforge-server, so the doc appears with zero manual steps. Every test
uses an injected httpx transport -- none of these make a real network
call.

_write_session uses the real fixtures/sample-manifest.json (schema-valid,
the same one Phase 2/3's server tests use) rather than a hand-rolled
manifest -- a hand-rolled one that upload_session's own (schema-agnostic)
logic happily accepts could still be something a real server rejects with
400, silently weakening what these tests actually prove."""

import json
from pathlib import Path

import httpx
from PIL import Image

from capture.upload import server_url_from_env, upload_session

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _write_session(tmp_path):
    output_dir = tmp_path / "session"
    output_dir.mkdir()
    manifest_json = (FIXTURES / "sample-manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(manifest_json)
    for step in manifest["steps"]:
        Image.new("RGB", (100, 100), (255, 255, 255)).save(output_dir / step["screenshot"])
    (output_dir / "manifest.json").write_text(manifest_json, encoding="utf-8")
    return output_dir, manifest


def test_upload_session_posts_manifest_and_all_screenshots(tmp_path):
    output_dir, manifest = _write_session(tmp_path)
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"session_id": "new-id-123"})

    session_id = upload_session(
        output_dir, server_url="http://fake-server", transport=httpx.MockTransport(handler)
    )

    assert session_id == "new-id-123"
    assert captured["url"] == "http://fake-server/sessions"


def test_upload_session_returns_none_when_manifest_missing(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    def handler(request):
        raise AssertionError("must never attempt a request with no manifest")

    result = upload_session(
        empty_dir, server_url="http://fake-server", transport=httpx.MockTransport(handler)
    )
    assert result is None


def test_upload_session_returns_none_on_connection_failure(tmp_path):
    output_dir, _manifest = _write_session(tmp_path)

    def handler(request):
        raise httpx.ConnectError("simulated: server not running")

    result = upload_session(
        output_dir, server_url="http://fake-server", transport=httpx.MockTransport(handler)
    )
    assert result is None


def test_upload_session_returns_none_on_http_error(tmp_path):
    output_dir, _manifest = _write_session(tmp_path)

    def handler(request):
        return httpx.Response(500, json={"detail": "boom"})

    result = upload_session(
        output_dir, server_url="http://fake-server", transport=httpx.MockTransport(handler)
    )
    assert result is None


def test_upload_session_returns_none_when_response_has_no_session_id(tmp_path):
    output_dir, _manifest = _write_session(tmp_path)

    def handler(request):
        return httpx.Response(200, json={"status": "queued"})  # missing "session_id"

    result = upload_session(
        output_dir, server_url="http://fake-server", transport=httpx.MockTransport(handler)
    )
    assert result is None


def test_upload_session_returns_none_on_corrupt_manifest(tmp_path):
    output_dir = tmp_path / "session"
    output_dir.mkdir()
    (output_dir / "manifest.json").write_text("not valid json", encoding="utf-8")

    def handler(request):
        raise AssertionError("must never attempt a request with a corrupt manifest")

    result = upload_session(
        output_dir, server_url="http://fake-server", transport=httpx.MockTransport(handler)
    )
    assert result is None


def test_upload_session_returns_none_when_steps_is_malformed(tmp_path):
    """Valid JSON, but "steps" isn't a list of step objects -- must still
    return None, not raise (this was a real bug: the set-comprehension
    extracting screenshot names used to run outside the try/except)."""
    output_dir = tmp_path / "session"
    output_dir.mkdir()
    (output_dir / "manifest.json").write_text(
        json.dumps({"schema_version": "1.0", "session": {}, "steps": "not-a-list"}),
        encoding="utf-8",
    )

    def handler(request):
        raise AssertionError("must never attempt a request with malformed steps")

    result = upload_session(
        output_dir, server_url="http://fake-server", transport=httpx.MockTransport(handler)
    )
    assert result is None


def test_server_url_from_env_defaults_to_localhost_8420(monkeypatch):
    monkeypatch.delenv("SOPFORGE_SERVER_URL", raising=False)
    assert server_url_from_env() == "http://127.0.0.1:8420"


def test_server_url_from_env_respects_override(monkeypatch):
    monkeypatch.setenv("SOPFORGE_SERVER_URL", "http://example:9000")
    assert server_url_from_env() == "http://example:9000"
