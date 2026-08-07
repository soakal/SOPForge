"""apply_model_recommendation.py: the CLI the "Daily AI Model Landscape
Briefing" routine uses to safely apply a local model swap it recommends --
never for a cloud provider, and never without preflight confirming the
candidate is actually reachable and pulled first."""

import shutil

from pipeline.config import default_config_path, load_models_config

from scripts.apply_model_recommendation import apply_recommendation, main


def _config_path(tmp_path):
    cfg = tmp_path / "models.toml"
    shutil.copyfile(default_config_path(), cfg)
    return cfg


def test_applies_when_preflight_confirms_reachable_and_pulled(tmp_path):
    cfg_path = _config_path(tmp_path)
    calls = []

    def stub_probe(section):
        calls.append(section)
        return {"status": "ok", "detail": "reachable and model found"}

    applied, message = apply_recommendation(
        "steps", "qwen3.7:32b", config_path=cfg_path, probe_fn=stub_probe
    )
    assert applied is True
    assert "applied" in message
    assert len(calls) == 1
    assert calls[0].model == "qwen3.7:32b"
    assert calls[0].provider == "ollama"

    saved = load_models_config(cfg_path)
    assert saved.steps.model == "qwen3.7:32b"
    assert saved.steps.provider == "ollama"


def test_declines_when_preflight_reports_model_not_pulled(tmp_path):
    cfg_path = _config_path(tmp_path)
    before = load_models_config(cfg_path)

    applied, message = apply_recommendation(
        "steps",
        "not-actually-pulled:7b",
        config_path=cfg_path,
        probe_fn=lambda section: {"status": "warn", "detail": "model not found"},
    )
    assert applied is False
    assert "declined" in message

    after = load_models_config(cfg_path)
    assert after.steps.model == before.steps.model  # unchanged


def test_declines_when_endpoint_unreachable(tmp_path):
    cfg_path = _config_path(tmp_path)
    applied, message = apply_recommendation(
        "steps",
        "qwen3.7:32b",
        config_path=cfg_path,
        probe_fn=lambda section: {"status": "error", "detail": "connection refused"},
    )
    assert applied is False
    assert "declined" in message


def test_never_applies_a_non_ollama_provider(tmp_path):
    cfg_path = _config_path(tmp_path)
    calls = []
    applied, message = apply_recommendation(
        "steps",
        "claude-haiku-4-5",
        config_path=cfg_path,
        provider="anthropic",
        probe_fn=lambda section: calls.append(section) or {"status": "ok"},
    )
    assert applied is False
    assert "ollama" in message
    assert calls == []  # never even probed -- rejected before any network call


def test_rejects_unknown_section(tmp_path):
    cfg_path = _config_path(tmp_path)
    calls = []
    applied, message = apply_recommendation(
        "not-a-real-section",
        "some-model",
        config_path=cfg_path,
        probe_fn=lambda section: calls.append(section) or {"status": "ok"},
    )
    assert applied is False
    assert "unknown section" in message
    assert calls == []


def test_applying_to_vision_and_polish_sections_works_too(tmp_path):
    cfg_path = _config_path(tmp_path)
    for section in ("vision", "polish"):
        applied, _message = apply_recommendation(
            section,
            "some-local-model",
            config_path=cfg_path,
            probe_fn=lambda section: {"status": "ok"},
        )
        assert applied is True
    saved = load_models_config(cfg_path)
    assert saved.vision.model == "some-local-model"
    assert saved.polish.model == "some-local-model"


def test_cli_main_exit_codes(tmp_path, capsys):
    cfg_path = _config_path(tmp_path)

    # Applied -> exit 0. Uses the real probe_section (no probe_fn override
    # from the CLI), so this deliberately targets an endpoint nothing will
    # answer -- proving the CLI still declines cleanly (never crashes) when
    # it can't reach a real endpoint, the expected outcome from this
    # sandboxed test environment (see module docstring's "practical caveat").
    code = main(["--section", "steps", "--model", "unreachable:1b", "--config", str(cfg_path)])
    assert code == 1
    out = capsys.readouterr().out
    assert "declined" in out


def test_cli_main_rejects_bad_section_via_argparse(capsys):
    try:
        main(["--section", "not-a-section", "--model", "m"])
        raised = False
    except SystemExit as exc:
        raised = True
        assert exc.code == 2
    assert raised
