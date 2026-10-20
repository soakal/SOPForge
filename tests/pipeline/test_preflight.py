"""preflight.probe_section: a cheap reachability/model-presence check for a
config section's LLM endpoint, over an injectable httpx transport -- no real
network needed. Mirrors test_llm_client.py's MockTransport pattern."""

import json

import httpx

from pipeline.config import SectionConfig
from pipeline.preflight import probe_section


def _mock_transport(status_code=200, payload=None, on_request=None):
    def handler(request):
        if on_request is not None:
            on_request(request)
        if payload is None:
            return httpx.Response(status_code)
        return httpx.Response(status_code, json=payload)

    return httpx.MockTransport(handler)


def test_probe_ollama_reachable_with_model_present():
    section = SectionConfig(endpoint="http://fake-ollama/v1", model="qwen3:32b")
    calls = []
    result = probe_section(
        section,
        transport=_mock_transport(
            payload={"data": [{"id": "qwen3:32b"}, {"id": "llama3:8b"}]},
            on_request=lambda r: calls.append(r),
        ),
    )
    assert result["status"] == "ok"
    assert result["reachable"] is True
    assert result["model_present"] is True
    assert isinstance(result["latency_ms"], int)
    assert result["latency_ms"] >= 0
    assert result["endpoint"] == "http://fake-ollama/v1/models"
    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert str(calls[0].url) == "http://fake-ollama/v1/models"


def test_probe_ollama_reachable_but_model_missing():
    section = SectionConfig(endpoint="http://fake-ollama/v1", model="not-pulled:7b")
    result = probe_section(
        section, transport=_mock_transport(payload={"data": [{"id": "qwen3:32b"}]})
    )
    assert result["status"] == "warn"
    assert result["reachable"] is True
    assert result["model_present"] is False
    assert "not-pulled:7b" in result["detail"]


def test_probe_http_error_response():
    section = SectionConfig(endpoint="http://fake-ollama/v1", model="qwen3:32b")
    result = probe_section(section, transport=_mock_transport(status_code=500))
    assert result["status"] == "error"
    assert result["reachable"] is False
    assert result["model_present"] is None
    assert "500" in result["detail"]


def test_probe_never_raises_on_transport_error():
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    section = SectionConfig(endpoint="http://fake-ollama/v1", model="qwen3:32b")
    result = probe_section(section, transport=httpx.MockTransport(handler))
    assert result["status"] == "error"
    assert result["reachable"] is False
    assert result["latency_ms"] is None


def test_probe_openrouter_uses_fixed_endpoint_and_bearer_auth(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    captured = {}

    def on_request(request):
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)

    section = SectionConfig(
        endpoint="http://this-endpoint-is-ignored", model="some/model", provider="openrouter"
    )
    result = probe_section(
        section,
        transport=_mock_transport(payload={"data": [{"id": "some/model"}]}, on_request=on_request),
    )
    assert captured["url"] == "https://openrouter.ai/api/v1/models"
    assert captured["headers"]["authorization"] == "Bearer or-test-key"
    assert result["status"] == "ok"


def test_probe_anthropic_uses_messages_headers_and_host(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    captured = {}

    def on_request(request):
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)

    section = SectionConfig(endpoint="unused", model="claude-haiku-4-5", provider="anthropic")
    result = probe_section(
        section,
        transport=_mock_transport(
            payload={"data": [{"id": "claude-sonnet-5"}]}, on_request=on_request
        ),
    )
    assert captured["url"] == "https://api.anthropic.com/v1/models"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["headers"]["x-api-key"] == "sk-ant-test-key"
    # The alias isn't expected to appear literally in Anthropic's own listing.
    assert result["status"] == "warn"
    assert result["reachable"] is True


def test_probe_keyed_provider_missing_key_makes_no_network_call(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    calls = []
    section = SectionConfig(endpoint="http://fake", model="m", provider="openrouter")
    result = probe_section(section, transport=_mock_transport(on_request=lambda r: calls.append(r)))
    assert calls == []
    assert result["status"] == "error"
    assert result["reachable"] is False
    assert "OPENROUTER_API_KEY" in result["detail"]


def test_probe_result_is_json_serializable():
    section = SectionConfig(endpoint="http://fake-ollama/v1", model="qwen3:32b")
    result = probe_section(
        section, transport=_mock_transport(payload={"data": [{"id": "qwen3:32b"}]})
    )
    json.dumps(result)  # must not raise
