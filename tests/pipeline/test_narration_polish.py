"""narration_polish: grounded rewrite (stage 2 of the narration pipeline).
A rewrite is only trusted if a mechanical gate confirms it didn't invent or
drop a fact; a step whose rewrite fails the gate keeps its verbatim stage-1
text instead -- polish is a pure quality layer, never able to undo correct
placement."""

import json

from pipeline.narration_polish import _gate, polish_narration

_CONTEXTS = [
    {
        "step_id": "step-001",
        "element_name": "Save",
        "window_title": "SmartDeploy Console",
        "step_text": "Click the Save Button in the SmartDeploy Console window.",
    },
    {
        "step_id": "step-002",
        "element_name": "Computer name",
        "window_title": "Answer File Editor",
        "step_text": "Enter a value in the Computer name field.",
    },
]


class _StubClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self.reply


class _RaisingClient:
    def chat(self, messages, **kwargs):
        raise RuntimeError("simulated outage")


def test_gate_rejects_degenerate_cjk_rewrite_that_would_otherwise_pass():
    """Regression: a CJK-flood rewrite has no a-z0-9 tokens at all, so the
    existing no-invented-content check (Latin-only _WORD_RE) passes it
    vacuously, and if its length happens to land in-band the length-ratio
    check misses it too. The shared degenerate_reason check (added after a
    real vision-caption bug hit this exact failure mode) closes the gap."""
    original = "open the settings menu then confirm the change"
    rewrite = "自动生成" * 8  # plausible-length flood, no Latin tokens to invent
    ok, reason, _dropped = _gate(original, rewrite, "")
    assert not ok
    assert reason is not None


def test_gate_accepts_a_faithful_rewrite_using_only_source_words():
    original = "open file explorer then open the c drive then goto users then select vrsi"
    rewrite = "Open File Explorer, open the C drive, then go to Users and select VRSI."
    ok, reason, dropped = _gate(original, rewrite, "")
    assert ok, reason
    assert dropped == []


def test_gate_rejects_invented_content():
    original = "click save to store the file"
    rewrite = "Click save to store the file, then restart the print spooler service."
    ok, reason, _dropped = _gate(original, rewrite, "")
    assert not ok
    assert reason == "introduced unsupported content"


def test_gate_rejects_dropped_or_altered_numeric_fact():
    original = "open the c drive and go to port 8420 for the server"
    rewrite = "Open the C drive and go to the server's port."
    ok, reason, _dropped = _gate(original, rewrite, "")
    assert not ok
    assert reason == "dropped or altered a literal fact"


def test_gate_rejects_wildly_short_rewrite():
    original = (
        "open file explorer then open the c drive then goto users then select "
        "vrsi then goto sopforge then captures folder"
    )
    rewrite = "Opened Explorer."
    ok, reason, _dropped = _gate(original, rewrite, "")
    assert not ok
    assert reason == "length ratio out of bounds"


def test_gate_flags_a_dropped_clause_without_rejecting():
    original = "open file explorer then go to the c drive then restart the printer spooler service"
    rewrite = "Open File Explorer, then go to the C drive."
    ok, reason, dropped = _gate(original, rewrite, "")
    assert ok, reason
    assert any("restart" in clause for clause in dropped)


def test_polish_narration_accepts_and_flags_end_to_end():
    per_step = {
        "step-001": "open file explorer then go to the c drive then restart the printer spooler service",
        "step-002": "type the computer name field vrsi one two three",
    }
    reply = json.dumps(
        [
            {"step_id": "step-001", "text": "Open File Explorer, then go to the C drive."},
            {
                "step_id": "step-002",
                "text": "Type VRSI one two three into the computer name field.",
            },
        ]
    )
    client = _StubClient(reply)
    final, meta = polish_narration(per_step, None, _CONTEXTS, client)

    assert meta["attempted"] is True
    assert "step-001" in meta["steps_polished"]
    assert "step-002" in meta["steps_polished"]
    assert "[verify]" in final["step-001"]  # the dropped "restart" clause is flagged
    assert meta["verify_claims"]


def test_polish_keeps_verbatim_when_rewrite_invents_content():
    per_step = {"step-001": "click save to store the file"}
    reply = json.dumps(
        [{"step_id": "step-001", "text": "Click save, then restart the print spooler."}]
    )
    client = _StubClient(reply)
    final, meta = polish_narration(per_step, None, _CONTEXTS, client)
    assert final["step-001"] == per_step["step-001"]
    assert meta["steps_kept_verbatim"]["step-001"] == "introduced unsupported content"
    assert "step-001" not in meta["steps_polished"]


def test_polish_keeps_verbatim_for_a_step_the_model_omitted():
    per_step = {"step-001": "click save", "step-002": "type the name"}
    reply = json.dumps([{"step_id": "step-001", "text": "Click Save."}])
    client = _StubClient(reply)
    final, meta = polish_narration(per_step, None, _CONTEXTS, client)
    assert final["step-002"] == "type the name"
    assert meta["steps_kept_verbatim"]["step-002"] == "not returned by model"


def test_polish_falls_back_to_verbatim_on_llm_failure():
    per_step = {"step-001": "click save", "step-002": "type the name"}
    final, meta = polish_narration(per_step, None, _CONTEXTS, _RaisingClient())
    assert final == per_step
    assert meta["attempted"] is True
    assert meta["steps_kept_verbatim"] == {
        "step-001": "polish call failed",
        "step-002": "polish call failed",
    }


def test_polish_falls_back_on_malformed_json():
    per_step = {"step-001": "click save"}
    client = _StubClient("not json")
    final, meta = polish_narration(per_step, None, _CONTEXTS, client)
    assert final == per_step
    assert meta["steps_kept_verbatim"]["step-001"] == "polish call failed"


def test_polish_is_a_noop_on_empty_per_step():
    final, meta = polish_narration({}, None, _CONTEXTS, _RaisingClient())
    assert final == {}
    assert meta["attempted"] is False
