"""docx_fields.py: a real Word TOC field (not SOPBuilder's own plain-text
toc()) layered onto the external SOP Factory 2 engine, driven through the
real engine end-to-end via assemble_docx -- proves the wiring, not just the
XML-builder functions in isolation."""

import re

import pytest
import zipfile
from pathlib import Path

from PIL import Image

from pipeline.docx_assembler import assemble_docx
from pipeline.manifest import load_manifest
from pipeline.render import render_steps_template_mode

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"

_FLD_CHAR_RE = re.compile(r'<w:fldChar w:fldCharType="(begin|separate|end)"/>')
_INSTR_TEXT_RE = re.compile(r"<w:instrText[^>]*>([^<]*)</w:instrText>")
_OUTLINE_LVL_RE = re.compile(r'<w:outlineLvl w:val="(\d+)"/>')


def _make_screenshots(manifest, directory):
    directory.mkdir(parents=True, exist_ok=True)
    for step in manifest.steps:
        Image.new("RGB", (1920, 1080), (255, 255, 255)).save(directory / step.screenshot)


def _build(tmp_path, narrative_text=None):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots = tmp_path / "screenshots"
    annotated = tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, _annotated_paths = render_steps_template_mode(manifest, screenshots, annotated)

    output_path = tmp_path / "out.docx"
    out, warnings = assemble_docx(
        manifest, step_results, annotated, output_path, narrative_text=narrative_text
    )
    assert warnings == []
    with zipfile.ZipFile(out) as zf:
        document_xml = zf.read("word/document.xml").decode("utf-8")
        settings_xml = zf.read("word/settings.xml").decode("utf-8")
    return manifest, document_xml, settings_xml


def test_toc_is_a_real_field_not_plain_text(tmp_path):
    _manifest, document_xml, _settings_xml = _build(tmp_path)

    fld_types = _FLD_CHAR_RE.findall(document_xml)
    assert fld_types == ["begin", "separate", "end"]

    instructions = _INSTR_TEXT_RE.findall(document_xml)
    assert len(instructions) == 1
    assert 'TOC \\o "1-2" \\h \\z \\u' in instructions[0]


def test_toc_field_caches_the_same_text_toc_lines_would_show(tmp_path):
    from pipeline.assembler import toc_lines

    manifest, document_xml, _settings_xml = _build(tmp_path)

    for line in toc_lines(manifest, narrative_text=None):
        assert line in document_xml


def test_section_and_step_headings_carry_outline_levels(tmp_path):
    _manifest, document_xml, _settings_xml = _build(tmp_path)

    levels = {int(v) for v in _OUTLINE_LVL_RE.findall(document_xml)}
    # Level 0: "Procedure"/"Revision History" section headings.
    # Level 1: per-step headings.
    assert levels == {0, 1}


def test_update_fields_on_open_is_set_so_word_refreshes_automatically(tmp_path):
    _manifest, _document_xml, settings_xml = _build(tmp_path)

    assert '<w:updateFields w:val="true"/>' in settings_xml


def test_refresh_toc_in_word_never_raises_and_times_out(monkeypatch, tmp_path):
    import threading
    import pipeline.docx_fields as fields

    path = tmp_path / "x.docx"
    path.write_bytes(b"original")

    def boom(_path, _tmp, _process):
        raise RuntimeError("COM server busy")

    monkeypatch.setattr(fields, "_update_tocs", boom)
    assert "COM server busy" in fields.refresh_toc_in_word(path)
    release, done = threading.Event(), threading.Event()

    def hang(source, target, process):
        assert source != path and source.read_bytes() == b"original"
        release.wait(5)
        target.write_bytes(b"late result")
        done.set()

    monkeypatch.setattr(fields, "_update_tocs", hang)
    try:
        assert "did not finish" in fields.refresh_toc_in_word(path, timeout=0.05)
        path.write_bytes(b"new export")
    finally:
        release.set()
    assert done.wait(2)
    assert path.read_bytes() == b"new export"


