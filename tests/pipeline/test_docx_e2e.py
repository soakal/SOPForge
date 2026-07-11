"""SOP Factory 2 docx assembler, wired in end-to-end (AC3): template-mode
fixture manifest -> complete docx, with zero LLM requests anywhere in the
path. The engine (sop_lib.SOPBuilder) is imported from SOP_Factory_2, not
vendored or rewritten."""

import inspect
import json
import re
import zipfile
from pathlib import Path

import httpx
from PIL import Image

from pipeline.docx_assembler import assemble_docx
from pipeline.manifest import load_manifest
from pipeline.polish import generate_polish_fields
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


_PROMPT_ITEM_RE = re.compile(r'^(.+?): "(.*)"$', re.M)


class _MarkerDroppingPolishStub:
    """A `.chat()` that genuinely parses generate_polish_fields' real
    prompt text (the `field_id: "text"` item-line format
    `_build_fields_prompt`, polish.py, emits) and replies with a rewrite
    per field: any line starting with the verify-blockquote marker "> " has
    JUST that marker dropped (the rest of the line, including any digits,
    kept verbatim) -- the exact reflow shape the Realist's real repro
    proved dangerous, that used to sail through `_field_gate` (its literal-
    fact check only requires a claim id's digits to survive as a substring
    ANYWHERE in the rewrite, not at that exact line-start position) and
    `validate_claim_coverage` (the claim's own text was still present,
    just no longer recognizable as a callout). Every other line is
    uppercased, so a genuine polish of the surrounding prose is still
    provable. If a verify-blockquote line ever reached this stub, it WOULD
    mangle it -- proving the real fix works because such a line structurally
    never reaches here, not because this stub declines to attack it."""

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


def test_polish_never_lets_a_verify_blockquote_reach_the_llm_and_docx_still_flags_it(tmp_path):
    """Regression for the gap the Realist's real repro proved: a gate-
    passing, claim-coverage-passing polish rewrite could reflow a
    "> [verify] (claim-id): ..." line, dropping just the leading "> "
    marker while keeping the claim's digits/text intact elsewhere in the
    sentence. Neither `_field_gate` (polish.py) nor `validate_claim_coverage`
    (claim_coverage.py) catches that -- both only check the claim's raw
    content survives SOMEWHERE, not that it's still recognizable as a
    callout -- so `docx_assembler.py`'s `parse_verify_line` would silently
    stop recognizing the line and the "Needs verification" Word callout
    would vanish with no warning recorded anywhere.

    Uses the REAL `generate_polish_fields` (not a monkeypatch) with
    `_MarkerDroppingPolishStub`, a stub LLM that reproduces that exact
    reflow. The fix under test: `generate_polish_fields` now strips every
    verify-blockquote line out of `narrative_text` before it's ever shown to
    the LLM and splices it back verbatim afterward -- so this test also
    confirms the stub's prompt calls never even contained the verify line,
    then feeds the polished result into the REAL `assemble_docx` and
    confirms the "Needs verification" callout still renders."""
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, _annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

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

    output_path = tmp_path / "out.docx"
    out, warnings = assemble_docx(
        manifest, polished_steps, annotated, output_path, narrative_text=polished_narrative
    )

    assert warnings == []
    with zipfile.ZipFile(out) as zf:
        document_xml = zf.read("word/document.xml").decode("utf-8")
    text = _document_text(document_xml)

    assert "Needs verification:" in text
    assert "The device firmware is version 42." in text
    assert "claim-042" not in text
    assert "[verify]" not in text


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
