"""Phase 7: authored content (authoring.py) in the VRSI-layout docx and PDF
-- commands, tables, warnings, blocks, prerequisites, revision rows, the
version stamp and title line 2 all render, and the manifest's steps are
still exactly the steps, in order."""

import re
from pathlib import Path

import docx
from PIL import Image
from pypdf import PdfReader

from pipeline import authoring as auth
from pipeline.assembler import step_heading
from pipeline.docx_assembler import assemble_docx
from pipeline.export_pdf import render_pdf
from pipeline.manifest import load_manifest
from pipeline.render import render_steps_template_mode

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _setup(tmp_path):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    shots = tmp_path / "screenshots"
    shots.mkdir()
    for step in manifest.steps:
        Image.new("RGB", (640, 360), (255, 255, 255)).save(shots / step.screenshot)
    results, paths = render_steps_template_mode(manifest, shots, tmp_path / "annotated")
    ids = manifest.step_ids()
    authoring = auth.validate(
        {
            "title_line2": "second line",
            "prerequisites": ["Admin rights on {{host}}"],
            "params": [{"name": "host", "label": "Host", "default": "srv01"}],
            "revisions": [
                {"date": "01/02/2025", "revision": "0.9", "comment": "Draft", "author": "BK"}
            ],
            "steps": {
                ids[0]: {
                    "bullets": ["Pick **Advanced**"],
                    "commands": ["ping {{host}}"],
                    "warning": "Do not reboot",
                    "table": {"headers": ["Scenario", "Example Value"], "rows": [["Lab", "L-1"]]},
                    "system_only": "Ford Only",
                    "width_in": 3.0,
                }
            },
            "blocks": [
                {
                    "after": ids[0],
                    "heading": "Open a prompt",
                    "text": "Run it as **admin**.",
                    "commands": ["ipconfig /flushdns"],
                    "placeholder": "prompt window",
                }
            ],
        },
        ids,
    )
    return manifest, results, paths, tmp_path / "annotated", authoring


def test_docx_renders_authored_content_and_keeps_steps(tmp_path):
    manifest, results, _paths, annotated, a = _setup(tmp_path)
    out, warnings = assemble_docx(
        manifest,
        results,
        annotated,
        tmp_path / "a.docx",
        authoring=a,
        version_stamp="SOPForge v9.9",
    )
    assert warnings == []
    document = docx.Document(out)
    texts = [p.text for p in document.paragraphs]
    body = "\n".join(texts)
    for needle in (
        "SOPForge v9.9",
        "SECOND LINE",
        "Admin rights on srv01",
        "Pick Advanced",
        "ping srv01",
        "Warning: Do not reboot",
        "Ford Only",
        "B)  Open a prompt",
        "Run it as admin.",
        "ipconfig /flushdns",
        "[IMAGE PLACEHOLDER: prompt window]",
        "1.  Before You Begin",
        "2.  Procedure",
    ):
        assert needle in body, needle
    cells = [c.text for t in document.tables for row in t.rows for c in row.cells]
    assert "L-1" in cells and "Draft" in cells and "Initial generation" in cells
    # Every step heading is lettered, once, in manifest order (block B in between).
    step_headings = [t for t in texts if re.match(r"^[A-Z]\)  ", t) and t != "B)  Open a prompt"]
    assert len(step_headings) == len(manifest.steps)
    captions = [t for t in texts if t.startswith("Step ")]
    assert captions == [
        step_heading(n, s, r) for n, (s, r) in enumerate(zip(manifest.steps, results), 1)
    ]


def test_pdf_renders_authored_content_and_keeps_steps(tmp_path):
    manifest, results, paths, _annotated, a = _setup(tmp_path)
    out = render_pdf(
        manifest, results, paths, tmp_path / "a.pdf", authoring=a, version_stamp="SOPForge v9.9"
    )
    text = re.sub(r"\s+", " ", "\n".join(p.extract_text() for p in PdfReader(str(out)).pages))
    for needle in (
        "SOPForge v9.9",
        "SECOND LINE",
        "Admin rights on srv01",
        "Pick Advanced",
        "ping srv01",
        "Warning: Do not reboot",
        "Ford Only",
        "B) Open a prompt",
        "ipconfig /flushdns",
        "[IMAGE PLACEHOLDER: prompt window]",
        "L-1",
        "Draft",
        "Initial generation",
    ):
        assert needle in text, needle
    positions = [text.index(r["text"]) for r in results]
    assert positions == sorted(positions)  # steps in manifest order


def test_empty_authoring_has_no_version_stamp_or_extras(tmp_path):
    manifest, results, _paths, annotated, _a = _setup(tmp_path)
    out, _ = assemble_docx(manifest, results, annotated, tmp_path / "e.docx", authoring=auth.EMPTY)
    body = "\n".join(p.text for p in docx.Document(out).paragraphs)
    assert "SOP Factory v" not in body and "Before You Begin" not in body
    assert "1.  Procedure" in body
