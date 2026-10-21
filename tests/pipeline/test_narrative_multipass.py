"""Multi-pass narrative generation: draft -> critique -> revise, using a
mock LLM client; final output is always gated through claim coverage
(task-08) regardless of how many passes ran."""

import pytest

from pipeline.config import load_models_config
from pipeline.claim_coverage import render_verify_blockquote
from pipeline.narrative import generate_narrative, narrative_unsupported, verify_lines

_CLAIMS = [
    {"claim_id": "claim-001", "text": "First, open the SmartDeploy console.", "ts": 0.0},
    {"claim_id": "claim-002", "text": "Then click Save to save your changes.", "ts": 2.5},
]

_COVERS_BOTH_CLAIMS = f"{_CLAIMS[0]['text']} {_CLAIMS[1]['text']}"


class _RecordingClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self.replies.pop(0)


def test_single_pass_makes_exactly_one_llm_call():
    client = _RecordingClient([_COVERS_BOTH_CLAIMS])
    final_text, covered, verify_ids = generate_narrative(_CLAIMS, client, passes=1)
    assert len(client.calls) == 1
    assert covered == ["claim-001", "claim-002"]
    assert verify_ids == []
    assert "[verify]" not in final_text


def test_three_passes_makes_five_llm_calls():
    client = _RecordingClient(
        [
            "draft v1",
            "critique v1",
            "draft v2, still missing facts",
            "critique v2",
            _COVERS_BOTH_CLAIMS,
        ]
    )
    final_text, covered, verify_ids = generate_narrative(_CLAIMS, client, passes=3)
    assert len(client.calls) == 5
    assert covered == ["claim-001", "claim-002"]


@pytest.mark.parametrize("passes,expected_calls", [(1, 1), (2, 3), (4, 7)])
def test_pass_count_is_configurable_and_determines_call_count(passes, expected_calls):
    replies = ["intermediate text"] * (expected_calls - 1) + [_COVERS_BOTH_CLAIMS]
    client = _RecordingClient(replies)
    generate_narrative(_CLAIMS, client, passes=passes)
    assert len(client.calls) == expected_calls


def test_uncovered_claims_still_get_verify_blockquote_regardless_of_passes():
    client = _RecordingClient(["Some narrative that mentions nothing relevant at all."])
    final_text, covered, verify_ids = generate_narrative(_CLAIMS, client, passes=1)
    assert covered == []
    assert verify_ids == ["claim-001", "claim-002"]
    assert "[verify] (claim-001)" in final_text
    assert "[verify] (claim-002)" in final_text


def test_invalid_pass_count_raises():
    client = _RecordingClient([])
    with pytest.raises(ValueError):
        generate_narrative(_CLAIMS, client, passes=0)
    assert client.calls == []  # never even attempted a call


def test_pass_count_from_committed_config_defaults_to_three():
    config = load_models_config()
    assert config.narrative.passes == 3


def _one(text, cid="claim-001"):
    return {"claim_id": cid, "text": text, "ts": 0.0}


def _gen(claims, reply):
    return generate_narrative(claims, _RecordingClient([reply]), passes=1)


def test_repeated_claim_is_rejected():
    claims = [_one("Transfer 100 dollars.")]
    reply = "Transfer 100 dollars. Then transfer 100 dollars."
    final, _, _ = _gen(claims, reply)
    assert "[verify] (claim-001)" in final
    assert "Then transfer" not in final
    assert narrative_unsupported(reply, claims)


def test_two_distinct_identical_claims_are_each_consumed_once():
    claims = [_one("Transfer 100 dollars."), _one("Transfer 100 dollars.", "claim-002")]
    reply = "Transfer 100 dollars. Then transfer 100 dollars."
    assert _gen(claims, reply)[2] == []
    assert narrative_unsupported(reply + " Then transfer 100 dollars.", claims)


def test_source_destination_reversal_is_rejected():
    claims = [_one("Copy files from backup to production.")]
    final, covered, _ = _gen(claims, "Copy files from production to backup.")
    assert "[verify] (claim-001)" in final
    assert "from production to backup" not in final
    assert covered == []


def test_invented_instruction_after_a_verbatim_claim_is_rejected():
    reply = "Open settings. Disable all security controls."
    final, _, verify_ids = _gen([_one("Open settings.")], reply)
    assert "Disable all security controls" not in final
    assert verify_ids == ["claim-001"]


def test_conditional_to_unconditional_is_rejected():
    assert _gen([_one("If prompted, click Yes.")], "Click Yes.")[2] == ["claim-001"]


def test_polarity_flip_is_rejected():
    claims = [_one("Do not disable security controls.")]
    assert narrative_unsupported("Disable security controls.", claims)


def test_reordered_claims_are_rejected_but_connectives_and_omissions_are_accepted():
    a, b = _one("Open settings."), _one("Click Save.", "claim-002")
    assert _gen([a, b], "Click Save. Open settings.")[2] == ["claim-001", "claim-002"]
    reply = "First, Open settings. Then, Click Save."
    final, _, verify_ids = _gen([a, b], reply)
    assert verify_ids == [] and final == reply
    assert _gen([a, b], "Open settings.")[2] == ["claim-002"]


def test_model_minted_verify_line_with_unknown_id_is_rejected():
    reply = "Open settings.\n> [verify] (claim-999): Disable all security controls."
    final, _, verify_ids = _gen([_one("Open settings.")], reply)
    assert "Disable all security controls" not in final
    assert "claim-999" not in final
    assert verify_ids == ["claim-001"]
    lines = [ln for ln in final.splitlines() if "[verify] (claim-001)" in ln]
    assert len(lines) == 1 and lines[0].endswith(": Open settings.")


def test_model_minted_verify_line_with_valid_id_is_rejected():
    reply = "Open settings.\n> [verify] (claim-001): Disable all security controls."
    final, _, verify_ids = _gen([_one("Open settings.")], reply)
    assert "Disable all security controls" not in final
    assert verify_ids == ["claim-001"]


def test_narrative_unsupported_verify_line_rules():
    a = _one("Open settings.")
    v = render_verify_blockquote(a)
    assert narrative_unsupported("Open settings.\n" + v, [a], allow_verify_lines=True) is None
    assert narrative_unsupported("Open settings.\n" + v, [a]) == v[:80]
    altered = v.replace("settings.", "settings!")
    assert narrative_unsupported("Open settings.\n" + altered, [a], allow_verify_lines=True)
    reid = v.replace("claim-001", "claim-009")
    assert narrative_unsupported("Open settings.\n" + reid, [a], allow_verify_lines=True)


def test_duplicate_exact_known_verify_line_is_rejected():
    a, b = _one("Open settings."), _one("Click Save.", "claim-002")
    vb = render_verify_blockquote(b)
    text = "Open settings.\n" + vb + "\n" + vb
    dup = narrative_unsupported(text, [a, b], allow_verify_lines=True)
    assert dup and dup.startswith("> [verify]")
    assert narrative_unsupported("Open settings.\n" + vb, [a, b], allow_verify_lines=True) is None
    assert verify_lines(text) == [vb, vb]


def test_critique_and_revise_prompts_include_the_claims():
    client = _RecordingClient([_COVERS_BOTH_CLAIMS] * 3)
    generate_narrative(_CLAIMS, client, passes=2)
    for call in client.calls:
        for c in _CLAIMS:
            assert c["text"] in call[0]["content"]
