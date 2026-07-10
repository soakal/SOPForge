"""Deterministic proper-noun spelling *consistency* pass (CLAUDE.md rule 2:
"completeness guarantees come from deterministic validation ..., never from
model judgment"). Photo-mode text (see server.py's _generate_photo) has no
manifest ground truth (element/window names) to validate against the way
the real-capture flow's roundtrip.py does -- a photo-mode session's step
text comes from a raw narration transcript and/or vision captions, and
speech-to-text transcription of an out-of-vocabulary proper noun (a
niche product/company name) can produce a different phonetic guess each
time it's spoken, e.g. "Hilscher" transcribed as "Hillshire" in one place
and "Hilschier" in another within the same document.

This module cannot fix that to the *correct* spelling -- the pipeline has
no ground truth for what a proper noun should be spelled like in general.
What it CAN do deterministically: notice when the same document uses
several near-identical spellings of what is obviously the same word, and
canonicalize all of them to a single one of those observed spellings, so
the shipped document is at least internally consistent. If the user typed
a session title, whichever variant matches a word in it wins as the
canonical spelling (the one piece of "ground truth" the user did supply);
otherwise the most frequent variant wins.

The fuzzy matching here is deliberately conservative: ordinary English
inflection (update/updated, close/closed) and unrelated same-length words
(pointer/printer) can sit just as edit-distance-close as a real ASR
misspelling pair -- a naive similarity threshold alone can't tell them
apart (verified against a real over-merge found in review). Two guards
narrow the candidate pool to proper-noun-shaped tokens before any fuzzy
matching happens: only words that appear capitalized somewhere OTHER than
the start of a sentence (or fully in caps, e.g. a shouted document title)
are eligible at all -- ordinary verbs/nouns almost never are, so
"updated" (lowercase, mid-sentence) never enters the pool even though
"Update" (a capitalized UI label) might. A word ONLY ever capitalized
because it starts a sentence doesn't count -- that's just grammar, not a
proper noun.
"""

import re
from difflib import SequenceMatcher

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z']*")
_MIN_LEN = 5
_SIMILARITY_THRESHOLD = 0.75
_SUFFIXES = ("s", "es", "d", "ed", "ing", "r", "er")
_SENTENCE_BOUNDARY_SKIP = " \t\n\r'\"([{-"
_SENTENCE_END = ".!?:"
_STOPLIST = frozenset(
    {
        "click",
        "clicked",
        "clicks",
        "select",
        "selected",
        "selects",
        "enter",
        "entered",
        "enters",
        "double",
        "right",
        "extract",
        "extracted",
        "extracting",
        "install",
        "installed",
        "installing",
        "installation",
        "installer",
        "setup",
        "folder",
        "folders",
        "file",
        "files",
        "window",
        "windows",
        "button",
        "download",
        "downloads",
        "archive",
        "archiving",
        "checkbox",
        "license",
        "agreement",
        "content",
        "captured",
        "provided",
        "begin",
        "device",
        "driver",
        "drivers",
        "communication",
        "studio",
        "explorer",
        "wizard",
        "complete",
        "finish",
        "continue",
        "cancel",
        "browse",
        "location",
        "destination",
        "system",
        "computer",
        "field",
        "value",
        "editor",
    }
)


def _is_sentence_initial(text, start):
    """True if the token starting at `start` in `text` is the first word of
    a sentence (or of the whole field) -- scans backward past whitespace
    and opening quotes/brackets to the nearest real character."""
    i = start - 1
    while i >= 0 and text[i] in _SENTENCE_BOUNDARY_SKIP:
        i -= 1
    if i < 0:
        return True
    return text[i] in _SENTENCE_END


def _is_proper_noun_like(word, text, start):
    """A word counts as proper-noun-shaped if it's fully capitalized (a
    shouted title/heading, e.g. "HILLSHIRE ...") or capitalized somewhere
    that isn't just sentence-initial grammar (e.g. "the 'Hilsshier
    Windows...' archive" -- capitalized, but not because it opens a
    sentence). A word that is ONLY ever capitalized at the start of a
    sentence (ordinary English grammar) or never capitalized at all
    (an ordinary lowercase word, e.g. a past-tense verb) does not count."""
    if word.isupper():
        return True
    if not word[0].isupper():
        return False
    return not _is_sentence_initial(text, start)


