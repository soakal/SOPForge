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

_CLICK_WORDS = (
    r"click|clicked|clicks|select|selected|selects|press|pressed|presses|"
    r"choose|chose|chosen|chooses|open|opened|opens|check|checked|checks|"
    r"expand|expanded|expands|toggle|toggled|toggles|tap|tapped|taps"
)
_TYPE_WORDS = (
    r"enter|entered|enters|type|typed|types|input|inputs|fill|filled|fills|"
    r"provide|provided|provides|paste|pasted|pastes|set"
)
_CLICK_VERBS = re.compile(rf"\b({_CLICK_WORDS})\b", re.IGNORECASE)
_TYPE_VERBS = re.compile(rf"\b({_TYPE_WORDS})\b", re.IGNORECASE)

_LEAD = r"(?:(?:the user|you)\s+)?(?:(?:double|right|left)[- ])?"
_Q = "['\"]?"
_DET = r"(?:(?:the|a|an|your|on the|on)\s+)?"
_OBJ = r"(?:\s+(?:(?:a|the)\s+)?(?:value|text)(?:\s+(?:in|into))?|\s+(?:in|into))?"
_CONTROLS = (
    "button|field|box|textbox|checkbox|menu|item|tab|link|icon|option|list|dropdown|control|element"
)
_AREAS = "element|control|field|button|item|area"


def _lit(value):
    """Escaped literal, internal whitespace flexible."""
    return r"\s+".join(re.escape(p) for p in value.split())


def _forms(step):
    """The closed set of anchored sentence forms for this step, instantiated
    with its own escaped manifest literals.

    ponytail: a correct phrasing outside these two forms is rejected and the
    always-correct template is used instead (CLAUDE.md rule 2 permits it).
    Upgrade path: add a form here, never a free filler set.
    """
    ct = _lit(step.element.control_type) if step.element.control_type else None
    if step.element.name:
        ctl = _CONTROLS + (f"|{ct}" if ct else "")
        target = rf"\s+{_DET}{_Q}{_lit(step.element.name)}{_Q}(?:\s+(?:{ctl}))?"
    else:
        areas = _AREAS + (f"|{ct}" if ct else "")
        pos = rf"position\s*\(\s*{step.screen.x},\s*{step.screen.y}\s*\)"
        # _DET too: a polish rephrase may prepend "the" to the template's own article.
        target = rf"\s+(?:somewhere|anywhere|here|{_DET}(?:an?|the)\s+(?:{areas}|{pos}))"
    if step.action == "click":
        verb, obj, ts = _CLICK_WORDS, "", ""
    else:
        verb, obj = _TYPE_WORDS, _OBJ
        ts = rf"(?:\s*\({re.escape(step.text_summary)}\))?" if step.text_summary else ""
    forms = []
    if step.window.title:
        w = f"{_Q}{_lit(step.window.title)}{_Q}"
        kinds = r"(?:\s+(?:window|dialog|screen|page|tab))?"
        win = rf"\s+(?:in|on|within|inside|from)\s+(?:the\s+)?{w}{kinds}"
        lead_in = rf"(?:in|on|within|inside)\s+(?:the\s+)?{w}(?:\s+(?:window|dialog))?,\s*"
        forms.append(rf"{lead_in}(?:{verb}){obj}{target}{ts}")
    else:
        win = r"\s+in\s+the\s+current\s+window"
    forms.append(rf"{_LEAD}(?:{verb}){obj}{target}{win}{ts}")
    return [re.compile(rf"^{f}\.?$", re.IGNORECASE) for f in forms]


def round_trip_ok(text, step):
    """Returns (ok, mismatches). mismatches holds "action", "element",
    "window" (the known fact is absent from the text) or "form" (all present
    but the text is not one of the anchored sentence forms)."""
    mismatches = []

    action_verbs = _CLICK_VERBS if step.action == "click" else _TYPE_VERBS
    if not action_verbs.search(text):
        mismatches.append("action")

    if step.element.name and step.element.name.lower() not in text.lower():
        mismatches.append("element")

    if step.window.title and step.window.title.lower() not in text.lower():
        mismatches.append("window")

    if not mismatches:
        collapsed = " ".join(text.split())
        if not any(f.match(collapsed) for f in _forms(step)):
            mismatches.append("form")

    return (len(mismatches) == 0, mismatches)
