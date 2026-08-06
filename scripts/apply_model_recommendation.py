"""CLI used by the "Daily AI Model Landscape Briefing" routine (a Claude
Code Remote scheduled trigger -- NOT part of the runtime app, and not
invoked by SOPForge itself) to safely apply a local model swap it
recommends for one of config/models.toml's sections.

Restricted to the local ollama provider, always: this never auto-switches
a section to a cloud provider. CLAUDE.md's local-first prime directive
("Anthropic API routing is an optional config flag, off by default") and
the user's own stated preference (local models ~90% of the time) both mean
a cloud swap is a recommendation for a human to apply via the /ui/config
page's own "Test connection" button, never something this script writes
unattended.

Even for the local case, nothing is written until preflight.probe_section
confirms the candidate model is both reachable AND already pulled on the
configured Ollama endpoint (status == "ok") -- a candidate that's merely
"a good idea" but not actually present locally is a decline, not a write;
pulling models is left to the user.

Practical caveat worth knowing: when this runs from the routine's own
remote/cloud container (as opposed to a machine with a real route to the
configured Ollama endpoint), the probe will almost always come back
unreachable -- the same network constraint test_llm_client.py's opt-in
Ollama integration test documents. That's the SAFE outcome in that
environment, not a bug: it declines and reports why rather than blind-
writing a model it never actually confirmed."""

import argparse
import sys

from pipeline.config import load_models_config, runtime_config_path, save_models_config
from pipeline.preflight import probe_section

_LOCAL_SECTIONS = ("steps", "narrative", "vision", "polish")


def apply_recommendation(section_name, model, config_path=None, provider="ollama", probe_fn=None):
    """Returns (applied: bool, message: str). Never raises for an expected
    decline (unknown section, non-local provider, unreachable endpoint,
    model not pulled) -- only a genuine I/O problem loading/saving the
    config propagates as an exception."""
    probe = probe_fn or probe_section
    if section_name not in _LOCAL_SECTIONS:
        return False, f"declined: unknown section {section_name!r}"
    if provider != "ollama":
        return False, (
            "declined: auto-apply is restricted to the local ollama provider -- "
            "propose cloud model changes to the user instead of applying them"
        )

    path = config_path or runtime_config_path()
    cfg = load_models_config(path)
    existing = getattr(cfg, section_name)
    candidate = existing.model_copy(update={"provider": "ollama", "model": model})

    result = probe(candidate)
    if result.get("status") != "ok":
        return False, (
            f"declined: preflight status={result.get('status')} "
            f"-- {result.get('detail', '(no detail)')}"
        )

    setattr(cfg, section_name, candidate)
    save_models_config(cfg, path)
    return (
        True,
        f"applied: [{section_name}].model = {model!r} (preflight confirmed reachable + pulled)",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--section", required=True, choices=_LOCAL_SECTIONS)
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--config", default=None, help="override the runtime config path")
    args = parser.parse_args(argv)

    try:
        applied, message = apply_recommendation(
            args.section, args.model, config_path=args.config, provider=args.provider
        )
    except Exception as exc:  # noqa: BLE001 - report cleanly, exit non-zero
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(message)
    return 0 if applied else 1


if __name__ == "__main__":
    sys.exit(main())
