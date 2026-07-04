"""Multi-pass narrative generation: draft -> critique -> revise, using a
mock LLM client; final output is always gated through claim coverage
(task-08) regardless of how many passes ran."""

import pytest

from pipeline.config import load_models_config
from pipeline.narrative import generate_narrative

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
