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
