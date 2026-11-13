"""SOP Factory 2 docx assembler (CLAUDE.md: "extend it, do not rewrite it").
Drives the existing SOPBuilder engine (python-docx-based, VRSI-formatted,
vendored at vendor/sop_factory_2/sop_lib.py — see that directory's README)
from a manifest plus already-rendered step text (task-06/12) and
already-annotated screenshots (task-10). This module calls the engine's
public methods in a new order specific to a captured SOP session, plus a
thin repo-side layer (docx_fields.py) that turns the engine's own
plain-text TOC into a real Word TOC field without touching the vendored
engine file itself."""

import os
import sys
from pathlib import Path

from docx.shared import Pt, RGBColor

from pipeline import authoring as auth
from pipeline.assembler import document_outline, is_grouped, step_heading, toc_lines
from pipeline.claim_coverage import parse_verify_line
from pipeline.docx_fields import add_toc_field, enable_update_fields_on_open, set_outline_level
from pipeline.resource_path import resource_path
from pipeline.review_notes import step_review_notes

DEFAULT_SOP_FACTORY_2_DIR = resource_path("vendor", "sop_factory_2")
_RED = RGBColor(0xC0, 0x00, 0x00)


def sop_factory_2_dir():
    """Vendored in-repo by default (vendor/sop_factory_2/, bundled into the
    frozen EXE the same way as any other in-repo resource — see
    resource_path()). SOPFORGE_SOP_FACTORY_2_DIR still overrides this in
    either mode, for testing against a different copy of the engine."""
    override = os.environ.get("SOPFORGE_SOP_FACTORY_2_DIR")
    if override:
        return Path(override)
    return resource_path("vendor", "sop_factory_2")


def _import_sop_builder():
    template_dir = str(sop_factory_2_dir())
    if template_dir not in sys.path:
        sys.path.insert(0, template_dir)
    from sop_lib import SOPBuilder

    return SOPBuilder


def _narrative_body(sop, narrative_text):
    """Writes narrative_text as one or more paragraphs, rendering any
    [verify]-flagged line (claim_coverage.parse_verify_line, which reverses
    render_verify_blockquote's own format -- the shared single point of
    truth export_pdf.py's counterpart also uses) as a distinct styled
    callout instead of raw "> [verify] (claim-...)" text that reads as
    debug scaffolding in a shipped document. The claim id itself is dropped
    from what's *shown* — it stays meaningful in the sidecar report, not
    the reader-facing doc."""
    for line in narrative_text.splitlines():
        claim_text = parse_verify_line(line)
        if claim_text is not None:
            p = sop.bullet_rich([("Needs verification: ", True), (claim_text, False)])
            label_run = p.runs[1]  # runs[0] is the bullet_rich "•  " marker
            label_run.italic = True
            label_run.font.color.rgb = _RED
        elif line.strip():
            sop.paragraph(line)


def _step_bullet(sop, step, result, system_only=None):
    """A step's own generated text as a bullet, with its target element's
    name bolded inline (bullet_rich) when that name appears verbatim in the
    text -- e.g. "Click **Save** in the SmartDeploy Console window." Falls
    back to a plain bullet whenever there's no element name, or the
    generated text doesn't literally contain it (an LLM reply that
    paraphrased instead of quoting), since bolding a substring that isn't
    actually there would either crash or silently do nothing useful.
    system_only: the authored red "— X only" flag (authoring.tag_of)."""
    text = result["text"]
    name = step.element.name
    if name and name in text:
        before, _, after = text.partition(name)
        sop.bullet_rich([(before, False), (name, True), (after, False)], system_only=system_only)
    else:
        sop.bullet(text, system_only=system_only)


def _parts(text, values):
    """Authored text -> bullet_rich parts, parameters substituted."""
    return auth.inline_parts(auth.substitute(text, values))


def _keep_last(sop):
    sop.doc.paragraphs[-1].paragraph_format.keep_with_next = True


def _rich_paragraph(sop, text, values):
    """A plain paragraph with **bold** / `code` runs (bullet_rich's styles)."""
    p = sop.doc.add_paragraph()
    for part, style in _parts(text, values):
        run = p.add_run(part)
        if style == "code":
            run.font.name = "Consolas"
            run.font.size = Pt(10)
        else:
            run.bold = bool(style)


def _table(sop, table, values):
    sop.scenario_table(
        [tuple(auth.substitute(c, values) for c in row) for row in table["rows"]],
        headers=tuple(auth.substitute(h, values) for h in table["headers"]),
    )


def _authored(sop, item, values, sub):
    """An authored step extra's / block's bullets, commands and warning,
    each kept with what follows so a step's extras stay with its image.
    Commands are verbatim (after substitution): copy-paste clean."""
    for text in item["bullets"]:
        sop.bullet_rich(_parts(text, values), sub=sub)
        _keep_last(sop)
    for command in item["commands"]:
        sop.code_block(auth.substitute(command, values))
    if item["warning"]:
        p = sop.bullet_rich([("Warning: ", "bold"), *_parts(item["warning"], values)], sub=sub)
        p.runs[1].font.color.rgb = _RED  # runs[0] is the bullet marker
        _keep_last(sop)


def _heading(sop, level, text):
    (sop.heading1 if level == 0 else sop.heading2)(text)
    set_outline_level(sop.doc.paragraphs[-1], level)


