"""Draw a highlight box on a screenshot at a vision-model-supplied bounding
box, so the reader sees exactly which UI element a step refers to (Scribe-style).
Best-effort: an out-of-range or degenerate box is ignored and the image is left
as-is -- a bad box must never break generation."""

from PIL import Image, ImageDraw

_COLOR = (230, 40, 40)


def highlight_region(src, box, out_path):
    """Copy src to out_path with a red box drawn around `box` ([x1,y1,x2,y2] in
    the image's own pixels). If the box is missing/invalid/out of bounds, just
    copies the image unmodified. Returns out_path."""
    img = Image.open(src).convert("RGB")
    w, h = img.size
    drawn = False
    if box and len(box) == 4:
        x1, y1, x2, y2 = (float(v) for v in box)
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        # A little padding so the box frames the element rather than clipping it.
        pad = max(4, int(min(w, h) * 0.006))
        x1 = max(0, int(x1) - pad)
        y1 = max(0, int(y1) - pad)
        x2 = min(w, int(x2) + pad)
        y2 = min(h, int(y2) + pad)
        # Only draw a sane box: inside the image and not absurdly large/tiny.
        if x2 > x1 + 4 and y2 > y1 + 4 and (x2 - x1) < w and (y2 - y1) < h:
            draw = ImageDraw.Draw(img)
            thickness = max(3, int(min(w, h) * 0.005))
            for t in range(thickness):
                draw.rectangle([x1 - t, y1 - t, x2 + t, y2 + t], outline=_COLOR)
            drawn = True
    img.save(out_path)
    return out_path, drawn
