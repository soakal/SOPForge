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

_ON_SCREEN_CONTEXT_PREFIX = (
    "The following step descriptions were written by looking at the actual screenshots, so "
    "any file, product, or company name in them was read directly off the screen. If the "
    "narration below spells such a name differently, prefer the spelling used in these "
    "descriptions:\n\n{on_screen_texts}\n\n"
)


def generate_title_and_overview(narration, llm_client, on_screen_texts=None):
    """Returns (title_or_None, overview_or_None). `on_screen_texts`, if
    given (vision captions -- see vision.py), are prepended as
    screen-grounded context: a raw narration transcript can misspell an
    out-of-vocabulary proper noun (speech-to-text guessing at an unfamiliar
    word), but a vision caption reads the actual on-screen pixels, so it's
    a stronger spelling signal when the two disagree."""
    if not narration or not narration.strip():
        return None, None
    prompt = _PROMPT
    if on_screen_texts:
        prompt = (
            _ON_SCREEN_CONTEXT_PREFIX.format(on_screen_texts="\n".join(on_screen_texts)) + prompt
        )
    try:
        reply = llm_client.chat([{"role": "user", "content": prompt + narration}])
        match = _JSON_RE.search(reply)
        obj = json.loads(match.group(0))
        title = (obj.get("title") or "").strip() or None
        overview = (obj.get("overview") or "").strip() or None
        return title, overview
    except Exception:  # noqa: BLE001 - best-effort; caller falls back to defaults
        return None, None
