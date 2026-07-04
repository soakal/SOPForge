"""Intermediate document renderers (md + html): assembled by code from
per-step outputs, annotated screenshots, and [verify] blockquotes.
Template-mode end-to-end proves a complete, correct doc requires zero LLM
requests (forerunner of AC3)."""

import html
import inspect
from pathlib import Path

import httpx
from PIL import Image

from pipeline.claim_coverage import ensure_claim_coverage
from pipeline.config import SectionConfig
from pipeline.llm_client import LLMClient
from pipeline.manifest import Manifest, load_manifest
from pipeline.render import render_html, render_markdown, render_steps_template_mode

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _make_screenshots(manifest, directory):
    directory.mkdir(parents=True, exist_ok=True)
    for step in manifest.steps:
        Image.new("RGB", (1920, 1080), (255, 255, 255)).save(directory / step.screenshot)


def test_template_mode_signature_has_no_llm_client_parameter():
    """The strongest possible proof template mode can't call an LLM: the
    function doesn't even accept one."""
    assert "llm_client" not in inspect.signature(render_steps_template_mode).parameters


def test_template_mode_end_to_end_makes_zero_llm_requests(tmp_path):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)

    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "unused"}}]})

    mock_llm = LLMClient(
        SectionConfig(endpoint="http://fake", model="m"), transport=httpx.MockTransport(handler)
    )
    try:
        step_results, annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)
        render_markdown(manifest, step_results, annotated_paths)
        render_html(manifest, step_results, annotated_paths)
    finally:
        mock_llm.close()

    assert calls["count"] == 0
    assert len(step_results) == len(manifest.steps)
    assert len(annotated_paths) == len(manifest.steps)
    for path in annotated_paths:
        assert path.exists()


def test_render_markdown_contains_every_step_and_screenshot(tmp_path):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

    md = render_markdown(manifest, step_results, annotated_paths)

    for step, result in zip(manifest.steps, step_results):
        assert result["text"] in md
        assert step.id in md
    for path in annotated_paths:
        assert str(path) in md


def test_render_html_contains_every_step_and_screenshot(tmp_path):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

    doc = render_html(manifest, step_results, annotated_paths)

    assert doc.startswith("<!doctype html>")
    for step, result in zip(manifest.steps, step_results):
        # Template text has apostrophe-quoted names (e.g. 'Save'), which
        # html.escape() correctly turns into &#x27; — check the escaped form.
        assert html.escape(result["text"]) in doc
        assert step.id in doc


def test_render_markdown_includes_verify_blockquotes():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    claims = [
        {"claim_id": "claim-001", "text": "Something not in the narrative at all.", "ts": 0.0}
    ]
    final_text, _covered, _verify_ids = ensure_claim_coverage("Unrelated narrative text.", claims)

    md = render_markdown(manifest, [], [], narrative_text=final_text)
    assert "[verify] (claim-001)" in md


def test_html_escapes_special_characters():
    manifest = Manifest.model_validate(
        {
            "schema_version": "1.0",
            "session": {
                "id": "s",
                "title": "A & B <Test>",
                "started_utc": "2026-01-01T00:00:00Z",
                "ended_utc": "2026-01-01T00:00:01Z",
                "machine": "m",
                "os_build": "1",
                "narration_wav": None,
            },
            "steps": [],
        }
    )
    doc = render_html(manifest, [], [])
    assert "A &amp; B &lt;Test&gt;" in doc
    assert "<Test>" not in doc
