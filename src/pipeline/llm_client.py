"""OpenAI-compatible chat-completions client (Ollama by default). Anthropic
routing is a per-section opt-in flag, off by default (CLAUDE.md: "Anthropic
routing per section is a config option, default off") — when a section has
anthropic=true, chat() routes to Anthropic's Messages API instead of the
configured OpenAI-compatible endpoint (that section's `endpoint` field is
then unused; Anthropic's API address is fixed). Uses the same httpx.Client
and injectable transport for both paths — httpx lets an absolute URL
bypass base_url on the same client, so one MockTransport covers both in
tests, no new SDK dependency needed for a single POST + two headers.

The Anthropic API key is read from the ANTHROPIC_API_KEY environment
variable only — never from a config file, never committed to the repo."""

import os

import httpx

from pipeline.config import PROVIDERS

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_DEFAULT_MAX_TOKENS = 1024


class AnthropicAPIKeyMissingError(RuntimeError):
    """A section routes to a provider whose API key env var isn't set. Fails
    loudly and immediately — the caller (task-06's orchestrator) treats
    this like any other chat() failure and falls back to the template,
    never retries, but the underlying cause must still be a clear,
    specific error, not a generic connection failure."""


class LLMClient:
    def __init__(self, section_config, transport=None, timeout=60.0, connect_timeout=5.0):
        """timeout applies to read/write/pool phases (generous, since a
        real model genuinely thinking can take a while); connect_timeout
        is capped much shorter (default 5s) since "can we even reach this
        host" is a network-level question, not a model-latency one — an
        unreachable/misconfigured endpoint must fail fast, not eat up to
        `timeout` seconds *per step*."""
        self.config = section_config
        self.provider = getattr(section_config, "provider", "ollama")
        # ollama uses the section's own endpoint; openrouter/openai have fixed
        # endpoints; anthropic uses its Messages API (absolute URL below).
        endpoint = PROVIDERS.get(self.provider, {}).get("endpoint") or section_config.endpoint
        self._client = httpx.Client(
            base_url=endpoint.rstrip("/"),
            transport=transport,
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
        )

    def _api_key(self):
        """The API key for this provider, from its env var. Returns None for a
        keyless provider (ollama). Raises if a keyed provider's var is unset."""
        key_env = PROVIDERS.get(self.provider, {}).get("key_env")
        if not key_env:
            return None
        key = os.environ.get(key_env)
        if not key:
            raise AnthropicAPIKeyMissingError(
                f"This section routes to '{self.provider}' but the {key_env} "
                "environment variable is not set."
            )
        return key

    def chat(self, messages, **kwargs):
        """messages: list of {"role": ..., "content": ...}. Returns the
        assistant's reply content (str). Raises on a non-2xx response or a
        missing API key — the caller falls back to the template, never
        retrying."""
        if self.provider == "anthropic":
            return self._chat_anthropic(messages, **kwargs)
        headers = {}
        key = self._api_key()  # None for ollama; raises if a keyed provider is unset
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = {"model": self.config.model, "messages": messages, **kwargs}
        response = self._client.post("/chat/completions", json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    def _chat_anthropic(self, messages, **kwargs):
        api_key = self._api_key()
        max_tokens = kwargs.pop("max_tokens", ANTHROPIC_DEFAULT_MAX_TOKENS)
        payload = {
            "model": self.config.model,
            "max_tokens": max_tokens,
            "messages": messages,
            **kwargs,
        }
        response = self._client.post(
            ANTHROPIC_MESSAGES_URL,
            json=payload,
            headers={"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION},
        )
        response.raise_for_status()
        data = response.json()
        return data["content"][0]["text"]

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
