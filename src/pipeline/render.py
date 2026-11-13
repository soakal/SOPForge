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

from pipeline import authoring as au
from pipeline.annotate import annotate_click, crop_to_element
from pipeline.assembler import assemble_steps, is_grouped, phase_starts, step_heading
from pipeline.claim_coverage import parse_verify_line
from pipeline.generation import generate_all_steps
from pipeline.review_notes import step_review_notes
from pipeline.template import render_step_template


def _escape_md_heading(text):
    """A heading (possibly a screenshot-derived vision title) stays one line
    and can't inject raw HTML or a link/image (escaped brackets) into a
    Markdown viewer. Other punctuation is left alone so doc.md still reads
    cleanly as plain text."""
    text = html.escape(" ".join(text.splitlines()), quote=False)
    return text.replace("[", r"\[").replace("]", r"\]")


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
    manifest,
    screenshot_dir,
    annotated_dir,
    llm_client,
    on_progress=None,
    max_concurrency=1,
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
        manifest,
        llm_client,
        on_progress=on_progress,
        max_concurrency=max_concurrency,
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


# ---- Phase 7 authored content (authoring.json) ------------------------------
# Every helper returns [] without authoring, so documents that have none are
# byte-identical to before. Authored text gets {{param}} substitution; LLM
# step text never does.


def _md_row(cells):
    return "| " + " | ".join(c.replace("|", r"\|") for c in cells) + " |"


def _md_items(item, values):
    """Bullets, commands, warning, table and the '— X only' flag of a step
    extra or block, as Markdown lines."""
    sub = lambda t: au.substitute(t, values)  # noqa: E731
    lines = []
    tag = au.tag_of(item)
    if tag:
        lines += [f"***— {sub(tag)}***", ""]
    if item["bullets"]:
        lines += [f"- {sub(b)}" for b in item["bullets"]] + [""]
    for cmd in item["commands"]:
        lines += ["```", sub(cmd), "```", ""]
    if item["warning"]:
        lines += [f"**Warning:** {sub(item['warning'])}", ""]
    if item["table"]:
        t = item["table"]
        lines += [_md_row([sub(h) for h in t["headers"]]), "| --- | --- |"]
        lines += [_md_row([sub(c) for c in r]) for r in t["rows"]] + [""]
    return lines


def md_step_extras(authoring, step_id):
    extra = au.step_extra(authoring, step_id) if authoring else None
    return _md_items(extra, au.param_values(authoring)) if extra else []


def md_blocks(authoring, after, step_level):
    """Blocks placed after step `after` ("" = before the first step)."""
    if not authoring:
        return []
    values = au.param_values(authoring)
    sub = lambda t: au.substitute(t, values)  # noqa: E731
    lines = []
    for b in au.blocks_after(authoring, after):
        if b["section"]:
            lines += [f"## {_escape_md_heading(sub(b['section']))}", ""]
        if b["heading"]:
            lines += [f"{step_level} {_escape_md_heading(sub(b['heading']))}", ""]
        for para in sub(b["text"]).split("\n\n"):
            if para.strip():
                lines += [para.strip(), ""]
        lines += _md_items(b, values)
        if b["placeholder"]:
            lines += [f"*[IMAGE PLACEHOLDER: {sub(b['placeholder'])}]*", ""]
    return lines


def md_prerequisites(authoring):
    if not authoring or not authoring["prerequisites"]:
        return []
    values = au.param_values(authoring)
    items = [f"- {au.substitute(p, values)}" for p in authoring["prerequisites"]]
    return ["## Before You Begin", "", *items, ""]


def md_revisions(authoring, revision, date, author):
    if not authoring or not authoring["revisions"] or None in (revision, date, author):
        return []
    rows = au.revision_rows(authoring, date, revision, author)
    return [
        "## Revision History",
        "",
        "| Date | Revision | Comment | Author |",
        "| --- | --- | --- | --- |",
        *(_md_row(r) for r in rows),
        "",
    ]


def _inline_html(text):
    out = []
    for piece, style in au.inline_parts(text):
        piece = html.escape(piece)
        if style == "bold":
            piece = f"<strong>{piece}</strong>"
        elif style == "code":
            piece = f"<code>{piece}</code>"
        out.append(piece)
    return "".join(out)


