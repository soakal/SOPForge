"""Semantic (LLM-based) transcript-to-step placement -- stage 1 of the
narration pipeline for a transcript with no structure to split on (no blank
lines between steps' narration, no "Step N:" labels). transcript.py's
deterministic placement can only split on structure that's actually there;
when there is none, it correctly (by its own documented design) collapses
everything onto one step -- see its v1.4.11 WARNING note. This module is
what makes that collapse fixable: one LLM call picks WHERE each step's
portion of the transcript begins, and this module's own code does the
slicing -- the model only ever chooses split points, never phrasing, so
placement stays grounded in the transcript's own verbatim words by
construction, never model-invented text."""

import json
import re

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.S)

_PROMPT_TEMPLATE = (
    "Below is a raw narration transcript describing a sequence of numbered steps a "
    "user performed, and the list of steps that were actually captured (with the "
    "window/element clicked and a short factual description of each). The transcript "
    "has NO punctuation or paragraph structure marking where one step's narration "
    "ends and the next begins -- your ONLY job is to find where each step's portion "
    "of the narration STARTS, using the EXACT words from the transcript. Do not "
    "paraphrase, summarize, or invent anything. Skip a step entirely if the "
    "transcript doesn't seem to mention it.\n\n"
    "Steps:\n{steps}\n\n"
    'Transcript:\n"""\n{transcript}\n"""\n\n'
    "Respond with ONLY a JSON array, like: "
    '[{{"step": 1, "starts_with": "first few exact words"}}, {{"step": 3, "starts_with": "..."}}]. '
    "Steps must be listed in strictly increasing order."
)


def build_step_contexts(manifest, step_results=None):
    """[{"index": 1-based position, "step_id", "window_title", "element_name",
    "control_type", "action", "step_text"}, ...] in manifest order --
    everything the LLM needs to recognize which step a piece of narration
    describes, without needing to see the screenshot itself (the generated/
    template step text is itself derived from the capture, and is a strong
    proxy for what's on screen)."""
    texts_by_id = {r["step_id"]: r["text"] for r in (step_results or [])}
    return [
        {
            "index": i,
            "step_id": step.id,
            "window_title": step.window.title,
            "element_name": step.element.name,
            "control_type": step.element.control_type,
            "action": step.action,
            "step_text": texts_by_id.get(step.id, ""),
        }
        for i, step in enumerate(manifest.steps, start=1)
    ]


def _format_step_list(step_contexts):
    lines = []
    for ctx in step_contexts:
        where = ctx["window_title"] or "the current window"
        what = ctx["element_name"] or ctx["control_type"] or "an element"
        lines.append(f'{ctx["index"]}. [{ctx["action"]}] {what} in "{where}" -- {ctx["step_text"]}')
    return "\n".join(lines)


def _build_prompt(content, step_contexts):
    return _PROMPT_TEMPLATE.format(steps=_format_step_list(step_contexts), transcript=content)


def _normalize_for_search(text):
    """Returns (normalized, index_map): normalized is `text` lowercased with
    runs of whitespace collapsed to a single space; index_map[i] is the
    original-`text` index the normalized character at position i came from,
    so a match position in `normalized` can be translated back to a real
    slice offset in the original, untouched transcript."""
    chars, index_map = [], []
    prev_space = True
    for i, ch in enumerate(text):
        if ch.isspace():
            if not prev_space:
                chars.append(" ")
                index_map.append(i)
            prev_space = True
        else:
            chars.append(ch.lower())
            index_map.append(i)
            prev_space = False
    return "".join(chars), index_map


def semantic_align(content, manifest, step_contexts, llm):
    """Returns (per_step, meta) on a trustworthy result, or None if the LLM
    call fails or its response doesn't pass the grounding gate below -- the
    caller falls back to the existing deterministic placement either way;
    this is never retried. `per_step` values are VERBATIM slices of
    `content` (code does the slicing at model-chosen offsets) -- nothing
    here is model-generated text, so coverage/groundedness is structural,
    not something that needs checking after the fact."""
    if len(step_contexts) < 2:
        return None  # nothing to split; the deterministic path is already correct

    prompt = _build_prompt(content, step_contexts)
    try:
        reply = llm.chat([{"role": "user", "content": prompt}])
        match = _JSON_ARRAY_RE.search(reply)
        boundaries = json.loads(match.group(0))
    except Exception:  # noqa: BLE001 - any failure means fall back, never retry
        return None

    valid_steps = {ctx["index"] for ctx in step_contexts}
    step_id_by_index = {ctx["index"]: ctx["step_id"] for ctx in step_contexts}

    cleaned = []
    last_step = 0
    for item in boundaries:
        if not isinstance(item, dict):
            return None
        step = item.get("step")
        phrase = item.get("starts_with")
        if not isinstance(step, int) or not isinstance(phrase, str) or not phrase.strip():
            return None
        if step not in valid_steps or step <= last_step:
            return None
        cleaned.append((step, phrase))
        last_step = step

    if len(cleaned) < 2:
        return None

    normalized, index_map = _normalize_for_search(content)
    resolved = []
    search_from = 0
    for step, phrase in cleaned:
        norm_phrase, _ = _normalize_for_search(phrase.strip())
        pos = normalized.find(norm_phrase, search_from)
        if pos == -1:
            continue  # drop this boundary; its words merge into the previous segment
        resolved.append((step, index_map[pos]))
        search_from = pos + len(norm_phrase)

    if len(resolved) < 2 or len(resolved) < len(cleaned) / 2:
        return None

    per_step = {}
    for i, (step, start) in enumerate(resolved):
        # The first resolved boundary absorbs everything before it too (any
        # lead-in the model didn't tag to a step, e.g. "okay so" filler) --
        # dropping it instead would silently discard real transcript words.
        real_start = 0 if i == 0 else start
        end = resolved[i + 1][1] if i + 1 < len(resolved) else len(content)
        text = content[real_start:end].strip()
        if text:
            per_step[step_id_by_index[step]] = text

    meta = {
        "mode": "semantic-llm",
        "boundaries_requested": len(cleaned),
        "boundaries_resolved": len(resolved),
    }
    return per_step, meta
