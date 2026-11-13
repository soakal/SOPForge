"""Per-session authored content (Phase 7): what SOP Factory's hand-written
build_*.py scripts add on top of the captured steps -- copyable commands,
extra bullets, warnings, scenario tables, prose blocks, prerequisites,
revision history, walkthrough paths and parameters, SPL callouts.

Stored in the session's authoring.json and edited in the review UI. Nothing
here changes WHICH steps a document has (CLAUDE.md prime directive 2): step
extras attach to an existing manifest step id, and blocks are not steps --
they sit between steps and never enter doc.step_ids. Every field is
optional; an empty or missing file renders exactly as before Phase 7.

Inline markup in text fields: **bold** and `code` (monospace). Parameters:
{{name}} anywhere in text is replaced by the parameter's value -- its
default in the documents, the reader's own input in the walkthrough."""

import json
import re

FILE_NAME = "authoring.json"
MAX_TEXT = 4000
MAX_ITEMS = 50
MAX_PATHS = 8
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")
_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_INLINE_RE = re.compile(r"(\*\*[^*\n]+\*\*|`[^`\n]+`)")
_PARAM_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
VERDICTS = ("correct", "incorrect")
SCREENS = ("terminal", "note")

EXAMPLE = {
    "title_line2": "",
    "title_connector": "AND",
    "date": "",
    "prerequisites": ["Administrator rights on the PC"],
    "revisions": [],
    "revision_comment": "",
    "paths": [],
    "params": [{"name": "server_ip", "label": "Server IP", "default": "10.0.0.5"}],
    "steps": {
        "<step id>": {
            "bullets": ["Pick **Advanced** if the dialog asks"],
            "commands": ["ping {{server_ip}}"],
            "warning": "",
            "table": {"headers": ["Scenario", "Example Value"], "rows": [["", ""]]},
            "system_only": "",
            "path": "",
            "width_in": 5.0,
            "callouts": [[50, 50]],
            "verdict": "",
        }
    },
    "blocks": [
        {
            "after": "",
            "section": "",
            "heading": "Open an elevated Command Prompt",
            "text": "",
            "bullets": [],
            "commands": ["ipconfig /flushdns"],
            "warning": "",
            "table": None,
            "placeholder": "",
            "path": "",
            "screen": "terminal",
        }
    ],
}


def _text(value, field, max_len=MAX_TEXT, multiline=False):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    value = value.strip() if multiline else " ".join(value.split())
    if len(value) > max_len:
        raise ValueError(f"{field} is longer than {max_len} characters")
    return value


def _text_list(value, field, multiline=False):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    if len(value) > MAX_ITEMS:
        raise ValueError(f"{field} has more than {MAX_ITEMS} entries")
    items = [_text(v, f"{field}[{i}]", multiline=multiline) for i, v in enumerate(value)]
    return [v for v in items if v]


def _table(value, field):
    if not value:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object with headers and rows")
    headers = value.get("headers") or ["Scenario", "Example Value"]
    if not isinstance(headers, list) or len(headers) != 2:
        raise ValueError(f"{field}.headers must be two column names")
    rows = value.get("rows") or []
    if not isinstance(rows, list) or len(rows) > MAX_ITEMS:
        raise ValueError(f"{field}.rows must be a list of up to {MAX_ITEMS} rows")
    clean_rows = []
    for i, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError(f"{field}.rows[{i}] must have two cells")
        cells = [_text(c, f"{field}.rows[{i}]") for c in row]
        if any(cells):
            clean_rows.append(cells)
    if not clean_rows:
        return None
    return {"headers": [_text(h, f"{field}.headers") for h in headers], "rows": clean_rows}


def _path(value, field, paths):
    value = _text(value, field, 60)
    if value and value not in paths:
        raise ValueError(f"{field} '{value}' is not one of paths {paths}")
    return value


def _common(raw, field, paths):
    return {
        "bullets": _text_list(raw.get("bullets"), f"{field}.bullets"),
        "commands": _text_list(raw.get("commands"), f"{field}.commands", multiline=True),
        "warning": _text(raw.get("warning"), f"{field}.warning"),
        "table": _table(raw.get("table"), f"{field}.table"),
        "path": _path(raw.get("path"), f"{field}.path", paths),
    }


