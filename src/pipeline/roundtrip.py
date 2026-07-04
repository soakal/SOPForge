"""Round-trip validator (invariant L2, CLAUDE.md): checks that generated
step text doesn't contradict or omit facts the manifest already knows for
certain — the action performed, the element's name (if any was resolved),
and the window title (if known). This is a deterministic, rule-based gate,
not another model's judgment call: presence/absence checks against the
manifest's own ground-truth strings, not free-form NLP extraction. A step
with genuinely empty UIA metadata has nothing to check for that field —
invariant L3's degrade-to-what's-known philosophy applies here too; this
validator flags contradictions and omissions of *known* facts, not the
absence of facts nobody had.
"""

import re

_CLICK_VERBS = re.compile(
    r"\b(click|clicked|clicks|select|selected|selects|press|pressed|presses|"
    r"choose|chose|chosen|chooses|open|opened|opens|check|checked|checks|"
    r"expand|expanded|expands|toggle|toggled|toggles|tap|tapped|taps)\b",
    re.IGNORECASE,
)
_TYPE_VERBS = re.compile(
    r"\b(enter|entered|enters|type|typed|types|input|inputs|fill|filled|fills|"
    r"provide|provided|provides|paste|pasted|pastes|set)\b",
    re.IGNORECASE,
)


def round_trip_ok(text, step):
    """Returns (ok, mismatches) where mismatches is a list of field names
    ("action", "element", "window") the text failed to correctly reflect."""
    mismatches = []

    action_verbs = _CLICK_VERBS if step.action == "click" else _TYPE_VERBS
    if not action_verbs.search(text):
        mismatches.append("action")

    if step.element.name and step.element.name.lower() not in text.lower():
        mismatches.append("element")

    if step.window.title and step.window.title.lower() not in text.lower():
        mismatches.append("window")

    return (len(mismatches) == 0, mismatches)
