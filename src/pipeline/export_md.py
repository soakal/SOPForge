"""Markdown export with a relative-image-link bundle (AC1 part 3): writes
<slug>.md plus an images/ subdirectory next to it, Obsidian-compatible —
every image link is a plain relative path, resolvable by copying the whole
bundle folder anywhere (a vault, a zip, a USB drive)."""

import re
import shutil
from pathlib import Path

from pipeline.render import render_markdown


def _slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "sop"


def export_markdown_bundle(
    manifest, step_results, annotated_paths, output_dir, narrative_text=None
):
    """Writes output_dir/<slug>.md and output_dir/images/<screenshot
    filename> for every step, with the markdown referencing each image as
    a plain relative "images/<filename>" link. Returns the .md file's path."""
    output_dir = Path(output_dir)
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    for step, shot in zip(manifest.steps, annotated_paths, strict=True):
        shutil.copyfile(shot, images_dir / step.screenshot)

    markdown = render_markdown(
        manifest, step_results, annotated_paths, narrative_text=narrative_text
    )
    for step, shot in zip(manifest.steps, annotated_paths, strict=True):
        markdown = markdown.replace(f"]({shot})", f"](images/{step.screenshot})")

    slug = _slugify(manifest.session.title or manifest.session.id)
    md_path = output_dir / f"{slug}.md"
    md_path.write_text(markdown, encoding="utf-8")
    return md_path
