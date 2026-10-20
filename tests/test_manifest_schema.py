import copy
import json
from pathlib import Path

import jsonschema
import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SCHEMA = json.loads((FIXTURES / "manifest.schema.json").read_text(encoding="utf-8"))
SAMPLE = json.loads((FIXTURES / "sample-manifest.json").read_text(encoding="utf-8"))


def validate(instance):
    jsonschema.Draft202012Validator(SCHEMA).validate(instance)


def test_sample_manifest_is_valid():
    validate(SAMPLE)


def test_missing_required_step_field_is_invalid():
    bad = copy.deepcopy(SAMPLE)
    del bad["steps"][0]["action"]
    with pytest.raises(jsonschema.ValidationError):
        validate(bad)


def test_wrong_type_is_invalid():
    bad = copy.deepcopy(SAMPLE)
    bad["steps"][0]["screen"]["x"] = "not-an-integer"
    with pytest.raises(jsonschema.ValidationError):
        validate(bad)


def test_screen_scale_is_optional_number():
    ok = copy.deepcopy(SAMPLE)
    ok["steps"][0]["screen"].pop("scale", None)
    validate(ok)
    ok["steps"][0]["screen"]["scale"] = 1.5
    validate(ok)
    ok["steps"][0]["screen"]["scale"] = "1.5"
    with pytest.raises(jsonschema.ValidationError):
        validate(ok)


def test_invalid_enum_is_invalid():
    bad = copy.deepcopy(SAMPLE)
    bad["steps"][0]["window"]["class"] = "not-a-real-class"
    with pytest.raises(jsonschema.ValidationError):
        validate(bad)
