"""Phase 6b (phases/06-factory-parity.md AC2): recorder gaps show up as
inline [REVIEW NEEDED] markers in every export format, derived from the
manifest alone, and never on photo-mode's synthetic steps."""

from pathlib import Path

import docx
from PIL import Image
from pypdf import PdfReader

from pipeline.docx_assembler import assemble_docx
from pipeline.export_html import render_single_file_html
from pipeline.export_pdf import render_pdf
from pipeline.manifest import Manifest, load_manifest
from pipeline.photo_build import is_synthetic_step, synthetic_manifest_dict
from pipeline.render import render_html, render_markdown, render_steps_template_mode
from pipeline.review_notes import (
    TARGET_NOT_IDENTIFIED,
    VALUE_NOT_CAPTURED,
    step_review_notes,
)
from pipeline.sidecar import build_sidecar_report

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _sample():
    return load_manifest(FIXTURES / "sample-manifest.json")


def _rendered(tmp_path):
    manifest = _sample()
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    for step in manifest.steps:
        Image.new("RGB", (1920, 1080), (255, 255, 255)).save(screenshots / step.screenshot)
    annotated = tmp_path / "annotated"
    step_results, annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)
    return manifest, step_results, annotated, annotated_paths


def test_notes_per_fixture_step():
    by_id = {step.id: step_review_notes(step) for step in _sample().steps}
    assert by_id == {
        "step-001": [],
        "step-002": [VALUE_NOT_CAPTURED],
        "step-003": [TARGET_NOT_IDENTIFIED],
    }


def test_edited_type_summary_gets_no_marker():
    step = _sample().steps[1].model_copy(update={"text_summary": "entered computer name"})
    assert step_review_notes(step) == []


def test_synthetic_photo_steps_get_no_markers():
    data = synthetic_manifest_dict("T", ["a.png", "b.png"], "2026-10-24T00:00:00.000Z")
    data["session"]["id"] = "s"
    manifest = Manifest.model_validate(data)
    assert all(is_synthetic_step(step) for step in manifest.steps)
    assert all(step_review_notes(step) == [] for step in manifest.steps)


def test_no_real_fixture_step_is_synthetic():
    for name in ("sample-manifest.json", "empty-elements-manifest.json"):
        manifest = load_manifest(FIXTURES / name)
        assert not any(is_synthetic_step(step) for step in manifest.steps), name


def test_markdown_and_html_show_markers(tmp_path):
    manifest, step_results, _, annotated_paths = _rendered(tmp_path)
    md = render_markdown(manifest, step_results, annotated_paths)
    page = render_html(manifest, step_results, annotated_paths)
    single = render_single_file_html(manifest, step_results, annotated_paths)
    for note in (VALUE_NOT_CAPTURED, TARGET_NOT_IDENTIFIED):
        assert f"**{note}**" in md
        for doc in (page, single):
            assert "review-needed" in doc
            assert note in doc


def test_docx_shows_markers_under_their_step(tmp_path):
    manifest, step_results, annotated, _ = _rendered(tmp_path)
    out, warnings = assemble_docx(manifest, step_results, annotated, tmp_path / "out.docx")
    assert warnings == []
    texts = [p.text for p in docx.Document(out).paragraphs]
    value_at = next(i for i, t in enumerate(texts) if VALUE_NOT_CAPTURED in t)
    target_at = next(i for i, t in enumerate(texts) if TARGET_NOT_IDENTIFIED in t)
    assert value_at < target_at
    assert step_results[1]["text"] in texts[value_at - 1]


def test_pdf_shows_markers(tmp_path):
    manifest, step_results, _, annotated_paths = _rendered(tmp_path)
    out = tmp_path / "out.pdf"
    render_pdf(manifest, step_results, annotated_paths, out)
    text = " ".join(" ".join((page.extract_text() or "").split()) for page in PdfReader(out).pages)
    assert "value not captured" in text
    assert "control not identified" in text


def test_sidecar_lists_uncaptured_value_steps():
    manifest = _sample()
    results = [{"step_id": s.id, "text": "x", "used_fallback": False} for s in manifest.steps]
    report = build_sidecar_report(manifest, results, [])
    assert report["uncaptured_value_steps"] == ["step-002"]
