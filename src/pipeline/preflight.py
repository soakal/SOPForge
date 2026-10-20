"""Cheap reachability probe for a config section's LLM endpoint -- "is this
provider reachable, and does it know about this model" -- without ever
generating text. Used by /ui/config/test (a human clicking "Test connection"
on the Configuration page) and, best-effort, once per generation job
(server.py:_generate) so a dead endpoint shows up as a diagnostic on the
session page instead of a silent wall of template-fallback steps.

Never raises: a network failure, a bad response shape, or a missing API key
is data for the caller to display, not an exception to propagate. Never
sends a request for a keyed provider whose key env var isn't set, and never
puts a key value in the returned dict -- only whether one was required."""

import time

import httpx

from pipeline.config import PROVIDERS, provider_api_key, provider_endpoint
from pipeline.llm_client import ANTHROPIC_VERSION

ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
PROBE_READ_TIMEOUT = 4.0
PROBE_CONNECT_TIMEOUT = 2.0


def models_url(provider, configured_endpoint):
    """The URL to GET a provider's model list from. Anthropic has a fixed
    address (like its Messages API); every other provider is the OpenAI-
    compatible base URL (fixed for openrouter/openai, the section's own
    configured endpoint for ollama) plus '/models'."""
    if provider == "anthropic":
        return ANTHROPIC_MODELS_URL
    endpoint = provider_endpoint(provider, configured_endpoint)
    return endpoint.rstrip("/") + "/models"


def _headers(provider, key):
    if provider == "anthropic":
        return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    if key:
        return {"Authorization": f"Bearer {key}"}
    return {}


def _model_ids(payload):
    """Best-effort extraction of model ids from an OpenAI/Anthropic-shaped
    {"data": [{"id": ...}, ...]} response. Returns None (not []) if the
    shape is unrecognized, so the caller can distinguish "no models" from
    "couldn't tell"."""
    try:
        return [item["id"] for item in payload["data"]]
    except (KeyError, TypeError):
        return None


def probe_section(
    section, transport=None, timeout=PROBE_READ_TIMEOUT, connect_timeout=PROBE_CONNECT_TIMEOUT
):
    """Probe one config section (SectionConfig/PolishConfig/VisionConfig --
    anything with .provider/.endpoint/.model). Returns a dict, always, never
    raises:

    {"provider", "model", "endpoint": the URL actually probed,
     "reachable": bool, "model_present": bool | None,
     "latency_ms": int | None, "status": "ok" | "warn" | "error", "detail": str}

    status is "ok" only when reachable and the configured model was found in
    the provider's own listing; "warn" when reachable but the model wasn't
    found (or the response shape couldn't be parsed -- e.g. an Anthropic
    model alias that doesn't appear literally in its own listing); "error"
    when unreachable, a non-2xx response, or a required API key is unset."""
    provider = getattr(section, "provider", "ollama")
    model = getattr(section, "model", "")
    endpoint = models_url(provider, getattr(section, "endpoint", ""))
    key_env = PROVIDERS.get(provider, {}).get("key_env")
    if key_env and not provider_api_key(provider):
        return {
            "provider": provider,
            "model": model,
            "endpoint": endpoint,
            "reachable": False,
            "model_present": None,
            "latency_ms": None,
            "status": "error",
            "detail": f"{key_env} is not set",
        }
    key = provider_api_key(provider)
    started = time.perf_counter()
    try:
        with httpx.Client(
            transport=transport, timeout=httpx.Timeout(timeout, connect=connect_timeout)
        ) as client:
            response = client.get(endpoint, headers=_headers(provider, key))
    except Exception as exc:  # noqa: BLE001 - a probe failure is data, never an exception
        return {
            "provider": provider,
            "model": model,
            "endpoint": endpoint,
            "reachable": False,
            "model_present": None,
            "latency_ms": None,
            "status": "error",
            "detail": str(exc)[:200],
        }
    latency_ms = int((time.perf_counter() - started) * 1000)
    if response.status_code >= 400:
        return {
            "provider": provider,
            "model": model,
            "endpoint": endpoint,
            "reachable": False,
            "model_present": None,
            "latency_ms": latency_ms,
            "status": "error",
            "detail": f"HTTP {response.status_code}",
        }
    try:
        ids = _model_ids(response.json())
    except Exception:  # noqa: BLE001 - unparseable body is a warn, not a crash
        ids = None
    if ids is None:
        return {
            "provider": provider,
            "model": model,
            "endpoint": endpoint,
            "reachable": True,
            "model_present": None,
            "latency_ms": latency_ms,
            "status": "warn",
            "detail": "reachable, but the model list response could not be parsed",
        }
    if model in ids:
        return {
            "provider": provider,
            "model": model,
            "endpoint": endpoint,
            "reachable": True,
            "model_present": True,
            "latency_ms": latency_ms,
            "status": "ok",
            "detail": "reachable and model found",
        }
    sample = ", ".join(ids[:5]) or "(none listed)"
    return {
        "provider": provider,
        "model": model,
        "endpoint": endpoint,
        "reachable": True,
        "model_present": False,
        "latency_ms": latency_ms,
        "status": "warn",
        "detail": f"reachable, but '{model}' was not found in the provider's list: {sample}",
    }
