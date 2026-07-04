"""Screenshot annotation (AC6): draws a red circle marker at a manifest
step's click coordinates onto its screenshot, so the rendered SOP doc shows
readers exactly where the user clicked. The marker's bounding box is
clamped to the image so it's never (partially) clipped off-canvas for a
near-edge or corner click — the click coordinate itself always stays within
the clamped box regardless."""

from PIL import Image, ImageDraw


def annotate_click(image_path, x, y, out_path=None, radius=15, color=(220, 40, 40), width=4):
    """Draws a circular marker centered on (x, y). Returns the path written."""
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)

    left = max(0, x - radius)
    top = max(0, y - radius)
    right = min(img.width - 1, x + radius)
    bottom = min(img.height - 1, y + radius)

    draw.ellipse((left, top, right, bottom), outline=color, width=width)
    out = out_path or image_path
    img.save(out)
    return out
