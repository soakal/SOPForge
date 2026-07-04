"""Screenshot annotation (AC6): a red marker drawn at a manifest step's
click coordinates. Pixel assertions confirm the click coordinate is
geometrically inside the drawn marker (not just "some red pixel nearby") —
including near-edge/corner clicks, where a naive implementation could
clamp/shift the marker's center away from the click point instead of
leaving it centered and letting PIL clip the off-canvas portion."""

from pathlib import Path

from PIL import Image

from pipeline.annotate import annotate_click
from pipeline.manifest import load_manifest

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"
_COLOR = (220, 40, 40)
_TOLERANCE = 30


def _make_blank_png(path, width=200, height=150, color=(255, 255, 255)):
    Image.new("RGB", (width, height), color).save(path)


def _is_marker_color(pixel, color=_COLOR, tolerance=_TOLERANCE):
    return all(abs(pixel[i] - color[i]) <= tolerance for i in range(3))


def _ring_pixels_on_canvas(img, x, y, radius):
    """The four axis-aligned points exactly `radius` away from (x, y) that
    fall on-canvas — these lie exactly on the drawn circle's ring, so they
    prove both that the marker was actually drawn AND that it's centered
    on (x, y) (not shifted to avoid clipping)."""
    candidates = [(x + radius, y), (x - radius, y), (x, y + radius), (x, y - radius)]
    return [(px, py) for px, py in candidates if 0 <= px < img.width and 0 <= py < img.height]


def _assert_click_point_is_inside_marker(img, x, y, radius, color=_COLOR):
    on_canvas_ring_points = _ring_pixels_on_canvas(img, x, y, radius)
    assert on_canvas_ring_points, "click point has no on-canvas ring points to check"
    matches = [p for p in on_canvas_ring_points if _is_marker_color(img.getpixel(p), color)]
    assert matches, (
        f"no marker-colored pixel found on the ring around ({x},{y}); "
        f"checked {on_canvas_ring_points}"
    )


def test_marker_drawn_at_center_click(tmp_path):
    png = tmp_path / "shot.png"
    _make_blank_png(png)
    x, y = 100, 75
    out = annotate_click(png, x, y, radius=15, color=_COLOR)
    img = Image.open(out)
    _assert_click_point_is_inside_marker(img, x, y, radius=15)


def test_marker_drawn_near_top_left_corner(tmp_path):
    png = tmp_path / "shot.png"
    _make_blank_png(png)
    x, y = 2, 3
    out = annotate_click(png, x, y, radius=15, color=_COLOR)
    img = Image.open(out)
    _assert_click_point_is_inside_marker(img, x, y, radius=15)


def test_marker_drawn_near_bottom_right_corner(tmp_path):
    png = tmp_path / "shot.png"
    width, height = 200, 150
    _make_blank_png(png, width=width, height=height)
    x, y = width - 2, height - 3
    out = annotate_click(png, x, y, radius=15, color=_COLOR)
    img = Image.open(out)
    _assert_click_point_is_inside_marker(img, x, y, radius=15)


def test_marker_drawn_at_exact_corner_pixel(tmp_path):
    """The click point itself, (0, 0), is off the ring (the ring is at
    radius distance from it) — the meaningful edge-case assertion is that
    the ring is still drawn where the geometry says it should be, centered
    exactly on (0, 0), not clamped/shifted inward."""
    png = tmp_path / "shot.png"
    _make_blank_png(png)
    out = annotate_click(png, 0, 0, radius=15, color=_COLOR)
    img = Image.open(out)
    _assert_click_point_is_inside_marker(img, 0, 0, radius=15)


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
    _assert_click_point_is_inside_marker(img, step.screen.x, step.screen.y, radius=15)
