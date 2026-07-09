"""Uploaded-transcript parsing + placement under manifest steps.

A user can upload a narration transcript (.txt or .md) alongside the
manifest/screenshots; its text is placed under the step it describes so the
narration lands "in the proper location", not in one lump.

Because a plain .txt/.md file has no timestamps, placement is by ORDER, with
two ways to write the transcript:

  1. LABELLED (most reliable) -- start each block with a step label, any of:
        Step 1: ...        1. ...        1) ...        ## Step 1
     The number picks the step, so blocks can be in any order and steps can be
     skipped. This is the recommended format.

  2. PLAIN paragraphs -- blank-line-separated paragraphs with no labels are
     assigned to steps in order (1st paragraph -> step 1, ...). Extra
     paragraphs beyond the last step append to it.

A timestamped .json transcript (the faster-whisper shape, transcription.py) is
also accepted and aligned by time -- used by the optional audio path. All of
this is deterministic string work: the transcript's own words go under the
step verbatim, never model-invented text.
"""

import json
import re
from datetime import datetime

# A leading step label is EITHER an explicit "step N" (optionally under
# markdown heading hashes: "Step 1", "Step 1:", "## Step 1") OR a numbered-list
# item ("1. text", "2) text" -- number, then . or ), then a space). Requiring
# the "step" word or a real list marker (punctuation + space) keeps ordinary
# prose from being mistaken for labels: "1.5 million", "10:30 we open", "3-4
# minutes", and a plain "## 2024 Results" heading are all NOT labels.
_STEP_LABEL = re.compile(r"^\s*#{0,6}\s*step\s+(\d+)\s*[:.)\-]?\s*(.*)$", re.IGNORECASE)
_NUM_LIST = re.compile(r"^\s*(\d+)[.)]\s+(\S.*)$")


def _label_match(line):
    """Return (step_number, rest_text) if the line is a step label, else None."""
    m = _STEP_LABEL.match(line)
    if m:
        return int(m.group(1)), m.group(2).strip()
    m = _NUM_LIST.match(line)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return None


def _parse_text_blocks(content):
    """Parse a .txt/.md transcript into ordered narration blocks. Returns
    (blocks, labelled) where blocks is a list of (step_number_or_None, text):
    step_number is the 1-based number from a label when present, else None
    (position-based). labelled is True if ANY block carried an explicit label
    (so the caller knows to place by number vs. by order).

    Block boundaries, in priority order:
      1. Explicit step labels ("Step 1:", "1.", "## Step 1") -> one block each.
      2. Otherwise, blank-line-separated paragraphs, IF there are 2+ of them
         (lines within a paragraph are joined).
      3. Otherwise (no blank lines / a single paragraph), one block PER LINE --
         so a transcript written as one line per step, with no blank lines,
         still spreads across the steps instead of collapsing onto step 1.
    """
    lines = content.splitlines()

    # 1. Label mode.
    if any(_label_match(ln) for ln in lines):
        blocks = []
        current_num, current = None, []

        def flush():
            text = " ".join(current).strip()
            if text:
                blocks.append((current_num, text))

        for line in lines:
            label = _label_match(line)
            if label:
                flush()
                current_num, rest = label
                current = [rest] if rest else []
            elif line.strip():
                current.append(line.strip())
            # blank lines just end nothing in label mode (blocks run to next label)
        flush()
        return blocks, True

    # 2. Blank-line paragraphs (2+).
    paragraphs = [
        " ".join(ln.strip() for ln in para.splitlines() if ln.strip())
        for para in re.split(r"\n[ \t]*\n", content)
    ]
    paragraphs = [p for p in paragraphs if p]
    if len(paragraphs) >= 2:
        return [(None, p) for p in paragraphs], False

    # 3. One block per non-empty line.
    line_blocks = [ln.strip() for ln in lines if ln.strip()]
    return [(None, ln) for ln in line_blocks], False


def _place_text_blocks(manifest, blocks, labelled):
    """Map parsed text blocks onto step ids. Labelled blocks go to the step
    with that number (clamped to the last step); unlabelled blocks fill steps
    in order, with any overflow appended to the last step. Returns
    {step_id: text}."""
    step_ids = manifest.step_ids()
    if not step_ids:
        return {}
    per_step = {}

    def add(idx, text):
        idx = max(0, min(idx, len(step_ids) - 1))
        sid = step_ids[idx]
        per_step[sid] = (per_step[sid] + " " + text).strip() if sid in per_step else text

    if labelled:
        for num, text in blocks:
            # A labelled transcript may still mix in an unlabelled lead-in
            # paragraph (num=None) -- attach it to the first step.
            add((num - 1) if num else 0, text)
    else:
        for i, (_num, text) in enumerate(blocks):
            add(i, text)
    return per_step


