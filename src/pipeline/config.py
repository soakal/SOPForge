"""Typed models.toml loader + writer: per-section (steps, narrative, vision)
LLM routing. Each section picks a PROVIDER -- ollama (local, default),
openrouter, openai, or anthropic -- plus a model (and, for ollama, an
endpoint). API keys are NEVER stored here: they're read from environment
variables per provider (OPENROUTER_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY),
so this file holds no secrets and is safe to edit/share.

The runtime config lives in a per-user writable location
(~/SOPForge/models.toml), seeded once from the bundled default -- the bundled
copy inside the frozen EXE is read-only, so the config editor writes to the
user copy instead."""

import os
import shutil
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pipeline.resource_path import resource_path

# provider -> fixed OpenAI-compatible endpoint (ollama uses its own configured
# endpoint; anthropic uses its own Messages API, handled specially) and the env
# var its API key comes from.
PROVIDERS = {
    "ollama": {"endpoint": None, "key_env": None},
    "openrouter": {"endpoint": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY"},
    "openai": {"endpoint": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY"},
    "anthropic": {"endpoint": None, "key_env": "ANTHROPIC_API_KEY"},
}

Provider = Literal["ollama", "openrouter", "openai", "anthropic"]
# Vision captioning goes through the OpenAI-compatible image path (base64
# image_url). Anthropic uses a different image format AND has no OpenAI-compat
# endpoint here -- allowing it would send ANTHROPIC_API_KEY as a Bearer token to
# the (leftover) ollama endpoint. So vision providers exclude anthropic.
VisionProvider = Literal["ollama", "openrouter", "openai"]


def default_config_path():
    """The bundled default config (read-only in a frozen build). Resolved
    fresh each call so it reflects sys.frozen at request time."""
    return resource_path("config", "models.toml")


def runtime_config_path():
    """The per-user, writable config. Seeded once from the bundled default so
    the config editor has something to edit and can save. Falls back to the
    bundled path if the user copy can't be created (then edits won't persist,
    but reads still work)."""
    user = Path.home() / "SOPForge" / "models.toml"
    if not user.exists():
        try:
            user.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(default_config_path(), user)
        except OSError:
            return default_config_path()
    return user


class SectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = "http://192.168.200.60:11434/v1"
    model: str
    provider: Provider = "ollama"
    # Legacy: older configs used `anthropic = true` instead of provider.
    anthropic: bool = False
    passes: int = Field(default=1, ge=1)
    # Only meaningful for [steps] (generation.py's per-step LLM calls are
    # independent; [narrative]'s draft->critique->revise passes are not, so
    # this field is simply never dumped for that section -- see
    # dump_models_config_toml). Defaults to 1 (strictly sequential): an
    # untuned Ollama instance just queues concurrent requests server-side,
    # and a queued step can then blow its own per-request timeout into a
    # template fallback it didn't need -- so any speedup from a
    # multi-GPU/parallel-capable Ollama server is opt-in, not assumed.
    max_concurrency: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _legacy_anthropic(self):
        if self.anthropic and self.provider == "ollama":
            self.provider = "anthropic"
        return self


class VisionConfig(BaseModel):
    """Vision-model captioning for the screenshots+transcript build mode."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    endpoint: str = "http://192.168.200.60:11434/v1"
    model: str = "qwen2.5vl:7b"
    provider: VisionProvider = "ollama"
    max_concurrency: int = Field(default=4, ge=1)


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: SectionConfig
    narrative: SectionConfig
    vision: VisionConfig = Field(default_factory=VisionConfig)


def provider_endpoint(provider, configured_endpoint):
    """The OpenAI-compatible base URL for a provider: ollama uses the section's
    configured endpoint; openrouter/openai have fixed ones."""
    return PROVIDERS.get(provider, {}).get("endpoint") or configured_endpoint


def provider_api_key(provider):
    """The API key for a provider from its env var, or None for a keyless
    provider (ollama). Does NOT raise -- callers decide how to handle a missing
    key (LLMClient raises loudly; vision captioning just lets the request fail
    and falls back)."""
    key_env = PROVIDERS.get(provider, {}).get("key_env")
    return os.environ.get(key_env) if key_env else None


def key_status(cfg: ModelsConfig):
    """{provider: bool} for every non-ollama provider used by the config --
    whether its API key env var is set. For the config page's indicator; never
    exposes the key value itself."""
    used = {cfg.steps.provider, cfg.narrative.provider, cfg.vision.provider}
    status = {}
    for provider in used:
        key_env = PROVIDERS.get(provider, {}).get("key_env")
        if key_env:
            status[provider] = bool(os.environ.get(key_env))
    return status


def load_models_config(path=None):
    if path is None:
        path = runtime_config_path()
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return ModelsConfig.model_validate(data)


def _toml_str(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    # Escape backslash and quote, plus control chars (a raw newline in a TOML
    # basic string is illegal -- without this, a multiline model value would
    # write invalid TOML and brick every subsequent config load).
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    s = "".join(c if c >= " " else f"\\u{ord(c):04x}" for c in s)
    return '"' + s + '"'


def dump_models_config_toml(cfg: ModelsConfig) -> str:
    """Serialize a validated ModelsConfig back to models.toml text. Only the
    provider/endpoint/model/passes/enabled fields are written -- never a secret
    (keys live in env vars)."""
    lines = [
        "# SOPForge model routing. Each section picks a provider (ollama /",
        "# openrouter / openai / anthropic) and a model. API keys are read from",
        "# environment variables, never stored here:",
        "#   openrouter -> OPENROUTER_API_KEY   openai -> OPENAI_API_KEY",
        "#   anthropic  -> ANTHROPIC_API_KEY",
        "#",
        "# max_concurrency (steps/vision only): how many LLM calls this section",
        "# dispatches at once. Raising it only helps if the Ollama server itself",
        "# is tuned for concurrent requests (OLLAMA_NUM_PARALLEL > 1, optionally",
        "# OLLAMA_SCHED_SPREAD=1 on the Ollama host to spread load across multiple",
        "# GPUs) -- that's server-side configuration this app cannot set remotely.",
        "# Against an untuned single-slot server, raising this just queues",
        "# requests and risks a queued step's own timeout expiring into a",
        "# template fallback it didn't need. Default 1 (steps) is safe/sequential.",
        "",
        "[steps]",
        f"provider = {_toml_str(cfg.steps.provider)}",
        f"endpoint = {_toml_str(cfg.steps.endpoint)}",
        f"model = {_toml_str(cfg.steps.model)}",
        f"max_concurrency = {_toml_str(cfg.steps.max_concurrency)}",
        "",
        "[narrative]",
        f"provider = {_toml_str(cfg.narrative.provider)}",
        f"endpoint = {_toml_str(cfg.narrative.endpoint)}",
        f"model = {_toml_str(cfg.narrative.model)}",
        f"passes = {_toml_str(cfg.narrative.passes)}",
        "",
        "[vision]",
        f"enabled = {_toml_str(cfg.vision.enabled)}",
        f"provider = {_toml_str(cfg.vision.provider)}",
        f"endpoint = {_toml_str(cfg.vision.endpoint)}",
        f"model = {_toml_str(cfg.vision.model)}",
        f"max_concurrency = {_toml_str(cfg.vision.max_concurrency)}",
        "",
    ]
    return "\n".join(lines)


def save_models_config(cfg: ModelsConfig, path):
    """Atomically write cfg to `path` (temp file + replace) so a crash mid-write
    never leaves a corrupt config."""
    path = Path(path)
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(dump_models_config_toml(cfg), encoding="utf-8")
    tmp.replace(path)
