"""Step generation orchestrator: per-record LLM call -> round-trip gate ->
template fallback (invariants L1/L2/L3), exactly one generation attempt per
step, never retried (CLAUDE.md: "never a retry loop"). AC2: >=95% round-trip
pass rate on fixtures/ manifests with realistic mock step text."""

from pathlib import Path

from pipeline.generation import generate_all_steps, generate_step_text
from pipeline.manifest import load_manifest
from pipeline.template import render_step_template

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


class _RecordingClient:
    """A mock LLMClient recording every call, replying via reply_for_index(i)
    where i is the 0-based call count — matches generate_all_steps' in-order
    per-step iteration, so reply_for_index(i) can look up manifest.steps[i]."""

    def __init__(self, reply_for_index):
        self.reply_for_index = reply_for_index
        self.calls = []

    def chat(self, messages, **kwargs):
        idx = len(self.calls)
        self.calls.append(messages)
        return self.reply_for_index(idx)


def _realistic_reply(step):
    target = step.element.name or step.element.control_type or "the field"
    window = step.window.title or "the window"
    verb = "Click" if step.action == "click" else "Enter a value into"
    return f"{verb} {target} in {window}."


def test_realistic_mock_achieves_at_least_95_percent_round_trip():
    total_ok = 0
    total = 0
    for fixture in ("sample-manifest.json", "empty-elements-manifest.json"):
        manifest = load_manifest(FIXTURES / fixture)
        steps = list(manifest.steps)
        client = _RecordingClient(lambda idx, steps=steps: _realistic_reply(steps[idx]))
        results = generate_all_steps(manifest, client)
        total += len(results)
        total_ok += sum(1 for r in results if not r["used_fallback"])
        assert len(client.calls) == len(manifest.steps)  # one attempt per step

    assert total_ok / total >= 0.95


def test_successful_generation_is_not_marked_as_fallback():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]
    client = _RecordingClient(lambda idx, s=step: _realistic_reply(s))
    text, used_fallback = generate_step_text(step, client)
    assert used_fallback is False
    assert text == _realistic_reply(step)
    assert len(client.calls) == 1


def test_injected_mismatch_falls_back_with_exactly_one_attempt_and_no_retry():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]  # click, Save button, SmartDeploy Console

    client = _RecordingClient(lambda idx: "Completely unrelated wrong sentence.")
    text, used_fallback = generate_step_text(step, client)

    assert used_fallback is True
    assert len(client.calls) == 1  # exactly one attempt, never retried
    assert text == render_step_template(step)


def test_llm_exception_falls_back_with_exactly_one_attempt():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]

    class RaisingClient:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, **kwargs):
            self.calls += 1
            raise RuntimeError("simulated LLM outage")

    client = RaisingClient()
    text, used_fallback = generate_step_text(step, client)
    assert used_fallback is True
    assert client.calls == 1
    assert text == render_step_template(step)


def test_malformed_response_falls_back_with_exactly_one_attempt():
    """Carried forward from task-05's review: a malformed LLM response
    (missing/empty `choices`, non-JSON body) must fall back cleanly rather
    than propagate an uncaught KeyError/IndexError/JSONDecodeError out of the
    orchestrator — generate_step_text's broad except covers whatever
    llm_client.chat() might raise, not just httpx.HTTPStatusError."""
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]

    class MalformedClient:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, **kwargs):
            self.calls += 1
            raise KeyError("choices")  # simulates llm_client.py's raw KeyError

    client = MalformedClient()
    text, used_fallback = generate_step_text(step, client)
    assert used_fallback is True
    assert client.calls == 1
    assert text == render_step_template(step)


def test_generate_all_steps_preserves_order_and_ids():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    client = _RecordingClient(lambda idx: "irrelevant text triggering fallback")
    results = generate_all_steps(manifest, client)
    assert [r["step_id"] for r in results] == [s.id for s in manifest.steps]


def test_generate_all_steps_reports_progress_after_each_step():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    client = _RecordingClient(lambda idx: "irrelevant text triggering fallback")
    calls = []
    generate_all_steps(manifest, client, on_progress=lambda i, n: calls.append((i, n)))
    total = len(manifest.steps)
    assert calls == [(i, total) for i in range(1, total + 1)]
