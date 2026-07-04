"""Step generation orchestrator: per-record prompt -> LLM call -> round-trip
gate -> template fallback (invariants L2/L3, CLAUDE.md). Exactly one
generation attempt per step, ever — "Round-trip validator: ... failures
demonstrably fall back to template, never retry loops." Any generation
failure at all (bad HTTP status, malformed response body missing/short
`choices`, non-JSON reply, round-trip mismatch) falls back immediately;
nothing is ever retried, and the failure mode never propagates to the
caller — a broken LLM must never take down doc generation."""

from pipeline.roundtrip import round_trip_ok
from pipeline.template import render_step_template


def _build_prompt(step):
    """A minimal, deterministic prompt built purely from manifest fields —
    the LLM only phrases what's already true, it never decides content
    (CLAUDE.md: "The LLM never decides what the steps are — only how to
    phrase them")."""
    action_word = "clicking" if step.action == "click" else "typing into"
    target = step.element.name or step.element.control_type or "an element"
    window = step.window.title or "the current window"
    return (
        f"Write one sentence describing a user {action_word} '{target}' "
        f"in the '{window}' window. Be factual and concise."
    )


def generate_step_text(step, llm_client):
    """Returns (text, used_fallback). Exactly one LLM call attempt; any
    failure — HTTP error, malformed response body, or a round-trip mismatch
    — falls back to the template, never retried."""
    try:
        prompt = _build_prompt(step)
        reply = llm_client.chat([{"role": "user", "content": prompt}])
    except Exception:  # noqa: BLE001 - any generation failure means fallback, never retry
        return render_step_template(step), True

    ok, _mismatches = round_trip_ok(reply, step)
    if not ok:
        return render_step_template(step), True
    return reply, False


def generate_all_steps(manifest, llm_client):
    """Returns [{"step_id", "text", "used_fallback"}, ...] in manifest order
    — one generation attempt per step, invariant L1's 1:1 mapping preserved."""
    results = []
    for step in manifest.steps:
        text, used_fallback = generate_step_text(step, llm_client)
        results.append({"step_id": step.id, "text": text, "used_fallback": used_fallback})
    return results
