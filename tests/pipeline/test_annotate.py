"""Screenshot annotation (AC6): a red marker drawn at a manifest step's
click coordinates. Pixel assertions confirm the click coordinate is
geometrically inside the drawn marker (not just "some red pixel nearby") —
including near-edge/corner clicks, where a naive implementation could
clamp/shift the marker's center away from the click point instead of
leaving it centered and letting PIL clip the off-canvas portion."""

from pathlib import Path

from PIL import Image

from pipeline.annotate import (
    _MIN_MARKER_RADIUS,
    annotate_click,
    compute_crop_box,
    crop_to_element,
)
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


def test_default_marker_size_matches_historical_value_at_reference_width(tmp_path):
    """At the 1920px reference width (annotate.py's _MARKER_REFERENCE_WIDTH),
    the default (no radius/width passed) must reproduce the pre-scaling
    behavior exactly -- radius=15 -- so nothing changes for the common case."""
    png = tmp_path / "shot.png"
    _make_blank_png(png, width=1920, height=1080)
    x, y = 900, 500
    out = annotate_click(png, x, y, color=_COLOR)
    img = Image.open(out)
    _assert_click_point_is_inside_marker(img, x, y, radius=15)


def test_default_marker_size_scales_up_for_a_wider_image(tmp_path):
    """A 4K-wide (3840px, 2x the reference) capture should get a
    proportionally larger default marker (~radius 30), not the historical
    fixed 15px that shrinks to a couple millimeters once scaled down for
    print."""
    png = tmp_path / "shot.png"
    _make_blank_png(png, width=3840, height=2160)
    x, y = 1900, 1000
    out = annotate_click(png, x, y, color=_COLOR)
    img = Image.open(out)
    _assert_click_point_is_inside_marker(img, x, y, radius=30)


def test_default_marker_size_floors_for_a_tiny_image(tmp_path):
    """A very small/thumbnail-scale image must not scale the marker down to
    a near-invisible or zero radius."""
    png = tmp_path / "shot.png"
    _make_blank_png(png, width=100, height=75)
    x, y = 50, 37
    out = annotate_click(png, x, y, color=_COLOR)
    img = Image.open(out)
    _assert_click_point_is_inside_marker(img, x, y, radius=_MIN_MARKER_RADIUS)


def test_crop_box_falls_back_to_full_frame_with_no_bounding_rect():
    assert compute_crop_box((1920, 1080), None, (400, 300)) == (0, 0, 1920, 1080)


def test_crop_box_falls_back_to_full_frame_for_degenerate_rect():
    # zero width (right == left)
    assert compute_crop_box((1920, 1080), [400, 300, 400, 350], (400, 300)) == (0, 0, 1920, 1080)


def test_crop_box_falls_back_to_full_frame_for_off_image_rect():
    assert compute_crop_box((1920, 1080), [-500, -500, -400, -450], (400, 300)) == (
        0,
        0,
        1920,
        1080,
    )


def test_crop_box_pads_and_centers_around_element_rect():
    box = compute_crop_box((1920, 1080), [900, 500, 1000, 540], (950, 520))
    left, top, right, bottom = box
    # padded rect should be strictly larger than the raw rect, and contain it
    assert left < 900 and top < 500 and right > 1000 and bottom > 540


def test_crop_box_enforces_a_minimum_size_for_a_tiny_element():
    box = compute_crop_box((1920, 1080), [950, 520, 960, 528], (955, 524))
    left, top, right, bottom = box
    assert (right - left) >= 1920 * 0.35
    assert (bottom - top) >= 1080 * 0.35


def test_crop_box_expands_to_include_a_click_point_outside_the_rect():
    # click far outside the element's own reported bounding_rect
    box = compute_crop_box((1920, 1080), [900, 500, 1000, 540], (100, 100))
    left, top, right, bottom = box
    assert left <= 100 <= right
    assert top <= 100 <= bottom


def test_crop_box_clamps_to_image_bounds_near_a_corner():
    box = compute_crop_box((1920, 1080), [0, 0, 40, 40], (10, 10))
    left, top, right, bottom = box
    assert left >= 0 and top >= 0 and right <= 1920 and bottom <= 1080


def test_crop_to_element_writes_a_smaller_image_when_rect_is_present(tmp_path):
    png = tmp_path / "shot.png"
    _make_blank_png(png, width=1920, height=1080)
    crop_to_element(png, [900, 500, 1000, 540], (950, 520))
    img = Image.open(png)
    assert img.size != (1920, 1080)
    assert img.width < 1920 and img.height < 1080


def test_crop_to_element_leaves_full_frame_when_no_rect(tmp_path):
    png = tmp_path / "shot.png"
    _make_blank_png(png, width=1920, height=1080)
    crop_to_element(png, None, (950, 520))
    img = Image.open(png)
    assert img.size == (1920, 1080)
