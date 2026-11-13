"""Phase 7 (phases/07-factory-parity-2.md): authored content -- validation,
stale-file tolerance, the shared VRSI outline, and the review-page route
reaching every format without changing the step set (AC1-AC3)."""

import io
import json
import time
import zipfile
from pathlib import Path

import docx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pipeline import authoring
from pipeline.assembler import document_outline, toc_lines
from pipeline.grouping import Phase
from pipeline.manifest import load_manifest
from pipeline.server import create_app

from _stub_llm import stub_llm_client_factory

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _manifest():
    return load_manifest(FIXTURES / "sample-manifest.json")


def _ids():
    return [s.id for s in _manifest().steps]


@pytest.mark.parametrize(
    "raw, field",
    [
        ({"steps": {"nope": {}}}, "steps"),
        ({"date": "2026-01-01"}, "date"),
        ({"paths": ["A"], "steps": {"__first__": {"path": "B"}}}, "path"),
        ({"params": [{"name": "1bad"}]}, "params"),
        ({"blocks": [{"after": "nope", "text": "x"}]}, "after"),
        ({"steps": {"__first__": {"width_in": 20}}}, "width_in"),
        ({"steps": {"__first__": {"callouts": [[120, 5]]}}}, "callouts"),
        ({"steps": {"__first__": {"table": {"rows": [["only one"]]}}}}, "rows"),
    ],
)
def test_invalid_authoring_names_the_field(raw, field):
    ids = _ids()
    raw = json.loads(json.dumps(raw).replace("__first__", ids[0]))
    with pytest.raises(ValueError, match=field):
        authoring.validate(raw, ids)


def test_example_validates_once_its_placeholder_is_dropped():
    assert authoring.validate(authoring.EXAMPLE, _ids())["steps"] == {}


def test_stale_file_keeps_what_still_applies(tmp_path):
    ids = _ids()
    (tmp_path / authoring.FILE_NAME).write_text(
        json.dumps(
            {
                "prerequisites": ["Admin rights"],
                "steps": {"gone-step": {"warning": "x"}, ids[0]: {"warning": "Careful"}},
                "blocks": [{"after": "gone-step", "text": "a"}, {"after": ids[0], "text": "b"}],
            }
        ),
        encoding="utf-8",
    )
    a = authoring.load(tmp_path, ids)
    assert a["prerequisites"] == ["Admin rights"]
    assert list(a["steps"]) == [ids[0]]
    assert [b["text"] for b in a["blocks"]] == ["b"]


def test_save_refuses_bad_json_and_blank_clears(tmp_path):
    with pytest.raises(ValueError, match="JSON"):
        authoring.save(tmp_path, "{nope", _ids())
    authoring.save(tmp_path, '{"prerequisites": ["x"]}', _ids())
    assert (tmp_path / authoring.FILE_NAME).exists()
    assert authoring.save(tmp_path, "  ", _ids()) == authoring.EMPTY
    assert not (tmp_path / authoring.FILE_NAME).exists()


def test_outline_keeps_every_step_once_in_order_and_letters_per_section():
    m = _manifest()
    ids = _ids()
    phases = [Phase("Open", tuple(ids[:2])), Phase("Save", tuple(ids[2:]))]
    a = authoring.validate(
        {
            "prerequisites": ["Admin rights"],
            "blocks": [
                {"after": "", "heading": "Before the first click", "text": "x"},
                {
                    "after": ids[0],
                    "section": "Extra",
                    "heading": "Run it",
                    "commands": ["ipconfig"],
                },
            ],
        },
        ids,
    )
    items = document_outline(m, None, "Narrative.", phases, a)
    assert [i[2].id for i in items if i[0] == "step"] == ids
    sections = [i[2] for i in items if i[0] == "section"]
    assert sections[0] == "1.  Overview"
    assert sections[1] == "2.  Before You Begin"
    assert sections[2] == "3.  Open"
    assert "4.  Extra" in sections and sections[-1].endswith("Revision History")
    labels = [i[-1] for i in items if i[0] in ("step", "block") and i[-1]]
    assert labels[0].startswith("A)  Before the first click")
    assert labels[1].startswith("B)  ")
    assert any(label.startswith("A)  Run it") for label in labels)
    assert toc_lines(m, "Narrative.", phases, None, a)[-1].endswith("Revision History")


