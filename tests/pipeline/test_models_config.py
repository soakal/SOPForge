"""config/models.toml typed loader tests."""

import pytest
from pydantic import ValidationError

from pipeline.config import (
    ModelsConfig,
    dump_models_config_toml,
    load_models_config,
    save_models_config,
)


def test_loads_committed_config():
    config = load_models_config()
    assert config.steps.model == "qwen3:14b"
    assert config.narrative.model == "qwen3:32b"
    assert config.steps.anthropic is False
    assert config.narrative.anthropic is False
    assert config.narrative.passes == 3


def test_anthropic_flag_defaults_off_when_omitted(tmp_path):
    path = tmp_path / "models.toml"
    path.write_text(
        '[steps]\nendpoint = "http://x"\nmodel = "m"\n'
        '[narrative]\nendpoint = "http://x"\nmodel = "m"\n',
        encoding="utf-8",
    )
    config = load_models_config(path)
    assert config.steps.anthropic is False
    assert config.steps.passes == 1


def test_rejects_unknown_section(tmp_path):
    path = tmp_path / "models.toml"
    path.write_text(
        '[steps]\nendpoint = "http://x"\nmodel = "m"\n'
        '[narrative]\nendpoint = "http://x"\nmodel = "m"\n'
        '[bogus]\nfoo = "bar"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_models_config(path)


def _base_cfg():
    return {
        "steps": {"provider": "ollama", "endpoint": "http://x", "model": "m"},
        "narrative": {"provider": "ollama", "endpoint": "http://x", "model": "m"},
        "vision": {"provider": "ollama", "endpoint": "http://x", "model": "m"},
    }


def test_vision_provider_rejects_anthropic():
    # anthropic as a vision provider would leak ANTHROPIC_API_KEY to the ollama
    # endpoint -- it must be rejected at validation.
    data = _base_cfg()
    data["vision"]["provider"] = "anthropic"
    with pytest.raises(ValidationError):
        ModelsConfig.model_validate(data)


def test_passes_must_be_at_least_one():
    data = _base_cfg()
    data["narrative"]["passes"] = 0
    with pytest.raises(ValidationError):
        ModelsConfig.model_validate(data)


def test_toml_writer_escapes_control_chars_and_roundtrips(tmp_path):
    # A model value with a newline must serialize to VALID toml (not brick every
    # subsequent load) and read back exactly.
    data = _base_cfg()
    data["steps"]["model"] = 'weird\nmodel\twith\\control"chars'
    cfg = ModelsConfig.model_validate(data)
    path = tmp_path / "models.toml"
    save_models_config(cfg, path)
    # Must be loadable (would raise TOMLDecodeError if the writer emitted a raw
    # newline) and preserve the value.
    reloaded = load_models_config(path)
    assert reloaded.steps.model == 'weird\nmodel\twith\\control"chars'
    assert "[steps]" in dump_models_config_toml(cfg)


def test_rejects_missing_required_field(tmp_path):
    # `model` is required (endpoint has a default); a section without it must fail.
    path = tmp_path / "models.toml"
    path.write_text(
        '[steps]\nendpoint = "http://x"\n[narrative]\nendpoint = "http://x"\nmodel = "m"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_models_config(path)
