"""Golden-file regression test (AC5): the docx assembler's output for
`fixtures/sample-manifest.json`, in template mode, must byte-compare equal
(after task-14's rsid/timestamp normalization) to the committed
`fixtures/golden-document.xml` — any unintended change to SOPBuilder's
output for this exact fixture fails this test."""

from pathlib import Path

from PIL import Image

from pipeline.docx_assembler import assemble_docx
from pipeline.golden import compare_document_xml, compare_document_xml_to_golden_file
from pipeline.manifest import load_manifest
from pipeline.render import render_steps_template_mode

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"
GOLDEN_XML = FIXTURES / "golden-document.xml"


def _build_docx(tmp_path):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    screenshots.mkdir(parents=True)
    for step in manifest.steps:
        Image.new("RGB", (1920, 1080), (255, 255, 255)).save(screenshots / step.screenshot)

    step_results, _annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)
    output_path = tmp_path / "out.docx"
    out, warnings = assemble_docx(manifest, step_results, annotated, output_path)
    assert warnings == []
    return out


def test_docx_matches_committed_golden_document_xml(tmp_path):
    out = _build_docx(tmp_path)
    match, actual, golden = compare_document_xml_to_golden_file(out, GOLDEN_XML)
    assert match, (
        f"generated document.xml no longer matches the golden fixture "
        f"({len(actual)} vs {len(golden)} bytes after normalization)"
    )


def test_two_independent_builds_produce_byte_identical_document_xml(tmp_path):
    """The docx assembler must be deterministic given the same manifest and
    screenshots — otherwise the golden-file test above would be inherently
    flaky rather than a real regression signal."""
    out_a = _build_docx(tmp_path / "a")
    out_b = _build_docx(tmp_path / "b")
    match, actual, golden = compare_document_xml(out_a, out_b)
    assert match
    assert actual == golden
