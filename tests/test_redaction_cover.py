"""Phase 6e (phases/06-factory-parity.md AC5): redaction hits are covered by a
solid box labelled with what was hidden, leaving no pixel of the original
text. OCR is stubbed here (Windows OCR can't run off Windows), so this runs
everywhere; tests/test_redaction.py covers the real OCR path on Windows."""

from PIL import Image, ImageDraw

import capture.redact as redact
from capture.redact import (
    COVER_FILL,
    COVER_PAD_PX,
    COVER_TEXT,
    cover_regions,
    load_config,
    pattern_label,
    redact_screenshot_tagged,
)

SECRET = (255, 0, 0)  # a colour that appears nowhere else in the test image


def _shot(path):
    img = Image.new("RGB", (600, 200), "white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 20), "admin@example.com", fill=SECRET, font_size=24)
    draw.text((20, 80), "10.0.0.12", fill=SECRET, font_size=24)
    draw.text((20, 140), "Hello World", fill="black", font_size=24)
    img.save(path)
    return img


def _pixels(img, box=None):
    region = img.crop(box) if box else img
    return [region.getpixel((x, y)) for y in range(region.height) for x in range(region.width)]


def _stub_ocr(monkeypatch, words):
    monkeypatch.setattr(redact, "ocr_words", lambda _path: words)


def test_labels_for_configured_patterns():
    assert pattern_label("email") == "email"
    assert pattern_label("ipv4") == "IP address"
    assert pattern_label("employee_id") == "employee id"


def test_hits_are_covered_and_labelled_others_untouched(tmp_path, monkeypatch):
    png = tmp_path / "shot.png"
    original = _shot(png)
    email_box, ip_box, hello_box = (18, 18, 260, 50), (18, 78, 140, 110), (18, 138, 200, 170)
    _stub_ocr(
        monkeypatch,
        [("admin@example.com", email_box), ("10.0.0.12", ip_box), ("Hello", hello_box)],
    )
    tagged = redact_screenshot_tagged(png, config=load_config())
    assert [t["reason"] for t in tagged] == ["pattern", "pattern"]
    redacted = Image.open(png).convert("RGB")

    for box in (email_box, ip_box):
        padded = (
            box[0] - COVER_PAD_PX,
            box[1] - COVER_PAD_PX,
            box[2] + COVER_PAD_PX,
            box[3] + COVER_PAD_PX,
        )
        assert list(padded) in [t["region"] for t in tagged]
        pixels = _pixels(redacted, padded)
        assert SECRET not in pixels  # nothing of the original text survives
        assert COVER_FILL in pixels and COVER_TEXT in pixels  # box + its label
    assert SECRET not in _pixels(redacted)
    assert _pixels(redacted, hello_box) == _pixels(original, hello_box)


def test_password_field_covers_exact_rect_without_ocr_hits(tmp_path, monkeypatch):
    png = tmp_path / "shot.png"
    _shot(png)
    _stub_ocr(monkeypatch, [])
    element = {
        "control_type": "Edit",
        "name": "Password",
        "automation_id": "",
        "bounding_rect": [300, 20, 580, 60],
    }
    tagged = redact_screenshot_tagged(png, element=element, config=load_config())
    assert tagged == [{"region": [300, 20, 580, 60], "reason": "password_heuristic"}]
    pixels = _pixels(Image.open(png).convert("RGB"), (300, 20, 580, 60))
    assert set(pixels) <= {COVER_FILL, COVER_TEXT} | set(_antialias(pixels))
    assert COVER_TEXT in pixels


def _antialias(pixels):
    """Label-edge pixels: blends of the fill and the text colour."""
    return [
        p
        for p in pixels
        if all(min(f, t) <= c <= max(f, t) for c, f, t in zip(p, COVER_FILL, COVER_TEXT))
    ]


def test_tiny_box_is_covered_without_a_label(tmp_path):
    png = tmp_path / "shot.png"
    _shot(png)
    applied = cover_regions(png, [(20, 20, 30, 26)], labels=["IP address"])
    assert applied == [(20, 20, 30, 26)]
    assert set(_pixels(Image.open(png).convert("RGB"), (20, 20, 30, 26))) == {COVER_FILL}


def test_regions_clamped_to_image_and_empty_dropped(tmp_path):
    png = tmp_path / "shot.png"
    _shot(png)
    applied = cover_regions(png, [(590, 190, 700, 300), (50, 50, 50, 60)], pad=2)
    assert applied == [(588, 188, 600, 200)]
