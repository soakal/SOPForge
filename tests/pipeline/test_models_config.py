"""config/models.toml typed loader tests."""

import pytest
from pydantic import ValidationError

from pipeline.config import (
    ModelsConfig,
    SectionConfig,
    default_config_path,
    dump_models_config_toml,
    load_models_config,
    resolve_polish_config,
    save_models_config,
)


def test_loads_committed_config():
    # Explicit path, not the no-arg call -- that falls through to the
    # per-user runtime copy (config.py's runtime_config_path, seeded once
    # from this same file), which on a machine that already has one can be
    # stale relative to the repo's own committed default.
    config = load_models_config(default_config_path())
    assert config.steps.model == "qwen3:32b"
    assert config.narrative.model == "qwen3.6:27b"
    assert config.steps.anthropic is False
    assert config.narrative.anthropic is False
    assert config.narrative.passes == 3
    assert config.steps.max_concurrency == 1
    assert config.vision.max_concurrency == 4


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


def test_max_concurrency_defaults_to_one_when_omitted_legacy_config(tmp_path):
    path = tmp_path / "models.toml"
    path.write_text(
        '[steps]\nendpoint = "http://x"\nmodel = "m"\n'
        '[narrative]\nendpoint = "http://x"\nmodel = "m"\n',
        encoding="utf-8",
    )
    config = load_models_config(path)
    assert config.steps.max_concurrency == 1
    assert config.vision.max_concurrency == 4


def test_max_concurrency_round_trips(tmp_path):
    data = _base_cfg()
    data["steps"]["max_concurrency"] = 6
    data["vision"]["max_concurrency"] = 2
    cfg = ModelsConfig.model_validate(data)
    path = tmp_path / "models.toml"
    save_models_config(cfg, path)
    reloaded = load_models_config(path)
    assert reloaded.steps.max_concurrency == 6
    assert reloaded.vision.max_concurrency == 2


def test_max_concurrency_rejects_zero():
    data = _base_cfg()
    data["steps"]["max_concurrency"] = 0
    with pytest.raises(ValidationError):
        ModelsConfig.model_validate(data)


def test_use_vision_defaults_to_false(tmp_path):
    path = tmp_path / "models.toml"
    path.write_text(
        '[steps]\nendpoint = "http://x"\nmodel = "m"\n'
        '[narrative]\nendpoint = "http://x"\nmodel = "m"\n',
        encoding="utf-8",
    )
    config = load_models_config(path)
    assert config.steps.use_vision is False


def test_use_vision_round_trips(tmp_path):
    data = _base_cfg()
    data["steps"]["use_vision"] = True
    cfg = ModelsConfig.model_validate(data)
    path = tmp_path / "models.toml"
    save_models_config(cfg, path)
    reloaded = load_models_config(path)
    assert reloaded.steps.use_vision is True
    assert "use_vision = true" in dump_models_config_toml(cfg)


def test_committed_default_config_lacks_use_vision_and_still_loads():
    # config/models.toml predates this field -- confirms an existing
    # real-world config without use_vision still validates (defaults False)
    # and extra="forbid" hasn't been loosened to silently accept anything.
    config = load_models_config(default_config_path())
    assert config.steps.use_vision is False
    with pytest.raises(ValidationError):
        SectionConfig(model="m", bogus_field=True)


def test_polish_defaults_sanely_when_section_is_absent(tmp_path):
    path = tmp_path / "models.toml"
    path.write_text(
        '[steps]\nendpoint = "http://x"\nmodel = "m"\n'
        '[narrative]\nendpoint = "http://x"\nmodel = "m"\n',
        encoding="utf-8",
    )
    config = load_models_config(path)
    assert config.polish.enabled is False
    assert config.polish.provider == "ollama"
    assert config.polish.model  # non-empty; default (gemma3:12b) is confirmed pulled/live on the Ollama host


def test_polish_parses_explicit_section(tmp_path):
    path = tmp_path / "models.toml"
    path.write_text(
        '[steps]\nendpoint = "http://x"\nmodel = "m"\n'
        '[narrative]\nendpoint = "http://x"\nmodel = "m"\n'
        '[polish]\nenabled = true\nprovider = "ollama"\nendpoint = "http://x"\nmodel = "gemma3n:e4b"\n',
        encoding="utf-8",
    )
    config = load_models_config(path)
    assert config.polish.enabled is True
    assert config.polish.model == "gemma3n:e4b"


def test_polish_round_trips_through_dump_and_reload(tmp_path):
    data = _base_cfg()
    data["polish"] = {
        "enabled": True,
        "provider": "ollama",
        "endpoint": "http://x",
        "model": "gemma3n:e4b",
    }
    cfg = ModelsConfig.model_validate(data)
    path = tmp_path / "models.toml"
    save_models_config(cfg, path)
    reloaded = load_models_config(path)
    assert reloaded.polish.enabled is True
    assert reloaded.polish.provider == "ollama"
    assert reloaded.polish.model == "gemma3n:e4b"
    assert "[polish]" in dump_models_config_toml(cfg)


def test_resolve_polish_config_off_returns_none():
    config = load_models_config(default_config_path())
    assert resolve_polish_config("off", config) is None


def test_resolve_polish_config_local_forces_ollama():
    data = _base_cfg()
    data["polish"] = {
        "enabled": False,
        "provider": "openai",
        "endpoint": "http://x",
        "model": "gemma3n:e4b",
    }
    config = ModelsConfig.model_validate(data)
    resolved = resolve_polish_config("local", config)
    assert resolved is not None
    assert resolved.provider == "ollama"
    assert resolved.enabled is True
    # Everything else about the section (e.g. the configured model) is
    # preserved -- only the provider (and enabled) are forced.
    assert resolved.model == "gemma3n:e4b"


def test_resolve_polish_config_haiku_forces_anthropic_claude_haiku():
    config = load_models_config(default_config_path())
    resolved = resolve_polish_config("haiku", config)
    assert resolved is not None
    assert resolved.provider == "anthropic"
    assert resolved.model == "claude-haiku-4-5"
    assert resolved.enabled is True


def test_resolve_polish_config_rejects_unknown_mode():
    config = load_models_config(default_config_path())
    with pytest.raises(ValueError):
        resolve_polish_config("bogus", config)


def test_rejects_missing_required_field(tmp_path):
    # `model` is required (endpoint has a default); a section without it must fail.
    path = tmp_path / "models.toml"
    path.write_text(
        '[steps]\nendpoint = "http://x"\n[narrative]\nendpoint = "http://x"\nmodel = "m"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_models_config(path)
