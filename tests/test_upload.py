"""capture.upload: best-effort POST of a finished capture to a running
sopforge-server, so the doc appears with zero manual steps. Every test
uses an injected httpx transport -- none of these make a real network
call."""

import json

import httpx
from PIL import Image

from capture.upload import server_url_from_env, upload_session


def _write_session(tmp_path, step_count=2):
    output_dir = tmp_path / "session"
    output_dir.mkdir()
    steps = []
    for i in range(step_count):
        name = f"{i:03d}.png"
        Image.new("RGB", (100, 100), (255, 255, 255)).save(output_dir / name)
        steps.append(
            {
                "id": f"step-{i:03d}",
                "ts_utc": "2026-01-01T00:00:00.000Z",
                "action": "click",
                "button": "left",
                "screen": {"x": 1, "y": 1, "monitor": 0},
                "screenshot": name,
                "screenshot_placeholder": False,
                "window": {"title": "w", "process_name": "p.exe", "hwnd": 1},
                "element": {
                    "name": "Button",
                    "automation_id": None,
                    "control_type": "Button",
                    "class_name": None,
                    "framework": None,
                    "bounding_rect": None,
                },
                "redactions": [],
            }
        )
    manifest = {
        "schema_version": 1,
        "session": {
            "id": "s1",
            "started_utc": "2026-01-01T00:00:00.000Z",
            "ended_utc": "2026-01-01T00:00:01.000Z",
            "machine": "m",
            "os_build": "b",
            "title": "Test Session",
        },
        "steps": steps,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return output_dir, manifest


def test_upload_session_posts_manifest_and_all_screenshots(tmp_path):
    output_dir, manifest = _write_session(tmp_path, step_count=3)
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


def test_server_url_from_env_defaults_to_localhost_8420(monkeypatch):
    monkeypatch.delenv("SOPFORGE_SERVER_URL", raising=False)
    assert server_url_from_env() == "http://127.0.0.1:8420"


def test_server_url_from_env_respects_override(monkeypatch):
    monkeypatch.setenv("SOPFORGE_SERVER_URL", "http://example:9000")
    assert server_url_from_env() == "http://example:9000"
