"""Self-contained single-file HTML export (AC1 part 2): every image
inlined as a base64 data URI, CSS inline, zero external references — the
resulting markup must never trigger a network request when opened."""

import re
from pathlib import Path

from PIL import Image

from pipeline.claim_coverage import ensure_claim_coverage
from pipeline.export_html import render_single_file_html
from pipeline.manifest import load_manifest
from pipeline.render import render_steps_template_mode

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"

_URL_RE = re.compile(r"(?:https?:)?//", re.IGNORECASE)
_SRC_HREF_RE = re.compile(r'(?:src|href)="([^"]*)"')
_DATA_URI_RE = re.compile(r'"data:[^"]*"')


def _strip_data_uris(markup):
    """Base64 payloads can coincidentally contain '//' (the alphabet
    includes '/'), which would false-positive a naive URL scan — strip
    data: URIs before checking for real external references."""
    return _DATA_URI_RE.sub('"data:stripped"', markup)


def _make_screenshots(manifest, directory):
    directory.mkdir(parents=True, exist_ok=True)
    for step in manifest.steps:
        Image.new("RGB", (1920, 1080), (255, 255, 255)).save(directory / step.screenshot)


def _build(tmp_path, narrative_text=None):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)
    doc = render_single_file_html(
        manifest, step_results, annotated_paths, narrative_text=narrative_text
    )
    return manifest, step_results, doc


def test_no_http_or_protocol_relative_references(tmp_path):
    _manifest, _results, doc = _build(tmp_path)
    stripped = _strip_data_uris(doc)
    assert not _URL_RE.search(stripped), "found an http(s):// or protocol-relative reference"


def test_every_src_and_href_is_data_uri_or_fragment_anchor(tmp_path):
    _manifest, _results, doc = _build(tmp_path)
    refs = _SRC_HREF_RE.findall(doc)
    assert refs, "expected at least one src/href attribute (the embedded images)"
    for ref in refs:
        assert ref.startswith("data:") or ref.startswith("#"), ref


def test_no_script_or_link_tags(tmp_path):
    _manifest, _results, doc = _build(tmp_path)
    assert "<script" not in doc.lower()
    assert "<link" not in doc.lower()


def test_css_is_inline_in_a_style_block(tmp_path):
    _manifest, _results, doc = _build(tmp_path)
    assert "<style>" in doc
    assert "font-family" in doc


def test_images_actually_inlined_and_decodable(tmp_path):
    import base64

    manifest, _results, doc = _build(tmp_path)
    data_uris = re.findall(r'src="(data:[^"]+)"', doc)
    assert len(data_uris) == len(manifest.steps)
    for uri in data_uris:
        _header, b64_data = uri.split(",", 1)
        raw = base64.b64decode(b64_data)
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"  # real PNG magic bytes, not garbage


def test_contains_every_step_text_and_verify_blockquote(tmp_path):
    claims = [
        {"claim_id": "claim-001", "text": "Something not in the narrative at all.", "ts": 0.0}
    ]
    final_text, _covered, _verify_ids = ensure_claim_coverage("Unrelated narrative.", claims)

    import html as html_module

    manifest, step_results, doc = _build(tmp_path, narrative_text=final_text)
    for result in step_results:
        assert html_module.escape(result["text"]) in doc
    assert (
        "<blockquote>[verify] (claim-001): Something not in the narrative at all.</blockquote>"
        in doc
    )
