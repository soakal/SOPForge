"""Template fallback (invariant L3): pure string interpolation, always
available (no LLM call), and always traceable back to the manifest record —
never invents information the manifest didn't capture."""

from pathlib import Path

import pytest

from pipeline.manifest import Step, load_manifest
from pipeline.template import render_step_template

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def test_click_step_with_named_element_mentions_it():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]  # click, element.name == "Save"
    text = render_step_template(step)
    assert "Save" in text
    assert "Button" in text
    assert step.window.title in text


def test_type_step_uses_text_summary_not_raw_content():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[1]  # type, element.name == "Computer name"
    text = render_step_template(step)
    assert "Computer name" in text
    assert step.text_summary in text
    assert step.window.title in text


def test_empty_element_click_falls_back_to_screen_coords_and_window_title():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[2]  # click, empty element, real window title
    assert step.element.name == "" and step.element.control_type == ""
    text = render_step_template(step)
    assert str(step.screen.x) in text
    assert str(step.screen.y) in text
    assert step.window.title in text
    # No fabricated element name/type appears.
    assert "None" not in text


def test_fully_empty_metadata_manifest_renders_without_fabrication():
    manifest = load_manifest(FIXTURES / "empty-elements-manifest.json")
    for step in manifest.steps:
        text = render_step_template(step)
        assert text
        assert str(step.screen.x) in text
        assert str(step.screen.y) in text


def test_empty_window_title_falls_back_to_generic_phrase():
    step = Step.model_validate(
        {
            "id": "step-001",
            "ts_utc": "2026-01-01T00:00:00Z",
            "action": "click",
            "button": "left",
            "screen": {"x": 5, "y": 5, "monitor": 1},
            "screenshot": "001.png",
            "window": {"title": "", "process": "", "class": ""},
            "element": {
                "name": "",
                "control_type": "",
                "automation_id": "",
                "framework": "",
                "bounding_rect": None,
            },
            "redactions": [],
        }
    )
    text = render_step_template(step)
    assert "the current window" in text
    assert "5" in text


def test_window_title_with_backslashes_appears_literally_not_repr_escaped():
    """Regression: rendering used to build phrases with !r (Python repr),
    which backslash-escapes strings — an ordinary elevated-window title like
    "Administrator: C:\\Windows\\system32\\cmd.exe" would render as
    "Administrator: C:\\\\Windows\\\\system32\\\\cmd.exe", no longer
    containing the manifest's raw title as a literal substring."""
    step = Step.model_validate(
        {
            "id": "step-001",
            "ts_utc": "2026-01-01T00:00:00Z",
            "action": "click",
            "button": "left",
            "screen": {"x": 5, "y": 5, "monitor": 1},
            "screenshot": "001.png",
            "window": {
                "title": r"Administrator: C:\Windows\system32\cmd.exe",
                "process": "cmd.exe",
                "class": "win32",
            },
            "element": {
                "name": "",
                "control_type": "",
                "automation_id": "",
                "framework": "",
                "bounding_rect": None,
            },
            "redactions": [],
        }
    )
    text = render_step_template(step)
    assert step.window.title in text


def test_unknown_action_raises():
    step = Step.model_validate(
        {
            "id": "step-001",
            "ts_utc": "2026-01-01T00:00:00Z",
            "action": "click",
            "button": "left",
            "screen": {"x": 0, "y": 0, "monitor": 1},
            "screenshot": "001.png",
            "window": {"title": "", "process": "", "class": ""},
            "element": {
                "name": "",
                "control_type": "",
                "automation_id": "",
                "framework": "",
                "bounding_rect": None,
            },
            "redactions": [],
        }
    )
    step.action = "scroll"  # bypasses the Literal type check post-construction
    with pytest.raises(ValueError, match="unknown action"):
        render_step_template(step)
