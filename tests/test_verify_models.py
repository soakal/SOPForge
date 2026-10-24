import httpx

import scripts.verify_models as vm
from pipeline.config import load_models_config

TAGS = {"qwen3:32b", "qwen2.5vl:7b", "gemma3:12b", "qwen3:14b", "qwen3.6:27b", "qwen3-vl:8b"}


def _transport(names):
    return httpx.MockTransport(
        lambda req: httpx.Response(200, json={"models": [{"name": n} for n in names]})
    )


def test_collect_expected_tags_includes_config_and_page_ollama_tags():
    assert TAGS <= vm.collect_expected_tags(load_models_config(vm.CONFIG))


def test_verify_models_reports_missing_tags(capsys):
    assert vm.main(transport=_transport(["qwen3:32b", "qwen2.5vl:7b"])) == 1
    out = capsys.readouterr().out
    for tag in TAGS - {"qwen3:32b", "qwen2.5vl:7b"}:
        assert f"missing: {tag}" in out
    everything = vm.collect_expected_tags(load_models_config(vm.CONFIG))
    assert vm.main(transport=_transport(everything)) == 0
