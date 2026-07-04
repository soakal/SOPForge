"""Claim-coverage validator (invariant L4, back half): every claim id must
appear in the final narrative text — either the narrative already covers
it, or an explicit [verify] blockquote is appended for it. A negative test
(AC4) proves the validator actually fails when a claim is dropped
entirely."""

from pipeline.claim_coverage import (
    ensure_claim_coverage,
    render_verify_blockquote,
    validate_claim_coverage,
)

_CLAIMS = [
    {"claim_id": "claim-001", "text": "First, open the SmartDeploy console.", "ts": 0.0},
    {"claim_id": "claim-002", "text": "Then click Save to save your changes.", "ts": 2.5},
    {
        "claim_id": "claim-003",
        "text": "Make sure you enter the computer name correctly.",
        "ts": 5.0,
    },
]


def test_ensure_claim_coverage_covers_present_claims_and_flags_missing_ones():
    narrative = f"{_CLAIMS[0]['text']} {_CLAIMS[1]['text']}"  # claim-003 missing
    final_text, covered, verify_ids = ensure_claim_coverage(narrative, _CLAIMS)

    assert covered == ["claim-001", "claim-002"]
    assert verify_ids == ["claim-003"]
    assert "[verify] (claim-003)" in final_text
    assert _CLAIMS[2]["text"] in final_text  # claim's own text preserved in the blockquote


def test_ensure_claim_coverage_result_always_passes_validation():
    narrative = _CLAIMS[0]["text"]  # only claim-001 covered by content
    final_text, _covered, _verify_ids = ensure_claim_coverage(narrative, _CLAIMS)
    ok, missing = validate_claim_coverage(final_text, _CLAIMS)
    assert ok is True
    assert missing == []


def test_all_claims_covered_passes_without_any_blockquote():
    narrative = " ".join(c["text"] for c in _CLAIMS)
    final_text, covered, verify_ids = ensure_claim_coverage(narrative, _CLAIMS)
    assert covered == [c["claim_id"] for c in _CLAIMS]
    assert verify_ids == []
    assert "[verify]" not in final_text


def test_validator_fails_when_a_claim_is_dropped_entirely():
    """Negative test (AC4): a doc that neither covers nor [verify]-flags a
    claim must fail validation — this is the one thing the validator exists
    to catch."""
    broken_text = f"{_CLAIMS[0]['text']} {_CLAIMS[1]['text']}"  # claim-003 dropped, no blockquote
    ok, missing = validate_claim_coverage(broken_text, _CLAIMS)
    assert ok is False
    assert missing == ["claim-003"]


def test_validator_passes_when_all_claims_covered_by_content():
    ok, missing = validate_claim_coverage(" ".join(c["text"] for c in _CLAIMS), _CLAIMS)
    assert ok is True
    assert missing == []


def test_render_verify_blockquote_format():
    line = render_verify_blockquote(_CLAIMS[0])
    assert line.startswith("> [verify] (claim-001):")
    assert _CLAIMS[0]["text"] in line


def test_empty_claims_list_always_passes():
    ok, missing = validate_claim_coverage("any text at all", [])
    assert ok is True
    assert missing == []


def test_flagged_marker_alone_satisfies_validation_without_content_coverage():
    """The marker (`[verify] (claim-id)`) must be sufficient on its own —
    without this test, deleting the flagged-branch check entirely would
    still pass every other test in this file, since ensure_claim_coverage's
    own blockquotes always embed the claim's full text too (making content
    coverage true regardless of the flagged check)."""
    claim = _CLAIMS[2]
    # Marker present, but the claim's own text is deliberately NOT included —
    # so _claim_covered(claim, text) is False and only the flagged branch
    # can make this pass.
    text_with_marker_but_no_content = (
        f"Some narrative.\n\n> [verify] ({claim['claim_id']}): [redacted]\n"
    )
    ok, missing = validate_claim_coverage(text_with_marker_but_no_content, [claim])
    assert ok is True
    assert missing == []


def test_whitespace_only_claim_text_is_never_treated_as_covered():
    """An empty/whitespace claim text must not trivially "cover" itself via
    `"" in text` always being True — that would silently satisfy invariant
    L4 for a claim whose presence was never actually verifiable, and it
    would never get flagged either. It must always land in verify_ids."""
    blank_claim = {"claim_id": "claim-999", "text": "   ", "ts": 0.0}
    final_text, covered, verify_ids = ensure_claim_coverage("Some narrative text.", [blank_claim])
    assert covered == []
    assert verify_ids == ["claim-999"]

    ok, missing = validate_claim_coverage("Some narrative text with no marker.", [blank_claim])
    assert ok is False
    assert missing == ["claim-999"]
