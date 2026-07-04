"""Markdown export bundle (AC1 part 3): <slug>.md + images/NNN.png,
Obsidian-compatible relative links — every image link must resolve
relative to the .md file, never absolute, never a URI."""

import re
import shutil
from pathlib import Path

from PIL import Image

from pipeline.claim_coverage import ensure_claim_coverage
from pipeline.export_md import _slugify, export_markdown_bundle
from pipeline.manifest import load_manifest
from pipeline.render import render_steps_template_mode

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"

_IMAGE_LINK_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


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
    output_dir = tmp_path / "bundle"
    md_path = export_markdown_bundle(
        manifest, step_results, annotated_paths, output_dir, narrative_text=narrative_text
    )
    return manifest, step_results, md_path


def test_every_image_link_resolves_relative_to_the_md_file_and_is_not_absolute(tmp_path):
    manifest, _results, md_path = _build(tmp_path)
    markdown = md_path.read_text(encoding="utf-8")
    links = _IMAGE_LINK_RE.findall(markdown)
    assert len(links) == len(manifest.steps)
    for link in links:
        assert not link.startswith("/")
        assert not re.match(r"^[a-zA-Z]:[\\/]", link)  # not an absolute Windows path
        assert "://" not in link
        target = (md_path.parent / link).resolve()
        assert target.exists(), f"{link} does not resolve to a real file"


def test_images_are_copied_into_images_subdirectory(tmp_path):
    manifest, _results, md_path = _build(tmp_path)
    images_dir = md_path.parent / "images"
    for step in manifest.steps:
        assert (images_dir / step.screenshot).exists()


def test_bundle_is_portable_when_moved(tmp_path):
    """The whole point of relative links: copying the bundle folder
    elsewhere must not break any image reference."""
    _manifest, _results, md_path = _build(tmp_path)

    moved_dir = tmp_path / "moved-elsewhere" / "deeper"
    shutil.copytree(md_path.parent, moved_dir)
    moved_md = moved_dir / md_path.name

    markdown = moved_md.read_text(encoding="utf-8")
    links = _IMAGE_LINK_RE.findall(markdown)
    assert links
    for link in links:
        assert (moved_md.parent / link).resolve().exists()


def test_slugify_produces_a_safe_filename():
    assert _slugify("Answer File Editor Setup!") == "answer-file-editor-setup"
    assert _slugify("") == "sop"
    assert _slugify("C:\\weird/path?") == "c-weird-path"


def test_verify_blockquote_included_in_markdown(tmp_path):
    claims = [
        {"claim_id": "claim-001", "text": "Something not in the narrative at all.", "ts": 0.0}
    ]
    final_text, _covered, _verify_ids = ensure_claim_coverage("Unrelated narrative.", claims)

    _manifest, _results, md_path = _build(tmp_path, narrative_text=final_text)
    markdown = md_path.read_text(encoding="utf-8")
    assert "[verify] (claim-001)" in markdown
