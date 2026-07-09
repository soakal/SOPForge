"""Manifest loader tests: schema-valid fixtures parse cleanly into typed
models; invalid data is rejected by the canonical JSON Schema check before
pydantic ever sees it."""

import json
from pathlib import Path

import jsonschema
import pytest

from pipeline.manifest import Manifest, load_manifest, select_manifest_steps

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def test_loads_sample_manifest():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    assert isinstance(manifest, Manifest)
    assert manifest.session.id == "20260703-142200-a1b2"
    assert [s.id for s in manifest.steps] == ["step-001", "step-002", "step-003"]


def test_loads_empty_elements_manifest():
    manifest = load_manifest(FIXTURES / "empty-elements-manifest.json")
    assert all(s.element.bounding_rect is None for s in manifest.steps)
    assert all(s.window.class_ == "" for s in manifest.steps)


def test_step_ids_helper_matches_order():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    assert manifest.step_ids() == [s.id for s in manifest.steps]


def test_accepts_dict_not_just_path():
    data = json.loads((FIXTURES / "sample-manifest.json").read_text(encoding="utf-8"))
    manifest = load_manifest(data)
    assert isinstance(manifest, Manifest)


def test_rejects_data_missing_required_field():
    data = json.loads((FIXTURES / "sample-manifest.json").read_text(encoding="utf-8"))
    del data["steps"][0]["action"]
    with pytest.raises(jsonschema.ValidationError):
        load_manifest(data)


def test_rejects_click_step_without_button():
    data = json.loads((FIXTURES / "sample-manifest.json").read_text(encoding="utf-8"))
    del data["steps"][0]["button"]
    with pytest.raises(jsonschema.ValidationError):
        load_manifest(data)


def test_select_manifest_steps_reorders_without_dropping():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    selected = select_manifest_steps(manifest, ["step-003", "step-001", "step-002"])
    assert selected.step_ids() == ["step-003", "step-001", "step-002"]


def test_select_manifest_steps_reorders_and_drops():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    selected = select_manifest_steps(manifest, ["step-003", "step-001"])
    assert selected.step_ids() == ["step-003", "step-001"]


def test_select_manifest_steps_rejects_unknown_id():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    with pytest.raises(ValueError, match="unknown step id"):
        select_manifest_steps(manifest, ["step-001", "step-999"])


def test_select_manifest_steps_rejects_duplicate_id():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    with pytest.raises(ValueError, match="duplicate step id"):
        select_manifest_steps(manifest, ["step-001", "step-002", "step-001"])
