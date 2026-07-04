import json
from pathlib import Path

import jsonschema
import pytest

from capture.manifest import ManifestBuilder

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SCHEMA = json.loads((FIXTURES / "manifest.schema.json").read_text(encoding="utf-8"))


def validate(instance):
    jsonschema.Draft202012Validator(SCHEMA).validate(instance)


def empty_element():
    return {
        "name": "",
        "control_type": "",
        "automation_id": "",
        "framework": "",
        "bounding_rect": None,
    }


def real_element():
    return {
        "name": "Btn",
        "control_type": "Button",
        "automation_id": "b1",
        "framework": "Win32",
        "bounding_rect": [0, 0, 10, 10],
    }


def window(cls="win32"):
    return {"title": "Test Window", "process": "test.exe", "class": cls}


def test_round_trip_schema_valid(tmp_path):
    builder = ManifestBuilder(
        "sess-1",
        title="t",
        started_utc="2026-01-01T00:00:00Z",
        machine="m",
        os_build="1",
    )
    builder.add_step(
        ts_utc="2026-01-01T00:00:01Z",
        action="click",
        button="left",
        screen={"x": 1, "y": 2, "monitor": 1},
        screenshot="001.png",
        window=window(),
        element=real_element(),
    )
    builder.finish("2026-01-01T00:00:02Z")
    out = tmp_path / "manifest.json"
    builder.write(out)

    data = json.loads(out.read_text(encoding="utf-8"))
    validate(data)
    assert data["steps"][0]["id"] == "step-001"


def test_step_order_preserved(tmp_path):
    builder = ManifestBuilder("sess-2")
    ids = [
        builder.add_step(
            ts_utc=f"2026-01-01T00:00:{i:02d}Z",
            action="type",
            text_summary="entered value",
            screen={"x": i, "y": i, "monitor": 1},
            screenshot=f"{i + 1:03d}.png",
            window=window(),
            element=empty_element(),
        )
        for i in range(5)
    ]
    assert ids == [f"step-{i + 1:03d}" for i in range(5)]
    assert builder.step_ids() == ids

    out = tmp_path / "manifest.json"
    builder.finish("2026-01-01T00:01:00Z")
    builder.write(out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [s["id"] for s in data["steps"]] == ids


def test_click_without_button_rejected():
    builder = ManifestBuilder("sess-3")
    with pytest.raises(ValueError):
        builder.add_step(
            ts_utc="2026-01-01T00:00:00Z",
            action="click",
            screen={"x": 0, "y": 0, "monitor": 1},
            screenshot="001.png",
            window=window(),
            element=empty_element(),
        )


def test_all_empty_elements_still_valid_worst_case(tmp_path):
    """Acceptance criterion 5: a session with zero UIA metadata must still
    produce a schema-valid manifest — elements empty, screenshots present."""
    builder = ManifestBuilder(
        "20260101-000000-empty",
        title="",
        started_utc="2026-01-01T00:00:00Z",
        machine="WS-EMPTY",
        os_build="26100",
    )
    for i in range(3):
        builder.add_step(
            ts_utc=f"2026-01-01T00:00:{i:02d}Z",
            action="click",
            button="left",
            screen={"x": 10 * i, "y": 10 * i, "monitor": 1},
            screenshot=f"{i + 1:03d}.png",
            window=window(cls=""),
            element=empty_element(),
        )
    builder.finish("2026-01-01T00:00:05Z")

    out = tmp_path / "manifest.json"
    builder.write(out)
    data = json.loads(out.read_text(encoding="utf-8"))
    validate(data)
    assert all(s["element"]["bounding_rect"] is None for s in data["steps"])
    assert all(s["screenshot"] for s in data["steps"])

    # Also emit as a committed Phase 2 fixture (the round-trip validator and
    # template-fallback path both need a real all-empty-metadata manifest).
    fixture_path = FIXTURES / "empty-elements-manifest.json"
    fixture_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
