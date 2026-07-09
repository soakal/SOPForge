"""Narration polish -- stage 2 of the semantic transcript pipeline: takes
each step's VERBATIM narration segment (from semantic_align, stage 1) and
asks the LLM to clean up the raw spoken-style phrasing (run-on "then...
then...then", filler words, missing punctuation) into clearer prose --
without inventing or dropping anything. Every rewrite is checked by a
mechanical gate before it's trusted; a step whose rewrite fails the gate
just keeps its stage-1 verbatim text -- polish is a pure quality layer on
top of already-correct placement, its failure can never undo that
placement. One attempt, never retried, matching the rest of the pipeline's
round-trip-then-fallback discipline."""

import json
import re

from pipeline.claim_coverage import render_verify_blockquote
from pipeline.textgate import degenerate_reason

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.S)
_WORD_RE = re.compile(r"[a-z0-9]+")

# Function words + the step-generation verb vocabulary -- excluded from the
# "no invented content" check below since they're structural/expected in any
# rephrasing, not facts the original segment needs to have supplied.
_STOPWORDS = {
    "a", "an", "the", "then", "next", "now", "so", "and", "to", "in", "on",
    "at", "of", "for", "with", "this", "that", "it", "is", "was", "were",
    "be", "been", "being", "you", "your", "we", "our", "i", "um", "uh",
    "okay", "ok", "well", "just", "up", "down", "into", "onto",
    "click", "clicks", "clicked", "clicking", "select", "selects", "selected",
    "selecting", "open", "opens", "opened", "opening", "enter", "enters",
    "entered", "entering", "type", "types", "typed", "typing",
}  # fmt: skip

_POLISH_PROMPT_TEMPLATE = (
    "Below are narration segments, one per captured step, transcribed verbatim from "
    "spoken audio (so they may run on without punctuation, or include filler words). "
    "Rewrite each into clear, well-punctuated prose -- but you may ONLY rephrase; do "
    "NOT add any fact, number, name, or detail that isn't already in the segment. Keep "
    "each rewrite close in length to its original.\n\n"
    "{items}\n\n"
    'Respond with ONLY a JSON array: [{{"step_id": "step-001", "text": "..."}}, ...] '
    "for every step_id given above."
)


def _normalize(text):
    return re.sub(r"\s+", " ", text.lower()).strip()


def _content_tokens(text):
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS and len(w) >= 2}


def _has_digit_or_path_marker(chunk):
    return any(ch.isdigit() for ch in chunk) or any(ch in chunk for ch in "\\/:")


def _gate(original, rewrite, support_text):
    """Returns (ok, reason, dropped_clauses). `support_text` is everything
    the rewrite is allowed to draw on beyond the original segment itself
    (the step's element name, window title, and its own generated text)."""
    # 0. Degenerate decoding (a repetition loop, a leaked chat-template
    # token) -- checks 1/2 below would already catch a Latin-script version
    # of this (novel tokens with nothing to trace back to), but they're
    # blind to a non-Latin-script flood that happens to land inside the
    # length-ratio band, since _WORD_RE only recognizes [a-z0-9]+ tokens.
    reason = degenerate_reason(rewrite)
    if reason:
        return False, reason, []

    orig_norm = _normalize(original)
    rewrite_norm = _normalize(rewrite)
    support_norm = _normalize(original + " " + support_text)

    # 1. Any literal fact (a number, a path, a drive/filename-looking token)
    # in the original must survive verbatim in the rewrite -- exactly the
    # kind of detail a "cleanup" pass must never quietly drop or alter.
    for chunk in original.split():
        stripped = chunk.strip(".,;:!?\"'()").lower()
        if stripped and _has_digit_or_path_marker(stripped) and stripped not in rewrite_norm:
            return False, "dropped or altered a literal fact", []

    # 2. Every meaningful word in the rewrite must trace back to the
    # original segment or this step's own known context -- nothing invented.
    for tok in _content_tokens(rewrite_norm):
        if tok not in support_norm:
            return False, "introduced unsupported content", []

    # 3. Length sanity -- a "cleanup" dramatically shorter or longer than
    # the source is not a faithful cleanup.
    if not (0.4 <= len(rewrite_norm) / max(len(orig_norm), 1) <= 1.8):
        return False, "length ratio out of bounds", []

    # 4. Soft check: a clause of the original whose content is entirely
    # missing from the rewrite gets flagged, not rejected -- the rewrite is
    # still used, just with what it dropped called out explicitly rather
    # than silently lost.
    clauses = re.split(r"[.;]|\bthen\b|\bnext\b|\bnow\b|\bso\b", original, flags=re.IGNORECASE)
    dropped = []
    rewrite_tokens = _content_tokens(rewrite_norm)
    for clause in clauses:
        clause = clause.strip()
        tokens = _content_tokens(clause)
        if tokens and not (tokens & rewrite_tokens):
            dropped.append(clause)
    return True, None, dropped


def _build_prompt(items):
    lines = [f'{item["step_id"]}: "{item["text"]}"' for item in items]
    return _POLISH_PROMPT_TEMPLATE.format(items="\n".join(lines))


def polish_narration(per_step, manifest, step_contexts, llm):
    """Returns (final_per_step, meta). Never raises -- any failure just
    means every step keeps its verbatim stage-1 text (meta explains why)."""
    context_by_id = {ctx["step_id"]: ctx for ctx in step_contexts}
    step_ids = list(per_step)

    meta = {
        "attempted": False,
        "steps_polished": [],
        "steps_kept_verbatim": {},
        "verify_claims": [],
    }
    if not step_ids:
        return per_step, meta

    meta["attempted"] = True
    items = [{"step_id": sid, "text": per_step[sid]} for sid in step_ids]
    try:
        reply = llm.chat([{"role": "user", "content": _build_prompt(items)}])
        match = _JSON_ARRAY_RE.search(reply)
        rewrites = json.loads(match.group(0))
        rewrite_by_id = {
            r["step_id"]: r["text"]
            for r in rewrites
            if isinstance(r, dict)
            and isinstance(r.get("step_id"), str)
            and isinstance(r.get("text"), str)
        }
    except Exception:  # noqa: BLE001 - a bad response means everyone stays verbatim
        meta["steps_kept_verbatim"] = {sid: "polish call failed" for sid in step_ids}
        return dict(per_step), meta

    final = {}
    verify_counter = 0
    for sid in step_ids:
        original = per_step[sid]
        rewrite = rewrite_by_id.get(sid)
        if rewrite is None:
            final[sid] = original
            meta["steps_kept_verbatim"][sid] = "not returned by model"
            continue

        ctx = context_by_id.get(sid, {})
        support_text = " ".join(
            str(ctx.get(k, "")) for k in ("element_name", "window_title", "step_text")
        )
        ok, reason, dropped_clauses = _gate(original, rewrite, support_text)
        if not ok:
            final[sid] = original
            meta["steps_kept_verbatim"][sid] = reason
            continue

        text = rewrite
        for clause in dropped_clauses:
            verify_counter += 1
            claim_id = f"nar-{sid}-{verify_counter}"
            meta["verify_claims"].append({"claim_id": claim_id, "text": clause})
            text = f"{text.rstrip()}\n\n{render_verify_blockquote({'claim_id': claim_id, 'text': clause})}"
        final[sid] = text
        meta["steps_polished"].append(sid)

    return final, meta
