"""Round-trip validator (invariant L2): generated step text must not
contradict or omit facts the manifest already knows (action verb, element
name, window title) — checked deterministically, not by another model."""

from pathlib import Path

from pipeline.manifest import load_manifest
from pipeline.roundtrip import round_trip_ok
from pipeline.template import render_step_template

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def test_every_template_output_passes_its_own_round_trip_sample():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    for step in manifest.steps:
        text = render_step_template(step)
        ok, mismatches = round_trip_ok(text, step)
        assert ok, (step.id, mismatches, text)


def test_every_template_output_passes_its_own_round_trip_empty_elements():
    manifest = load_manifest(FIXTURES / "empty-elements-manifest.json")
    for step in manifest.steps:
        text = render_step_template(step)
        ok, mismatches = round_trip_ok(text, step)
        assert ok, (step.id, mismatches, text)


def test_wrong_action_verb_fails():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]  # click
    ok, mismatches = round_trip_ok("Type your name here.", step)
    assert not ok
    assert "action" in mismatches


def test_missing_element_name_fails():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]  # click, element.name == "Save"
    ok, mismatches = round_trip_ok("Click the button.", step)
    assert not ok
    assert "element" in mismatches


def test_wrong_window_title_fails():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]
    ok, mismatches = round_trip_ok("Click the Save button in Notepad.", step)
    assert not ok
    assert "window" in mismatches


def test_empty_element_step_has_nothing_to_check_for_element():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[2]  # click, empty element, real window title
    assert step.element.name == ""
    ok, mismatches = round_trip_ok(f"Click somewhere in {step.window.title}.", step)
    assert ok
    assert mismatches == []


def test_correct_text_with_all_facts_present_passes():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]  # click, Save button, SmartDeploy Console
    ok, mismatches = round_trip_ok(
        f"Click the {step.element.name} button in {step.window.title}.", step
    )
    assert ok
    assert mismatches == []
