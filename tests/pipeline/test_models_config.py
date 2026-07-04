"""config/models.toml typed loader tests."""

import pytest
from pydantic import ValidationError

from pipeline.config import load_models_config


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


def test_rejects_missing_required_field(tmp_path):
    path = tmp_path / "models.toml"
    path.write_text(
        '[steps]\nmodel = "m"\n[narrative]\nendpoint = "http://x"\nmodel = "m"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_models_config(path)
