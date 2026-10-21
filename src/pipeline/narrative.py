"""Multi-pass narrative generation: draft -> critique -> revise, using the
LLM client (task-05) for each pass, with the pass count configurable via
config/models.toml's narrative.passes. The final text is always run
through task-08's claim-coverage gate (ensure_claim_coverage) — no matter
how many passes ran or what the LLM produced, every claim ends up covered
or [verify]-flagged; this is what makes multi-pass safe to add at all."""

import logging
import re

from pipeline.claim_coverage import ensure_claim_coverage, render_verify_blockquote

_VERIFY_PREFIX = "> [verify]"
_CONNECTIVE_RE = re.compile(
    r"^(?:first|then|next|finally|lastly|afterwards|after that|now)\b[,:]?\s*"
)
_LEADING_JUNK_RE = re.compile(r"^[^a-z0-9]+")


def _norm(text):
    return " ".join(text.lower().split())


def verify_lines(text):
    """The ordered '> [verify]' lines of `text`, stripped."""
    return [ln.strip() for ln in text.splitlines() if ln.strip().startswith(_VERIFY_PREFIX)]


# ponytail: free prose from the narrative model is rejected whole; upgrade path
# is a constrained transformation that preserves predicate arguments.
def narrative_unsupported(text, claims, allow_verify_lines=False):
    """None if `text` is only the claims quoted verbatim (increasing order,
    each at most once, optional leading connectives); else the first
    unsupported fragment. A '> [verify]' line is accepted only when
    allow_verify_lines and it is the exact blockquote of a known claim not
    already consumed by an earlier verify line."""
    exact = {render_verify_blockquote(c): c["claim_id"] for c in claims}
    seen_ids = set()
    body = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(_VERIFY_PREFIX):
            cid = exact.get(stripped)
            if not allow_verify_lines or cid is None or cid in seen_ids:
                return stripped[:80]
            seen_ids.add(cid)
        else:
            body.append(line)
    rest = _norm(" ".join(body))
    keys = []
    for i, c in enumerate(claims):
        key = _norm(c["text"]).rstrip(" .!?")
        if key:
            keys.append((i, key))
    pos = 0
    while True:
        rest = _LEADING_JUNK_RE.sub("", rest)
        if not rest:
            return None
        hit = None
        for i, key in sorted((k for k in keys if k[0] >= pos), key=lambda k: -len(k[1])):
            if rest.startswith(key) and (len(rest) == len(key) or not rest[len(key)].isalnum()):
                hit = (i, key)
                break
        if hit:
            pos = hit[0] + 1
            rest = rest[len(hit[1]) :]
            continue
        m = _CONNECTIVE_RE.match(rest)
        if not m:
            return rest[:80]
        rest = rest[m.end() :]


def _claim_bullets(claims):
    return "\n".join(f"- {c['text']}" for c in claims)


def _draft_prompt(claims):
    return (
        "Write a short narrative describing this workflow using ONLY the "
        "following sentences, copied verbatim, in this order, each at most "
        "once. You may omit some, or prefix one with 'First,', 'Then,', "
        "'Next,' or 'Finally,'. Add nothing else:\n"
        f"{_claim_bullets(claims)}"
    )


def _critique_prompt(draft, claims):
    return (
        "Critique the following narrative for factual accuracy and clarity. "
        "The only permitted sentences are:\n"
        f"{_claim_bullets(claims)}\n\nList any issues briefly:\n\n{draft}"
    )


def _revise_prompt(draft, critique, claims):
    return (
        f"Revise this narrative based on the critique.\n\nNarrative:\n{draft}\n\n"
        f"Critique:\n{critique}\n\nThe only permitted sentences (verbatim, in "
        f"order, each at most once) are:\n{_claim_bullets(claims)}\n\n"
        "Return only the revised narrative."
    )


def generate_narrative(claims, llm_client, passes=1, placed_text=""):
    """Runs one initial draft pass, then `passes - 1` critique+revise
    rounds, then gates the final text through claim coverage (claims already
    placed verbatim in `placed_text` count as covered). Returns
    (final_text, covered_claim_ids, verify_claim_ids)."""
    if passes < 1:
        raise ValueError("passes must be >= 1")

    text = llm_client.chat([{"role": "user", "content": _draft_prompt(claims)}])
    for _ in range(passes - 1):
        critique = llm_client.chat([{"role": "user", "content": _critique_prompt(text, claims)}])
        text = llm_client.chat(
            [{"role": "user", "content": _revise_prompt(text, critique, claims)}]
        )

    reason = narrative_unsupported(text, claims)
    if reason:
        logging.getLogger(__name__).warning("narrative rejected, ungrounded text: %r", reason)
        text = ""

    return ensure_claim_coverage(text, claims, placed_text=placed_text)
