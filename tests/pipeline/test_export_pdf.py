"""PDF export (AC1 part 1): golden fixture manifest -> PDF with one section
per step, annotated screenshots embedded, [verify] blockquotes rendered.
pypdf (test-only) verifies the actual rendered text, not just that a file
was written."""

import re
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from pipeline.claim_coverage import ensure_claim_coverage
from pipeline.export_pdf import render_pdf
from pipeline.manifest import load_manifest
from pipeline.render import render_steps_template_mode

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _make_screenshots(manifest, directory):
    directory.mkdir(parents=True, exist_ok=True)
    for step in manifest.steps:
        Image.new("RGB", (1920, 1080), (255, 255, 255)).save(directory / step.screenshot)


def _extract_text(pdf_path):
    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() for page in reader.pages)


def _normalize_whitespace(text):
    """multi_cell() word-wraps long lines, so pypdf's extracted text embeds
    real newlines mid-sentence — a rendering artifact, not a content
    difference. Collapse all whitespace runs before substring comparison."""
    return re.sub(r"\s+", " ", text).strip()


def test_pdf_has_pdf_header_and_page_count_exceeds_step_count(tmp_path):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

    output_path = tmp_path / "out.pdf"
    render_pdf(manifest, step_results, annotated_paths, output_path)

    assert output_path.read_bytes()[:5] == b"%PDF-"
    reader = PdfReader(str(output_path))
    assert len(reader.pages) > len(manifest.steps)


def test_pdf_text_contains_every_step_title_and_text(tmp_path):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

    output_path = tmp_path / "out.pdf"
    render_pdf(manifest, step_results, annotated_paths, output_path)

    text = _normalize_whitespace(_extract_text(output_path))
    for n, (step, result) in enumerate(zip(manifest.steps, step_results), start=1):
        assert f"Step {n}" in text
        assert _normalize_whitespace(result["text"]) in text


def test_pdf_contains_verify_blockquote_claim_text(tmp_path):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

    claims = [
        {"claim_id": "claim-001", "text": "Something not in the narrative at all.", "ts": 0.0}
    ]
    final_text, _covered, _verify_ids = ensure_claim_coverage("Unrelated narrative.", claims)

    output_path = tmp_path / "out.pdf"
    render_pdf(manifest, step_results, annotated_paths, output_path, narrative_text=final_text)

    text = _normalize_whitespace(_extract_text(output_path))
    assert "[verify] (claim-001)" in text
    assert "Something not in the narrative at all." in text


def test_pdf_export_never_crashes_on_non_latin1_text(tmp_path):
    """Speech-transcribed claim text can realistically contain characters
    outside fpdf2's core-font Latin-1 support (curly quotes, accents,
    emoji) — the export must degrade gracefully, never raise."""
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

    narrative_text = "Café naïve “curly quotes” — em dash — 😀 emoji."
    output_path = tmp_path / "out.pdf"
    render_pdf(manifest, step_results, annotated_paths, output_path, narrative_text=narrative_text)

    assert output_path.read_bytes()[:5] == b"%PDF-"


def test_pdf_handles_missing_screenshot_without_crashing(tmp_path):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

    missing_paths = [None] + list(annotated_paths[1:])
    output_path = tmp_path / "out.pdf"
    render_pdf(manifest, step_results, missing_paths, output_path)

    assert output_path.read_bytes()[:5] == b"%PDF-"
