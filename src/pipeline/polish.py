"""Polish pass -- optional 4th pipeline stage: a formatting/tone pass over an
already-assembled document's text, using the LLM client. Two entry points
share the same fail-safe discipline: a rewrite is only trusted if a
mechanical gate confirms it didn't invent content, drop a literal fact, or
come back degenerate.

`generate_polish_pass` (the original entry point) operates on the whole
document as a single string and returns a single rewritten string, gated as
a unit by `_gate`. `generate_polish_fields` operates at field granularity --
`narrative_text` and each step's `"text"`/`"narration"` submitted as
separate items in one JSON-array LLM call, mirroring narration_polish.py
(stage 2 of the narration pipeline)'s proven per-unit pattern -- with each
field gated independently by `_field_gate` (the checks `_gate` delegates to,
factored out so both entry points share one implementation). Any failure at
all -- a raised exception, a gate rejection, an empty/non-string reply --
returns the ORIGINAL text unchanged, at whatever granularity that entry
point operates: the whole document for `generate_polish_pass`, just the one
field for `generate_polish_fields`. One attempt, never retried, and neither
function ever raises: a broken or misconfigured polish pass can never take
down, or silently corrupt, an otherwise-correct document."""

import json
import re

from pipeline.claim_coverage import parse_verify_line
from pipeline.textgate import degenerate_shape_reason

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.S)

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
    "format", "reformat", "delete", "remove", "wipe", "erase", "disable",
    "uninstall", "drop", "kill", "terminate",
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

_VOWELS = set("aeiou")


def _cleanup_stem(stem):
    """Consonant-doubling / silent-e cleanup applied after `_stem` strips an
    -ing/-ed suffix, mirroring the relevant subset of the Porter stemmer's
    step-1b cleanup: a stem ending in "at"/"bl"/"iz" gets a silent e
    restored (disabl -> disable, from "disabling"); a stem ending in a
    doubled consonant (other than l/s/z, which are legitimately doubled in
    real words like "install"/"kiss" and must NOT be undone) has the double
    undone (formatt -> format, from "formatting"/"reformatting"); and a
    short consonant-vowel-consonant stem (last letter not w/x/y) gets a
    silent e restored (wip -> wipe from "wiping", eras -> erase from
    "erased"). Anything else is returned unchanged."""
    if stem.endswith(("at", "bl", "iz")):
        return stem + "e"
    if len(stem) >= 2 and stem[-1] == stem[-2] and stem[-1] not in "lsz":
        return stem[:-1]
    if (
        len(stem) >= 3
        and stem[-1] not in _VOWELS
        and stem[-1] not in "wxy"
        and stem[-2] in _VOWELS
        and stem[-3] not in _VOWELS
    ):
        return stem + "e"
    return stem


def _stem(word):
    """Light inflectional stemmer used ONLY to normalize denylist
    comparisons (see `_denylisted_word`) -- strips a single trailing
    -ing/-ed/-s suffix (the -es case, e.g. "deletes"/"erases", falls out of
    the same -s rule, since those are just a silent-e lemma plus -s) and
    runs the result through `_cleanup_stem`, so an inflected variant of a
    word (formatting/reformatting, deletes, wiping, erased, disabling)
    reduces to the same stem as its bare lemma (format/reformat, delete,
    wipe, erase, disable).

    Deliberately produces a value for EQUALITY comparison only, never a
    prefix/substring check -- `_denylisted_word` compares `_stem(novel_word)
    == _stem(denylisted_word)`, so an unrelated word that merely shares a
    prefix with a denylisted word (dropdown vs drop, skillet vs kill,
    terminal vs terminate) does not collapse onto it: none of those end in
    -ing/-ed/-s, so they pass through unchanged and stay distinct from the
    stemmed denylist word."""
    if word.endswith("ing") and len(word) > 5:
        return _cleanup_stem(word[:-3])
    if word.endswith("ed") and len(word) > 4:
        return _cleanup_stem(word[:-2])
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


_DENYLIST_STEMS = {_stem(w) for w in _DENYLIST_WORDS}
_DENYLIST_INFRA_NOUN_STEMS = {_stem(w) for w in _DENYLIST_INFRA_NOUNS}
_RESTART_STEM = _stem("restart")


def _denylisted_word(novel_tokens, rewrite_tokens):
    """Returns the first denylisted high-risk/destructive word found among
    `novel_tokens`, or None. Comparisons are done on STEMS (`_stem`), not
    raw words, so inflected variants (reformatting, deletes, wiping,
    erased, disabling, ...) are rejected just like their bare lemmas.
    `rewrite_tokens` (the full content-token set of the rewrite, novel or
    not) is used only for the "restart" + infra-noun combination check."""
    rewrite_stems = {_stem(w) for w in rewrite_tokens}
    for word in novel_tokens:
        stem = _stem(word)
        if stem in _DENYLIST_STEMS:
            return word
        if stem == _RESTART_STEM and rewrite_stems & _DENYLIST_INFRA_NOUN_STEMS:
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


