"""LLM client tests: OpenAI-compatible chat-completions over an injectable
httpx transport — no real network needed for these. One opt-in integration
test at the bottom exercises a real Ollama endpoint if (and only if)
SOPFORGE_OLLAMA_URL is set and reachable; it skips (never fails) otherwise —
this build environment has no network route to the real endpoint (see
.claude/skills/uia-notes.md's Phase 2 notes, or CLAUDE.md's Models section
for the address), so it is expected to always skip here."""

import json
import os
import socket
from urllib.parse import urlparse

import httpx
import pytest

from pipeline.config import SectionConfig
from pipeline.llm_client import AnthropicAPIKeyMissingError, LLMClient


def _mock_transport(expected_model=None, reply="mocked reply"):
    def handler(request):
        body = json.loads(request.content)
        if expected_model is not None:
            assert body["model"] == expected_model
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})

    return httpx.MockTransport(handler)


def test_chat_returns_assistant_content():
    config = SectionConfig(endpoint="http://fake", model="qwen3:14b")
    client = LLMClient(
        config, transport=_mock_transport(expected_model="qwen3:14b", reply="hi there")
    )
    try:
        assert client.chat([{"role": "user", "content": "hello"}]) == "hi there"
    finally:
        client.close()


def test_chat_raises_on_http_error():
    def handler(request):
        return httpx.Response(500, json={"error": "boom"})

    client = LLMClient(
        SectionConfig(endpoint="http://fake", model="m"), transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(httpx.HTTPStatusError):
            client.chat([{"role": "user", "content": "hi"}])
    finally:
        client.close()


def test_anthropic_routes_to_the_messages_api_with_correct_payload_and_headers(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "hi from claude"}]})

    config = SectionConfig(endpoint="http://fake-ollama", model="claude-sonnet-5", anthropic=True)
    client = LLMClient(config, transport=httpx.MockTransport(handler))
    try:
        reply = client.chat([{"role": "user", "content": "hello"}])
    finally:
        client.close()

    assert reply == "hi from claude"
    # Anthropic's own fixed API address, NOT the section's Ollama endpoint.
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "sk-ant-test-key"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["body"]["model"] == "claude-sonnet-5"
    assert captured["body"]["messages"] == [{"role": "user", "content": "hello"}]
    assert captured["body"]["max_tokens"] > 0


def test_anthropic_missing_api_key_raises_without_any_network_call(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})

    config = SectionConfig(endpoint="http://fake", model="claude-x", anthropic=True)
    client = LLMClient(config, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AnthropicAPIKeyMissingError):
            client.chat([{"role": "user", "content": "hi"}])
    finally:
        client.close()
    assert calls == []  # never even attempted the request without a key


def test_anthropic_custom_max_tokens_is_passed_through(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    config = SectionConfig(endpoint="http://fake", model="claude-x", anthropic=True)
    client = LLMClient(config, transport=httpx.MockTransport(handler))
    try:
        client.chat([{"role": "user", "content": "hi"}], max_tokens=42)
    finally:
        client.close()
    assert captured["body"]["max_tokens"] == 42


def test_anthropic_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

    def handler(request):
        return httpx.Response(401, json={"error": "invalid api key"})

    config = SectionConfig(endpoint="http://fake", model="claude-x", anthropic=True)
    client = LLMClient(config, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(httpx.HTTPStatusError):
            client.chat([{"role": "user", "content": "hi"}])
    finally:
        client.close()


def test_context_manager_closes_client():
    with LLMClient(
        SectionConfig(endpoint="http://fake", model="m"),
        transport=_mock_transport(reply="x"),
    ) as client:
        assert client.chat([{"role": "user", "content": "hi"}]) == "x"


def _ollama_reachable(url, timeout=2.0):
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def test_real_ollama_integration_opt_in():
    url = os.environ.get("SOPFORGE_OLLAMA_URL")
    if not url:
        pytest.skip("SOPFORGE_OLLAMA_URL not set; opt-in integration test skipped")
    if not _ollama_reachable(url):
        pytest.skip(f"{url} unreachable; opt-in integration test skipped")

    config = SectionConfig(endpoint=url, model="qwen3:14b")
    with LLMClient(config) as client:
        reply = client.chat([{"role": "user", "content": "Reply with only the word 'ok'."}])
        assert reply


def test_real_anthropic_integration_opt_in():
    """Opt-in: only runs if ANTHROPIC_API_KEY is actually set. Skips
    cleanly (never fails) otherwise — this is a real network call to a
    real paid API, never exercised by the default suite."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set; opt-in Anthropic integration test skipped")

    model = os.environ.get("SOPFORGE_ANTHROPIC_TEST_MODEL", "claude-haiku-4-5-20251001")
    config = SectionConfig(endpoint="unused-for-anthropic", model=model, anthropic=True)
    with LLMClient(config) as client:
        reply = client.chat(
            [{"role": "user", "content": "Reply with only the word 'ok'."}], max_tokens=10
        )
        assert reply
