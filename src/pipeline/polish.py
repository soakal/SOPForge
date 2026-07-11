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

# Max share of a rewrite's distinct content tokens allowed to be absent from
# the original before check 2 below rejects it as invented content. A single
# unsupported word can be an incidental paraphrase artifact (e.g. a connector
# adjective); a cluster of them is fabricated content. Calibrated against
# test_gate_rejects_invented_content: injecting "restart the print spooler
# service" into a short original makes 4 of the rewrite's 8 distinct content
# tokens (50%) novel -- well above this threshold -- while a faithful
# paraphrase of a realistic ~130-content-token document that picks up a
# couple of incidental new words (e.g. "carefully", "successfully") lands
# under 2%, comfortably below it.
_MAX_NOVEL_CONTENT_FRACTION = 0.3

# Absolute cap on novel content tokens, applied ALONGSIDE (not instead of)
# the fraction above. The fraction alone is exploitable on realistic-sized
# documents: appending one entire fabricated-but-plausible extra SOP step
# (e.g. "Restart the network switch located in the server rack and confirm
# all indicator lights turn solid green.") to a faithful rewrite of a
# ~130-content-token document adds ~12 novel tokens -- only ~8% of the
# rewrite's total, comfortably under the 30% fraction -- yet is a fully
# invented, actionable step a reader could act on. A real document only
# grows the *budget* for novel words under a fraction rule; it should not
# grow the amount of fabrication that's tolerable. This cap is sized so:
#   - the benign 2-new-word paraphrase case (see
#     test_gate_accepts_benign_paraphrase_with_a_few_new_words) stays well
#     under it (2 << 8), and
#   - a single fabricated extra step (~12 novel tokens, see
#     test_gate_rejects_a_fabricated_extra_step) exceeds it and is rejected,
#     regardless of how large the surrounding document is.
_MAX_NOVEL_CONTENT_ABSOLUTE = 8

# Deterministic backstop for check 2, independent of the fraction/absolute
# counts above. An attacker doesn't need many novel words to smuggle in a
# dangerous fabricated instruction -- they can reuse the document's own
# vocabulary (stopwords, structural words like "step"/"session") for
# everything except a handful of genuinely new words, and still clear both
# the fraction and absolute caps. Demonstrated case: appending "Step 20:
# Format the c drive to complete the session." to a faithful rewrite of a
# ~130-content-token document introduces only 4 novel tokens (20, complete,
# drive, format) -- 2.9% of the rewrite, comfortably under both caps -- yet
# is a fully invented, destructive instruction. No amount of retuning the
# fraction/absolute thresholds can close this: a bare novel-word-COUNT
# heuristic can't see WHAT the novel words mean. This set closes that gap by
# rejecting unconditionally, regardless of cluster size, when a novel word is
# itself a high-risk/destructive action verb -- no legitimate "fix
# grammar/phrasing" rewrite needs to introduce a new word like this that
# wasn't already in the source.
_DENYLIST_WORDS = {
    "format", "delete", "remove", "wipe", "erase", "disable", "uninstall",
    "drop", "kill", "terminate",
}  # fmt: skip

# "restart" alone is too common a benign word (e.g. a legitimate rewrite
# might faithfully carry over "restart the tray app" from the original) to
# denylist outright -- but "restart" newly applied to a piece of
# infrastructure is exactly the shape of a plausible-sounding, invented,
# disruptive instruction (e.g. "restart the network switch", "restart the
# print spooler service"), so it's denylisted only when it co-occurs with an
# infrastructure noun anywhere in the rewrite.
_DENYLIST_INFRA_NOUNS = {
    "server", "service", "switch", "router", "database", "firewall",
    "network", "controller", "cluster", "node", "spooler", "domain",
}  # fmt: skip


def _denylisted_word(novel_tokens, rewrite_tokens):
    """Returns the first denylisted high-risk/destructive word found among
    `novel_tokens`, or None. `rewrite_tokens` (the full content-token set of
    the rewrite, novel or not) is used only for the "restart" + infra-noun
    combination check."""
    for word in novel_tokens:
        if word in _DENYLIST_WORDS:
            return word
        if word == "restart" and rewrite_tokens & _DENYLIST_INFRA_NOUNS:
            return word
    return None


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

    # 2. Most meaningful words in the rewrite must trace back to the
    # original document. A handful of genuinely new words is tolerated as
    # incidental paraphrase vocabulary; a cluster of unsupported new words
    # is rejected as invented content -- whether that cluster is large
    # relative to the rewrite (_MAX_NOVEL_CONTENT_FRACTION) or large in
    # absolute terms (_MAX_NOVEL_CONTENT_ABSOLUTE, which a big document
    # can't dilute away). Independently of cluster size, ANY novel word that
    # is itself high-risk/destructive (_DENYLIST_WORDS) is rejected
    # unconditionally -- a count-based rule alone can be evaded by padding a
    # fabricated instruction with the document's own reused vocabulary so
    # only a few genuinely novel words are needed. Any one of these three
    # checks failing is sufficient to reject.
    orig_tokens = _content_tokens(orig_norm)
    rewrite_tokens = _content_tokens(rewrite_norm)
    novel_tokens = rewrite_tokens - orig_tokens
    if novel_tokens:
        if _denylisted_word(novel_tokens, rewrite_tokens):
            return False, "introduced a high-risk or destructive instruction"
        exceeds_fraction = len(novel_tokens) / len(rewrite_tokens) > _MAX_NOVEL_CONTENT_FRACTION
        exceeds_absolute = len(novel_tokens) > _MAX_NOVEL_CONTENT_ABSOLUTE
        if exceeds_fraction or exceeds_absolute:
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
