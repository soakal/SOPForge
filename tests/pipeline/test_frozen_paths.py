"""Frozen-bundle resource resolution (AC3 de-risk, AC5 prerequisite):
resource_path() and every call site that depends on it (config.py's
default config path, docx_assembler.py's SOP Factory 2 directory) must
resolve correctly whether running from source or as a frozen PyInstaller
build. Proven by monkeypatching sys.frozen/sys._MEIPASS, not by trusting
the dev-mode branch alone."""

import sys
from pathlib import Path

from pipeline.config import default_config_path
from pipeline.docx_assembler import DEFAULT_SOP_FACTORY_2_DIR, sop_factory_2_dir
from pipeline.resource_path import resource_path


def test_resource_path_dev_mode_resolves_relative_to_repo_root():
    assert not getattr(sys, "frozen", False)
    path = resource_path("config", "models.toml")
    assert path.exists()
    assert path.name == "models.toml"


def test_resource_path_frozen_mode_resolves_relative_to_meipass(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    path = resource_path("config", "models.toml")
    assert path == tmp_path / "config" / "models.toml"


def test_default_config_path_switches_with_frozen_state(monkeypatch, tmp_path):
    dev_path = default_config_path()
    assert dev_path.exists()  # real repo-relative config/models.toml

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    frozen_path = default_config_path()
    assert frozen_path == tmp_path / "config" / "models.toml"
    assert frozen_path != dev_path


def test_sop_factory_2_dir_dev_mode_is_the_external_clone():
    assert not getattr(sys, "frozen", False)
    assert sop_factory_2_dir() == DEFAULT_SOP_FACTORY_2_DIR


def test_sop_factory_2_dir_frozen_mode_resolves_inside_the_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    path = sop_factory_2_dir()
    assert path == tmp_path / "sop_factory_2"
    assert path != DEFAULT_SOP_FACTORY_2_DIR


def test_sop_factory_2_dir_env_override_wins_in_both_modes(monkeypatch, tmp_path):
    override_dir = tmp_path / "custom-clone"
    monkeypatch.setenv("SOPFORGE_SOP_FACTORY_2_DIR", str(override_dir))

    assert sop_factory_2_dir() == Path(override_dir)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "meipass"), raising=False)
    assert sop_factory_2_dir() == Path(override_dir)


def test_config_explicit_path_wins_in_frozen_mode(monkeypatch, tmp_path):
    """default_config_path() itself has no env override, but
    load_models_config(path=...) accepts an explicit path regardless of
    frozen state — confirm the explicit-path branch is never shadowed by
    frozen resolution."""
    from pipeline.config import load_models_config

    custom = tmp_path / "custom-models.toml"
    custom.write_text(
        '[steps]\nendpoint = "http://x"\nmodel = "m"\n'
        '[narrative]\nendpoint = "http://x"\nmodel = "m"\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "meipass"), raising=False)

    config = load_models_config(path=custom)
    assert config.steps.model == "m"