def _html_table(headers, rows):
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>" for r in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _html_items(item, values):
    """HTML counterpart of _md_items; every authored string is escaped."""
    sub = lambda t: au.substitute(t, values)  # noqa: E731
    parts = []
    tag = au.tag_of(item)
    if tag:
        parts.append(f'<p><em class="only">— {html.escape(sub(tag))}</em></p>')
    if item["bullets"]:
        lis = "".join(f"<li>{_inline_html(sub(b))}</li>" for b in item["bullets"])
        parts.append(f"<ul>{lis}</ul>")
    for cmd in item["commands"]:
        parts.append(f"<pre><code>{html.escape(sub(cmd))}</code></pre>")
    if item["warning"]:
        warning = _inline_html(sub(item["warning"]))
        parts.append(f'<p class="warning"><strong>Warning:</strong> {warning}</p>')
    if item["table"]:
        t = item["table"]
        parts.append(
            _html_table([sub(h) for h in t["headers"]], [[sub(c) for c in r] for r in t["rows"]])
        )
    return parts


def html_step_extras(authoring, step_id):
    extra = au.step_extra(authoring, step_id) if authoring else None
    return _html_items(extra, au.param_values(authoring)) if extra else []


def html_blocks(authoring, after, step_tag):
    """Blocks placed after step `after` ("" = before the first step)."""
    if not authoring:
        return []
    values = au.param_values(authoring)
    sub = lambda t: au.substitute(t, values)  # noqa: E731
    parts = []
    for b in au.blocks_after(authoring, after):
        if b["section"]:
            parts.append(f"<h2>{html.escape(sub(b['section']))}</h2>")
        if b["heading"]:
            parts.append(f"<{step_tag}>{html.escape(sub(b['heading']))}</{step_tag}>")
        for para in sub(b["text"]).split("\n\n"):
            if para.strip():
                parts.append(f"<p>{_inline_html(para.strip())}</p>")
        parts += _html_items(b, values)
        if b["placeholder"]:
            parts.append(
                f"<p><em>[IMAGE PLACEHOLDER: {html.escape(sub(b['placeholder']))}]</em></p>"
            )
    return parts


def html_prerequisites(authoring):
    if not authoring or not authoring["prerequisites"]:
        return []
    values = au.param_values(authoring)
    lis = "".join(
        f"<li>{_inline_html(au.substitute(p, values))}</li>" for p in authoring["prerequisites"]
    )
    return ["<h2>Before You Begin</h2>", f"<ul>{lis}</ul>"]


def html_revisions(authoring, revision, date, author):
    if not authoring or not authoring["revisions"] or None in (revision, date, author):
        return []
    rows = au.revision_rows(authoring, date, revision, author)
    return [
        "<h2>Revision History</h2>",
        _html_table(["Date", "Revision", "Comment", "Author"], rows),
    ]


def render_markdown(
    manifest,
    step_results,
    annotated_paths,
    narrative_text=None,
    base_dir=None,
    phases=None,
    omitted_screenshots=(),
    authoring=None,
    revision=None,
    date=None,
    author=None,
):
    """Assembles a Markdown document: title, optional narrative section,
    then one section per step with its text and annotated screenshot. When
    steps are grouped into phases (6c), each phase gets a `##` heading and
    step headings drop to `###`; ungrouped output is unchanged."""
    grouped = is_grouped(phases)
    starts = phase_starts(manifest, phases) if grouped else {}
    step_level = "###" if grouped else "##"
    lines = [f"# {manifest.session.title or manifest.session.id}", ""]
    if narrative_text:
        lines.append(narrative_text)
        lines.append("")
    lines += md_prerequisites(authoring) + md_blocks(authoring, "", step_level)
    for n, (step, result, shot) in enumerate(
        zip(manifest.steps, step_results, annotated_paths, strict=True), start=1
    ):
        if step.id in starts:
            lines.append(f"## {starts[step.id]}")
            lines.append("")
        heading = step_heading(n, step, result)
        lines.append(f"{step_level} {_escape_md_heading(heading)}")
        lines.append("")
        lines.append(result["text"])
        lines.append("")
        for note in step_review_notes(step):
            lines.append(f"**{note}**")
            lines.append("")
        lines += md_step_extras(authoring, step.id)
        if result.get("narration"):
            lines.append(f"> **Narration:** {result['narration']}")
            lines.append("")
        if step.id not in omitted_screenshots:
            lines.append(f"![{_escape_md_alt_text(heading)}]({_image_ref(shot, base_dir)})")
            lines.append("")
        lines += md_blocks(authoring, step.id, step_level)
    lines += md_revisions(authoring, revision, date, author)
    return "\n".join(lines)