def test_no_authoring_outline_matches_plain_numbering():
    m = _manifest()
    items = document_outline(m)
    assert [i[2] for i in items if i[0] == "section"] == ["1.  Procedure", "2.  Revision History"]
    assert [i[4][:3] for i in items if i[0] == "step"][:2] == ["A) ", "B) "]


def _wait(client, session_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status = client.get(f"/sessions/{session_id}/status").json()["status"]
        if status in ("done", "error"):
            assert status == "done"
            return
        time.sleep(0.05)
    raise AssertionError("session never finished")


def test_authoring_route_reaches_every_format(tmp_path):
    client = TestClient(
        create_app(
            sessions_root=tmp_path / "sessions",
            llm_client_factory=stub_llm_client_factory,
            narrative_llm_client_factory=stub_llm_client_factory,
        )
    )
    files = []
    for step in _manifest().steps:
        p = tmp_path / step.screenshot
        Image.new("RGB", (1920, 1080), "white").save(p)
        files.append(("files", (step.screenshot, p.open("rb"), "image/png")))
    manifest_json = (FIXTURES / "sample-manifest.json").read_text()
    resp = client.post("/sessions", data={"manifest_json": manifest_json}, files=files)
    sid = resp.json()["session_id"]
    _wait(client, sid)
    first = _ids()[0]

    bad = client.post(f"/ui/sessions/{sid}/authoring", data={"authoring": '{"date": "soon"}'})
    assert bad.status_code == 400 and "MM/DD/YYYY" in bad.text and "soon" in bad.text

    body = {
        "date": "02/03/2026",
        "prerequisites": ["Admin rights on the PC"],
        "params": [{"name": "ip", "label": "Server IP", "default": "10.9.8.7"}],
        "revisions": [
            {"date": "01/01/2025", "revision": "0.9", "comment": "Draft", "author": "BK"}
        ],
        "steps": {first: {"commands": ["ping {{ip}}"], "warning": "Mind the gap"}},
        "blocks": [{"after": first, "heading": "Flush DNS", "commands": ["ipconfig /flushdns"]}],
    }
    resp = client.post(
        f"/ui/sessions/{sid}/authoring",
        data={"authoring": json.dumps(body)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    _wait(client, sid)

    d = docx.Document(io.BytesIO(client.get(f"/sessions/{sid}/doc.docx").content))
    text = "\n".join(p.text for p in d.paragraphs)
    cells = "\n".join(c.text for t in d.tables for row in t.rows for c in row.cells)
    assert "ping 10.9.8.7" in text and "ipconfig /flushdns" in text
    assert "Mind the gap" in text and "Admin rights on the PC" in text
    assert "Draft" in cells and "02/03/2026" in cells

    md = client.get(f"/sessions/{sid}/doc.md").text
    assert "ping 10.9.8.7" in md and "Flush DNS" in md
    walk = client.get(f"/sessions/{sid}/walkthrough.html").text
    assert "ipconfig /flushdns" in walk and "Server IP" in walk
    with zipfile.ZipFile(io.BytesIO(client.get(f"/sessions/{sid}/spl.pptx").content)) as z:
        slides = "".join(
            z.read(n).decode("utf-8", "replace") for n in z.namelist() if "slides/slide" in n
        )
    assert "Admin rights on the PC" in slides

    report = client.get(f"/sessions/{sid}/report").json()
    assert report["document"]["date"] == "02/03/2026"
    page = client.get(f"/ui/sessions/{sid}").text
    assert "Authored content" in page and "ipconfig /flushdns" in page
