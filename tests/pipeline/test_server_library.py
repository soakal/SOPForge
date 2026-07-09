"""Server library + config endpoints: GET /library?q= searches the
persistent session index by title/date substring; GET /config returns the
parsed config/models.toml for read-only display."""

import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from pipeline.config import load_models_config
from pipeline.manifest import load_manifest
from pipeline.server import create_app

from _stub_llm import stub_llm_client_factory

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _make_client(tmp_path):
    app = create_app(
        sessions_root=tmp_path / "sessions",
        llm_client_factory=stub_llm_client_factory,
        narrative_llm_client_factory=stub_llm_client_factory,
    )
    return TestClient(app)


def _wait_until_done(client, session_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/sessions/{session_id}/status").json()
        if status["status"] == "done":
            return
        if status["status"] == "error":
            raise AssertionError(f"session failed: {status.get('error')}")
        time.sleep(0.05)
    raise AssertionError(f"session {session_id} never reached done")


def _create_session(client, tmp_path, fixture="sample-manifest.json"):
    manifest_path = FIXTURES / fixture
    manifest = load_manifest(manifest_path)
    shots_dir = tmp_path / f"shots-{fixture}"
    shots_dir.mkdir(exist_ok=True)
    files = []
    for step in manifest.steps:
        p = shots_dir / step.screenshot
        Image.new("RGB", (1920, 1080), (255, 255, 255)).save(p)
        files.append(("files", (step.screenshot, p.open("rb"), "image/png")))
    resp = client.post(
        "/sessions", data={"manifest_json": manifest_path.read_text(encoding="utf-8")}, files=files
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]
    _wait_until_done(client, session_id)
    return session_id


def test_library_lists_completed_session_after_processing(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_session(client, tmp_path)

    resp = client.get("/library")
    assert resp.status_code == 200
    entries = resp.json()
    assert any(e["session_id"] == session_id for e in entries)
    entry = next(e for e in entries if e["session_id"] == session_id)
    assert entry["empty_metadata_count"] == 1  # sample-manifest.json's step-003


def test_library_search_by_query_param(tmp_path):
    client = _make_client(tmp_path)
    session_id = _create_session(client, tmp_path)

    entries = client.get("/library").json()
    title = next(e["title"] for e in entries if e["session_id"] == session_id)

    matched = client.get("/library", params={"q": title[:6]}).json()
    assert any(e["session_id"] == session_id for e in matched)

    unmatched = client.get("/library", params={"q": "definitely-not-a-real-title"}).json()
    assert unmatched == []


def test_library_empty_before_any_session(tmp_path):
    client = _make_client(tmp_path)
    assert client.get("/library").json() == []


def test_config_endpoint_matches_the_real_parsed_config(tmp_path):
    client = _make_client(tmp_path)
    resp = client.get("/config")
    assert resp.status_code == 200
    body = resp.json()
    expected = load_models_config().model_dump()
    assert body == expected
    assert body["steps"]["model"]
    assert body["narrative"]["passes"] >= 1
