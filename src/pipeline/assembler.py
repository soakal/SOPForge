"""Step assembler (invariant L1, CLAUDE.md): the manifest's step ids and
order are structural ground truth, established once in Phase 1's
ManifestBuilder and never modified downstream. This assembler generates one
doc entry per manifest step (id attached directly from the manifest, never
invented) and reassembles them with plain code — a list comprehension, not
anything that could drop, duplicate, or reorder entries."""

import datetime as dt


def step_heading(n, step):
    """A short, descriptive per-step heading built purely from manifest
    fields (no LLM, no invariant risk) -- "Step 3 — Click 'Save'" instead of
    a bare "Step 3". Shared by every export format (docx/pdf/html/md) so a
    step's heading, TOC entry, and image caption are always the same text."""
    if step.element.name:
        target = f"'{step.element.name}'"
    elif step.element.control_type:
        target = f"the {step.element.control_type}"
    else:
        target = "the screen"
    verb = "Click" if step.action == "click" else "Enter value in"
    return f"Step {n} — {verb} {target}"


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
