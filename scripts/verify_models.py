"""Check that every ollama model tag SOPForge configures or suggests is actually
installed on the Ollama host (GET /api/tags). Exits 1 if any are missing."""

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pipeline.config import load_models_config  # noqa: E402
from pipeline.webui import pages  # noqa: E402

CONFIG = Path(__file__).resolve().parent.parent / "config" / "models.toml"


def collect_expected_tags(cfg):
    sections = (cfg.steps, cfg.narrative, cfg.vision, cfg.polish)
    tags = {s.model for s in sections if s.provider == "ollama"}
    for table in (pages._RECOMMENDED, pages._MODEL_SUGGESTIONS):
        for per_provider in table.values():
            ollama = per_provider.get("ollama", [])
            tags.update([ollama] if isinstance(ollama, str) else ollama)
    return tags


def fetch_installed(endpoint, transport=None):
    base = endpoint.rstrip("/").removesuffix("/v1")
    with httpx.Client(transport=transport, trust_env=False, timeout=10) as client:
        resp = client.get(f"{base}/api/tags")
        resp.raise_for_status()
        return {m["name"] for m in resp.json()["models"]}


def main(transport=None):
    cfg = load_models_config(CONFIG)
    missing = sorted(collect_expected_tags(cfg) - fetch_installed(cfg.steps.endpoint, transport))
    for tag in missing:
        print(f"missing: {tag}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
