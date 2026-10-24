"""Phase 6g (phases/06-factory-parity.md AC7): document metadata is inferred
so nothing has to be entered, a reviewer can override any field, and
downloads are named after the procedure."""

import io
import json
import time
import zipfile
from pathlib import Path

import docx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import pipeline.doc_meta as doc_meta
from pipeline.config import DocumentConfig
from pipeline.doc_meta import (
    DocMeta,
    download_basename,
    inferred_meta,
    resolve_meta,
    save_overrides,
    validate_overrides,
)
from pipeline.manifest import load_manifest
from pipeline.server import create_app

from _stub_llm import stub_llm_client_factory

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _manifest():
    return load_manifest(FIXTURES / "sample-manifest.json")


def test_defaults_need_no_input(monkeypatch):
    monkeypatch.setattr(doc_meta.getpass, "getuser", lambda: "bkalsic")
    m = _manifest()
    meta = inferred_meta(m, DocumentConfig())
    assert meta.author == "bkalsic"  # the stock "SOPForge" author isn't a person
    assert meta.doc_no == f"SOP-{m.session.id[-4:].upper()}"
    assert meta.revision == "1.0" and meta.subtitle is None
    assert meta.title == (m.session.title or m.session.id)


def test_configured_author_and_prefix_win(monkeypatch):
    monkeypatch.setattr(doc_meta.getpass, "getuser", lambda: "bkalsic")
    meta = inferred_meta(_manifest(), DocumentConfig(author="BK", doc_no_prefix="VRSI"))
    assert meta.author == "BK" and meta.doc_no.startswith("VRSI-")


def test_no_user_name_falls_back(monkeypatch):
    def boom():
        raise OSError("no user")

    monkeypatch.setattr(doc_meta.getpass, "getuser", boom)
    assert inferred_meta(_manifest(), DocumentConfig()).author == "SOPForge"


def test_overrides_round_trip_and_blank_clears(tmp_path):
    m = _manifest()
    save_overrides(tmp_path, {"subtitle": "  VRSI   procedure ", "revision": "2.1", "title": "x"})
    meta = resolve_meta(m, DocumentConfig(), tmp_path)
    assert meta.subtitle == "VRSI procedure" and meta.revision == "2.1"
    assert "title" not in json.loads((tmp_path / "metadata.json").read_text())
    save_overrides(tmp_path, {"revision": ""})
    assert resolve_meta(m, DocumentConfig(), tmp_path).revision == "1.0"


def test_bad_values_rejected():
    with pytest.raises(ValueError, match="revision"):
        validate_overrides({"revision": "draft"})
    with pytest.raises(ValueError, match="longer"):
        validate_overrides({"author": "x" * 121})


def test_download_basename_is_factory_style():
    def base(title, rev="1.0"):
        return download_basename(DocMeta(title, None, "", "", rev))

    assert base("Create a SmartDeploy Answer File") == "Create_a_SmartDeploy_Answer_File_v1.0"
    assert base('Reset: Password / Duo "Enable-Disable"', "2.0") == (
        "Reset_Password_Duo_Enable-Disable_v2.0"
    )
    assert base("???") == "SOP_v1.0"


def _wait(client, session_id):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status = client.get(f"/sessions/{session_id}/status").json()["status"]
        if status in ("done", "error"):
            assert status == "done"
            return
        time.sleep(0.05)
    raise AssertionError("session never finished")


def test_metadata_edit_reexports_every_format(tmp_path):
    client = TestClient(
        create_app(
            sessions_root=tmp_path / "sessions",
            llm_client_factory=stub_llm_client_factory,
            narrative_llm_client_factory=stub_llm_client_factory,
        )
    )
    manifest_path = FIXTURES / "sample-manifest.json"
    files = []
    for step in _manifest().steps:
        p = tmp_path / step.screenshot
        Image.new("RGB", (1920, 1080), "white").save(p)
        files.append(("files", (step.screenshot, p.open("rb"), "image/png")))
    resp = client.post("/sessions", data={"manifest_json": manifest_path.read_text()}, files=files)
    session_id = resp.json()["session_id"]
    _wait(client, session_id)
    assert client.get(f"/sessions/{session_id}/report").json()["document"]["revision"] == "1.0"

    bad = client.post(f"/ui/sessions/{session_id}/metadata", data={"revision": "final"})
    assert bad.status_code == 400

    resp = client.post(
        f"/ui/sessions/{session_id}/metadata",
        data={
            "title": "Create a SmartDeploy Answer File",
            "subtitle": "VRSI procedure",
            "author": "BK (Brian Kalsic)",
            "revision": "2.0",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    _wait(client, session_id)

    report = client.get(f"/sessions/{session_id}/report").json()["document"]
    assert report["title"] == "Create a SmartDeploy Answer File" and report["revision"] == "2.0"
    resp = client.get(f"/sessions/{session_id}/doc.docx")
    assert (
        'filename="Create_a_SmartDeploy_Answer_File_v2.0.docx"'
        in resp.headers["content-disposition"]
    )
    text = "\n".join(p.text for p in docx.Document(io.BytesIO(resp.content)).paragraphs)
    assert "VRSI procedure" in text and "BK (Brian Kalsic)" in text
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        header = "".join(
            zf.read(n).decode("utf-8", "ignore") for n in zf.namelist() if "header" in n
        )
    assert "2.0" in header
    library = client.get("/library").json()
    assert any(e.get("title") == "Create a SmartDeploy Answer File" for e in library)


def test_doc_numbers_are_sequential_unique_and_stable(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from pipeline.doc_meta import assign_doc_number

    dirs = []
    for i in range(20):
        d = tmp_path / f"session-{i}"
        d.mkdir()
        dirs.append(d)
    with ThreadPoolExecutor(max_workers=8) as pool:
        numbers = list(pool.map(lambda d: assign_doc_number(d, "SOP-IT"), dirs))
    assert sorted(numbers) == [f"SOP-IT-{n:04d}" for n in range(1, 21)]
    # A session keeps its number on every later export.
    assert [assign_doc_number(d, "SOP-IT") for d in dirs] == numbers
    # Each prefix counts on its own.
    other = tmp_path / "other"
    other.mkdir()
    assert assign_doc_number(other, "VRSI") == "VRSI-0001"


def test_resolve_meta_uses_the_counter_and_overrides_still_win(tmp_path):
    m = _manifest()
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    cfg = DocumentConfig(doc_no_prefix="SOP-IT-LIC")
    assert resolve_meta(m, cfg, first).doc_no == "SOP-IT-LIC-0001"
    assert resolve_meta(m, cfg, second).doc_no == "SOP-IT-LIC-0002"
    save_overrides(first, {"doc_no": "SOP-IT-LIC-001"})
    assert resolve_meta(m, cfg, first).doc_no == "SOP-IT-LIC-001"
