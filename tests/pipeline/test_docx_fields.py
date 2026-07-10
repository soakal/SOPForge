"""docx_fields.py: a real Word TOC field (not SOPBuilder's own plain-text
toc()) layered onto the external SOP Factory 2 engine, driven through the
real engine end-to-end via assemble_docx -- proves the wiring, not just the
XML-builder functions in isolation."""

import re
import zipfile
from pathlib import Path

from PIL import Image

from pipeline.docx_assembler import assemble_docx
from pipeline.manifest import load_manifest
from pipeline.render import render_steps_template_mode

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"

_FLD_CHAR_RE = re.compile(r'<w:fldChar w:fldCharType="(begin|separate|end)"/>')
_INSTR_TEXT_RE = re.compile(r"<w:instrText[^>]*>([^<]*)</w:instrText>")
_OUTLINE_LVL_RE = re.compile(r'<w:outlineLvl w:val="(\d+)"/>')


def _make_screenshots(manifest, directory):
    directory.mkdir(parents=True, exist_ok=True)
    for step in manifest.steps:
        Image.new("RGB", (1920, 1080), (255, 255, 255)).save(directory / step.screenshot)


def _build(tmp_path, narrative_text=None):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, _annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

    output_path = tmp_path / "out.docx"
    out, warnings = assemble_docx(
        manifest, step_results, annotated, output_path, narrative_text=narrative_text
    )
    assert warnings == []
    with zipfile.ZipFile(out) as zf:
        document_xml = zf.read("word/document.xml").decode("utf-8")
        settings_xml = zf.read("word/settings.xml").decode("utf-8")
    return manifest, document_xml, settings_xml


def test_toc_is_a_real_field_not_plain_text(tmp_path):
    _manifest, document_xml, _settings_xml = _build(tmp_path)

    fld_types = _FLD_CHAR_RE.findall(document_xml)
    assert fld_types == ["begin", "separate", "end"]

    instructions = _INSTR_TEXT_RE.findall(document_xml)
    assert len(instructions) == 1
    assert 'TOC \\o "1-2" \\h \\z \\u' in instructions[0]


def test_toc_field_caches_the_same_text_toc_lines_would_show(tmp_path):
    from pipeline.assembler import toc_lines

    manifest, document_xml, _settings_xml = _build(tmp_path)

    for line in toc_lines(manifest, narrative_text=None):
        assert line in document_xml


def test_section_and_step_headings_carry_outline_levels(tmp_path):
    _manifest, document_xml, _settings_xml = _build(tmp_path)

    levels = {int(v) for v in _OUTLINE_LVL_RE.findall(document_xml)}
    # Level 0: "Procedure"/"Revision History" section headings.
    # Level 1: per-step headings.
    assert levels == {0, 1}


def test_update_fields_on_open_is_set_so_word_refreshes_automatically(tmp_path):
    _manifest, _document_xml, settings_xml = _build(tmp_path)

    assert '<w:updateFields w:val="true"/>' in settings_xml
