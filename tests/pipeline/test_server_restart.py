"""Session persistence across a server restart, and the new UI-only
routes (upload form, rerender redirect, delete) added alongside the
restart fix. Before this fix, `sessions` was purely in-memory and
populated only by POST /sessions -- a real, reproducible bug: after any
restart, every previously-completed session became permanently
inaccessible via the API/UI even though the persistent library index
still listed it and its generated docs were still on disk."""

import io
import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from pipeline.manifest import load_manifest
from pipeline.server import create_app

from _stub_llm import stub_llm_client_factory

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _make_client(sessions_root):
    app = create_app(sessions_root=sessions_root, llm_client_factory=stub_llm_client_factory)
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


def _create_and_wait(client, tmp_path, fixture="sample-manifest.json"):
    manifest_json, files = _manifest_and_files(tmp_path, fixture)
    resp = client.post("/sessions", data={"manifest_json": manifest_json}, files=files)
    session_id = resp.json()["session_id"]
    for _ in range(200):
        status = client.get(f"/sessions/{session_id}/status").json()
        if status["status"] == "done":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("session never reached done")
    return session_id


def test_session_survives_a_simulated_server_restart(tmp_path):
    sessions_root = tmp_path / "sessions"
    client1 = _make_client(sessions_root)
    session_id = _create_and_wait(client1, tmp_path)

    # A restart means a brand new create_app() call (fresh in-memory
    # `sessions` dict, fresh JobRunner) against the SAME sessions_root --
    # nothing here shares Python-level state with client1.
    client2 = _make_client(sessions_root)

    status = client2.get(f"/sessions/{session_id}/status").json()
    assert status["status"] == "done"

    report = client2.get(f"/sessions/{session_id}/report")
    assert report.status_code == 200

    docx = client2.get(f"/sessions/{session_id}/doc.docx")
    assert docx.status_code == 200
    assert docx.content[:2] == b"PK"  # a real zip/docx, not an error page

    ui_page = client2.get(f"/ui/sessions/{session_id}")
    assert ui_page.status_code == 200
    assert "Back to library" in ui_page.text

    library = client2.get("/library").json()
    assert any(e["session_id"] == session_id for e in library)


def test_rerender_still_works_after_a_restart(tmp_path):
    """Rerendering a restored session must work -- this exercises that the
    restored screenshots_dir/annotated_dir paths are correct, not just the
    manifest object."""
    sessions_root = tmp_path / "sessions"
    client1 = _make_client(sessions_root)
    session_id = _create_and_wait(client1, tmp_path)

    client2 = _make_client(sessions_root)
    resp = client2.post(f"/sessions/{session_id}/rerender")
    assert resp.status_code == 200
    for _ in range(200):
        status = client2.get(f"/sessions/{session_id}/status").json()
        if status["status"] == "done":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("rerender after restart never reached done")


def test_uploaded_session_not_yet_done_is_not_restored(tmp_path):
    """A session directory with a manifest.json but no report.json (never
    finished, e.g. the process died mid-generation) must not be restored --
    there is no completed output to serve, and it was never added to the
    library index either."""
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    orphan_dir = sessions_root / "orphan-session"
    orphan_dir.mkdir()
    (orphan_dir / "manifest.json").write_text("{}", encoding="utf-8")
    # deliberately no report.json

    client = _make_client(sessions_root)
    resp = client.get("/sessions/orphan-session/status")
    assert resp.status_code == 404


def test_ui_upload_form_creates_a_session_and_redirects(tmp_path):
    client = _make_client(tmp_path / "sessions")
    manifest_json, files = _manifest_and_files(tmp_path)
    manifest_file = (
        "manifest_file",
        ("manifest.json", io.BytesIO(manifest_json.encode()), "application/json"),
    )

    resp = client.post(
        "/ui/upload",
        files=[manifest_file] + files,
        follow_redirects=False,
    )
    assert resp.status_code == 303
    session_id = resp.headers["location"].removeprefix("/ui/sessions/")

    for _ in range(200):
        status = client.get(f"/sessions/{session_id}/status").json()
        if status["status"] == "done":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("session created via /ui/upload never reached done")


def test_library_page_has_upload_form():
    from pipeline.webui.pages import render_library_page

    page = render_library_page([])
    assert '<form method="post" action="/ui/upload" enctype="multipart/form-data">' in page
    assert 'name="manifest_file"' in page
    assert 'name="files"' in page


def test_ui_delete_removes_session_from_disk_library_and_memory(tmp_path):
    sessions_root = tmp_path / "sessions"
    client = _make_client(sessions_root)
    session_id = _create_and_wait(client, tmp_path)

    session_dir = sessions_root / session_id
    assert session_dir.exists()
    assert any(e["session_id"] == session_id for e in client.get("/library").json())

    resp = client.post(f"/ui/sessions/{session_id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui"

    assert not session_dir.exists()
    assert not any(e["session_id"] == session_id for e in client.get("/library").json())
    assert client.get(f"/sessions/{session_id}/status").status_code == 404


def test_ui_delete_unknown_session_returns_404(tmp_path):
    client = _make_client(tmp_path / "sessions")
    resp = client.post("/ui/sessions/does-not-exist/delete")
    assert resp.status_code == 404
