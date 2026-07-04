"""SOP Factory 2 docx assembler (CLAUDE.md: "extend it, do not rewrite it").
Drives the existing SOPBuilder engine (python-docx-based, VRSI-formatted,
at C:\\Users\\Brian\\Documents\\SOP_Factory_2\\template\\sop_lib.py) from a
manifest plus already-rendered step text (task-06/12) and already-annotated
screenshots (task-10). This module contains none of SOPBuilder's own
formatting logic — it only calls its public methods in a new order
specific to a captured SOP session; the engine itself is imported, not
copied."""

import os
import sys
from pathlib import Path

DEFAULT_SOP_FACTORY_2_DIR = Path(r"C:\Users\Brian\Documents\SOP_Factory_2\template")


def sop_factory_2_dir():
    override = os.environ.get("SOPFORGE_SOP_FACTORY_2_DIR")
    return Path(override) if override else DEFAULT_SOP_FACTORY_2_DIR


def _import_sop_builder():
    template_dir = str(sop_factory_2_dir())
    if template_dir not in sys.path:
        sys.path.insert(0, template_dir)
    from sop_lib import SOPBuilder

    return SOPBuilder


def assemble_docx(
    manifest,
    step_results,
    annotated_dir,
    output_path,
    revision="1.0",
    date="01/01/2026",
    author="SOPForge",
):
    """Builds a complete docx from a manifest's steps (already rendered via
    task-06/task-12's step_results, one dict per step with a "text" key)
    and their annotated screenshots (already written by task-10's
    annotate_click into annotated_dir, one file per step named
    step.screenshot). Returns (output_path, warnings) — warnings is
    SOPBuilder's own missing-image/unpatched-header list."""
    sop_builder_cls = _import_sop_builder()
    factory_dir = sop_factory_2_dir()

    sop = sop_builder_cls(
        template_docx=factory_dir / "SOP_TEMPLATE_WITH_PHOTOS.docx",
        output_docx=output_path,
        active_dir=annotated_dir,
        revision=revision,
        date=date,
    )
    title = manifest.session.title or manifest.session.id
    sop.title_page(title.upper(), author=author)
    sop.heading1("Steps")
    for step, result in zip(manifest.steps, step_results):
        sop.heading2(f"Step {step.id}")
        sop.bullet(result["text"])
        sop.image(step.screenshot, caption=step.id)
    sop.revision_history([(date, revision, "Initial generation", author)])
    out = sop.save()
    return out, sop.warnings
