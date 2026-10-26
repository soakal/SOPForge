"""Step assembler (invariant L1, CLAUDE.md): the manifest's step ids and
order are structural ground truth, established once in Phase 1's
ManifestBuilder and never modified downstream. This assembler generates one
doc entry per manifest step (id attached directly from the manifest, never
invented) and reassembles them with plain code — a list comprehension, not
anything that could drop, duplicate, or reorder entries."""

import datetime as dt

from pipeline.grouping import DEFAULT_PHASE_NAME, default_phases


def step_heading(n, step, result=None):
    """A short, descriptive per-step heading built purely from manifest
    fields (no LLM, no invariant risk) -- "Step 3 — Click 'Save'" instead of
    a bare "Step 3". Shared by every export format (docx/pdf/html/md) so a
    step's heading, TOC entry, and image caption are always the same text.

    Falls back to a bare "Step N" (no verb, no target) when the element has
    neither a name nor a control_type -- claiming a specific action ("Click
    the screen") would be a fabrication for the manifest-free photo-build
    flow (photo_build.py's synthetic steps carry a placeholder action="click"
    for every step even though no click ever happened), and is uninformative
    even for a real capture step with genuinely empty UIA metadata.

    A `result["title"]` (a vision model's short title for a photo-build
    step, read off the screenshot) wins over both: it only names the step,
    the step itself still comes from the manifest."""
    title = (result or {}).get("title")
    if title:
        return f"Step {n} — {title}"
    if step.element.name:
        target = f"'{step.element.name}'"
    elif step.element.control_type:
        target = f"the {step.element.control_type}"
    else:
        return f"Step {n}"
    verb = "Click" if step.action == "click" else "Enter value in"
    return f"Step {n} — {verb} {target}"


def resolve_phases(manifest, phases=None):
    """phases, or the single default "Procedure" phase -- the pre-6c layout."""
    return phases or default_phases(manifest)


def is_grouped(phases):
    """True when phases differ from the single default "Procedure" phase.
    Markdown/HTML only add phase headings (and demote step headings) then,
    so an ungrouped doc renders exactly as before."""
    return bool(phases) and (len(phases) > 1 or phases[0].name != DEFAULT_PHASE_NAME)


def phase_starts(manifest, phases=None):
    """{first step id of each phase: phase name}, for renderers that emit a
    phase heading just before that step."""
    return {p.step_ids[0]: p.name for p in resolve_phases(manifest, phases) if p.step_ids}


def toc_entries(manifest, narrative_text, phases=None, step_results=None):
    """The document outline as [(key, display string)]: an optional numbered
    "N.  Overview" line, a numbered "N.  Procedure" line followed by each
    step's indented heading, and a closing "N.  Revision History" line.
    Grouped into phases, it lists the numbered phase sections only, SOP
    Factory style (no per-step lines). `key` ("overview", "revision", a
    step id, or "phase:<first step id>"/"procedure") lets a renderer attach
    a page number to each line. Shared by docx_assembler.py and
    export_pdf.py so their two TOCs can never independently drift out of
    sync for the same session."""
    results = step_results or [None] * len(manifest.steps)
    section = 0
    entries = []
    if narrative_text:
        section += 1
        entries.append(("overview", f"{section}.  Overview"))
    starts = phase_starts(manifest, phases)
    grouped = is_grouped(phases)
    if not starts:
        section += 1
        entries.append(("procedure", f"{section}.  {DEFAULT_PHASE_NAME}"))
    for n, (step, result) in enumerate(zip(manifest.steps, results, strict=True), 1):
        if step.id in starts:
            section += 1
            entries.append((f"phase:{step.id}", f"{section}.  {starts[step.id]}"))
        if not grouped:
            entries.append((step.id, f"      {step_heading(n, step, result)}"))
    section += 1
    entries.append(("revision", f"{section}.  Revision History"))
    return entries


def toc_lines(manifest, narrative_text, phases=None, step_results=None):
    """toc_entries' display strings only."""
    return [line for _key, line in toc_entries(manifest, narrative_text, phases, step_results)]


def format_doc_date(started_utc):
    """A manifest session's started_utc (ISO 8601, e.g.
    "2026-01-01T00:00:00.000Z") as "MM/DD/YYYY" for a document's title page
    and revision table -- the session's own real date, never a hardcoded
    placeholder. Falls back to today's date only for a value that doesn't
    parse (never expected for a schema-valid manifest, but formatting must
    not raise and break doc generation over a display string)."""
    try:
        parsed = dt.datetime.fromisoformat(started_utc.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        parsed = dt.datetime.now(dt.timezone.utc)
    return parsed.strftime("%m/%d/%Y")


def doc_number(prefix, session_id):
    """ "SOP-A1B2" style document number: a configured prefix (e.g. "SOP") plus
    the last 4 characters of the session id, which is enough to distinguish
    documents without pretending to be a curated sequential number SOPForge
    has no way to track. Returns None (omit the doc_no line entirely) when
    no prefix is configured, since an invented number would be worse than
    none."""
    if not prefix:
        return None
    return f"{prefix}-{session_id[-4:].upper()}"


def assemble_steps(manifest, render_step):
    """render_step(step) -> str. Returns [{"step_id": ..., "text": ...}, ...]
    in exactly manifest.steps order, one entry per manifest step."""
    return [{"step_id": step.id, "text": render_step(step)} for step in manifest.steps]


def check_1to1_mapping(manifest, doc_steps):
    """True iff doc_steps has exactly one entry per manifest step, in the
    same order, with matching ids — the literal statement of invariant L1
    ("set(doc.step_ids) == set(manifest.step_ids), order preserved")."""
    manifest_ids = [step.id for step in manifest.steps]
    doc_ids = [entry["step_id"] for entry in doc_steps]
    return doc_ids == manifest_ids