def _field_gate(original, rewrite):
    """Returns (ok, reason). Mirrors narration_polish._gate's fail-safe
    checks (degenerate decoding, dropped/altered literal facts, invented
    content, length sanity) but without the soft dropped-clause flagging --
    a field-level pass has no per-step [verify] blockquote to attach one to
    for `narrative_text` (which isn't step-scoped), so a dropped clause here
    is just a hard reject. `original`/`rewrite` are a single field's text --
    the whole document (`generate_polish_pass`, via `_gate` below), a step's
    `"text"`, a step's `"narration"`, or `narrative_text`
    (`generate_polish_fields`) -- the checks themselves don't care which.

    Uses `degenerate_shape_reason` rather than `degenerate_reason` -- the
    latter's absolute `_LENGTH_CAP` (600 chars) is sized for short per-step
    captions/narration segments and would reject any real multi-step
    document or narrative outright. Document-scale length sanity is instead
    covered by check 3 below (length ratio against the original)."""
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


def _gate(original, rewrite):
    """Returns (ok, reason). Whole-document alias of `_field_gate`, kept as
    its own name for `generate_polish_pass`'s call site and this module's
    existing tests -- the checks themselves are identical; a whole document
    is just the one field `generate_polish_pass` gates."""
    return _field_gate(original, rewrite)


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


_FIELDS_POLISH_PROMPT_TEMPLATE = (
    "Below are text fields from a completed Standard Operating Procedure "
    "document, each tagged with a field id. Perform ONE formatting and tone "
    "pass ONLY on each field: fix grammar, punctuation, and phrasing; make "
    "wording read consistently. Do NOT add, remove, or alter any fact, "
    "number, name, step, or instruction -- every fact in a field's original "
    "text must appear unchanged in your output for that field, and you may "
    "ONLY rephrase. Keep each rewrite close in length to its original.\n\n"
    "{items}\n\n"
    'Respond with ONLY a JSON array: [{{"field_id": "narrative", "text": "..."}}, ...] '
    "for every field id given above."
)


def _split_verify_lines(narrative_text):
    """Returns (body_text, verify_lines): `verify_lines` is every line of
    `narrative_text` that `claim_coverage.parse_verify_line` recognizes as a
    rendered "> [verify] (claim-id): ..." blockquote (render_verify_blockquote's
    own format), in original order, verbatim; `body_text` is everything else,
    rejoined with newlines.

    Why this exists: `_field_gate`'s literal-fact check (below) only requires
    a claim id's digit-bearing token to survive as a substring ANYWHERE in a
    rewrite -- it does not, and cannot cheaply, pin the exact "> [verify]
    (claim-XXX):" prefix to the start of a line. A polish rewrite that
    faithfully preserves a claim's digits/wording while reflowing away the
    leading "> " marker and "[verify]" tag (e.g. merging the blockquote into
    surrounding prose) would sail through the gate AND validate_claim_coverage
    (claim_coverage.py) -- coverage only checks the claim's text is present
    SOMEWHERE, not that it's still recognizable as a verify callout -- yet
    docx_assembler.py/export_pdf.py's `parse_verify_line` would no longer
    recognize the line, silently dropping the "Needs verification" callout
    and rendering the claim as ordinary body prose with no warning recorded
    anywhere.

    The fix: never let a verify-blockquote line reach the polish LLM at all.
    `generate_polish_fields` calls this to pull every such line out of
    narrative_text before it becomes the "narrative" field item, and splices
    them back verbatim (see `_splice_verify_lines`) after resolving whatever
    the LLM did (or didn't) do to the rest -- so the exact syntax
    `parse_verify_line` depends on can never be touched by a rewrite,
    independent of how good or bad the gate's fact-preservation heuristic
    is."""
    body_lines = []
    verify_lines = []
    for line in narrative_text.splitlines():
        if parse_verify_line(line) is not None:
            verify_lines.append(line)
        else:
            body_lines.append(line)
    return "\n".join(body_lines), verify_lines


def _splice_verify_lines(body_text, verify_lines):
    """Reattaches `verify_lines` (from `_split_verify_lines`) to `body_text`
    after polish, mirroring claim_coverage.ensure_claim_coverage's own
    append-at-the-end format -- the only place verify blockquotes are ever
    produced in production, so this never changes their position relative to
    where they started."""
    if not verify_lines:
        return body_text
    block = "\n".join(verify_lines)
    if body_text and body_text.strip():
        return f"{body_text.rstrip()}\n\n{block}\n"
    return f"{block}\n"


