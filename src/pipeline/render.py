"""Intermediate document renderers (md + html), assembled by plain code
from per-step outputs, annotated screenshots, and [verify] blockquotes.
`render_steps_template_mode` renders every step via task-02's template
fallback (invariant L3) through task-04's assembler — it doesn't even
accept an LLM client parameter, so there is no code path through which it
could make a model call. This is the forerunner of AC3: a complete,
correct doc requires zero LLM requests."""

import html

from pipeline.annotate import annotate_click
from pipeline.assembler import assemble_steps
from pipeline.template import render_step_template


def render_steps_template_mode(manifest, screenshot_dir, annotated_dir):
    """Renders every manifest step via the template fallback and writes an
    annotated copy of each step's screenshot. Returns (step_results,
    annotated_paths), both in manifest order — no LLM call anywhere."""
    step_results = assemble_steps(manifest, render_step_template)

    annotated_dir.mkdir(parents=True, exist_ok=True)
    annotated_paths = []
    for step in manifest.steps:
        src = screenshot_dir / step.screenshot
        out = annotated_dir / step.screenshot
        annotate_click(src, step.screen.x, step.screen.y, out_path=out)
        annotated_paths.append(out)

    return step_results, annotated_paths


def render_markdown(manifest, step_results, annotated_paths, narrative_text=None):
    """Assembles a Markdown document: title, optional narrative section,
    then one section per step with its text and annotated screenshot."""
    lines = [f"# {manifest.session.title or manifest.session.id}", ""]
    if narrative_text:
        lines.append(narrative_text)
        lines.append("")
    for step, result, shot in zip(manifest.steps, step_results, annotated_paths):
        lines.append(f"## Step {step.id}")
        lines.append("")
        lines.append(result["text"])
        lines.append("")
        lines.append(f"![{step.id}]({shot})")
        lines.append("")
    return "\n".join(lines)


def render_html(manifest, step_results, annotated_paths, narrative_text=None):
    """Assembles a minimal HTML document mirroring render_markdown's
    structure — plain string building, no templating engine dependency."""
    title = manifest.session.title or manifest.session.id
    parts = [
        "<!doctype html>",
        f'<html><head><meta charset="utf-8"><title>{html.escape(title)}</title></head><body>',
        f"<h1>{html.escape(title)}</h1>",
    ]
    if narrative_text:
        parts.append(f"<p>{html.escape(narrative_text)}</p>")
    for step, result, shot in zip(manifest.steps, step_results, annotated_paths):
        parts.append(f"<h2>Step {html.escape(step.id)}</h2>")
        parts.append(f"<p>{html.escape(result['text'])}</p>")
        parts.append(f'<img src="{html.escape(str(shot))}" alt="{html.escape(step.id)}">')
    parts.append("</body></html>")
    return "\n".join(parts)
