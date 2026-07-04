"""Typed config/models.toml loader: per-section (steps, narrative) LLM
routing — endpoint, model, and an opt-in Anthropic-routing flag (default
off, per CLAUDE.md's "Anthropic routing per section is a config option,
default off")."""

import tomllib

from pydantic import BaseModel, ConfigDict

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


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: SectionConfig
    narrative: SectionConfig


def load_models_config(path=None):
    if path is None:
        path = default_config_path()
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return ModelsConfig.model_validate(data)
