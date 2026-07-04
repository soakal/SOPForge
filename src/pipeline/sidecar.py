"""Sidecar review report (invariant L5, CLAUDE.md): "Every doc ships with a
sidecar review report: template-fallback steps, [verify] claims, steps with
empty UIA metadata." Built entirely from data the earlier pipeline stages
already computed (task-06's step results, task-08/09's uncovered claim ids,
the manifest's own element metadata) — no re-deriving or re-scanning
rendered text, so there's nothing here that can drift out of sync with the
formats those stages actually use."""


def _step_has_empty_uia_metadata(step):
    return not step.element.name and not step.element.control_type


def build_sidecar_report(manifest, step_results, verify_claim_ids, claims_by_id=None):
    """
    manifest: the loaded Manifest.
    step_results: [{"step_id", "text", "used_fallback"}, ...] (task-06 output).
    verify_claim_ids: [claim_id, ...] left uncovered by task-08/09.
    claims_by_id: optional {claim_id: claim_dict} to include each claim's text.

    Returns a JSON-serializable dict with three lists — every doc-affecting
    fact a reviewer needs to check, none of it inferred by a model."""
    claims_by_id = claims_by_id or {}

    return {
        "template_fallback_steps": [r["step_id"] for r in step_results if r["used_fallback"]],
        "verify_claims": [
            {"claim_id": cid, "text": claims_by_id.get(cid, {}).get("text")}
            for cid in verify_claim_ids
        ],
        "empty_metadata_steps": [
            step.id for step in manifest.steps if _step_has_empty_uia_metadata(step)
        ],
    }
