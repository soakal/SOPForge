"""Template fallback renderer (invariant L3, CLAUDE.md): pure string
interpolation from a manifest step's own fields. Never LLM-generated, so it
is always available (no network/model dependency) and always factually
correct by construction — every word traces back to a manifest field, and
when a field is empty (zero UIA metadata), the wording degrades to what *is*
known (screen coordinates, window title) rather than inventing anything."""


def _location_phrase(step):
    # Manual single-quoting, not !r/repr(): repr backslash-escapes strings
    # like a Windows title ("Administrator: C:\Windows\..."), which would
    # make the rendered text no longer contain the manifest's raw window
    # title as a literal substring — breaking round_trip_ok's own check
    # against this template's output (invariant L2 must hold for L3's text).
    if step.window.title:
        return f"the '{step.window.title}' window"
    return "the current window"


def _target_phrase(step):
    if step.element.name:
        control = step.element.control_type or "element"
        return f"the '{step.element.name}' {control}"
    if step.element.control_type:
        return f"the {step.element.control_type}"
    return f"the position ({step.screen.x}, {step.screen.y})"


def render_step_template(step):
    """Returns a plain-text sentence describing one manifest step. Never
    raises for a schema-valid step; the only way to reach the ValueError is
    an `action` value the schema itself would already reject."""
    location = _location_phrase(step)
    if step.action == "click":
        return f"Click {_target_phrase(step)} in {location}."
    if step.action == "type":
        return f"Enter a value in {_target_phrase(step)} in {location} ({step.text_summary})."
    raise ValueError(f"unknown action {step.action!r}")
