"""Multi-pass narrative generation: draft -> critique -> revise, using the
LLM client (task-05) for each pass, with the pass count configurable via
config/models.toml's narrative.passes. The final text is always run
through task-08's claim-coverage gate (ensure_claim_coverage) — no matter
how many passes ran or what the LLM produced, every claim ends up covered
or [verify]-flagged; this is what makes multi-pass safe to add at all."""

from pipeline.claim_coverage import ensure_claim_coverage


def _draft_prompt(claims):
    claim_lines = "\n".join(f"- {c['text']}" for c in claims)
    return (
        "Write a short narrative paragraph describing this workflow, based "
        f"only on these facts:\n{claim_lines}\nBe factual and concise."
    )


def _critique_prompt(draft):
    return (
        "Critique the following narrative for factual accuracy and "
        f"clarity. List any issues briefly:\n\n{draft}"
    )


def _revise_prompt(draft, critique):
    return (
        f"Revise this narrative based on the critique.\n\nNarrative:\n{draft}\n\n"
        f"Critique:\n{critique}\n\nReturn only the revised narrative."
    )


def generate_narrative(claims, llm_client, passes=1):
    """Runs one initial draft pass, then `passes - 1` critique+revise
    rounds, then gates the final text through claim coverage. Returns
    (final_text, covered_claim_ids, verify_claim_ids)."""
    if passes < 1:
        raise ValueError("passes must be >= 1")

    text = llm_client.chat([{"role": "user", "content": _draft_prompt(claims)}])
    for _ in range(passes - 1):
        critique = llm_client.chat([{"role": "user", "content": _critique_prompt(text)}])
        text = llm_client.chat([{"role": "user", "content": _revise_prompt(text, critique)}])

    return ensure_claim_coverage(text, claims)
