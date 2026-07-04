"""Post-capture redaction pass: OCR each screenshot (Windows.Media.Ocr via
winsdk) for text matching configured patterns (email, IPv4) and Gaussian-blur
those regions. Password fields never show their real value as OCR-able text
(they're masked on screen), so they're caught separately via a UIA-metadata
heuristic on the manifest step's element — control_type "Edit" plus a
password-ish name/automation_id — which blurs the whole element bounding box.
"""

import asyncio
import re
import tomllib
from pathlib import Path

from PIL import Image, ImageFilter
from winsdk.windows.graphics.imaging import BitmapDecoder
from winsdk.windows.media.ocr import OcrEngine
from winsdk.windows.storage import StorageFile

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "redaction.toml"


def load_config(config_path=DEFAULT_CONFIG_PATH):
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def compile_patterns(config):
    return {
        name: re.compile(pattern, re.IGNORECASE)
        for name, pattern in config.get("patterns", {}).items()
    }


def is_password_field(element, config):
    if (element.get("control_type") or "").lower() != "edit":
        return False
    keywords = config.get("password_heuristic", {}).get("name_contains", [])
    haystack = f"{element.get('name', '')} {element.get('automation_id', '')}".lower()
    return any(keyword in haystack for keyword in keywords)


async def _ocr_words(image_path):
    """(text, (left, top, right, bottom)) for every word OCR finds in the
    image at image_path. Reads via WinRT's BitmapDecoder (a real file
    on disk) rather than hand-building a SoftwareBitmap from raw pixel
    bytes — the latter silently produced empty OCR results in testing."""
    file = await StorageFile.get_file_from_path_async(str(image_path))
    stream = await file.open_async(0)  # FileAccessMode.READ
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return []
    result = await engine.recognize_async(bitmap)
    words = []
    for line in result.lines:
        for word in line.words:
            rect = word.bounding_rect
            box = (
                int(rect.x),
                int(rect.y),
                int(rect.x + rect.width),
                int(rect.y + rect.height),
            )
            words.append((word.text, box))
    return words


def ocr_words(image_path):
    return asyncio.run(_ocr_words(image_path))


def find_pattern_regions(image_path, patterns):
    """Bounding boxes of every OCR'd word matching any configured pattern."""
    regions = []
    for text, box in ocr_words(image_path):
        if any(pattern.search(text) for pattern in patterns.values()):
            regions.append(box)
    return regions


def blur_regions(image_path, regions, out_path=None, radius=12):
    """Gaussian-blurs each region of the image at image_path (in place, or to
    out_path if given). Returns the regions actually blurred (after clamping
    to image bounds and dropping empty ones)."""
    img = Image.open(image_path).convert("RGB")
    applied = []
    for left, top, right, bottom in regions:
        left, top = max(left, 0), max(top, 0)
        right, bottom = min(right, img.width), min(bottom, img.height)
        if right <= left or bottom <= top:
            continue
        region = img.crop((left, top, right, bottom))
        blurred = region.filter(ImageFilter.GaussianBlur(radius))
        img.paste(blurred, (left, top))
        applied.append((left, top, right, bottom))
    img.save(out_path or image_path)
    return applied


def redact_screenshot(image_path, element=None, config=None, out_path=None):
    """Full redaction pass for one screenshot: OCR-matched pattern regions,
    plus the element's whole bounding box if it looks like a password field."""
    config = config or load_config()
    patterns = compile_patterns(config)
    regions = find_pattern_regions(image_path, patterns)
    if element is not None and is_password_field(element, config):
        rect = element.get("bounding_rect")
        if rect:
            regions.append(tuple(rect))
    return blur_regions(image_path, regions, out_path=out_path)
