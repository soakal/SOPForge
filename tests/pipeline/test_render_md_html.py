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


def test_template_mode_end_to_end_makes_zero_llm_requests(tmp_path, monkeypatch):
    """Patches httpx.Client.send itself — the transport-level chokepoint
    every LLMClient request must go through, regardless of how or where one
    gets constructed — so this test would catch a future regression where
    render_steps_template_mode (or anything it calls) starts making a real
    HTTP request, not just a client object nobody happens to invoke."""
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)

    calls = {"count": 0}
    original_send = httpx.Client.send

    def counting_send(self, *args, **kwargs):
        calls["count"] += 1
        return original_send(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", counting_send)

    step_results, annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)
    render_markdown(manifest, step_results, annotated_paths, base_dir=annotated)
    render_html(manifest, step_results, annotated_paths, base_dir=annotated)

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

    for result in step_results:
        assert result["text"] in md
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
    for result in step_results:
        # Template text has apostrophe-quoted names (e.g. 'Save'), which
        # html.escape() correctly turns into &#x27; — check the escaped form.
        assert html.escape(result["text"]) in doc
    for path in annotated_paths:
        assert str(path) in doc


def test_image_refs_are_relative_when_base_dir_given_even_with_a_space_in_path(tmp_path):
    """Without base_dir, an absolute path is embedded verbatim — broken for
    Markdown's ![]() syntax if it contains a space or ), and unresolvable
    once task-13's server serves the doc from a different root."""
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "with space" / "screenshots"
    annotated = tmp_path / "with space" / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

    md = render_markdown(manifest, step_results, annotated_paths, base_dir=annotated)
    doc = render_html(manifest, step_results, annotated_paths, base_dir=annotated)

    for step in manifest.steps:
        assert f"]({step.screenshot})" in md  # relative, no absolute/spaced path
        assert str(annotated) not in md
        assert f'src="{step.screenshot}"' in doc
        assert str(annotated) not in doc


def test_render_markdown_includes_verify_blockquotes():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    claims = [
        {"claim_id": "claim-001", "text": "Something not in the narrative at all.", "ts": 0.0}
    ]
    final_text, _covered, _verify_ids = ensure_claim_coverage("Unrelated narrative text.", claims)

    step_results = [{"step_id": s.id, "text": ""} for s in manifest.steps]
    annotated = [None] * len(manifest.steps)
    md = render_markdown(manifest, step_results, annotated, narrative_text=final_text)
    assert "[verify] (claim-001)" in md


def test_render_html_includes_verify_blockquotes_as_blockquote_elements():
    """The HTML renderer must not flatten [verify] lines into one escaped
    paragraph (which would collapse the newline and render the '>' marker
    as inline text) — it must emit a distinct <blockquote> per marker,
    still containing the literal [verify] (claim-id) text so
    validate_claim_coverage keeps matching."""
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    claims = [
        {"claim_id": "claim-001", "text": "Something not in the narrative at all.", "ts": 0.0}
    ]
    final_text, _covered, _verify_ids = ensure_claim_coverage("Unrelated narrative text.", claims)

    step_results = [{"step_id": s.id, "text": ""} for s in manifest.steps]
    annotated = [None] * len(manifest.steps)
    doc = render_html(manifest, step_results, annotated, narrative_text=final_text)
    assert (
        "<blockquote>[verify] (claim-001): Something not in the narrative at all.</blockquote>"
        in doc
    )
    assert "<p>Unrelated narrative text.</p>" in doc


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
