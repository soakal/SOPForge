"""Intermediate document renderers (md + html), assembled by plain code
from per-step outputs, annotated screenshots, and [verify] blockquotes.
`render_steps_template_mode` renders every step via task-02's template
fallback (invariant L3) through task-04's assembler — it doesn't even
accept an LLM client parameter, so there is no code path through which it
could make a model call. This is the forerunner of AC3: a complete,
correct doc requires zero LLM requests.

`render_steps_llm_mode` is the LLM-backed counterpart: one generation
attempt per step through task-06's round-trip gate, falling back to the
exact same template per step on any failure (invariant L2/L3) — never a
retry loop. Both share the same screenshot-annotation logic below; only
how each step's text gets generated differs."""

from pathlib import Path

import html

from pipeline.annotate import annotate_click, crop_to_element
from pipeline.assembler import assemble_steps, step_heading
from pipeline.generation import generate_all_steps
from pipeline.template import render_step_template


def _escape_md_alt_text(text):
    """Escapes markdown-significant characters for use inside an image
    alt-text slot `![...]`. A step heading can embed a raw UIA element name
    (step_heading); an unmatched `[` or `]` in that name would prematurely
    close the alt-text bracket and break the `![...](...)`  image reference
    (a balanced pair like "Save [Ctrl+S]" happens to still parse under
    CommonMark, but nothing here can assume every real element name is
    balanced). Backslash is escaped first so the bracket escapes that follow
    can't be misread as forming a different escape sequence."""
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _annotate_all(manifest, screenshot_dir, annotated_dir):
    """Writes an annotated copy of each step's screenshot -- marker drawn
    first, then cropped to the clicked element's neighborhood (a no-op-sized
    full-frame "crop" when the step has no bounding_rect, e.g. an
    empty-UIA-metadata step). Returns annotated_paths in manifest order."""
    annotated_dir.mkdir(parents=True, exist_ok=True)
    annotated_paths = []
    for step in manifest.steps:
        src = screenshot_dir / step.screenshot
        out = annotated_dir / step.screenshot
        annotate_click(src, step.screen.x, step.screen.y, out_path=out)
        crop_to_element(out, step.element.bounding_rect, (step.screen.x, step.screen.y))
        annotated_paths.append(out)
    return annotated_paths


def render_steps_template_mode(manifest, screenshot_dir, annotated_dir):
    """Renders every manifest step via the template fallback and writes an
    annotated copy of each step's screenshot. Returns (step_results,
    annotated_paths), both in manifest order — no LLM call anywhere."""
    step_results = assemble_steps(manifest, render_step_template)
    annotated_paths = _annotate_all(manifest, screenshot_dir, annotated_dir)
    return step_results, annotated_paths


def render_steps_llm_mode(
    manifest, screenshot_dir, annotated_dir, llm_client, on_progress=None, max_concurrency=1
):
    """Renders every manifest step via the LLM with a round-trip gate and
    per-step template fallback (task-06's generate_all_steps — one
    generation attempt per step, never retried), and writes an annotated
    copy of each step's screenshot. Returns (step_results, annotated_paths);
    each step_result also carries "used_fallback" (bool), unlike
    render_steps_template_mode's plain {"step_id", "text"} shape.
    `on_progress`, if given, is passed straight through to generate_all_steps.
    `max_concurrency` is passed straight through too — see generate_all_steps'
    own docstring for what it does and why it defaults to 1."""
    step_results = generate_all_steps(
        manifest, llm_client, on_progress=on_progress, max_concurrency=max_concurrency
    )
    annotated_paths = _annotate_all(manifest, screenshot_dir, annotated_dir)
    return step_results, annotated_paths


def _image_ref(shot, base_dir):
    """A doc-relative path for embedding an image, so the rendered doc
    doesn't hardcode an absolute filesystem path (which won't resolve once
    task-13's server serves the doc from a different root, and can break
    Markdown's `![]()` syntax outright if it contains a space or `)`)."""
    if base_dir is None:
        return str(shot)
    return Path(shot).relative_to(base_dir).as_posix()


def render_markdown(manifest, step_results, annotated_paths, narrative_text=None, base_dir=None):
    """Assembles a Markdown document: title, optional narrative section,
    then one section per step with its text and annotated screenshot."""
    lines = [f"# {manifest.session.title or manifest.session.id}", ""]
    if narrative_text:
        lines.append(narrative_text)
        lines.append("")
    for n, (step, result, shot) in enumerate(
        zip(manifest.steps, step_results, annotated_paths, strict=True), start=1
    ):
        heading = step_heading(n, step)
        lines.append(f"## {heading}")
        lines.append("")
        lines.append(result["text"])
        lines.append("")
        if result.get("narration"):
            lines.append(f"> **Narration:** {result['narration']}")
            lines.append("")
        lines.append(f"![{_escape_md_alt_text(heading)}]({_image_ref(shot, base_dir)})")
        lines.append("")
    return "\n".join(lines)


def narrative_html_blocks(narrative_text):
    """Splits narrative text into HTML blocks, rendering '> '-prefixed
    lines (task-08's [verify] blockquotes) as <blockquote> elements instead
    of flattening them into a single escaped paragraph — otherwise the
    blockquote marker and its newlines collapse into unreadable inline text."""
    blocks = []
    paragraph = []

    def flush_paragraph():
        if paragraph:
            blocks.append(f"<p>{html.escape(' '.join(paragraph))}</p>")
            paragraph.clear()

    for line in narrative_text.splitlines():
        if line.startswith("> "):
            flush_paragraph()
            blocks.append(f"<blockquote>{html.escape(line[2:])}</blockquote>")
        elif line.strip():
            paragraph.append(line)
        else:
            flush_paragraph()
    flush_paragraph()
    return blocks


def render_html(manifest, step_results, annotated_paths, narrative_text=None, base_dir=None):
    """Assembles a minimal HTML document mirroring render_markdown's
    structure — plain string building, no templating engine dependency."""
    title = manifest.session.title or manifest.session.id
    parts = [
        "<!doctype html>",
        f'<html><head><meta charset="utf-8"><title>{html.escape(title)}</title></head><body>',
        f"<h1>{html.escape(title)}</h1>",
    ]
    if narrative_text:
        parts.extend(narrative_html_blocks(narrative_text))
    for n, (step, result, shot) in enumerate(
        zip(manifest.steps, step_results, annotated_paths, strict=True), start=1
    ):
        heading = step_heading(n, step)
        parts.append(f"<h2>{html.escape(heading)}</h2>")
        parts.append(f"<p>{html.escape(result['text'])}</p>")
        if result.get("narration"):
            parts.append(
                '<blockquote class="narration"><strong>Narration:</strong> '
                f"{html.escape(result['narration'])}</blockquote>"
            )
        img_ref = _image_ref(shot, base_dir)
        parts.append(f'<img src="{html.escape(img_ref)}" alt="{html.escape(heading)}">')
    parts.append("</body></html>")
    return "\n".join(parts)
