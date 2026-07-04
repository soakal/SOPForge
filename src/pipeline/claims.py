"""Atomic claim extraction (invariant L4 front half, CLAUDE.md): "extract
atomic claims with timestamps first". Each transcript segment (whisper
already segments narration into natural utterance chunks) becomes one
atomic claim with a stable id and its segment's start timestamp —
deterministic, no LLM involved in extraction itself. Later stages (task-08's
claim-coverage validator, task-09's narrative multi-pass) check whether
generated prose actually covers each claim id, never re-deciding what the
claims are."""


def extract_claims(segments):
    """segments: [{"text", "start", "end"}, ...] (transcription.py's shape).
    Returns [{"claim_id", "text", "ts"}, ...] with stable claim-NNN ids in
    segment order, ts taken from each segment's start time."""
    return [
        {"claim_id": f"claim-{i + 1:03d}", "text": segment["text"], "ts": segment["start"]}
        for i, segment in enumerate(segments)
    ]
