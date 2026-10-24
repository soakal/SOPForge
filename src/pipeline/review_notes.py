"""Inline [REVIEW NEEDED] markers (Phase 6b), SOP Factory's rule that a
recorder gap becomes a visible marker in the document itself, not a guess
and not only a line in the sidecar report.

Derived purely from the manifest step, never from generated text, and
rendered after polish -- so no LLM pass can reword or drop a marker. Every
export format calls step_review_notes() so they all show the same notes.

Photo-mode steps (photo_build.py) are skipped: their manifest is synthetic
and empty by design, so "control not identified" would flag every step."""

from pipeline.photo_build import is_synthetic_step

# Mirrors capture/hooks.py's TYPE_SUMMARY: the capture agent never records
# typed content, it writes this placeholder instead. A text_summary a person
# has since edited (e.g. "entered computer name") no longer contains it and
# gets no marker.
UNCAPTURED_VALUE_PHRASE = "content not captured"

VALUE_NOT_CAPTURED = "[REVIEW NEEDED — value not captured: add the value to enter]"
TARGET_NOT_IDENTIFIED = "[REVIEW NEEDED — control not identified: check the screenshot]"


def has_uncaptured_value(step):
    return step.action == "type" and UNCAPTURED_VALUE_PHRASE in (step.text_summary or "")


def has_empty_uia_metadata(step):
    return not step.element.name and not step.element.control_type


def step_review_notes(step):
    """Marker strings for one step, in display order (possibly empty)."""
    if is_synthetic_step(step):
        return []
    notes = []
    if has_uncaptured_value(step):
        notes.append(VALUE_NOT_CAPTURED)
    if has_empty_uia_metadata(step):
        notes.append(TARGET_NOT_IDENTIFIED)
    return notes
