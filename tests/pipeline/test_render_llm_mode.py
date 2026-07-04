"""render_steps_llm_mode: the LLM-backed counterpart to task-12's
render_steps_template_mode — one generation attempt per step through
task-06's round-trip gate, falling back to the template per step on any
failure, never a retry loop."""

from pathlib import Path

from PIL import Image

from pipeline.manifest import load_manifest
from pipeline.render import render_steps_llm_mode
from pipeline.template import render_step_template

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


class _RecordingClient:
    def __init__(self, reply_for_index):
        self.reply_for_index = reply_for_index
        self.calls = []

    def chat(self, messages, **kwargs):
        idx = len(self.calls)
        self.calls.append(messages)
        return self.reply_for_index(idx)


def _make_screenshots(manifest, directory):
    directory.mkdir(parents=True, exist_ok=True)
    for step in manifest.steps:
        Image.new("RGB", (1920, 1080), (255, 255, 255)).save(directory / step.screenshot)


def test_llm_mode_uses_realistic_llm_replies_and_annotates_screenshots(tmp_path):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)

    steps = list(manifest.steps)

    def realistic_reply(idx):
        step = steps[idx]
        target = step.element.name or step.element.control_type or "the field"
        window = step.window.title or "the window"
        verb = "Click" if step.action == "click" else "Enter a value into"
        return f"{verb} {target} in {window}."

    client = _RecordingClient(realistic_reply)
    step_results, annotated_paths = render_steps_llm_mode(manifest, screenshots, annotated, client)

    assert len(client.calls) == len(manifest.steps)  # one attempt per step
    assert len(step_results) == len(manifest.steps)
    assert len(annotated_paths) == len(manifest.steps)
    for result in step_results:
        assert "used_fallback" in result
    for path in annotated_paths:
        assert path.exists()


def test_llm_mode_falls_back_to_template_per_step_on_bad_reply(tmp_path):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)

    client = _RecordingClient(lambda idx: "irrelevant reply triggering fallback")
    step_results, _annotated_paths = render_steps_llm_mode(manifest, screenshots, annotated, client)

    assert len(client.calls) == len(manifest.steps)  # never retried
    assert all(r["used_fallback"] for r in step_results)
    for step, result in zip(manifest.steps, step_results):
        assert result["text"] == render_step_template(step)


def test_llm_mode_step_ids_match_manifest_order(tmp_path):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)

    client = _RecordingClient(lambda idx: "irrelevant reply triggering fallback")
    step_results, _annotated_paths = render_steps_llm_mode(manifest, screenshots, annotated, client)

    assert [r["step_id"] for r in step_results] == [s.id for s in manifest.steps]
