"""Manifest loading: pydantic models mirroring fixtures/manifest.schema.json,
with the canonical JSON Schema (ground truth per CLAUDE.md — "The manifest is
ground truth. The LLM never decides what the steps are") validated first,
before parsing into typed models for convenient use in the rest of the
pipeline."""

import json
from pathlib import Path
from typing import Literal

import jsonschema
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pipeline.resource_path import resource_path

# A module-level constant (not lazily resolved like config.py's
# default_config_path()) is fine here: sys.frozen is already set correctly
# by the time this module is first imported in a real frozen EXE (unlike a
# GET /config request path that needs to reflect a *test's* monkeypatch
# after import), and the schema itself never changes at runtime.
SCHEMA_PATH = resource_path("fixtures", "manifest.schema.json")
_SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
_VALIDATOR = jsonschema.Draft202012Validator(_SCHEMA)


class Session(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    started_utc: str
    ended_utc: str
    machine: str
    os_build: str
    narration_wav: str | None


class Screen(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int
    y: int
    monitor: int


class Window(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    title: str
    process: str
    class_: Literal["win32", "chromium", "electron", ""] = Field(alias="class")


class Element(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    control_type: str
    automation_id: str
    framework: str
    bounding_rect: list[int] | None = None


class Redaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: list[int]
    reason: str


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    ts_utc: str
    action: Literal["click", "type"]
    button: Literal["left", "right", "middle"] | None = None
    text_summary: str | None = None
    screen: Screen
    screenshot: str
    screenshot_placeholder: bool = False
    window: Window
    element: Element
    redactions: list[Redaction] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_action_specific_fields(self):
        if self.action == "click" and self.button is None:
            raise ValueError("click step requires button")
        if self.action == "type" and self.text_summary is None:
            raise ValueError("type step requires text_summary")
        return self


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    session: Session
    steps: list[Step]

    def step_ids(self):
        return [s.id for s in self.steps]


def load_manifest(source):
    """Loads + validates a manifest against the canonical JSON Schema (raises
    jsonschema.ValidationError on mismatch — this is the ground-truth check,
    run before pydantic ever sees the data), then parses into typed models.
    `source` may be a file path or an already-loaded dict."""
    if isinstance(source, (str, Path)):
        data = json.loads(Path(source).read_text(encoding="utf-8"))
    else:
        data = source
    _VALIDATOR.validate(data)
    return Manifest.model_validate(data)


def filter_manifest_steps(manifest, keep_ids):
    """Returns a new Manifest with only the steps whose id is in keep_ids, in
    original manifest order (never reordered/renumbered) -- the one place a
    step-removal review UI is allowed to shrink the manifest. IDs are NOT
    renumbered; a gap (step-002 removed, step-001/step-003 remain) is fine --
    nothing downstream assumes contiguous numbering, only that id/order is
    preserved 1:1 with the resulting doc (assembler.py). No re-validation
    against the JSON Schema is needed: dropping entries from an already-valid
    manifest's steps list can't introduce a schema violation (no minItems on
    steps)."""
    keep = set(keep_ids)
    kept_steps = [s for s in manifest.steps if s.id in keep]
    return manifest.model_copy(update={"steps": kept_steps})


def manifest_to_schema_dict(manifest):
    """Serializes a Manifest back to schema-valid JSON -- the inverse of
    load_manifest, used when a manifest built in memory (e.g. via
    filter_manifest_steps) needs writing back to disk. by_alias restores
    "class" from Window.class_. button/text_summary are popped when None:
    the schema requires each ABSENT (not null) on the action it doesn't
    apply to, unlike element.bounding_rect/session.narration_wav, which are
    required-but-nullable and so are left as explicit `null`."""
    data = manifest.model_dump(by_alias=True)
    for step in data["steps"]:
        if step.get("button") is None:
            step.pop("button", None)
        if step.get("text_summary") is None:
            step.pop("text_summary", None)
    return data
