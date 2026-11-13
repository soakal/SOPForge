"""Pull the embedded images out of a PDF or DOCX, in document order, so the
manifest-free photo build can start from a document instead of loose
screenshots (SOP Factory's prep_active.py parity). pypdf, not PyMuPDF:
PyMuPDF is AGPL.

Each returned item is raw image bytes Pillow can open; the caller does the
EXIF rotation + PNG conversion. One bad image is skipped, never raised."""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

MIN_IMAGE_BYTES = 5 * 1024  # SOP Factory SKILL.md: smaller is an icon/bullet, not a step
MAX_IMAGES = 200  # prep_active.py's MAX_LOOSE_IMAGES_DEFAULT
MAX_TOTAL_BYTES = 200 * 1024 * 1024  # same ceiling as one uploaded screenshot

DOC_EXTS = {"pdf", "docx"}


def _usable(data: bytes) -> bool:
    if len(data) < MIN_IMAGE_BYTES:
        return False
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        return True
    except Exception:  # noqa: BLE001 -- unsupported/corrupt image: skip it
        return False


def _pdf_blobs(src):
    for page in PdfReader(src).pages:
        try:
            images = list(page.images)
        except Exception:  # noqa: BLE001 -- one broken page must not sink the doc
            continue
        for img in images:
            try:
                yield img.data
            except Exception:  # noqa: BLE001
                continue


def _docx_blobs(src, max_bytes):
    with zipfile.ZipFile(src) as z:
        names = set(z.namelist())
        media = sorted(n for n in names if n.startswith("word/media/"))
        # Document order = order of r:embed references in document.xml,
        # resolved through its relationships file. Header/footer images
        # (logos) live in other rels files and are deliberately left out.
        order = []
        if "word/document.xml" in names and "word/_rels/document.xml.rels" in names:
            rels = z.read("word/_rels/document.xml.rels").decode("utf-8", "replace")
            targets = {}
            for tag in re.findall(r"<Relationship\b[^>]*>", rels):
                rid = re.search(r'\bId="([^"]+)"', tag)
                tgt = re.search(r'\bTarget="([^"]+)"', tag)
                if rid and tgt:
                    targets[rid.group(1)] = posixpath.normpath(posixpath.join("word", tgt.group(1)))
            doc = z.read("word/document.xml").decode("utf-8", "replace")
            for rid in re.findall(r'r:(?:embed|link)="([^"]+)"', doc):
                name = targets.get(rid)
                if name in names and name not in order:
                    order.append(name)
        for name in order or media:
            if z.getinfo(name).file_size > max_bytes:  # zip-bomb guard
                continue
            yield z.read(name)


def extract_images(
    src, *, max_images: int = MAX_IMAGES, max_bytes: int = MAX_TOTAL_BYTES
) -> list[bytes]:
    """Images embedded in a .pdf/.docx, in document order. `src` is a path,
    or the file's bytes (then the type comes from the magic number: PDFs
    start with %PDF-, anything else is treated as a DOCX zip). Images < 5 KB or not loadable by Pillow are
    skipped; stops at `max_images` images / `max_bytes` total. Raises
    ValueError only when the document itself can't be parsed."""
    if isinstance(src, (bytes, bytearray)):
        is_pdf = bytes(src[:5]) == b"%PDF-"
        src = io.BytesIO(src)
    else:
        is_pdf = Path(src).suffix.lower() == ".pdf"
    out, total = [], 0
    try:
        blobs = _pdf_blobs(src) if is_pdf else _docx_blobs(src, max_bytes)
        for data in blobs:
            if len(out) >= max_images or total + len(data) > max_bytes:
                break
            if _usable(data):
                out.append(data)
                total += len(data)
    except Exception as exc:  # noqa: BLE001 -- unparseable document
        raise ValueError(f"could not read document: {exc}") from exc
    return out