def validate(raw, step_ids):
    """A cleaned authoring dict from user JSON, or raises ValueError naming
    the bad field. step_ids: the manifest's step ids, in order -- extras and
    blocks may only reference those."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("authoring must be a JSON object")
    known = set(step_ids)
    paths = _text_list(raw.get("paths"), "paths")
    if len(paths) > MAX_PATHS or len(set(paths)) != len(paths):
        raise ValueError(f"paths must be up to {MAX_PATHS} distinct names")
    date = _text(raw.get("date"), "date", 10)
    if date and not _DATE_RE.match(date):
        raise ValueError("date must look like MM/DD/YYYY")

    params = []
    for i, p in enumerate(raw.get("params") or []):
        if not isinstance(p, dict):
            raise ValueError(f"params[{i}] must be an object")
        name = _text(p.get("name"), f"params[{i}].name", 40)
        if not _NAME_RE.match(name):
            raise ValueError(f"params[{i}].name must be letters, digits and _")
        params.append(
            {
                "name": name,
                "label": _text(p.get("label"), f"params[{i}].label", 80) or name,
                "default": _text(p.get("default"), f"params[{i}].default", 200),
            }
        )
    if len({p["name"] for p in params}) != len(params):
        raise ValueError("params names must be distinct")

    revisions = []
    for i, r in enumerate(raw.get("revisions") or []):
        if not isinstance(r, dict):
            raise ValueError(f"revisions[{i}] must be an object")
        revisions.append(
            {
                k: _text(r.get(k), f"revisions[{i}].{k}", 200)
                for k in ("date", "revision", "comment", "author")
            }
        )

    steps = {}
    raw_steps = raw.get("steps") or {}
    if not isinstance(raw_steps, dict):
        raise ValueError("steps must be an object keyed by step id")
    for step_id, extra in raw_steps.items():
        if step_id not in known:
            if step_id == "<step id>":
                continue  # the untouched example entry
            raise ValueError(f"steps: '{step_id}' is not a step in this document")
        if not isinstance(extra, dict):
            raise ValueError(f"steps.{step_id} must be an object")
        f = f"steps.{step_id}"
        clean = _common(extra, f, paths)
        clean["system_only"] = _text(extra.get("system_only"), f"{f}.system_only", 60)
        width = extra.get("width_in")
        if width in (None, ""):
            clean["width_in"] = None
        elif isinstance(width, (int, float)) and 1.0 <= width <= 6.5:
            clean["width_in"] = float(width)
        else:
            raise ValueError(f"{f}.width_in must be a number from 1 to 6.5")
        callouts = []
        for j, c in enumerate(extra.get("callouts") or []):
            if (
                not isinstance(c, list)
                or len(c) != 2
                or not all(isinstance(v, (int, float)) and 0 <= v <= 100 for v in c)
            ):
                raise ValueError(f"{f}.callouts[{j}] must be [x%, y%] from 0 to 100")
            callouts.append([float(c[0]), float(c[1])])
        clean["callouts"] = callouts[:9]
        verdict = _text(extra.get("verdict"), f"{f}.verdict", 10).lower()
        if verdict and verdict not in VERDICTS:
            raise ValueError(f"{f}.verdict must be correct or incorrect")
        clean["verdict"] = verdict
        if any(v for v in clean.values()):
            steps[step_id] = clean

    blocks = []
    raw_blocks = raw.get("blocks") or []
    if not isinstance(raw_blocks, list) or len(raw_blocks) > MAX_ITEMS:
        raise ValueError(f"blocks must be a list of up to {MAX_ITEMS}")
    for i, b in enumerate(raw_blocks):
        if not isinstance(b, dict):
            raise ValueError(f"blocks[{i}] must be an object")
        f = f"blocks[{i}]"
        after = _text(b.get("after"), f"{f}.after", 80)
        if after and after not in known:
            raise ValueError(f"{f}.after '{after}' is not a step in this document")
        clean = _common(b, f, paths)
        clean.update(
            after=after,
            section=_text(b.get("section"), f"{f}.section", 120),
            heading=_text(b.get("heading"), f"{f}.heading", 200),
            text=_text(b.get("text"), f"{f}.text", multiline=True),
            placeholder=_text(b.get("placeholder"), f"{f}.placeholder", 300),
        )
        screen = _text(b.get("screen"), f"{f}.screen", 10).lower()
        if screen and screen not in SCREENS:
            raise ValueError(f"{f}.screen must be terminal or note")
        clean["screen"] = screen or ("terminal" if clean["commands"] else "note")
        if any(
            clean[k]
            for k in (
                "section",
                "heading",
                "text",
                "bullets",
                "commands",
                "warning",
                "table",
                "placeholder",
            )
        ):
            blocks.append(clean)

    return {
        "title_line2": _text(raw.get("title_line2"), "title_line2", 120),
        "title_connector": _text(raw.get("title_connector"), "title_connector", 20) or "AND",
        "date": date,
        "prerequisites": _text_list(raw.get("prerequisites"), "prerequisites"),
        "revisions": revisions,
        "revision_comment": _text(raw.get("revision_comment"), "revision_comment", 200),
        "paths": paths,
        "params": params,
        "steps": steps,
        "blocks": blocks,
    }


EMPTY = validate({}, [])


def load(session_dir, step_ids):
    """This session's authoring, cleaned; EMPTY when absent. A file that no
    longer validates (e.g. a step it named was hidden since) keeps whatever
    still applies: unknown step extras and blocks are dropped, not fatal."""
    try:
        raw = json.loads((session_dir / FILE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return EMPTY
    if not isinstance(raw, dict):
        return EMPTY
    known = set(step_ids)
    raw = dict(raw)
    if isinstance(raw.get("steps"), dict):
        raw["steps"] = {k: v for k, v in raw["steps"].items() if k in known}
    if isinstance(raw.get("blocks"), list):
        raw["blocks"] = [
            b
            for b in raw["blocks"]
            if not isinstance(b, dict) or not b.get("after") or b["after"] in known
        ]
    try:
        return validate(raw, step_ids)
    except ValueError:
        return EMPTY


def raw_text(session_dir):
    """The file as the reviewer last saved it (for the editor), or ''."""
    try:
        return (session_dir / FILE_NAME).read_text(encoding="utf-8")
    except OSError:
        return ""


def save(session_dir, text, step_ids):
    """Validates JSON text and writes it; returns the cleaned dict. Raises
    ValueError with a readable reason on bad JSON or a bad field."""
    if not text.strip():
        (session_dir / FILE_NAME).unlink(missing_ok=True)
        return EMPTY
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    clean = validate(raw, step_ids)
    (session_dir / FILE_NAME).write_text(json.dumps(raw, indent=2), encoding="utf-8")
    return clean


# ---- helpers shared by every renderer --------------------------------------


def is_empty(authoring):
    return not authoring or authoring == EMPTY


def step_extra(authoring, step_id):
    return (authoring or EMPTY)["steps"].get(step_id)


def blocks_after(authoring, step_id):
    """Blocks placed right after step_id ("" = before the first step)."""
    return [b for b in (authoring or EMPTY)["blocks"] if b["after"] == step_id]


def param_values(authoring):
    """{name: value} for document formats: the default, else <Label>."""
    return {p["name"]: p["default"] or f"<{p['label']}>" for p in (authoring or EMPTY)["params"]}


def substitute(text, values):
    """{{name}} -> values[name]; unknown names are left as written."""
    return _PARAM_RE.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def inline_parts(text):
    """[(text, style)] for **bold** / `code` markup, style False | "bold" |
    "code" -- SOPBuilder.bullet_rich's own part format."""
    parts = []
    for piece in _INLINE_RE.split(text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**") and len(piece) > 4:
            parts.append((piece[2:-2], "bold"))
        elif piece.startswith("`") and piece.endswith("`") and len(piece) > 2:
            parts.append((piece[1:-1], "code"))
        else:
            parts.append((piece, False))
    return parts


def plain(text):
    """Text with the inline markup removed."""
    return "".join(t for t, _ in inline_parts(text))


def tag_of(extra):
    """The red '— X only' flag for a step or block: system_only, else the
    path it belongs to."""
    if not extra:
        return ""
    if extra.get("system_only"):
        return extra["system_only"]
    return f"{extra['path']} only" if extra.get("path") else ""


def revision_rows(authoring, date, revision, author):
    """Revision table rows: prior history in order, then the current
    revision -- unless the history already lists it."""
    a = authoring or EMPTY
    rows = [
        (r["date"], r["revision"], r["comment"], r["author"])
        for r in a["revisions"]
        if any(r.values())
    ]
    if not any(r[1] == revision for r in rows):
        rows.append((date, revision, a["revision_comment"] or "Initial generation", author))
    return rows


if __name__ == "__main__":  # smallest self-check
    ids = ["s1", "s2"]
    a = validate(
        {
            "steps": {"s1": {"commands": ["ping {{ip}}"], "path": "Win"}},
            "paths": ["Win"],
            "params": [{"name": "ip", "default": "1.2.3.4"}],
            "blocks": [{"after": "s1", "text": "x"}],
        },
        ids,
    )
    assert substitute(a["steps"]["s1"]["commands"][0], param_values(a)) == "ping 1.2.3.4"
    assert tag_of(step_extra(a, "s1")) == "Win only"
    assert blocks_after(a, "s1")[0]["screen"] == "note"
    assert inline_parts("Click **Save** then `ipconfig`") == [
        ("Click ", False),
        ("Save", "bold"),
        (" then ", False),
        ("ipconfig", "code"),
    ]
    assert revision_rows(a, "01/01/2026", "1.0", "me")[-1][2] == "Initial generation"
    for bad in ({"steps": {"zz": {}}}, {"date": "2026-01-01"}, {"blocks": [{"after": "nope"}]}):
        try:
            validate(bad, ids)
            raise AssertionError(bad)
        except ValueError:
            pass
    print("ok")
