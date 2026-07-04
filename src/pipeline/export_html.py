"""Self-contained single-file HTML export (AC1 part 2): every image
inlined as a base64 data URI, CSS inline in a <style> block — the
resulting file has zero external references, so opening it never makes a
network request (CLAUDE.md's "nothing leaves your network" posture applies
to the exported artifact itself, not just the generation pipeline)."""

import base64
import html
import mimetypes
from pathlib import Path

from pipeline.render import narrative_html_blocks

_STYLE = (
    "body{font-family:sans-serif;max-width:800px;margin:2em auto;padding:0 1em;}"
    "img{max-width:100%;}"
    "blockquote{color:#a00;border-left:3px solid #a00;padding-left:1em;margin-left:0;}"
)


def _data_uri(image_path):
    path = Path(image_path)
    mime, _ = mimetypes.guess_type(str(path))
    mime = mime or "application/octet-stream"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def render_single_file_html(manifest, step_results, annotated_paths, narrative_text=None):
    """Same structure as render.render_html, but every image is inlined as
    a base64 data URI instead of referenced by path. Returns one complete
    HTML document string with no external references at all."""
    title = manifest.session.title or manifest.session.id
    parts = [
        "<!doctype html>",
        f'<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
        f"<style>{_STYLE}</style></head><body>",
        f"<h1>{html.escape(title)}</h1>",
    ]
    if narrative_text:
        parts.extend(narrative_html_blocks(narrative_text))
    for step, result, shot in zip(manifest.steps, step_results, annotated_paths, strict=True):
        parts.append(f"<h2>Step {html.escape(step.id)}</h2>")
        parts.append(f"<p>{html.escape(result['text'])}</p>")
        parts.append(f'<img src="{_data_uri(shot)}" alt="{html.escape(step.id)}">')
    parts.append("</body></html>")
    return "\n".join(parts)
