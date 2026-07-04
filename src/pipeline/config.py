"""Typed config/models.toml loader: per-section (steps, narrative) LLM
routing — endpoint, model, and an opt-in Anthropic-routing flag (default
off, per CLAUDE.md's "Anthropic routing per section is a config option,
default off")."""

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict

# TODO(phase-03/task-08): frozen-path resolution. This resolves relative to
# __file__, which won't exist under a PyInstaller frozen build's _internal
# layout — GET /config (server.py) will break in the packaged EXE until
# task-08's resource_path() helper is adopted here too (see phases/03-tasks.md).
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "models.toml"


class SectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str
    model: str
    anthropic: bool = False
    passes: int = 1


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: SectionConfig
    narrative: SectionConfig


def load_models_config(path=DEFAULT_CONFIG_PATH):
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return ModelsConfig.model_validate(data)
