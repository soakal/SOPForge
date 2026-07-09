"""semantic_align: LLM-picked boundaries + verbatim slicing (stage 1 of the
narration pipeline for an unstructured transcript). The model only ever
chooses split points; this module's own code does the slicing, so the
result is always made of the transcript's own words."""

import json
from pathlib import Path

from pipeline.manifest import load_manifest
from pipeline.semantic_align import build_step_contexts, semantic_align

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _manifest():
    # sample-manifest.json has step-001 (click Save), step-002 (type Computer
    # name), step-003 (click, empty UIA metadata).
    return load_manifest(FIXTURES / "sample-manifest.json")


class _StubClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self.reply


TRANSCRIPT = (
    "first we click save on the deploy console then we type in the computer "
    "name then finally we click somewhere in chrome to finish up"
)


def test_places_narration_across_multiple_steps_from_valid_boundaries():
    manifest = _manifest()
    contexts = build_step_contexts(manifest)
    reply = json.dumps(
        [
            {"step": 1, "starts_with": "first we click save"},
            {"step": 2, "starts_with": "then we type in the computer name"},
            {"step": 3, "starts_with": "then finally we click somewhere"},
        ]
    )
    client = _StubClient(reply)
    result = semantic_align(TRANSCRIPT, manifest, contexts, client)
    assert result is not None
    per_step, meta = result
    assert per_step["step-001"] == "first we click save on the deploy console"
    assert per_step["step-002"] == "then we type in the computer name"
    assert per_step["step-003"] == "then finally we click somewhere in chrome to finish up"
    # Concatenation of segments in order reconstructs the whole transcript --
    # coverage is structural, not something checked after the fact.
    assert (
        per_step["step-001"] + " " + per_step["step-002"] + " " + per_step["step-003"] == TRANSCRIPT
    )
    assert meta["mode"] == "semantic-llm"
    assert meta["boundaries_resolved"] == 3


def test_returns_none_on_malformed_json():
    manifest = _manifest()
    contexts = build_step_contexts(manifest)
    client = _StubClient("not json at all, sorry")
    assert semantic_align(TRANSCRIPT, manifest, contexts, client) is None


def test_returns_none_on_non_increasing_steps():
    manifest = _manifest()
    contexts = build_step_contexts(manifest)
    reply = json.dumps(
        [
            {"step": 2, "starts_with": "then we type"},
            {"step": 1, "starts_with": "first we click save"},  # out of order
        ]
    )
    client = _StubClient(reply)
    assert semantic_align(TRANSCRIPT, manifest, contexts, client) is None


def test_returns_none_on_out_of_range_step_number():
    manifest = _manifest()
    contexts = build_step_contexts(manifest)
    reply = json.dumps(
        [
            {"step": 1, "starts_with": "first we click save"},
            {"step": 99, "starts_with": "then we type"},
        ]
    )
    client = _StubClient(reply)
    assert semantic_align(TRANSCRIPT, manifest, contexts, client) is None


def test_drops_a_single_unresolvable_phrase_but_still_succeeds():
    manifest = _manifest()
    contexts = build_step_contexts(manifest)
    reply = json.dumps(
        [
            {"step": 1, "starts_with": "first we click save"},
            {"step": 2, "starts_with": "this exact phrase is not in the transcript"},
            {"step": 3, "starts_with": "then finally we click somewhere"},
        ]
    )
    client = _StubClient(reply)
    result = semantic_align(TRANSCRIPT, manifest, contexts, client)
    assert result is not None
    per_step, meta = result
    assert meta["boundaries_requested"] == 3
    assert meta["boundaries_resolved"] == 2
    assert "step-002" not in per_step  # its boundary never resolved


def test_returns_none_when_majority_of_phrases_unresolvable():
    manifest = _manifest()
    contexts = build_step_contexts(manifest)
    reply = json.dumps(
        [
            {"step": 1, "starts_with": "first we click save"},
            {"step": 2, "starts_with": "nonexistent phrase one"},
            {"step": 3, "starts_with": "nonexistent phrase two"},
        ]
    )
    client = _StubClient(reply)
    assert semantic_align(TRANSCRIPT, manifest, contexts, client) is None


def test_returns_none_without_calling_llm_when_fewer_than_two_steps():
    manifest = _manifest()
    contexts = build_step_contexts(manifest)[:1]
    client = _StubClient(json.dumps([{"step": 1, "starts_with": "anything"}]))
    assert semantic_align(TRANSCRIPT, manifest, contexts, client) is None
    assert client.calls == []


def test_build_step_contexts_shape_and_order():
    manifest = _manifest()
    contexts = build_step_contexts(manifest)
    assert [c["step_id"] for c in contexts] == ["step-001", "step-002", "step-003"]
    assert [c["index"] for c in contexts] == [1, 2, 3]
    assert contexts[0]["element_name"] == "Save"
    assert contexts[0]["window_title"] == "SmartDeploy Console"
    assert contexts[0]["action"] == "click"


def test_build_step_contexts_uses_step_results_text_when_given():
    manifest = _manifest()
    step_results = [
        {"step_id": "step-001", "text": "Click Save."},
        {"step_id": "step-002", "text": "Enter the computer name."},
        {"step_id": "step-003", "text": "Click in Chrome."},
    ]
    contexts = build_step_contexts(manifest, step_results)
    assert contexts[0]["step_text"] == "Click Save."
    assert contexts[1]["step_text"] == "Enter the computer name."
