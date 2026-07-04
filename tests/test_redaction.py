"""Redaction pass: PIL-rendered screenshot with known email/IP text (seeded),
OCR'd via Windows OCR and blurred; a password-field heuristic (driven by
manifest element metadata, not OCR — masked password text has no plaintext
to recognize) is tested separately."""

import numpy as np
from PIL import Image, ImageDraw

from capture.redact import (
    compile_patterns,
    find_pattern_regions,
    is_password_field,
    load_config,
    redact_screenshot,
)


def _render(path, lines):
    img = Image.new("RGB", (500, 150), "white")
    draw = ImageDraw.Draw(img)
    y = 10
    for text in lines:
        draw.text((10, y), text, fill="black", font_size=22)
        y += 40
    img.save(path)


def _mean_abs_diff(img_a, img_b, box):
    a = np.asarray(img_a.crop(box).convert("RGB"), dtype=int)
    b = np.asarray(img_b.crop(box).convert("RGB"), dtype=int)
    return abs(a - b).mean()


def test_ocr_matches_email_and_ipv4(tmp_path):
    png = tmp_path / "shot.png"
    _render(png, ["Contact: test@example.com", "Server: 192.168.1.1", "Hello World"])

    config = load_config()
    patterns = compile_patterns(config)
    regions = find_pattern_regions(png, patterns)
    assert len(regions) >= 2


def test_matched_regions_blurred_others_untouched(tmp_path):
    png = tmp_path / "shot.png"
    _render(png, ["Contact: test@example.com", "Server: 192.168.1.1", "Hello World"])
    original = Image.open(png).convert("RGB")

    out = tmp_path / "redacted.png"
    regions = redact_screenshot(png, out_path=out)
    assert regions

    redacted = Image.open(out).convert("RGB")
    for box in regions:
        assert _mean_abs_diff(original, redacted, box) > 5

    corner_box = (0, 0, 8, 8)  # clearly outside any matched word
    assert _mean_abs_diff(original, redacted, corner_box) < 0.5


def test_password_field_blurs_whole_element_regardless_of_ocr(tmp_path):
    png = tmp_path / "shot.png"
    _render(png, ["Hello World"])  # nothing OCR-matchable here at all
    original = Image.open(png).convert("RGB")

    element = {
        "control_type": "Edit",
        "name": "Password",
        "automation_id": "",
        "bounding_rect": [10, 5, 200, 25],
    }
    out = tmp_path / "redacted.png"
    regions = redact_screenshot(png, element=element, out_path=out)
    assert (10, 5, 200, 25) in [tuple(r) for r in regions]

    redacted = Image.open(out).convert("RGB")
    assert _mean_abs_diff(original, redacted, (10, 5, 200, 25)) > 5


def test_non_password_edit_field_not_flagged():
    config = load_config()
    element = {
        "control_type": "Edit",
        "name": "Computer name",
        "automation_id": "txtComputerName",
    }
    assert is_password_field(element, config) is False


def test_password_edit_field_flagged_by_name():
    config = load_config()
    element = {"control_type": "Edit", "name": "Password", "automation_id": ""}
    assert is_password_field(element, config) is True


def test_non_edit_control_never_flagged_even_with_password_name():
    config = load_config()
    element = {"control_type": "Text", "name": "Password", "automation_id": ""}
    assert is_password_field(element, config) is False
