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
from pipeline.llm_client import LLMClient


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


def test_anthropic_flag_not_yet_implemented_raises_without_network_call():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})

    config = SectionConfig(endpoint="http://fake", model="claude-x", anthropic=True)
    client = LLMClient(config, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(NotImplementedError):
            client.chat([{"role": "user", "content": "hi"}])
    finally:
        client.close()
    assert calls == []  # never even attempted the wrong-endpoint request


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
