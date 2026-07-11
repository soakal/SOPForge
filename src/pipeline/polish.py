"""Polish pass -- optional 4th pipeline stage: a single formatting/tone pass
over an already-assembled document's text, using the LLM client. Unlike
narration_polish.py (stage 2 of the narration pipeline, which rewrites many
short per-step segments via one JSON-array call), this operates on the whole
document as a single string and returns a single rewritten string -- but the
same fail-safe discipline applies: a rewrite is only trusted if a mechanical
gate confirms it didn't invent content, drop a literal fact, or come back
degenerate. Any failure at all -- a raised exception, a gate rejection, an
empty/non-string reply -- returns the ORIGINAL document text unchanged. One
attempt, never retried, and this function itself never raises: a broken or
misconfigured polish pass can never take down, or silently corrupt, an
otherwise-correct document."""

import re

from pipeline.textgate import degenerate_shape_reason

_WORD_RE = re.compile(r"[a-z0-9]+")

# Function words excluded from the "no invented content" check below --
# structural/expected in any rephrasing, not facts the source document needs
# to have supplied. Mirrors narration_polish.py's _STOPWORDS.
_STOPWORDS = {
    "a", "an", "the", "then", "next", "now", "so", "and", "to", "in", "on",
    "at", "of", "for", "with", "this", "that", "it", "is", "was", "were",
    "be", "been", "being", "you", "your", "we", "our", "i", "as", "by",
    "or", "not", "if", "when", "will", "can", "may", "should", "please",
}  # fmt: skip

_POLISH_PROMPT_TEMPLATE = (
    "Below is a completed Standard Operating Procedure document. Perform ONE "
    "formatting and tone pass ONLY: fix grammar, punctuation, and phrasing; make "
    "headings and lists read consistently. Do NOT add, remove, or alter any fact, "
    "number, name, step, or instruction -- every fact in the original must appear "
    "unchanged in your output, and you may ONLY rephrase.\n\n"
    "{document}\n\n"
    "Return ONLY the polished document text, with no preamble or explanation."
)


def _normalize(text):
    return re.sub(r"\s+", " ", text.lower()).strip()


def _content_tokens(text):
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS and len(w) >= 2}


def _has_digit_or_path_marker(chunk):
    return any(ch.isdigit() for ch in chunk) or any(ch in chunk for ch in "\\/:")


def _gate(original, rewrite):
    """Returns (ok, reason). Mirrors narration_polish._gate's fail-safe
    checks (degenerate decoding, dropped/altered literal facts, invented
    content, length sanity) but scoped to a whole document rather than a
    per-step segment, and without the soft dropped-clause flagging -- a
    whole-document pass has no per-step [verify] blockquote to attach one
    to, so a dropped clause here is just a hard reject.

    Uses `degenerate_shape_reason` rather than `degenerate_reason` -- the
    latter's absolute `_LENGTH_CAP` (600 chars) is sized for short per-step
    captions/narration segments and would reject any real multi-step
    document outright. Document-scale length sanity is instead covered by
    check 3 below (length ratio against the original)."""
    reason = degenerate_shape_reason(rewrite)
    if reason:
        return False, reason

    if not rewrite.strip():
        return False, "empty rewrite"

    orig_norm = _normalize(original)
    rewrite_norm = _normalize(rewrite)

    # 1. Any literal fact (a number, a path, a drive/filename-looking token)
    # in the original must survive verbatim in the rewrite.
    for chunk in original.split():
        stripped = chunk.strip(".,;:!?\"'()").lower()
        if stripped and _has_digit_or_path_marker(stripped) and stripped not in rewrite_norm:
            return False, "dropped or altered a literal fact"

    # 2. Every meaningful word in the rewrite must trace back to the
    # original document -- nothing invented.
    orig_tokens = _content_tokens(orig_norm)
    for tok in _content_tokens(rewrite_norm):
        if tok not in orig_tokens:
            return False, "introduced unsupported content"

    # 3. Length sanity -- a "formatting/tone" pass is not a summary or an
    # expansion.
    if not (0.4 <= len(rewrite_norm) / max(len(orig_norm), 1) <= 1.8):
        return False, "length ratio out of bounds"

    return True, None


def generate_polish_pass(document_text, llm):
    """Runs one formatting/tone LLM pass over `document_text`. Returns the
    polished text if it passes the fail-safe gate; otherwise returns
    `document_text` completely unchanged. Never raises -- any LLM failure,
    malformed reply, or gate rejection is swallowed and treated as "keep the
    original", exactly like narration_polish.py's stage-2 discipline: polish
    is a pure quality layer that can never undo or corrupt an already-correct
    document."""
    if not document_text or not document_text.strip():
        return document_text

    try:
        reply = llm.chat(
            [{"role": "user", "content": _POLISH_PROMPT_TEMPLATE.format(document=document_text)}]
        )
        ok, _reason = _gate(document_text, reply)
    except Exception:  # noqa: BLE001 - any failure here means keep the original document
        return document_text

    if not ok:
        return document_text

    return reply
