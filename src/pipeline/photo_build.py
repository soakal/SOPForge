"""Manifest-free document mode: build a SOP from just screenshots (+ an
optional narration transcript), with no capture agent involved. Each uploaded
image becomes one step, in upload order, and the transcript supplies each
step's text (placed by the same label/order rules as the capture flow).

This deliberately does NOT weaken the "manifest is ground truth" rule for the
capture pipeline: it synthesizes a schema-valid manifest from the images so the
existing renderers/exporters work unchanged, then generation copies the images
through without a click marker and uses the transcript text verbatim (no LLM --
there's no recorded action to phrase)."""


def synthetic_manifest_dict(title, screenshot_names, started_iso):
    """A schema-valid manifest with one step per screenshot. Steps carry no
    real action metadata (empty window/element, a placeholder click at 0,0) --
    they exist only to hang an image + its narration on. `session.id` is filled
    in by the caller."""
    steps = [
        {
            "id": f"step-{i:03d}",
            "ts_utc": started_iso,
            "action": "click",
            "button": "left",
            "screen": {"x": 0, "y": 0, "monitor": 1},
            "screenshot": name,
            "screenshot_placeholder": False,
            "window": {"title": "", "process": "", "class": ""},
            "element": {
                "name": "",
                "control_type": "",
                "automation_id": "",
                "framework": "",
                "bounding_rect": None,
            },
            "redactions": [],
        }
        for i, name in enumerate(screenshot_names, start=1)
    ]
    return {
        "schema_version": "1.0",
        "session": {
            "id": "",
            "title": title or "",
            "started_utc": started_iso,
            "ended_utc": started_iso,
            "machine": "",
            "os_build": "",
            "narration_wav": None,
        },
        "steps": steps,
    }
