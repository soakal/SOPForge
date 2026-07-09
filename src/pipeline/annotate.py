"""Screenshot annotation (AC6): draws a red circle marker at a manifest
step's click coordinates onto its screenshot, so the rendered SOP doc shows
readers exactly where the user clicked. The circle is always centered
exactly on (x, y) — never clamped/shifted to fit the canvas — so the click
coordinate is always geometrically inside the marker, even at a corner.
PIL's ImageDraw safely clips any off-canvas portion of the ellipse on its
own; there is no need (and it would be wrong) to shrink or recenter the box
to keep it fully on-screen.

Also crops the annotated screenshot down to the clicked element's
neighborhood (compute_crop_box) — a real SOP crops tight to the relevant UI
region rather than shipping a full, possibly multi-monitor, desktop
screenshot per step. Cropping runs AFTER the marker is drawn (a plain PIL
.crop() on the already-annotated image), so the marker's pixel position
needs no separate remapping — it's simply wherever cropping leaves it."""

from PIL import Image, ImageDraw

# Marker size/stroke scale with image width (see annotate_click); this is
# the reference resolution those historical fixed values (radius=15,
# width=4) were tuned for, so behavior is unchanged at that width and scales
# proportionally above/below it.
_MARKER_REFERENCE_WIDTH = 1920
_MIN_MARKER_RADIUS = 8
_MIN_MARKER_STROKE = 2

CROP_PAD_PX = 80
CROP_MIN_WIDTH_RATIO = 0.35
CROP_MIN_HEIGHT_RATIO = 0.35


def annotate_click(image_path, x, y, out_path=None, radius=None, color=(220, 40, 40), width=None):
    """Draws a circular marker centered on (x, y), sized proportionally to
    the image's width (so it stays visible on a 4K capture scaled down for
    print, not the historically fixed 15px that shrinks to a couple
    millimeters). Pass explicit `radius`/`width` to override. Returns the
    path written."""
    img = Image.open(image_path).convert("RGB")
    scale = img.width / _MARKER_REFERENCE_WIDTH
    if radius is None:
        radius = max(_MIN_MARKER_RADIUS, round(15 * scale))
    if width is None:
        width = max(_MIN_MARKER_STROKE, round(4 * scale))
    draw = ImageDraw.Draw(img)
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=color, width=width)
    out = out_path or image_path
    img.save(out)
    return out


def compute_crop_box(img_size, bounding_rect, click_xy, pad_px=CROP_PAD_PX):
    """Returns (left, top, right, bottom): a crop region around
    `bounding_rect` (an element/window rect, in the same pixel frame as
    click_xy — see recorder.py's monitor-local translation) padded for
    visual context, with a floor on crop size so a tiny icon doesn't crop to
    a postage stamp with no surrounding UI, and the click point always
    guaranteed to fall inside it. Falls back to the full frame
    `(0, 0, width, height)` whenever bounding_rect is missing, degenerate
    (zero/negative width or height), or doesn't overlap the image at all —
    a bad rect must never crop out the only thing that matters."""
    width, height = img_size
    x, y = click_xy

    if not bounding_rect:
        return (0, 0, width, height)
    left, top, right, bottom = bounding_rect
    if (
        right <= left
        or bottom <= top
        or right <= 0
        or bottom <= 0
        or left >= width
        or top >= height
    ):
        return (0, 0, width, height)

    left, top, right, bottom = left - pad_px, top - pad_px, right + pad_px, bottom + pad_px

    min_w, min_h = width * CROP_MIN_WIDTH_RATIO, height * CROP_MIN_HEIGHT_RATIO
    cx, cy = (left + right) / 2, (top + bottom) / 2
    if (right - left) < min_w:
        left, right = cx - min_w / 2, cx + min_w / 2
    if (bottom - top) < min_h:
        top, bottom = cy - min_h / 2, cy + min_h / 2

    # The click point (and its marker) must always end up inside the crop --
    # expand outward (never shift) if it doesn't, since a click can legally
    # land just outside the UIA-reported bounding_rect for its own element.
    if x < left:
        left = x - pad_px
    if x > right:
        right = x + pad_px
    if y < top:
        top = y - pad_px
    if y > bottom:
        bottom = y + pad_px

    left, top = max(0, left), max(0, top)
    right, bottom = min(width, right), min(height, bottom)
    if right <= left or bottom <= top:
        return (0, 0, width, height)
    return (int(left), int(top), int(right), int(bottom))


def crop_to_element(image_path, bounding_rect, click_xy, out_path=None):
    """Crops the image at image_path to compute_crop_box's region and saves
    it (in place by default). A no-op-equivalent full-frame "crop" when
    bounding_rect is absent/degenerate still runs through PIL (cheap,
    keeps this function's behavior uniform rather than branching around it)."""
    img = Image.open(image_path)
    box = compute_crop_box(img.size, bounding_rect, click_xy)
    cropped = img.crop(box)
    cropped.save(out_path or image_path)
    return out_path or image_path