def _ts_to_seconds(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def _parse_json_segments(content):
    # Well-formed JSON of the wrong SHAPE ({"foo":1}, a bare string, a list of
    # strings) would otherwise raise KeyError/TypeError/AttributeError deep in
    # here and 500 the upload -- normalize all of those to ValueError so the
    # caller returns a clean 400 (its documented contract).
    try:
        data = json.loads(content)
        raw = data["segments"] if isinstance(data, dict) else data
        segments = []
        for seg in raw:
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            segments.append({"text": text, "start": float(seg.get("start", 0.0))})
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError(f"malformed JSON transcript: {exc}") from exc
    return sorted(segments, key=lambda s: s["start"])


def _parse_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _place_timed_segments(manifest, segments):
    if not manifest.steps:
        return {}
    session_start = _parse_iso(manifest.session.started_utc)
    step_offsets = [
        (step.id, (_parse_iso(step.ts_utc) - session_start).total_seconds())
        for step in manifest.steps
    ]
    step_ids = [sid for sid, _ in step_offsets]
    per_step = {}

    def add(sid, text):
        per_step[sid] = (per_step[sid] + " " + text).strip() if sid in per_step else text

    # Degenerate timing (e.g. the synthetic photo-mode manifest gives every step
    # the SAME ts, so all offsets are equal) -- timestamp placement is
    # meaningless, so fall back to positional (segment i -> step i, overflow to
    # the last step) instead of piling everything on one step.
    if len({off for _, off in step_offsets}) <= 1:
        for i, seg in enumerate(segments):
            add(step_ids[min(i, len(step_ids) - 1)], seg["text"])
        return per_step

    # A segment belongs to the step with the LARGEST offset that is <= its start
    # time; among tied offsets, the FIRST such step (not the last).
    for seg in segments:
        chosen, best = step_ids[0], None
        for sid, offset in step_offsets:
            if offset <= seg["start"] and (best is None or offset > best):
                chosen, best = sid, offset
        add(chosen, seg["text"])
    return per_step


def align_transcript_to_steps(filename, content, manifest):
    """Parse an uploaded transcript and return (per_step, note):
      per_step -- {step_id: narration text} for steps that got narration
      note     -- a short human-readable placement summary for the review report
    Dispatches on extension: .txt/.md -> order/label placement, .json ->
    timestamp placement. Raises ValueError on an unusable file (bad extension
    or no text) so the caller returns a clear 400."""
    ext = (filename or "").lower().rsplit(".", 1)[-1] if "." in (filename or "") else ""
    if ext in ("txt", "md"):
        blocks, labelled = _parse_text_blocks(content)
        if not blocks:
            raise ValueError("transcript contained no usable text")
        per_step = _place_text_blocks(manifest, blocks, labelled)
        how = "by step label" if labelled else "in order"
        note = (
            f"{len(blocks)} transcript block(s) placed {how} across "
            f"{len(per_step)} of {len(manifest.steps)} step(s)"
        )
        # The single most common way this silently goes wrong: an unlabelled
        # transcript written as one run-on line/paragraph (no blank lines
        # between what should be separate steps' narration, no "Step N:"
        # labels) parses to exactly one block, which -- correctly, per how
        # placement is documented to work -- lands entirely on step 1. That's
        # not a bug in the placement logic; it's the transcript's own format
        # giving the deterministic splitter nothing to split on. Flag it
        # loudly rather than let it look like a normal 1-block transcript.
        if not labelled and len(blocks) == 1 and len(manifest.steps) > 1:
            note += (
                " -- WARNING: the whole transcript landed on step 1 because it has "
                "no blank lines between different steps' narration and no 'Step N:' "
                "labels for the splitter to use. Add either (blank lines between "
                "each step's narration, or a 'Step N:' label per block) and "
                "re-upload to distribute it across the steps it actually describes."
            )
    elif ext == "json":
        segments = _parse_json_segments(content)
        if not segments:
            raise ValueError("transcript contained no usable text")
        per_step = _place_timed_segments(manifest, segments)
        note = f"{len(segments)} timed segment(s) placed across {len(per_step)} step(s)"
    else:
        raise ValueError(f"unsupported transcript format: .{ext} (use .txt, .md, or .json)")
    return per_step, note
