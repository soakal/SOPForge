"""Step assembler (invariant L1, CLAUDE.md): the manifest's step ids and
order are structural ground truth, established once in Phase 1's
ManifestBuilder and never modified downstream. This assembler generates one
doc entry per manifest step (id attached directly from the manifest, never
invented) and reassembles them with plain code — a list comprehension, not
anything that could drop, duplicate, or reorder entries."""


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