def _build_field_items(narrative_text, step_results):
    """Returns the ordered list of (field_id, text) items to submit in one
    polish call: `narrative_text` (when present/non-empty) as `"narrative"`,
    then each step's `"text"` as its `step_id`, then -- only when that step
    actually carries one -- its `"narration"` as `f"{step_id}:narration"`.
    The narration truthiness check mirrors render_markdown's own
    `result.get("narration")` check, so a step with no narration contributes
    no item and is never sent to the model for that field."""
    items = []
    if narrative_text and narrative_text.strip():
        items.append(("narrative", narrative_text))
    for step in step_results:
        step_id = step["step_id"]
        items.append((step_id, step["text"]))
        narration = step.get("narration")
        if narration:
            items.append((f"{step_id}:narration", narration))
    return items


def _build_fields_prompt(items):
    lines = [f'{field_id}: "{text}"' for field_id, text in items]
    return _FIELDS_POLISH_PROMPT_TEMPLATE.format(items="\n".join(lines))


def generate_polish_fields(narrative_text, step_results, llm):
    """Runs one formatting/tone LLM pass covering `narrative_text` and every
    step's `"text"`/present `"narration"` as separate items in a single
    JSON-array call (see `_build_field_items`) -- narration_polish.py's
    proven per-unit pattern applied to stage-4 polish, so a bad rewrite of
    one field can never discard a good rewrite of another the way
    `generate_polish_pass`'s whole-document gate would.

    Returns `(polished_narrative_text, polished_step_results, meta)`.
    `polished_step_results` is a new list of whole step dicts (a shallow
    copy of each original with `"text"`/`"narration"` possibly replaced),
    not a text-only shadow structure. Each field is independently checked by
    `_field_gate`; a field whose rewrite is missing from the reply or fails
    the gate keeps its ORIGINAL text -- recorded in
    `meta["fields_kept_verbatim"]`. Never raises: any exception raised by
    the LLM call or while parsing its reply leaves every field at its
    original text, exactly like `generate_polish_pass`'s "polish can never
    corrupt or block a document" discipline.

    `narrative_text` may embed "> [verify] (claim-id): ..." blockquote lines
    (claim_coverage.render_verify_blockquote's format). Those lines are
    pulled out via `_split_verify_lines` BEFORE the "narrative" field item is
    built and never shown to the LLM at all, then spliced back verbatim (
    `_splice_verify_lines`) onto whatever the narrative field resolves to --
    see `_split_verify_lines`'s docstring for why: no gate can safely allow a
    rewrite to touch that exact syntax, so the only safe rule is that it
    never gets the chance to."""
    narrative_body, verify_lines = (
        _split_verify_lines(narrative_text) if narrative_text else (narrative_text, [])
    )
    items = _build_field_items(narrative_body, step_results)

    meta = {
        "attempted": False,
        "fields_polished": [],
        "fields_kept_verbatim": {},
    }
    if not items:
        return narrative_text, list(step_results), meta

    meta["attempted"] = True
    try:
        reply = llm.chat([{"role": "user", "content": _build_fields_prompt(items)}])
        match = _JSON_ARRAY_RE.search(reply)
        rewrites = json.loads(match.group(0))
        rewrite_by_id = {
            r["field_id"]: r["text"]
            for r in rewrites
            if isinstance(r, dict)
            and isinstance(r.get("field_id"), str)
            and isinstance(r.get("text"), str)
        }
    except Exception:  # noqa: BLE001 - a bad response means every field stays original
        meta["fields_kept_verbatim"] = {field_id: "polish call failed" for field_id, _ in items}
        return narrative_text, list(step_results), meta

    resolved = {}
    for field_id, original in items:
        rewrite = rewrite_by_id.get(field_id)
        if rewrite is None:
            resolved[field_id] = original
            meta["fields_kept_verbatim"][field_id] = "not returned by model"
            continue
        ok, reason = _field_gate(original, rewrite)
        if not ok:
            resolved[field_id] = original
            meta["fields_kept_verbatim"][field_id] = reason
            continue
        resolved[field_id] = rewrite
        meta["fields_polished"].append(field_id)

    polished_narrative_text = _splice_verify_lines(
        resolved.get("narrative", narrative_body), verify_lines
    )

    polished_step_results = []
    for step in step_results:
        step_id = step["step_id"]
        new_step = dict(step)
        new_step["text"] = resolved.get(step_id, step["text"])
        if step.get("narration"):
            new_step["narration"] = resolved.get(f"{step_id}:narration", step["narration"])
        polished_step_results.append(new_step)

    return polished_narrative_text, polished_step_results, meta
