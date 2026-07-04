"""OpenAI-compatible chat-completions client (Ollama by default). Anthropic
routing is a per-section opt-in flag, off by default (CLAUDE.md: "Anthropic
routing per section is a config option, default off") — not yet
implemented, so a section configured with anthropic=true raises rather than
silently talking to the wrong endpoint. Uses httpx with an injectable
transport so tests never need a real network call unless explicitly opted
into (see the opt-in integration test in tests/pipeline/test_llm_client.py)."""

import httpx


class LLMClient:
    def __init__(self, section_config, transport=None, timeout=60.0):
        self.config = section_config
        self._client = httpx.Client(
            base_url=section_config.endpoint.rstrip("/"),
            transport=transport,
            timeout=timeout,
        )

    def chat(self, messages, **kwargs):
        """messages: list of {"role": ..., "content": ...}. Returns the
        assistant's reply content (str). Raises httpx.HTTPStatusError on a
        non-2xx response — the caller (task-06's orchestrator) is
        responsible for falling back to the template, never retrying."""
        if self.config.anthropic:
            raise NotImplementedError(
                "Anthropic routing is not yet implemented; this section's "
                "config has anthropic=true but no Anthropic client exists"
            )
        payload = {"model": self.config.model, "messages": messages, **kwargs}
        response = self._client.post("/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
