"""Claim-coverage validator + [verify] blockquote rendering (invariant L4
back half, CLAUDE.md): "every claim ID must appear in output or be
rendered as a [verify]-flagged blockquote." Every atomic claim (task-07)
must be accounted for in the final narrative text — either the text
already covers the claim's content, or an explicit [verify] blockquote is
appended for it. A claim satisfying neither is exactly what
validate_claim_coverage exists to catch.

Coverage is a deterministic presence check (does the claim's raw text
appear in the narrative?), matching invariant L2's round-trip philosophy —
no model judgment involved in deciding what counts as "covered"."""


def _claim_covered(claim, text):
    stripped = claim["text"].strip()
    if not stripped:
        # An empty/whitespace claim (e.g. a whisper segment that stripped to
        # nothing) can never be "covered" by content — "" is a substring of
        # everything, which would otherwise silently satisfy the invariant
        # without the claim's presence being verifiable at all. Force it to
        # the [verify] path instead.
        return False
    return stripped.lower() in text.lower()


def _verify_marker(claim_id):
    """The one place that defines what a flagged claim looks like in
    rendered text — render_verify_blockquote and validate_claim_coverage
    both go through this, so they can't drift out of sync with each other."""
    return f"[verify] ({claim_id})"


def render_verify_blockquote(claim):
    return f"> {_verify_marker(claim['claim_id'])}: {claim['text']}"


def ensure_claim_coverage(narrative_text, claims):
    """Returns (final_text, covered_claim_ids, verify_claim_ids). Every claim
    ends up covered by the narrative's own content or flagged with an
    appended [verify] blockquote — never silently dropped."""
    covered = []
    uncovered = []
    for claim in claims:
        if _claim_covered(claim, narrative_text):
            covered.append(claim["claim_id"])
        else:
            uncovered.append(claim)

    final_text = narrative_text
    if uncovered:
        blockquotes = "\n".join(render_verify_blockquote(c) for c in uncovered)
        final_text = f"{narrative_text.rstrip()}\n\n{blockquotes}\n"

    verify_ids = [c["claim_id"] for c in uncovered]
    return final_text, covered, verify_ids


def validate_claim_coverage(final_text, claims):
    """Returns (ok, missing_claim_ids). ok is True iff every claim's id
    appears in final_text either via content coverage or a [verify]
    blockquote. This is the safety-net check for a doc that may have
    bypassed ensure_claim_coverage entirely and dropped a claim."""
    missing = []
    for claim in claims:
        covered = _claim_covered(claim, final_text)
        flagged = _verify_marker(claim["claim_id"]) in final_text
        if not covered and not flagged:
            missing.append(claim["claim_id"])
    return (len(missing) == 0, missing)
