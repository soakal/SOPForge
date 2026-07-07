"""Title/overview generation from narration (no network)."""

from pipeline.summarize import generate_title_and_overview


class _StubLLM:
    def __init__(self, reply):
        self._reply = reply

    def chat(self, messages):
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
