"""Typed config/models.toml loader: per-section (steps, narrative) LLM
routing — endpoint, model, and an opt-in Anthropic-routing flag (default
off, per CLAUDE.md's "Anthropic routing per section is a config option,
default off")."""

import tomllib

from pydantic import BaseModel, ConfigDict, Field

from pipeline.resource_path import resource_path


def default_config_path():
    """Resolved fresh on every call (not a load-once module constant) so
    that GET /config (server.py) actually reflects sys.frozen/sys._MEIPASS
    at request time — a frozen-at-import-time constant would still hold
    the dev-mode path even after a test (or a real frozen build) changes
    sys.frozen, since nothing re-imports this module to recompute it."""
    return resource_path("config", "models.toml")


class SectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str
    model: str
    anthropic: bool = False
    passes: int = 1


class VisionConfig(BaseModel):
    """Vision-model captioning for the screenshots+transcript build mode: when
    enabled, each screenshot is captioned by a vision LLM (looking at the image
    plus the narration) instead of relying on the transcript's own per-step
    structure. Optional -- a config without a [vision] section defaults to
    off, and old configs keep working."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    endpoint: str = "http://192.168.200.60:11434/v1"
    model: str = "qwen2.5vl:7b"
    # Draw a box around the target UI element from the model's bounding box.
    # OFF by default: qwen2.5vl:7b's element LOCALIZATION is unreliable (boxes
    # are often misplaced), and a wrong highlight misleads the reader more than
    # no highlight. Captioning is accurate; only the box coordinates aren't.
    # Enable to experiment, ideally with a larger vision model (qwen2.5vl:32b).
    highlight: bool = False


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: SectionConfig
    narrative: SectionConfig
    vision: VisionConfig = Field(default_factory=VisionConfig)


def load_models_config(path=None):
    if path is None:
        path = default_config_path()
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return ModelsConfig.model_validate(data)