def _block(sop, block, heading, values):
    if heading:
        _heading(sop, 1, auth.substitute(heading, values))
        tag = auth.tag_of(block)
        if tag:
            sop.doc.paragraphs[-1].add_run(f"  — {tag}").font.color.rgb = _RED
    for para in block["text"].split("\n\n") if block["text"] else ():
        _rich_paragraph(sop, para.strip(), values)
    _authored(sop, block, values, sub=False)
    if block["table"]:
        _table(sop, block["table"], values)
    if block["placeholder"]:
        sop.placeholder(auth.substitute(block["placeholder"], values))


def _step(sop, n, step, result, heading, extra, values, omitted_screenshots):
    _heading(sop, 1, heading)
    # SOPBuilder.image() keeps only the paragraph directly before the image
    # with it; the step bullet, extras and markers between must also keep
    # with next, or the step text can strand on the previous page.
    _step_bullet(sop, step, result, auth.tag_of(extra) or None)
    _keep_last(sop)
    if extra:
        _authored(sop, extra, values, sub=True)
    for note in step_review_notes(step):
        p = sop.bullet(note, sub=True)
        p.paragraph_format.keep_with_next = True
        for run in p.runs[1:]:  # runs[0] is the bullet marker
            run.bold = True
            run.font.color.rgb = _RED
    if result.get("narration"):
        sop.bullet(f"Narration: {result['narration']}", sub=True)
    if step.id not in omitted_screenshots:
        sop.image(
            step.screenshot,
            caption=step_heading(n, step, result),
            width_in=extra.get("width_in") or 5.0,
        )
    if extra.get("table"):
        _table(sop, extra["table"], values)


def assemble_docx(
    manifest,
    step_results,
    annotated_dir,
    output_path,
    revision="1.0",
    date="01/01/2026",
    author="SOPForge",
    doc_no=None,
    narrative_text=None,
    phases=None,
    subtitle=None,
    omitted_screenshots=(),
    authoring=None,
    version_stamp=None,
):
    """Builds a complete docx from a manifest's steps (already rendered via
    task-06/task-12's step_results, one dict per step with a "text" key)
    and their annotated screenshots (already written by task-10's
    annotate_click into annotated_dir, one file per step named
    step.screenshot). Returns (output_path, warnings) — warnings is
    SOPBuilder's own missing-image/unpatched-header list.

    Layout: assembler.document_outline, the VRSI convention SOP Factory's
    build scripts use -- numbered "N.  Section" headings, lettered
    "A)  Step" headings, the step text as bullets under them -- plus any
    authored content (authoring.py) around and under the steps. The steps
    are still exactly the manifest's, in order.

    `date`/`author`/`doc_no` are meant to be the caller's real, computed
    values (assembler.py's format_doc_date/doc_number, config.py's
    DocumentConfig) -- the defaults here exist only for direct/test callers
    that don't care, never as production fallbacks. An authored `date`
    overrides `date`. `version_stamp` (e.g. "SOPForge v1.12.0") is printed
    small on the title page when given."""
    a = authoring or auth.EMPTY
    values = auth.param_values(a)
    date = a["date"] or date
    sop_builder_cls = _import_sop_builder()
    factory_dir = sop_factory_2_dir()

    sop = sop_builder_cls(
        template_docx=factory_dir / "SOP_TEMPLATE_WITH_PHOTOS.docx",
        output_docx=output_path,
        active_dir=annotated_dir,
        revision=revision,
        date=date,
    )
    sop.version_stamp = version_stamp
    title = manifest.session.title or manifest.session.id
    line2 = auth.substitute(a["title_line2"], values).upper() or None
    sop.title_page(
        title.upper(), a["title_connector"], line2, author=author, doc_no=doc_no, subtitle=subtitle
    )

    # A real Word TOC field (docx_fields.py), not SOPBuilder's own
    # plain-text toc() -- the cached text is the same toc_lines() would
    # show either way, but Word can now actually build/update it. Every
    # heading below is marked with a matching outline level (0 for section
    # headings, 1 for step/block headings) since SOPBuilder's
    # heading1/heading2 carry no named Word style for the field's \u switch
    # to key off. Grouped: sections only (SOP Factory style), so the field
    # collects outline level 0 only -- otherwise Word's first refresh would
    # put every step back into the TOC.
    add_toc_field(
        sop,
        toc_lines(manifest, narrative_text, phases, step_results, a),
        levels="1-1" if is_grouped(phases) else "1-2",
    )

    for item in document_outline(manifest, step_results, narrative_text, phases, a):
        if item[0] == "section":
            _, key, text = item
            _heading(sop, 0, text)
            if key == "overview":
                _narrative_body(sop, narrative_text)
            elif key == "prereq":
                for prereq in a["prerequisites"]:
                    sop.bullet_rich(_parts(prereq, values))
            elif key == "revision":
                sop.revision_history(auth.revision_rows(a, date, revision, author))
        elif item[0] == "block":
            _block(sop, item[1], item[2], values)
        else:
            _, n, step, result, heading = item
            extra = auth.step_extra(a, step.id) or {}
            _step(sop, n, step, result, heading, extra, values, omitted_screenshots)
    enable_update_fields_on_open(sop.doc)
    out = sop.save()
    return out, sop.warnings
