"""SOP Factory 2 docx assembler, wired in end-to-end (AC3): template-mode
fixture manifest -> complete docx, with zero LLM requests anywhere in the
path. The engine (sop_lib.SOPBuilder) is imported from SOP_Factory_2, not
vendored or rewritten."""

import inspect
import re
import zipfile
from pathlib import Path

import httpx
from PIL import Image

from pipeline.docx_assembler import assemble_docx
from pipeline.manifest import load_manifest
from pipeline.render import render_steps_template_mode

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"

_RUN_TEXT_RE = re.compile(r"<w:t[^>]*>([^<]*)</w:t>")


def _document_text(document_xml):
    """All <w:t> run text, concatenated in document order. A step's bullet
    text can be split across multiple runs (e.g. docx_assembler.py's
    bullet_rich bolding the target element's name mid-sentence), so it's no
    longer a literal substring of the raw XML — but it IS a substring of the
    runs' text joined back together, since bullet_rich never introduces or
    drops characters, only run boundaries."""
    return "".join(_RUN_TEXT_RE.findall(document_xml))


def _make_screenshots(manifest, directory):
    directory.mkdir(parents=True, exist_ok=True)
    for step in manifest.steps:
        Image.new("RGB", (1920, 1080), (255, 255, 255)).save(directory / step.screenshot)


def test_docx_assembler_signature_has_no_llm_client_parameter():
    assert "llm_client" not in inspect.signature(assemble_docx).parameters


def test_template_mode_docx_end_to_end_makes_zero_llm_requests(tmp_path, monkeypatch):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, _annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

    calls = {"count": 0}
    original_send = httpx.Client.send

    def counting_send(self, *args, **kwargs):
        calls["count"] += 1
        return original_send(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", counting_send)

    output_path = tmp_path / "out.docx"
    out, warnings = assemble_docx(manifest, step_results, annotated, output_path)

    assert calls["count"] == 0
    assert Path(out).exists()
    assert warnings == []


def test_docx_contains_every_step_text_and_a_title_page(tmp_path):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, _annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

    output_path = tmp_path / "out.docx"
    out, warnings = assemble_docx(manifest, step_results, annotated, output_path)

    assert warnings == []
    with zipfile.ZipFile(out) as zf:
        document_xml = zf.read("word/document.xml").decode("utf-8")
    text = _document_text(document_xml)

    title = manifest.session.title or manifest.session.id
    assert title  # sanity: the fixture must actually have a non-empty title/id,
    # otherwise the assertion below would be vacuously true (empty string is
    # a substring of everything).
    assert title.upper() in text
    for result in step_results:
        assert result["text"] in text


def test_docx_is_a_valid_zip_with_expected_parts(tmp_path):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, _annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

    output_path = tmp_path / "out.docx"
    out, _warnings = assemble_docx(manifest, step_results, annotated, output_path)

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert "word/document.xml" in names
    assert "[Content_Types].xml" in names


def test_verify_claim_renders_as_a_callout_not_raw_marker_text(tmp_path):
    """A "> [verify] (claim-id): ..." line (claim_coverage.py) must not
    ship as literal raw text in the doc -- it reads as debug scaffolding.
    docx_assembler.py's _narrative_body styles it as a "Needs verification"
    callout instead and drops the claim id from what's shown (it stays
    meaningful in the sidecar report only)."""
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, _annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

    narrative_text = "Some narration.\n> [verify] (claim-001): Something not in the narrative."
    output_path = tmp_path / "out.docx"
    out, warnings = assemble_docx(
        manifest, step_results, annotated, output_path, narrative_text=narrative_text
    )

    assert warnings == []
    with zipfile.ZipFile(out) as zf:
        document_xml = zf.read("word/document.xml").decode("utf-8")
    text = _document_text(document_xml)

    assert "Needs verification:" in text
    assert "Something not in the narrative." in text
    assert "claim-001" not in text


def test_missing_screenshot_produces_a_warning_not_a_crash(tmp_path):
    """A manifest step referencing a screenshot that was never annotated
    must not crash the whole docx build — SOPBuilder degrades to an
    [IMAGE NOT FOUND] marker and records a warning instead."""
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, _annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

    # Delete one annotated screenshot after rendering, simulating a gap.
    missing_step = manifest.steps[0]
    (annotated / missing_step.screenshot).unlink()

    output_path = tmp_path / "out.docx"
    out, warnings = assemble_docx(manifest, step_results, annotated, output_path)

    assert Path(out).exists()
    assert any(missing_step.screenshot in w for w in warnings)
