"""Golden-file test infrastructure (AC5 plumbing): docx unzip + document.xml
byte-compare helper with a timestamp/rsid normalizer, proven against
synthetic docx (zip) fixtures — engine-independent, no SOP Factory 2
output needed for this task."""

import zipfile

import pytest

from pipeline.golden import compare_document_xml, extract_document_xml, normalize_document_xml


def _make_docx(path, document_xml_bytes):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", document_xml_bytes)
        zf.writestr("[Content_Types].xml", b"<Types/>")


def test_extract_document_xml_reads_the_right_part(tmp_path):
    content = b"<w:document><w:body><w:p>Hello</w:p></w:body></w:document>"
    docx = tmp_path / "a.docx"
    _make_docx(docx, content)
    assert extract_document_xml(docx) == content


def test_normalize_strips_rsid_attributes():
    xml = b'<w:p w:rsidR="00AB12CD" w:rsidRDefault="00112233"><w:r>Text</w:r></w:p>'
    normalized = normalize_document_xml(xml)
    assert b"rsid" not in normalized
    assert b"Text" in normalized


def test_normalize_strips_timestamp_attributes():
    xml = (
        b'<w:sectPr w:date="2026-01-01T00:00:00Z">'
        b'<dcterms:created dcterms:created="2026-01-01T00:00:00Z"/></w:sectPr>'
    )
    normalized = normalize_document_xml(xml)
    assert b"2026-01-01" not in normalized


def test_two_docs_differing_only_in_rsid_and_timestamp_compare_equal(tmp_path):
    base = (
        b'<w:p w:rsidR="00AB12CD" w:date="2026-01-01T00:00:00Z">'
        b"<w:r><w:t>Same content</w:t></w:r></w:p>"
    )
    variant = (
        b'<w:p w:rsidR="00FFEE11" w:date="2026-06-15T12:30:00Z">'
        b"<w:r><w:t>Same content</w:t></w:r></w:p>"
    )

    docx_a = tmp_path / "a.docx"
    docx_b = tmp_path / "b.docx"
    _make_docx(docx_a, base)
    _make_docx(docx_b, variant)

    match, actual, golden = compare_document_xml(docx_a, docx_b)
    assert match is True
    assert actual == golden


def test_docs_with_real_content_difference_do_not_compare_equal(tmp_path):
    base = b"<w:p><w:r><w:t>Original content</w:t></w:r></w:p>"
    changed = b"<w:p><w:r><w:t>Different content</w:t></w:r></w:p>"

    docx_a = tmp_path / "a.docx"
    docx_b = tmp_path / "b.docx"
    _make_docx(docx_a, base)
    _make_docx(docx_b, changed)

    match, actual, golden = compare_document_xml(docx_a, docx_b)
    assert match is False
    assert actual != golden


def test_identical_docs_compare_equal(tmp_path):
    content = b"<w:p><w:r><w:t>Identical</w:t></w:r></w:p>"
    docx_a = tmp_path / "a.docx"
    docx_b = tmp_path / "b.docx"
    _make_docx(docx_a, content)
    _make_docx(docx_b, content)

    match, _actual, _golden = compare_document_xml(docx_a, docx_b)
    assert match is True


def test_missing_document_xml_raises_keyerror(tmp_path):
    docx = tmp_path / "broken.docx"
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("[Content_Types].xml", b"<Types/>")

    with pytest.raises(KeyError):
        extract_document_xml(docx)
