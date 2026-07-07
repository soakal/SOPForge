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

# A leading step label. A line counts as a label only if it carries an
# explicit marker -- markdown heading hashes (group 1), a "step" word
# (group 2), or number punctuation like . ) : - (group 4) -- so an ordinary
# paragraph that merely starts with a number ("2024 was ...") is NOT mistaken
# for a step label. Group 3 is the number, group 5 the rest of the line.
_LABEL = re.compile(r"^\s*(#{1,6}\s*)?(step\s*)?(\d+)\s*([.)\:\-])?\s*(.*)$", re.IGNORECASE)


def _parse_text_blocks(content):
    """Parse a .txt/.md transcript into ordered narration blocks. Returns
    (blocks, labelled) where blocks is a list of (step_number_or_None, text):
    step_number is the 1-based number from a label when present, else None
    (position-based). labelled is True if ANY block carried an explicit label
    (so the caller knows to place by number vs. by order)."""
    lines = content.splitlines()
    blocks = []
    labelled = False
    current_num = None
    current = []
    saw_label_start = False

    def flush():
        text = " ".join(current).strip()
        if text:
            blocks.append((current_num, text))

    for line in lines:
        m = _LABEL.match(line)
        is_label = bool(m and (m.group(1) or m.group(2) or m.group(4)))
        if is_label:
            flush()
            current_num = int(m.group(3))
            rest = m.group(5).strip()
            current = [rest] if rest else []
            labelled = True
            saw_label_start = True
        elif not line.strip():
            # Blank line = paragraph boundary, but ONLY before the first label:
            # once labelled, blocks run until the next label, so blanks inside
            # a labelled block are just skipped.
            if not saw_label_start:
                flush()
                current_num, current = None, []
        else:
            current.append(line.strip())
    flush()
    return blocks, labelled


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
    data = json.loads(content)
    raw = data["segments"] if isinstance(data, dict) else data
    segments = []
    for seg in raw:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start = float(seg.get("start", 0.0))
        segments.append({"text": text, "start": start})
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
    per_step = {}
    for seg in segments:
        chosen = step_offsets[0][0]
        for step_id, offset in step_offsets:
            if offset <= seg["start"]:
                chosen = step_id
            else:
                break
        per_step.setdefault(chosen, []).append(seg["text"])
    return {sid: " ".join(texts) for sid, texts in per_step.items()}


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
        note = f"{len(blocks)} transcript block(s) placed {how} across {len(per_step)} step(s)"
    elif ext == "json":
        segments = _parse_json_segments(content)
        if not segments:
            raise ValueError("transcript contained no usable text")
        per_step = _place_timed_segments(manifest, segments)
        note = f"{len(segments)} timed segment(s) placed across {len(per_step)} step(s)"
    else:
        raise ValueError(f"unsupported transcript format: .{ext} (use .txt, .md, or .json)")
    return per_step, note
