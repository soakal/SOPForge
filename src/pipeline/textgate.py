"""Degenerate-output detection: a shared, mechanical gate for spotting a
local model's decoding gone wrong (a repetition loop, a leaked chat-template
special token) before that text is trusted as a vision caption or a
narration rewrite. Pure text-in, reason-or-None-out -- no I/O, no model
calls, so it's cheap to run on every reply.

Unlike round_trip_ok (roundtrip.py), there's often no known-good text to
check substring-containment against here (a vision caption has no manifest
ground truth to compare to) -- these checks instead look for the SHAPE of
degenerate decoding itself: literal token repetition, leaked special tokens,
and single-word dominance, all of which a real, coherent sentence never
exhibits regardless of language or phrasing."""

import re
from collections import Counter

# <|...|> is a chat-template special-token shape (<|im_start|>, <|im_end|>,
# <|endoftext|>, etc.) that should never appear in model-FACING output text --
# no legitimate SOP caption/narration contains a literal angle-bracket-pipe
# sequence, so this alone is a safe, unconditional reject.
_SPECIAL_TOKEN_RE = re.compile(r"<\|[^|>]{1,32}\|>")

# Any 3-30 character chunk immediately repeated 4+ times in a row (1 initial
# + 3 repeats) -- the signature of a real decoding repetition loop. Real
# prose never immediately repeats a chunk this many times ("Click Next,
# then click Next again" repeats "Next" once, not 4 times back to back).
_REPEAT_RE = re.compile(r"(\S.{2,29}?)\1{3,}")

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

_LENGTH_CAP = 600
_WORD_DOMINANCE_MIN_WORDS = 8
_WORD_DOMINANCE_RATIO = 0.4


def degenerate_reason(text):
    """Returns a short reason string if `text` looks like degenerate model
    decoding, else None. Order matters only for which reason is reported;
    any single failing check is a reject."""
    if _SPECIAL_TOKEN_RE.search(text):
        return "leaked a chat-template special token"

    collapsed = re.sub(r"\s+", " ", text)
    if _REPEAT_RE.search(collapsed):
        return "a chunk of text repeats immediately several times in a row"

    words = [w.casefold() for w in _WORD_RE.findall(text)]
    if len(words) >= _WORD_DOMINANCE_MIN_WORDS:
        _word, count = Counter(words).most_common(1)[0]
        if count / len(words) >= _WORD_DOMINANCE_RATIO:
            return "one word dominates the text (an interleaved repetition loop)"

    if len(text) > _LENGTH_CAP:
        return f"text is implausibly long for a caption/narration ({len(text)} chars)"

    return None
