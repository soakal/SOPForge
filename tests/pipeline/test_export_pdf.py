"""PDF export (AC1 part 1): golden fixture manifest -> PDF with one section
per step, annotated screenshots embedded, [verify] blockquotes rendered.
pypdf (test-only) verifies the actual rendered text, not just that a file
was written."""

import json
import re
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from pipeline.claim_coverage import ensure_claim_coverage
from pipeline.export_pdf import render_pdf
from pipeline.manifest import load_manifest
from pipeline.polish import generate_polish_fields
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
    # The raw "[verify] (claim-id)" marker is intentionally NOT shown in the
    # rendered doc (it reads as debug scaffolding) -- it's styled as a
    # "Needs verification" callout instead; the claim id stays meaningful in
    # the sidecar report only.
    assert "Needs verification:" in text
    assert "Something not in the narrative at all." in text
    assert "claim-001" not in text


_PROMPT_ITEM_RE = re.compile(r'^(.+?): "(.*)"$', re.M)


class _MarkerDroppingPolishStub:
    """A `.chat()` that genuinely parses generate_polish_fields' real
    prompt text (the `field_id: "text"` item-line format
    `_build_fields_prompt`, polish.py, emits) and replies with a rewrite
    per field: any line starting with the verify-blockquote marker "> " has
    JUST that marker dropped (the rest of the line, including any digits,
    kept verbatim) -- mirrors test_docx_e2e.py's identical stub, proving the
    same generate_polish_fields protection (verify lines stripped before the
    LLM ever sees them, spliced back verbatim after) also covers doc.pdf now
    that server.py wires render_pdf to the polished fields. Every other line
    is uppercased, so a genuine polish of the surrounding prose is still
    provable."""

    def __init__(self):
        self.calls = []

    def chat(self, messages, **kwargs):
        content = messages[0]["content"]
        self.calls.append(content)
        items = _PROMPT_ITEM_RE.findall(content)
        assert items, "expected at least one 'field_id: \"text\"' line in the real polish prompt"
        rewrites = []
        for field_id, text in items:
            mangled = "\n".join(
                line[2:] if line.startswith("> ") else line.upper() for line in text.split("\n")
            )
            rewrites.append({"field_id": field_id, "text": mangled})
        return json.dumps(rewrites)


def test_polish_never_lets_a_verify_blockquote_reach_the_llm_and_pdf_still_flags_it(tmp_path):
    """Regression mirroring test_docx_e2e.py's identical-purpose test: doc.pdf
    (export_pdf.py) shares its [verify]-blockquote rendering with doc.docx --
    both call the same claim_coverage.parse_verify_line and render a "Needs
    verification: ..." callout with the claim id dropped (see
    export_pdf._narrative_body / docx_assembler._narrative_body). Uses the
    REAL generate_polish_fields (not a monkeypatch) with
    _MarkerDroppingPolishStub, a stub LLM that reflows a verify-blockquote
    line by dropping just its leading "> " marker -- the same attack proven
    dangerous for docx. generate_polish_fields strips every verify-blockquote
    line out of narrative_text before it's ever shown to the LLM and splices
    it back verbatim afterward, so this also confirms the stub's prompt calls
    never contained the verify line, then feeds the polished result into the
    REAL render_pdf and confirms the "Needs verification" callout still
    renders."""
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

    verify_line = "> [verify] (claim-042): The device firmware is version 42."
    narrative_text = f"Open the console and review the settings.\n{verify_line}\n"

    stub = _MarkerDroppingPolishStub()
    polished_narrative, polished_steps, meta = generate_polish_fields(
        narrative_text, step_results, stub
    )

    assert stub.calls, "the polish LLM stub was never called"
    for call in stub.calls:
        assert "[verify]" not in call, (
            "a verify-blockquote line reached the polish LLM prompt -- it must be "
            "stripped out before the call, not merely survive gating afterward"
        )

    # The surrounding prose genuinely got polished (proves this isn't a
    # no-op / gate-rejection making the test vacuous)...
    assert "OPEN THE CONSOLE" in polished_narrative
    # ...while the verify blockquote line itself survived byte-for-byte,
    # never mangled, never dropped.
    assert verify_line in polished_narrative
    assert "narrative" in meta["fields_polished"]

    output_path = tmp_path / "out.pdf"
    render_pdf(
        manifest, polished_steps, annotated_paths, output_path, narrative_text=polished_narrative
    )

    text = _normalize_whitespace(_extract_text(output_path))
    assert "Needs verification:" in text
    assert "The device firmware is version 42." in text
    assert "claim-042" not in text
    assert "[verify]" not in text


def test_bullet_marker_uses_a_real_glyph_dejavu_can_render(tmp_path, caplog):
    """Regression: the bullet marker used to be chr(149) ('\\x95', a control
    character with no printable glyph in Unicode) which only ever looked
    like a bullet because fpdf2's old core Helvetica font decoded it via
    cp1252 (where byte 0x95 happens to display as '•'). Since DejaVu
    (a real Unicode TTF) was wired in, that stopped working -- fpdf2 logs a
    "missing glyph" warning and the bullet doesn't render. Must be a real
    '•' character instead, which DejaVu actually has a glyph for."""
    import logging

    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

    output_path = tmp_path / "out.pdf"
    with caplog.at_level(logging.WARNING):
        render_pdf(manifest, step_results, annotated_paths, output_path)

    assert not any("missing" in record.message.lower() for record in caplog.records)
    text = _extract_text(output_path)
    assert "•" in text


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


def test_pdf_renders_smart_punctuation_verbatim_with_dejavu(tmp_path):
    """With the bundled DejaVu Sans font registered (assets/fonts/dejavu-sans),
    "smart" typography punctuation outside Latin-1 -- curly quotes, em dash,
    ellipsis -- must render as the real character, not get transliterated or
    mangled to '?'. Transliteration is a last-resort fallback that only
    kicks in if DejaVu registration itself fails (see _safe_text); this test
    proves the primary, expected path actually works end to end."""
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

    narrative_text = "Café naïve “curly quotes” — em dash … ellipsis."
    output_path = tmp_path / "out.pdf"
    render_pdf(manifest, step_results, annotated_paths, output_path, narrative_text=narrative_text)

    text = _extract_text(output_path)
    assert "Café naïve “curly quotes” — em dash … ellipsis." in _normalize_whitespace(text)
    assert "?" not in text.replace("Café", "").replace("naïve", "")


def test_register_font_falls_back_to_helvetica_and_transliterates(tmp_path, monkeypatch):
    """If the bundled DejaVu font files are ever missing (a packaging
    regression, e.g. sopforge-server.spec's datas entries dropped), export
    must still succeed via the Helvetica + transliteration fallback rather
    than raising -- the same "always succeeds" guarantee as the docx
    template fallback."""
    import pipeline.export_pdf as export_pdf_module

    def _boom(*_args, **_kwargs):
        raise FileNotFoundError("simulated missing font file")

    monkeypatch.setattr(export_pdf_module, "resource_path", _boom)

    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

    narrative_text = "Curly ‘quotes’ and an em dash — here."
    output_path = tmp_path / "out.pdf"
    render_pdf(manifest, step_results, annotated_paths, output_path, narrative_text=narrative_text)

    text = _normalize_whitespace(_extract_text(output_path))
    assert output_path.read_bytes()[:5] == b"%PDF-"
    assert "Curly 'quotes' and an em dash -- here." in text


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
