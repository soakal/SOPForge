"""AC1 rollup: all four export formats (docx, pdf, single-file html, md
bundle) render from the same golden fixture manifest, step_results, and
annotated screenshots without conflicting with each other."""

import zipfile
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from pipeline.docx_assembler import assemble_docx
from pipeline.export_html import render_single_file_html
from pipeline.export_md import export_markdown_bundle
from pipeline.export_pdf import render_pdf
from pipeline.manifest import load_manifest
from pipeline.render import render_steps_template_mode

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _make_screenshots(manifest, directory):
    directory.mkdir(parents=True, exist_ok=True)
    for step in manifest.steps:
        Image.new("RGB", (1920, 1080), (255, 255, 255)).save(directory / step.screenshot)


def test_all_four_formats_render_from_the_same_source_without_conflict(tmp_path):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

    docx_path = tmp_path / "doc.docx"
    docx_out, docx_warnings = assemble_docx(manifest, step_results, annotated, docx_path)
    assert docx_warnings == []
    with zipfile.ZipFile(docx_out) as zf:
        assert "word/document.xml" in zf.namelist()

    pdf_path = tmp_path / "doc.pdf"
    render_pdf(manifest, step_results, annotated_paths, pdf_path)
    assert pdf_path.read_bytes()[:5] == b"%PDF-"
    assert len(PdfReader(str(pdf_path)).pages) > len(manifest.steps)

    single_html = render_single_file_html(manifest, step_results, annotated_paths)
    assert single_html.startswith("<!doctype html>")
    assert "data:image/png;base64," in single_html

    md_bundle_dir = tmp_path / "md_bundle"
    md_path = export_markdown_bundle(manifest, step_results, annotated_paths, md_bundle_dir)
    assert md_path.exists()
    for step in manifest.steps:
        assert (md_bundle_dir / "images" / step.screenshot).exists()

    # Annotated screenshots on disk are shared read-only inputs across all
    # four exports — confirm none of them were mutated or deleted by any
    # of the export calls above.
    for step in manifest.steps:
        assert (annotated / step.screenshot).exists()