def test_word_process_owns_hwnd_handle_and_cancels_late_activation(monkeypatch):
    import sys
    from types import SimpleNamespace
    from pipeline.docx_fields import _WordProcess

    killed, opened = [], []
    handle = SimpleNamespace(Close=lambda: None)

    def open_process(access, inherit, pid):
        opened.append(pid)
        return handle

    monkeypatch.setitem(
        sys.modules,
        "win32api",
        SimpleNamespace(
            OpenProcess=open_process,
            TerminateProcess=lambda h, code: killed.append(h),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(
            GetWindowThreadProcessId=lambda hwnd: (1, {123: 456}[hwnd]),
        ),
    )
    process = _WordProcess()
    process.attach(123)
    process.cancel()
    assert opened == [456]  # Only the COM object's HWND, never other new Word PIDs.
    assert killed == [handle]  # Retained handle, so PID reuse cannot change the target.
    process.close()
    late = _WordProcess()
    late.cancel()
    with pytest.raises(RuntimeError, match="cancelled"):
        late.attach(123)
    assert killed == [handle, handle]
    late.close()


def test_word_refresh_uses_unique_copies_and_preserves_original_on_error(monkeypatch, tmp_path):
    import pipeline.docx_fields as fields

    path = tmp_path / "x.docx"
    path.write_bytes(b"original")
    targets = []

    def update(source, target, process):
        assert source.read_bytes() == path.read_bytes()
        targets.append(target)
        target.write_bytes(b"refreshed")

    monkeypatch.setattr(fields, "_update_tocs", update)
    assert fields.refresh_toc_in_word(path) is None
    assert path.read_bytes() == b"refreshed"
    assert fields.refresh_toc_in_word(path) is None
    assert targets[0] != targets[1]
    monkeypatch.setattr(
        fields.os, "replace", lambda *args: (_ for _ in ()).throw(PermissionError("locked"))
    )
    assert "locked" in fields.refresh_toc_in_word(path)
    assert path.read_bytes() == b"refreshed"
    assert not list(tmp_path.glob("*.toc-refresh*"))


_WORD_INSTALLED = __import__("pipeline.docx_fields").docx_fields.word_available()


def _refreshed_toc_xml(docx_path):
    import zipfile

    from pipeline.docx_fields import refresh_toc_in_word

    assert refresh_toc_in_word(docx_path) is None
    xml = zipfile.ZipFile(docx_path).read("word/document.xml").decode()
    start = xml.index("TOC \\o")
    # Word's rebuilt entries carry nested PAGEREF fields (\h), so the TOC
    # field's own end is the one that closes the outer begin.
    depth = 1
    for m in re.finditer(r'<w:fldChar w:fldCharType="(begin|end)"/>', xml[start:]):
        depth += 1 if m.group(1) == "begin" else -1
        if depth == 0:
            return xml[start : start + m.end()]
    raise AssertionError("TOC field never closed")


@pytest.mark.skipif(not _WORD_INSTALLED, reason="Microsoft Word not installed")
def test_refresh_toc_in_word_fills_in_page_numbers(tmp_path):
    """Real Word: after the refresh the TOC field's result carries page
    numbers, which the cached (pre-Word) entries never do."""
    _build(tmp_path)
    toc = _refreshed_toc_xml(tmp_path / "out.docx")
    assert re.search(r"<w:t[^>]*>\d+</w:t>", toc), "no page numbers in the refreshed TOC"
    assert not list(tmp_path.glob("*.toc-refresh*"))


@pytest.mark.skipif(not _WORD_INSTALLED, reason="Microsoft Word not installed")
def test_word_refresh_keeps_grouped_toc_to_sections(tmp_path):
    """Real Word: with phases, the TOC field collects outline level 1 only,
    so Word's rebuild lists the sections and never puts the steps back."""
    from pipeline.grouping import Phase

    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    screenshots, annotated = tmp_path / "screenshots", tmp_path / "annotated"
    _make_screenshots(manifest, screenshots)
    step_results, _paths = render_steps_template_mode(manifest, screenshots, annotated)
    ids = manifest.step_ids()
    phases = [Phase("Sign in", tuple(ids[:1])), Phase("Configure", tuple(ids[1:]))]
    out = tmp_path / "grouped.docx"
    assemble_docx(manifest, step_results, annotated, out, phases=phases)

    text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", _refreshed_toc_xml(out)))
    assert "Sign in" in text and "Configure" in text, text
    assert "Step 1" not in text, text


def test_concurrent_word_refreshes_use_independent_files(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import pipeline.docx_fields as fields

    barrier = threading.Barrier(2)
    paths = [tmp_path / "a.docx", tmp_path / "b.docx"]
    for path in paths:
        path.write_bytes(path.name.encode())
    copies = []

    def update(source, target, process):
        copies.extend([source, target])
        barrier.wait(timeout=2)
        target.write_bytes(source.read_bytes() + b" refreshed")

    monkeypatch.setattr(fields, "_update_tocs", update)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(fields.refresh_toc_in_word, paths)) == [None, None]
    assert len(set(copies)) == 4
    for path in paths:
        assert path.read_bytes() == path.name.encode() + b" refreshed"
    assert not list(tmp_path.glob("*.toc-refresh*"))
