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


def test_single_run_on_line_collapses_to_step_1_with_a_loud_warning():
    """Regression from a real report: a transcript written as one unbroken
    line (no blank lines between what should be separate steps' narration,
    no 'Step N:' labels) parses to exactly one block, which -- correctly,
    per how placement is documented -- lands entirely on step 1. That's not
    a bug in the placement logic; the note must say so loudly rather than
    read like an ordinary 1-block transcript."""
    content = (
        "open file explorer then open the c drive then goto users then select "
        "the user that is on your system then goto the folder then rename the file"
    )
    per_step, note = align_transcript_to_steps("t.md", content, _manifest())
    assert per_step == {"step-001": content}
    assert "WARNING" in note
    assert "step 1" in note


def test_single_line_transcript_on_a_single_step_manifest_is_not_a_warning():
    # One block landing on step 1 is entirely normal when there's only one
    # step to begin with -- must not be flagged.
    from pipeline.photo_build import synthetic_manifest_dict

    md = synthetic_manifest_dict("T", ["001.png"], "2026-01-01T00:00:00Z")
    md["session"]["id"] = "one-step-session"
    manifest = load_manifest(md)
    per_step, note = align_transcript_to_steps("t.md", "just one line of narration", manifest)
    assert per_step == {manifest.steps[0].id: "just one line of narration"}
    assert "WARNING" not in note


def test_single_labelled_block_is_not_a_warning():
    # An intentional single "Step 1: ..." label is a deliberate choice, not
    # an accidental collapse -- must not be flagged even with multiple steps.
    per_step, note = align_transcript_to_steps("t.md", "Step 1: just this one note.", _manifest())
    assert per_step == {"step-001": "just this one note."}
    assert "WARNING" not in note


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


def test_numberish_prose_not_treated_as_labels():
    # Regression: "10:30", "1.5 million", "3-4 minutes" must NOT be mistaken for
    # step labels (they previously flipped the whole file into label mode and
    # misplaced every paragraph). They stay plain prose, placed in order.
    content = "10:30 we open the app.\n\nWe processed 1.5 million records.\n\nIt took 3-4 minutes."
    per_step, note = align_transcript_to_steps("t.txt", content, _manifest())
    assert per_step["step-001"] == "10:30 we open the app."
    assert per_step["step-002"] == "We processed 1.5 million records."
    assert per_step["step-003"] == "It took 3-4 minutes."
    assert "in order" in note


def test_malformed_json_transcript_raises_valueerror():
    # Well-formed JSON of the wrong shape must be a clean ValueError (-> 400),
    # not a KeyError/TypeError/AttributeError 500.
    for bad in ('{"foo": 1}', '"just a string"', "[1, 2, 3]", "123"):
        with pytest.raises(ValueError):
            align_transcript_to_steps("t.json", bad, _manifest())


def test_timed_transcript_on_synthetic_manifest_distributes_positionally():
    # A photo-build synthetic manifest gives every step the SAME timestamp, so
    # timed placement can't use timing -- it must fall back to positional
    # (segment i -> step i), not dump every segment on the last step.
    from pipeline.photo_build import synthetic_manifest_dict

    md = synthetic_manifest_dict("T", ["001.png", "002.png", "003.png"], "2026-01-01T00:00:00Z")
    md["session"]["id"] = "photo-session"  # the server sets this before loading
    manifest = load_manifest(md)
    sids = [s.id for s in manifest.steps]
    content = (
        '{"segments":['
        '{"text":"first","start":1.0},'
        '{"text":"second","start":2.0},'
        '{"text":"third","start":3.0}]}'
    )
    per_step, _ = align_transcript_to_steps("w.json", content, manifest)
    assert per_step[sids[0]] == "first"
    assert per_step[sids[1]] == "second"
    assert per_step[sids[2]] == "third"


def test_unsupported_extension_raises():
    with pytest.raises(ValueError, match="unsupported transcript format"):
        align_transcript_to_steps("audio.mp3", "data", _manifest())


def test_empty_transcript_raises():
    with pytest.raises(ValueError, match="no usable text"):
        align_transcript_to_steps("empty.txt", "   \n\n  ", _manifest())
