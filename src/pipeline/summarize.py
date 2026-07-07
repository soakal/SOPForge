"""Generate a title and a short overview paragraph for a SOP from its narration,
using the text LLM. Best-effort: returns (None, None) when there's no narration
or the model reply can't be parsed, so the caller falls back to a plain default
title and no overview -- never raises."""

import json
import re

_JSON_RE = re.compile(r"\{.*\}", re.S)

_PROMPT = (
    "Below is a narration describing a step-by-step computer procedure. Respond with ONLY a "
    'JSON object {"title": "...", "overview": "..."} where "title" is a short descriptive '
    'title for the procedure (4-8 words, no quotes) and "overview" is a single sentence '
    "stating the purpose of the procedure. Narration:\n\n"
)


def generate_title_and_overview(narration, llm_client):
    """Returns (title_or_None, overview_or_None)."""
    if not narration or not narration.strip():
        return None, None
    try:
        reply = llm_client.chat([{"role": "user", "content": _PROMPT + narration}])
        match = _JSON_RE.search(reply)
        obj = json.loads(match.group(0))
        title = (obj.get("title") or "").strip() or None
        overview = (obj.get("overview") or "").strip() or None
        return title, overview
    except Exception:  # noqa: BLE001 - best-effort; caller falls back to defaults
        return None, None
