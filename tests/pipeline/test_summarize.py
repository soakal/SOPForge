"""Title/overview generation from narration (no network)."""

from pipeline.summarize import generate_title_and_overview


class _StubLLM:
    def __init__(self, reply):
        self._reply = reply
        self.last_prompt = None

    def chat(self, messages):
        self.last_prompt = messages[0]["content"]
        return self._reply


def test_title_overview_parsed_from_json():
    llm = _StubLLM('Sure: {"title": "Install the Driver", "overview": "How to install it."}')
    title, overview = generate_title_and_overview("do this then that", llm)
    assert title == "Install the Driver"
    assert overview == "How to install it."


def test_title_overview_falls_back_on_bad_reply():
    llm = _StubLLM("not json at all")
    assert generate_title_and_overview("narration", llm) == (None, None)


def test_title_overview_empty_narration():
    assert generate_title_and_overview("   ", _StubLLM("{}")) == (None, None)


def test_on_screen_texts_are_added_to_the_prompt_when_given():
    """See consistency.py's module docstring: a raw narration transcript
    can misspell an out-of-vocabulary proper noun (ASR guessing at an
    unfamiliar word), but a vision caption reads the actual on-screen
    pixels -- a stronger spelling signal the title/overview prompt should
    be told to prefer when the two disagree."""
    llm = _StubLLM('{"title": "Install the Driver", "overview": "How to install it."}')
    generate_title_and_overview(
        "narration mentioning a misspelled product name",
        llm,
        on_screen_texts=["Select the Hilscher installer.", "Click Extract."],
    )
    assert "Select the Hilscher installer." in llm.last_prompt
    assert "Click Extract." in llm.last_prompt
    assert "prefer the spelling used in these descriptions" in llm.last_prompt


def test_prompt_is_unchanged_when_on_screen_texts_omitted():
    llm = _StubLLM('{"title": "Install the Driver", "overview": "How to install it."}')
    generate_title_and_overview("do this then that", llm)
    assert "prefer the spelling used in these descriptions" not in llm.last_prompt


def test_prompt_is_unchanged_when_on_screen_texts_empty_list():
    llm = _StubLLM('{"title": "Install the Driver", "overview": "How to install it."}')
    generate_title_and_overview("do this then that", llm, on_screen_texts=[])
    assert "prefer the spelling used in these descriptions" not in llm.last_prompt
