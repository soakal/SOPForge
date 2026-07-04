"""Shared fast, deterministic stub LLM client for tests that create
sessions through the live server (create_app). Without injecting this via
create_app's llm_client_factory parameter, every session would make a real
network attempt to the (usually unreachable in a dev/test environment)
configured Ollama endpoint before falling back per step — several seconds
each, multiplied by every step in every test that touches a session.

Not a test file itself (no test_ functions), so pytest doesn't collect it."""


class StubLLMClient:
    """Always triggers task-06's per-step template fallback: fast,
    deterministic, and exercises the exact same code path a genuinely
    unreachable/misconfigured LLM endpoint would in production, without
    the network latency."""

    def chat(self, messages, **kwargs):
        return "stub reply that never matches any manifest, forcing template fallback"


def stub_llm_client_factory():
    return StubLLMClient()
