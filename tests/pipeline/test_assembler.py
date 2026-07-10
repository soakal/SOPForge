"""assembler.py: step_heading/format_doc_date/doc_number/check_1to1_mapping --
small, pure, deterministic helpers shared across every export format."""

from pipeline.assembler import (
    check_1to1_mapping,
    doc_number,
    format_doc_date,
    step_heading,
    toc_lines,
)
from pipeline.manifest import Element, Screen, Window
from pipeline.manifest import Step as ManifestStep


def _step(action="click", name="", control_type="", window_title="Test Window"):
    return ManifestStep(
        id="step-001",
        ts_utc="2026-01-01T00:00:00.000Z",
        action=action,
        button="left" if action == "click" else None,
        text_summary=None if action == "click" else "entered value",
        screen=Screen(x=0, y=0, monitor=1),
        screenshot="001.png",
        window=Window(title=window_title, process="test.exe", class_="win32"),
        element=Element(name=name, control_type=control_type, automation_id="", framework="win32"),
        redactions=[],
    )


def test_step_heading_uses_element_name_when_present():
    step = _step(name="Save", control_type="Button")
    assert step_heading(3, step) == "Step 3 — Click 'Save'"


def test_step_heading_falls_back_to_control_type_when_no_name():
    step = _step(control_type="Edit")
    assert step_heading(2, step) == "Step 2 — Click the Edit"


def test_step_heading_falls_back_to_bare_step_number_when_element_is_fully_empty():
    """Regression: previously fell back to a fabricated 'Click the screen'
    even when nothing about the target is actually known -- fabricating a
    specific action neither the manifest nor (for photo-mode) reality
    actually supports. A fully-empty element must produce a neutral
    heading with no verb/target claim."""
    step = _step(name="", control_type="")
    assert step_heading(5, step) == "Step 5"


def test_step_heading_bare_fallback_applies_regardless_of_action():
    """The fabrication risk is specifically the VERB+TARGET claim, not the
    action value itself -- a type-action step with empty element must fall
    back the same way a click-action step does."""
    step = _step(action="type", name="", control_type="")
    assert step_heading(1, step) == "Step 1"


def test_step_heading_type_action_uses_enter_value_phrasing():
    step = _step(action="type", name="Computer name", control_type="Edit")
    assert step_heading(4, step) == "Step 4 — Enter value in 'Computer name'"


def test_format_doc_date_formats_iso_timestamp():
    assert format_doc_date("2026-03-05T14:22:04.120Z") == "03/05/2026"


def test_format_doc_date_falls_back_to_today_on_unparseable_input():
    # Never raises -- a display-string formatting failure must not break doc
    # generation, matching invariant L3's "always succeeds" spirit.
    result = format_doc_date("not-a-date")
    assert len(result) == 10  # MM/DD/YYYY, still well-formed
    assert result.count("/") == 2


def test_doc_number_returns_none_when_no_prefix_configured():
    assert doc_number("", "20260101-120000-a1b2") is None
    assert doc_number(None, "20260101-120000-a1b2") is None


def test_doc_number_combines_prefix_and_session_suffix():
    assert doc_number("SOP", "20260101-120000-a1b2") == "SOP-A1B2"


def test_check_1to1_mapping_true_for_matching_order():
    class _Manifest:
        steps = [_step()]

    assert check_1to1_mapping(_Manifest(), [{"step_id": "step-001", "text": "x"}]) is True


def test_check_1to1_mapping_false_for_mismatch():
    class _Manifest:
        steps = [_step()]

    assert check_1to1_mapping(_Manifest(), []) is False
    assert check_1to1_mapping(_Manifest(), [{"step_id": "step-999", "text": "x"}]) is False


class _Manifest:
    def __init__(self, steps):
        self.steps = steps


def test_toc_lines_without_narrative_numbers_procedure_first():
    manifest = _Manifest([_step(name="Save")])
    lines = toc_lines(manifest, narrative_text=None)
    assert lines[0] == "1.  Procedure"
    assert lines[1] == f"      {step_heading(1, manifest.steps[0])}"
    assert lines[-1] == "2.  Revision History"


def test_toc_lines_with_narrative_adds_overview_first():
    """docx_assembler.py and export_pdf.py both build their TOC from this
    same helper (assembler.toc_lines) so the two documents' outlines can
    never independently drift for the same session -- this pins the exact
    numbering contract both renderers depend on."""
    manifest = _Manifest([_step(name="Save")])
    lines = toc_lines(manifest, narrative_text="Some overview text.")
    assert lines[0] == "1.  Overview"
    assert lines[1] == "2.  Procedure"
    assert lines[-1] == "3.  Revision History"


def test_toc_lines_step_entries_are_indented_and_match_step_heading():
    manifest = _Manifest([_step(name="Save"), _step(name="Cancel")])
    lines = toc_lines(manifest, narrative_text=None)
    step_entries = [line for line in lines if line.startswith("      ")]
    assert step_entries == [
        f"      {step_heading(1, manifest.steps[0])}",
        f"      {step_heading(2, manifest.steps[1])}",
    ]
