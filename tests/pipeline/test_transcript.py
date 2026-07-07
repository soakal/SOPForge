"""Transcript parsing + per-step placement (.txt/.md by order/label, .json by time)."""

from pathlib import Path

import pytest

from pipeline.manifest import load_manifest
from pipeline.transcript import align_transcript_to_steps

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _manifest():
    # sample-manifest.json has step-001, step-002, step-003.
    return load_manifest(FIXTURES / "sample-manifest.json")


def test_plain_paragraphs_placed_in_order():
    content = "Open the console first.\n\nClick Save to store it.\n\nCheck the computer name."
    per_step, note = align_transcript_to_steps("notes.txt", content, _manifest())
    assert per_step["step-001"] == "Open the console first."
    assert per_step["step-002"] == "Click Save to store it."
    assert per_step["step-003"] == "Check the computer name."
    assert "in order" in note


def test_one_line_per_step_no_blank_lines_spreads_across_steps():
    # Regression: a transcript with one line per step and NO blank lines must
    # NOT collapse onto step 1 -- each line is its own step.
    content = "Open the first screen\nThen click the second\nFinally save on the third"
    per_step, note = align_transcript_to_steps("t.txt", content, _manifest())
    assert per_step == {
        "step-001": "Open the first screen",
        "step-002": "Then click the second",
        "step-003": "Finally save on the third",
    }
    assert "in order" in note


def test_numbered_list_labels_place_by_number():
    content = "1. First action.\n2. Second action.\n3. Third action."
    per_step, note = align_transcript_to_steps("t.md", content, _manifest())
    assert per_step == {
        "step-001": "First action.",
        "step-002": "Second action.",
        "step-003": "Third action.",
    }
    assert "by step label" in note


def test_markdown_step_headings_place_by_number_and_can_skip():
    content = "## Step 1\nOpen it.\n\n## Step 3\nFinish up.\n"
    per_step, _ = align_transcript_to_steps("t.md", content, _manifest())
    assert per_step["step-001"] == "Open it."
    assert per_step["step-003"] == "Finish up."
    assert "step-002" not in per_step  # skipped label is respected


def test_step_word_labels():
    content = "Step 1: click here\nStep 2: type there"
    per_step, _ = align_transcript_to_steps("t.txt", content, _manifest())
    assert per_step["step-001"] == "click here"
    assert per_step["step-002"] == "type there"


def test_paragraph_starting_with_year_is_not_a_label():
    content = "2024 was the year we started.\n\nSecond paragraph."
    per_step, _ = align_transcript_to_steps("t.txt", content, _manifest())
    # Treated as plain paragraphs (in order), not a label for step 2024.
    assert per_step["step-001"] == "2024 was the year we started."
    assert per_step["step-002"] == "Second paragraph."


def test_more_paragraphs_than_steps_overflow_to_last_step():
    content = "one\n\ntwo\n\nthree\n\nfour\n\nfive"  # 5 blocks, 3 steps
    per_step, _ = align_transcript_to_steps("t.txt", content, _manifest())
    assert per_step["step-001"] == "one"
    assert per_step["step-002"] == "two"
    assert per_step["step-003"] == "three four five"


def test_labelled_lead_in_paragraph_attaches_to_first_step():
    content = "Overview of the whole thing.\n\nStep 2: the middle bit."
    per_step, _ = align_transcript_to_steps("t.md", content, _manifest())
    assert per_step["step-001"] == "Overview of the whole thing."
    assert per_step["step-002"] == "the middle bit."


def test_multiline_labelled_block_joins_lines():
    content = "## Step 1\nfirst line\nsecond line\n"
    per_step, _ = align_transcript_to_steps("t.md", content, _manifest())
    assert per_step["step-001"] == "first line second line"


def test_json_timed_transcript_aligns_by_timestamp():
    # step offsets from session start: step-001=4.12s, step-002=19.874s, step-003=62.0s
    content = (
        '{"segments":['
        '{"text":"pre and first","start":1.0,"end":3.0},'
        '{"text":"still first","start":6.0,"end":8.0},'
        '{"text":"second now","start":25.0,"end":27.0},'
        '{"text":"third finally","start":70.0,"end":72.0}]}'
    )
    per_step, note = align_transcript_to_steps("whisper.json", content, _manifest())
    assert per_step["step-001"] == "pre and first still first"
    assert per_step["step-002"] == "second now"
    assert per_step["step-003"] == "third finally"
    assert "timed segment" in note


def test_unsupported_extension_raises():
    with pytest.raises(ValueError, match="unsupported transcript format"):
        align_transcript_to_steps("audio.mp3", "data", _manifest())


def test_empty_transcript_raises():
    with pytest.raises(ValueError, match="no usable text"):
        align_transcript_to_steps("empty.txt", "   \n\n  ", _manifest())
