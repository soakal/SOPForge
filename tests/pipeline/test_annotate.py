"""Screenshot annotation (AC6): a red marker drawn at a manifest step's
click coordinates. Pixel assertions confirm the marker is actually drawn on
canvas (not just that the coordinate math claims it should be), including
for near-edge/corner clicks where a naive implementation could clip it off."""

from pathlib import Path

from PIL import Image

from pipeline.annotate import annotate_click
from pipeline.manifest import load_manifest

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"
_COLOR = (220, 40, 40)


def _make_blank_png(path, width=200, height=150, color=(255, 255, 255)):
    Image.new("RGB", (width, height), color).save(path)


def _has_marker_color_near(img, x, y, radius, color, tolerance=30):
    """True if any pixel within radius+2 of (x, y) matches color closely —
    proves the marker was actually drawn there."""
    for dx in range(-radius - 2, radius + 3):
        for dy in range(-radius - 2, radius + 3):
            px, py = x + dx, y + dy
            if 0 <= px < img.width and 0 <= py < img.height:
                r, g, b = img.getpixel((px, py))
                if (
                    abs(r - color[0]) <= tolerance
                    and abs(g - color[1]) <= tolerance
                    and abs(b - color[2]) <= tolerance
                ):
                    return True
    return False


def test_marker_drawn_at_center_click(tmp_path):
    png = tmp_path / "shot.png"
    _make_blank_png(png)
    x, y = 100, 75
    out = annotate_click(png, x, y, radius=15, color=_COLOR)
    img = Image.open(out)
    assert _has_marker_color_near(img, x, y, radius=15, color=_COLOR)


def test_marker_drawn_near_top_left_corner(tmp_path):
    png = tmp_path / "shot.png"
    _make_blank_png(png)
    x, y = 2, 3
    out = annotate_click(png, x, y, radius=15, color=_COLOR)
    img = Image.open(out)
    assert _has_marker_color_near(img, x, y, radius=15, color=_COLOR)


def test_marker_drawn_near_bottom_right_corner(tmp_path):
    png = tmp_path / "shot.png"
    width, height = 200, 150
    _make_blank_png(png, width=width, height=height)
    x, y = width - 2, height - 3
    out = annotate_click(png, x, y, radius=15, color=_COLOR)
    img = Image.open(out)
    assert _has_marker_color_near(img, x, y, radius=15, color=_COLOR)


def test_marker_drawn_at_exact_corner_pixel(tmp_path):
    png = tmp_path / "shot.png"
    _make_blank_png(png)
    out = annotate_click(png, 0, 0, radius=15, color=_COLOR)
    img = Image.open(out)
    assert _has_marker_color_near(img, 0, 0, radius=15, color=_COLOR)


def test_marker_does_not_appear_far_from_click(tmp_path):
    png = tmp_path / "shot.png"
    _make_blank_png(png)
    annotate_click(png, 20, 20, radius=10, color=_COLOR, out_path=png)
    img = Image.open(png)
    assert img.getpixel((190, 140)) == (255, 255, 255)


def test_annotate_uses_real_manifest_click_coords(tmp_path):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]  # click at (412, 233)

    png = tmp_path / "shot.png"
    _make_blank_png(png, width=1920, height=1080)
    out = annotate_click(png, step.screen.x, step.screen.y, radius=15, color=_COLOR)
    img = Image.open(out)
    assert _has_marker_color_near(img, step.screen.x, step.screen.y, radius=15, color=_COLOR)