def review_note_html(step):
    """step_review_notes() as HTML paragraphs, shared by render_html and the
    single-file export so both show the same markers."""
    return [
        f'<p class="review-needed"><strong>{html.escape(note)}</strong></p>'
        for note in step_review_notes(step)
    ]


# Inline so the callout looks right in every HTML export, including
# render_html's style-less page.
_VERIFY_STYLE = (
    "border-left:3px solid #c00000;margin:.75em 0;padding:.4em .9em;"
    "background:#fdf2f2;color:#7a1414"
)


def narrative_html_blocks(narrative_text):
    """Splits narrative text into HTML blocks, rendering '> '-prefixed
    lines (task-08's [verify] blockquotes) as <blockquote> elements instead
    of flattening them into a single escaped paragraph — otherwise the
    blockquote marker and its newlines collapse into unreadable inline text.

    A [verify] line renders as a styled "Needs verification:" callout, the
    way the docx and PDF already show it, instead of raw "[verify]
    (claim-001): ..." text. The claim id stays on the element (data-claim)
    and in the sidecar report; claim coverage is validated on the narrative
    text itself, never on this HTML."""
    blocks = []
    paragraph = []

    def flush_paragraph():
        if paragraph:
            blocks.append(f"<p>{html.escape(' '.join(paragraph))}</p>")
            paragraph.clear()

    for line in narrative_text.splitlines():
        if line.startswith("> "):
            flush_paragraph()
            claim_text = parse_verify_line(line)
            if claim_text is None:
                blocks.append(f"<blockquote>{html.escape(line[2:])}</blockquote>")
            else:
                claim_id = line[len("> [verify] (") :].split(")", 1)[0]
                blocks.append(
                    f'<blockquote class="verify" data-claim="{html.escape(claim_id)}" '
                    f'style="{_VERIFY_STYLE}"><strong>Needs verification:</strong> '
                    f"{html.escape(claim_text)}</blockquote>"
                )
        elif line.strip():
            paragraph.append(line)
        else:
            flush_paragraph()
    flush_paragraph()
    return blocks


def render_html(
    manifest,
    step_results,
    annotated_paths,
    narrative_text=None,
    base_dir=None,
    phases=None,
    omitted_screenshots=(),
    authoring=None,
    revision=None,
    date=None,
    author=None,
):
    """Assembles a minimal HTML document mirroring render_markdown's
    structure — plain string building, no templating engine dependency."""
    grouped = is_grouped(phases)
    starts = phase_starts(manifest, phases) if grouped else {}
    tag = "h3" if grouped else "h2"
    title = manifest.session.title or manifest.session.id
    parts = [
        "<!doctype html>",
        f'<html><head><meta charset="utf-8"><title>{html.escape(title)}</title></head><body>',
        f"<h1>{html.escape(title)}</h1>",
    ]
    if narrative_text:
        parts.extend(narrative_html_blocks(narrative_text))
    parts += html_prerequisites(authoring) + html_blocks(authoring, "", tag)
    for n, (step, result, shot) in enumerate(
        zip(manifest.steps, step_results, annotated_paths, strict=True), start=1
    ):
        if step.id in starts:
            parts.append(f"<h2>{html.escape(starts[step.id])}</h2>")
        heading = step_heading(n, step, result)
        # id=step.id anchors each step so a link like doc.html#step-003 (the
        # session page's report rows, webui/pages.py's _finding_row) jumps
        # straight to it.
        parts.append(f'<{tag} id="{html.escape(step.id)}">{html.escape(heading)}</{tag}>')
        parts.append(f"<p>{html.escape(result['text'])}</p>")
        parts.extend(review_note_html(step))
        parts += html_step_extras(authoring, step.id)
        if result.get("narration"):
            parts.append(
                '<blockquote class="narration"><strong>Narration:</strong> '
                f"{html.escape(result['narration'])}</blockquote>"
            )
        if step.id not in omitted_screenshots:
            img_ref = _image_ref(shot, base_dir)
            parts.append(f'<img src="{html.escape(img_ref)}" alt="{html.escape(heading)}">')
        parts += html_blocks(authoring, step.id, tag)
    parts += html_revisions(authoring, revision, date, author)
    parts.append("</body></html>")
    return "\n".join(parts)