def _tokens_in_order(text):
    """Yields (lowercase_key, exact_form, is_proper_noun_like) for every
    candidate token in `text`, in the order they appear."""
    for match in _WORD_RE.finditer(text):
        word = match.group(0)
        if len(word) < _MIN_LEN:
            continue
        key = word.lower()
        if key in _STOPLIST:
            continue
        yield key, word, _is_proper_noun_like(word, text, match.start())


def _is_suffix_variant(a, b):
    """True if `a` is just `b` (or vice versa) plus a common English
    inflectional/derivational suffix -- a real grammatical difference, not
    a misspelling, e.g. "window"/"windows" or "manage"/"manager". Guards
    the fuzzy grouping below from merging linguistically distinct words
    that happen to be edit-distance close."""
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if not longer.startswith(shorter):
        return False
    return any(longer == shorter + suffix for suffix in _SUFFIXES)


def _group_variants(eligible_keys):
    """Union-find grouping of proper-noun-shaped lowercase keys whose
    pairwise SequenceMatcher ratio clears the similarity threshold,
    skipping pairs that are just a suffix apart. Only `eligible_keys`
    (see _is_proper_noun_like) ever enter this pool -- an ordinary word
    that never appears proper-noun-shaped is never touched, no matter how
    similar it looks to something else. Returns a list of groups (each a
    set of keys), singletons excluded."""
    parent = {key: key for key in eligible_keys}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    keys = list(eligible_keys)
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            if _is_suffix_variant(a, b):
                continue
            if SequenceMatcher(None, a, b).ratio() >= _SIMILARITY_THRESHOLD:
                union(a, b)

    groups = {}
    for key in keys:
        groups.setdefault(find(key), set()).add(key)
    return [group for group in groups.values() if len(group) > 1]


def _adapt_case(canonical_key, original):
    """Renders `canonical_key` (always lowercase) to match `original`'s
    casing pattern -- so replacing a misspelled variant never injects one
    fixed casing (e.g. an ALL-CAPS document title) into an unrelated,
    differently-cased sentence elsewhere in the document."""
    if original.isupper() and len(original) > 1:
        return canonical_key.upper()
    if original[0].isupper():
        return canonical_key.capitalize()
    return canonical_key


def canonicalize_terms(fields, anchor_text=None):
    """`fields` is a list of strings (e.g. [title, narrative_text, *step
    texts]) that together make up one generated document. Returns
    (canonicalized_fields, actions): canonicalized_fields is a new list,
    same length/order, with every near-duplicate proper-noun spelling
    replaced by a single canonical form (case-adapted per occurrence,
    see _adapt_case); actions is a list of {"canonical": str, "variants":
    [str, ...]} dicts (empty if nothing was merged), suitable for the
    sidecar review report.

    Pure and idempotent: running it twice on its own output is a no-op.
    """
    key_total_count = {}  # key -> total occurrences, any casing
    key_first_seen = {}  # key -> earliest document-order position
    eligible_keys = set()
    position = 0
    for field in fields:
        for key, _exact_form, proper_noun_like in _tokens_in_order(field):
            key_total_count[key] = key_total_count.get(key, 0) + 1
            if key not in key_first_seen:
                key_first_seen[key] = position
            position += 1
            if proper_noun_like:
                eligible_keys.add(key)

    anchor_keys = set()
    if anchor_text:
        anchor_keys = {key for key, _exact, _proper in _tokens_in_order(anchor_text)}

    groups = _group_variants(eligible_keys)

    key_to_canonical = {}  # variant key -> canonical key
    actions = []
    for group in groups:
        anchor_matches = group & anchor_keys
        pool = anchor_matches or group

        best_count = max(key_total_count[key] for key in pool)
        top = [key for key in pool if key_total_count[key] == best_count]
        canonical_key = min(top, key=lambda key: key_first_seen[key])

        variant_keys = sorted(group - {canonical_key}, key=lambda key: key_first_seen[key])
        if not variant_keys:
            continue
        for variant_key in variant_keys:
            key_to_canonical[variant_key] = canonical_key
        actions.append(
            {
                "canonical": canonical_key.capitalize(),
                "variants": [key.capitalize() for key in variant_keys],
            }
        )

    if not key_to_canonical:
        return list(fields), []

    pattern = re.compile(
        r"\b(" + "|".join(re.escape(key) for key in key_to_canonical) + r")\b",
        re.IGNORECASE,
    )

    def _replace(match):
        original = match.group(0)
        canonical_key = key_to_canonical[original.lower()]
        return _adapt_case(canonical_key, original)

    canonicalized = [pattern.sub(_replace, field) for field in fields]
    return canonicalized, actions
