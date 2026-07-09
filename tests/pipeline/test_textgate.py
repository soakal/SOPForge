"""degenerate_reason: a shared, mechanical gate for local-model decoding
gone wrong (repetition loops, leaked chat-template tokens) -- used by both
vision.py's caption acceptance and narration_polish.py's rewrite gate.
Regression fixtures are real garbage pulled from an actual generated doc
(fixtures/degenerate_captions.json), alongside real good captions from the
same document that must NOT be false-flagged."""

import json
from pathlib import Path

import pytest

from pipeline.textgate import degenerate_reason

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"
_DATA = json.loads((FIXTURES / "degenerate_captions.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("text", _DATA["garbage"])
def test_real_garbage_examples_are_rejected(text):
    assert degenerate_reason(text) is not None


@pytest.mark.parametrize("text", _DATA["good"])
def test_real_good_captions_are_not_false_flagged(text):
    assert degenerate_reason(text) is None


def test_bare_leaked_special_token():
    assert degenerate_reason("<|im_start|>") is not None


def test_leaked_special_token_sibling_shapes():
    assert degenerate_reason("Click Save. <|im_end|>") is not None
    assert degenerate_reason("<|endoftext|> Click Save.") is not None


def test_glued_cjk_repetition():
    assert degenerate_reason("自动生成" * 10) is not None


def test_short_legitimate_repetition_is_not_flagged():
    assert degenerate_reason("Click Next, then click Next again.") is None


def test_implausibly_long_text_is_rejected():
    # Varied, non-repeating words so this isolates the length check alone,
    # not the repetition/dominance checks (which "word word word..." would
    # also trip on their own).
    words = [f"word{i}" for i in range(150)]
    assert degenerate_reason(" ".join(words)) is not None


def test_empty_and_short_text_is_not_flagged():
    assert degenerate_reason("") is None
    assert degenerate_reason("Click OK.") is None
