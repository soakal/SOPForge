"""Atomic claim extraction (invariant L4, front half): each transcript
segment becomes one atomic claim with a stable id and timestamp."""

import json
from pathlib import Path

from pipeline.claims import extract_claims

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def test_extract_claims_from_sample_transcript():
    data = json.loads((FIXTURES / "sample-transcript.json").read_text(encoding="utf-8"))
    claims = extract_claims(data["segments"])
    assert len(claims) == len(data["segments"])
    assert [c["claim_id"] for c in claims] == [
        f"claim-{i + 1:03d}" for i in range(len(data["segments"]))
    ]


def test_claim_ids_are_stable_across_calls():
    segments = [
        {"text": "a", "start": 0.0, "end": 1.0},
        {"text": "b", "start": 1.0, "end": 2.0},
    ]
    assert extract_claims(segments) == extract_claims(segments)


def test_claim_carries_segment_start_timestamp_and_text():
    segments = [{"text": "hello", "start": 3.5, "end": 4.0}]
    claims = extract_claims(segments)
    assert claims[0]["ts"] == 3.5
    assert claims[0]["text"] == "hello"


def test_empty_segments_produce_no_claims():
    assert extract_claims([]) == []


def test_claim_order_matches_segment_order():
    segments = [
        {"text": "third-ish content but first in list", "start": 5.0, "end": 6.0},
        {"text": "second", "start": 1.0, "end": 2.0},
    ]
    claims = extract_claims(segments)
    assert claims[0]["text"] == segments[0]["text"]
    assert claims[1]["text"] == segments[1]["text"]
