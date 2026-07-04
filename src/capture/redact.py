"""Post-capture redaction pass: OCR each screenshot (Windows.Media.Ocr via
winsdk) for text matching configured patterns (email, IPv4) and Gaussian-blur
those regions. Password fields never show their real value as OCR-able text
(they're masked on screen), so they're caught separately via a UIA-metadata
heuristic on the manifest step's element — control_type "Edit" plus a
password-ish name/automation_id — which blurs the whole element bounding box.
"""

import re
import sys
import tomllib
from pathlib import Path

from PIL import Image, ImageFilter

# winsdk's WinRT projection modules are deferred (imported inside _ocr_words,
# not here) — importing winsdk.windows.storage alone drags in most of the
# WinRT metadata tree (applicationmodel, background, contacts, ui.text, ...)
# and cost ~500ms at cold start, which is otherwise unused until the first
# screenshot is actually redacted. See scripts/verify_exe.py (criterion 4).


def _app_root():
    """Repo root in dev mode; the PyInstaller onefile extraction directory
    (sys._MEIPASS) when frozen — sopforge.spec bundles config/redaction.toml
    into that same 'config' subdirectory via its `datas` entry."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent.parent


DEFAULT_CONFIG_PATH = _app_root() / "config" / "redaction.toml"

_WORD_RE = re.compile(r"[a-z0-9]+")


class OcrUnavailableError(RuntimeError):
    """Raised when no OCR engine/language pack is available. A redaction
    engine must never silently skip pattern matching — the caller has to
    decide how to handle it (e.g. fail the capture, or fall back to
    UIA-metadata-only redaction for that step), not get an unredacted image
    with no signal that anything was skipped."""


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
    keywords = {k.lower() for k in config.get("password_heuristic", {}).get("name_contains", [])}
    raw = f"{element.get('name', '')} {element.get('automation_id', '')}"
    # Split camelCase boundaries (txtPassword -> txt Password) *before*
    # lowercasing, so "PasswordBox"/"txtPassword"-style automation IDs still
    # tokenize to a "password" word instead of one opaque lowercased blob.
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw)
    tokens = set(_WORD_RE.findall(spaced.lower()))
    return bool(tokens & keywords)


async def _ocr_words(image_path):
    """(text, (left, top, right, bottom)) for every word OCR finds in the
    image at image_path. Reads via WinRT's BitmapDecoder (a real file
    on disk) rather than hand-building a SoftwareBitmap from raw pixel
    bytes — the latter silently produced empty OCR results in testing."""
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.storage import StorageFile

    file = await StorageFile.get_file_from_path_async(str(image_path))
    stream = await file.open_async(0)  # FileAccessMode.READ
    try:
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
    finally:
        stream.close()

    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise OcrUnavailableError("no OCR engine available for the user profile's languages")
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
    import asyncio  # deferred alongside winsdk — see _ocr_words's imports

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


def redact_screenshot_tagged(image_path, element=None, config=None, out_path=None):
    """Like redact_screenshot, but returns each applied region tagged with
    *why* it was blurred — `[{"region": [...], "reason": "pattern" |
    "password_heuristic"}, ...]` — so callers (e.g. the manifest) can record
    which rule matched instead of conflating a password-field blur as an
    OCR pattern match. Regions come from `blur_regions`' own return value
    (post-clamping), not the raw input, so a manifest entry never claims a
    region wider than what was actually blurred.

    Always writes out_path (or image_path in place) exactly once for the
    pattern pass, even with zero matches, matching redact_screenshot's
    original "always produces an output file" behavior; the password pass
    (if any) then blurs on top of that same file."""
    config = config or load_config()
    patterns = compile_patterns(config)
    pattern_regions = find_pattern_regions(image_path, patterns)

    applied_pattern = blur_regions(image_path, pattern_regions, out_path=out_path)
    tagged = [{"region": list(r), "reason": "pattern"} for r in applied_pattern]

    if element is not None and is_password_field(element, config):
        rect = element.get("bounding_rect")
        if rect:
            target = out_path or image_path
            applied_password = blur_regions(target, [tuple(rect)], out_path=out_path)
            tagged += [
                {"region": list(r), "reason": "password_heuristic"} for r in applied_password
            ]
    return tagged


def redact_screenshot(image_path, element=None, config=None, out_path=None):
    """Full redaction pass for one screenshot: OCR-matched pattern regions,
    plus the element's whole bounding box if it looks like a password field.
    Raises OcrUnavailableError rather than silently skipping pattern
    matching if no OCR engine is available. Returns bare region tuples; use
    redact_screenshot_tagged for the region-to-reason mapping."""
    tagged = redact_screenshot_tagged(image_path, element=element, config=config, out_path=out_path)
    return [tuple(entry["region"]) for entry in tagged]
