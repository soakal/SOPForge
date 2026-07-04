"""Screenshot annotation (AC6): draws a red circle marker at a manifest
step's click coordinates onto its screenshot, so the rendered SOP doc shows
readers exactly where the user clicked. The circle is always centered
exactly on (x, y) — never clamped/shifted to fit the canvas — so the click
coordinate is always geometrically inside the marker, even at a corner.
PIL's ImageDraw safely clips any off-canvas portion of the ellipse on its
own; there is no need (and it would be wrong) to shrink or recenter the box
to keep it fully on-screen."""

from PIL import Image, ImageDraw


def annotate_click(image_path, x, y, out_path=None, radius=15, color=(220, 40, 40), width=4):
    """Draws a circular marker centered on (x, y). Returns the path written."""
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=color, width=width)
    out = out_path or image_path
    img.save(out)
    return out
