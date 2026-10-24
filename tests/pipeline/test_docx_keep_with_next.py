"""Phase 6a (phases/06-factory-parity.md AC1): the re-vendored SOP Factory
engine keeps headings and the bullet before each screenshot on the same page
as what follows, so a step's text never strands at the bottom of a page with
its image on the next. Also pins that code_block() came across with it."""

from pathlib import Path

import docx
from PIL import Image

from pipeline.docx_assembler import _import_sop_builder, assemble_docx
from pipeline.manifest import load_manifest
from pipeline.render import render_steps_template_mode

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _build(tmp_path):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    screenshots.mkdir(parents=True)
    for step in manifest.steps:
        Image.new("RGB", (1920, 1080), (255, 255, 255)).save(screenshots / step.screenshot)
    step_results, _ = render_steps_template_mode(manifest, screenshots, annotated)
    out, warnings = assemble_docx(manifest, step_results, annotated, tmp_path / "out.docx")
    assert warnings == []
    return manifest, docx.Document(out)


def _has_image(paragraph):
    return bool(paragraph._p.xpath(".//w:drawing"))


def test_paragraph_before_each_image_keeps_with_next(tmp_path):
    manifest, document = _build(tmp_path)
    paragraphs = document.paragraphs
    image_indexes = [i for i, p in enumerate(paragraphs) if _has_image(p)]
    assert len(image_indexes) == len(manifest.steps)
    for i in image_indexes:
        assert paragraphs[i - 1].paragraph_format.keep_with_next, paragraphs[i - 1].text


def test_step_headings_keep_with_next(tmp_path):
    _, document = _build(tmp_path)
    for heading in ("Procedure", "Revision History"):
        matches = [p for p in document.paragraphs if p.text == heading]
        assert matches, heading
        assert matches[0].paragraph_format.keep_with_next


def test_vendored_engine_has_code_block():
    assert callable(getattr(_import_sop_builder(), "code_block", None))
