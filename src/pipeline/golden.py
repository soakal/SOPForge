"""Golden-file test infrastructure (AC5 plumbing): unzips a docx (a ZIP
archive), extracts word/document.xml, and byte-compares it against a
committed golden reference after normalizing volatile fields — w:rsid*
attributes and timestamp attributes — that vary between otherwise-
identical Word/docx-engine saves. Without normalization, a golden-file
compare would spuriously fail on every single run. Engine-independent: this
module never generates a docx itself, only compares two that already exist."""

import re
import zipfile

_RSID_ATTR_RE = re.compile(rb'\sw:rsid\w*="[0-9A-Fa-f]+"')
_TIMESTAMP_ATTR_RE = re.compile(rb'\s(?:w:date|dcterms:created|dcterms:modified)="[^"]*"')


def extract_document_xml(docx_path):
    """Returns the raw bytes of word/document.xml from a docx (zip) file."""
    with zipfile.ZipFile(docx_path) as zf:
        return zf.read("word/document.xml")


def normalize_document_xml(xml_bytes):
    """Strips volatile w:rsid* and timestamp attributes so a byte-compare
    only ever catches real content differences."""
    xml_bytes = _RSID_ATTR_RE.sub(b"", xml_bytes)
    xml_bytes = _TIMESTAMP_ATTR_RE.sub(b"", xml_bytes)
    return xml_bytes


def compare_document_xml(docx_path, golden_path):
    """Returns (match, actual_normalized, golden_normalized). Both paths
    point at docx (zip) files; only word/document.xml is compared, after
    normalization."""
    actual = normalize_document_xml(extract_document_xml(docx_path))
    golden = normalize_document_xml(extract_document_xml(golden_path))
    return actual == golden, actual, golden
