"""Highlight box drawing + title/overview generation (no network)."""

from PIL import Image

from pipeline.highlight import highlight_region
from pipeline.summarize import generate_title_and_overview


def test_highlight_draws_box_and_reports_drawn(tmp_path):
    src = tmp_path / "s.png"
    Image.new("RGB", (200, 150), (255, 255, 255)).save(src)
    out = tmp_path / "o.png"
    result, drawn = highlight_region(src, [40, 30, 120, 90], out)
    assert result == out and drawn is True
    # a red box was drawn -> the image is no longer all-white
    img = Image.open(out).convert("RGB")
    assert any(px != (255, 255, 255) for px in img.getdata())
    assert any(px[0] > 150 and px[1] < 100 and px[2] < 100 for px in img.getdata())  # red-ish


def test_highlight_ignores_bad_box(tmp_path):
    src = tmp_path / "s.png"
    Image.new("RGB", (100, 100), (255, 255, 255)).save(src)
    out = tmp_path / "o.png"
    # box out of range / degenerate -> no draw, image copied unmodified
    _result, drawn = highlight_region(src, [5000, 5000, 6000, 6000], out)
    assert drawn is False
    assert Image.open(out).convert("RGB").getpixel((50, 50)) == (255, 255, 255)


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
