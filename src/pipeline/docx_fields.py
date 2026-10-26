"""Real Word TOC field support, layered on top of the vendored SOP Factory 2
engine (`sop_lib.SOPBuilder`, vendor/sop_factory_2/, CLAUDE.md: "extend it,
do not rewrite it").

The engine's own `SOPBuilder.toc()` writes the table of contents as plain
static paragraphs — no `TOC` field code, so Word can never build page
numbers or navigation for it, and right-click "Update Field" has nothing to
act on. `SOPBuilder.heading1()`/`heading2()` likewise never set an outline
level, so even a real `TOC` field would have nothing to collect.

Rather than hand-patch the vendored engine file, this module drives
`SOPBuilder`'s own public methods for the visible formatting (so the VRSI
look is untouched) and layers real OOXML field/outline-level plumbing on
top via `sop.doc`, the `python-docx` Document the engine already exposes.
The static text `toc_lines()` produces becomes the field's *cached result*
(the runs Word shows before the first "Update Field") so every non-Word
consumer of the docx, and the pre-update view in Word itself, still show
exactly the same text as before this module existed.
"""

import contextlib
import logging
import os
import shutil
import uuid
import sys
import threading
from pathlib import Path

from docx.oxml import OxmlElement
from docx.oxml.ns import qn

logger = logging.getLogger(__name__)


def set_outline_level(paragraph, level):
    """Marks `paragraph` as outline level `level` (0-based, matching Word's
    "Level 1" = 0) by adding `<w:outlineLvl>` to its `pPr`. This is the
    signal a `TOC \\o \\u` field switch collects entries by — it works
    whether or not the paragraph carries a named Word heading style, so it
    doesn't require changing SOPBuilder's own heading1/heading2 formatting
    (which uses manually bolded/italicized runs, not built-in styles)."""
    pPr = paragraph._p.get_or_add_pPr()
    outline_lvl = OxmlElement("w:outlineLvl")
    outline_lvl.set(qn("w:val"), str(level))
    pPr.append(outline_lvl)


def add_toc_field(sop, cached_lines, levels="1-2"):
    """Replaces SOPBuilder.toc()'s plain-text table of contents with a real
    Word `TOC` field, using `cached_lines` (assembler.py's `toc_lines()`
    output — the same strings the old plain-text TOC showed) as the field's
    cached result. `\\o "1-2"` collects outline levels 0-1 (set_outline_level
    marks section headings 0, step headings 1); `\\h` makes entries
    hyperlinks; `\\z` hides tab leaders in web view; `\\u` collects by
    paragraph outline level in addition to style — the switch this whole
    mechanism relies on, since SOPBuilder's headings carry no named style."""
    sop.heading1("TABLE OF CONTENTS")

    paragraph = sop.doc.add_paragraph()
    run = paragraph.add_run()

    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    run._r.append(begin)

    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f' TOC \\o "{levels}" \\h \\z \\u '
    run._r.append(instr)

    separate_run = paragraph.add_run()
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    separate_run._r.append(separate)

    for line in cached_lines:
        sop.doc.add_paragraph(line)

    end_paragraph = sop.doc.add_paragraph()
    end_run = end_paragraph.add_run()
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    end_run._r.append(end)

    sop.page_break()


def enable_update_fields_on_open(doc):
    """Sets `<w:updateFields w:val="true"/>` in word/settings.xml so Word
    refreshes the TOC field's page numbers/links automatically when the
    document is opened, instead of requiring a manual right-click ->
    "Update Field" (or F9) before it shows anything real."""
    settings = doc.settings.element
    update_fields = OxmlElement("w:updateFields")
    update_fields.set(qn("w:val"), "true")
    settings.append(update_fields)


def word_available():
    """True when Word's COM class is registered (Windows only); checked in
    the registry, so nothing is launched just to find out."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Word.Application"))
        return True
    except OSError:
        return False


_WD_FORMAT_DOCX = 12  # WdSaveFormat.wdFormatXMLDocument


class _WordProcess:
    """Own the process behind this COM application's HWND, never a PID diff.

    Retaining the process handle also prevents PID reuse from targeting a
    different process. Cancellation and late COM activation share a lock.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.cancelled = False
        self.handle = None

    def attach(self, hwnd):
        import win32api
        import win32process

        with self.lock:
            _thread, pid = win32process.GetWindowThreadProcessId(hwnd)
            self.handle = win32api.OpenProcess(1, False, pid)  # PROCESS_TERMINATE
            if self.cancelled:
                win32api.TerminateProcess(self.handle, 1)
                raise RuntimeError("Word refresh cancelled")

    def cancel(self):
        with self.lock:
            self.cancelled = True
            if self.handle is not None:
                import win32api

                with contextlib.suppress(Exception):
                    win32api.TerminateProcess(self.handle, 1)

    def close(self):
        with self.lock:
            if self.handle is not None:
                self.handle.Close()
                self.handle = None


def _update_tocs(docx_path, tmp_path, process):
    """Refresh a private copy; only the waiting caller may publish it."""
    import pythoncom  # export jobs run on worker threads: COM must be initialized per thread
    import win32com.client

    pythoncom.CoInitialize()
    word = None
    try:
        # A private, hidden instance: never touches a Word the user has open.
        word = win32com.client.DispatchEx("Word.Application")
        # Word.Application has no Hwnd (unlike Excel): give this instance a
        # unique caption and find its main window ("OpusApp") by it. Only
        # the kill-on-timeout safety net depends on this, so a failed lookup
        # never fails the refresh itself.
        import win32gui

        word.Caption = f"SOPForge TOC refresh {uuid.uuid4().hex}"
        hwnd = win32gui.FindWindow("OpusApp", word.Caption)
        if hwnd:
            process.attach(hwnd)
        elif process.cancelled:
            raise RuntimeError("Word refresh cancelled")
        word.Visible = False
        word.DisplayAlerts = 0
        # Open(FileName, ConfirmConversions, ReadOnly, AddToRecentFiles):
        # read-only, so the shipped docx is never locked or half-written by
        # Word; the refreshed copy is saved beside it and swapped in below,
        # only once Word has closed cleanly.
        doc = word.Documents.Open(str(docx_path), False, True, False)
        try:
            for toc in doc.TablesOfContents:
                toc.Update()
            # SaveAs2(FileName, FileFormat, LockComments, Password, AddToRecentFiles)
            doc.SaveAs2(str(tmp_path), _WD_FORMAT_DOCX, False, "", False)
        finally:
            doc.Close(0)
    finally:
        if word is not None:
            with contextlib.suppress(Exception):  # already gone if it was killed
                word.Quit(0)
        pythoncom.CoUninitialize()


def refresh_toc_in_word(docx_path, timeout=90.0):
    """Has real Word rebuild the TOC field (page numbers, final entries) and
    save, so the docx ships filled in even where fields never update on
    open (Protected View, preview panes, Word Online). Returns None on
    success or a short reason string; never raises -- the unrefreshed
    field (cached entries + updateFields) is still a valid document.
    Runs on its own thread with a timeout so a stuck Word dialog can't
    hang the build queue. Once its HWND is known, a stuck instance can be
    terminated safely. A hang inside COM activation itself has no owned
    handle yet: never guess which process to kill."""
    docx_path = Path(docx_path).resolve()
    token = uuid.uuid4().hex
    tmp_path = docx_path.with_name(f"{docx_path.stem}.toc-refresh-{token}.docx")
    source_path = tmp_path.with_suffix(".source.docx")
    error = []
    process = _WordProcess()

    def run():
        try:
            # Word may lock even a read-only document. Never open the export
            # itself, so a stuck COM call cannot block subsequent re-exports.
            shutil.copyfile(docx_path, source_path)
            _update_tocs(source_path, tmp_path, process)
        except Exception as exc:  # noqa: BLE001 - reported to the caller instead
            error.append(exc)
        finally:
            process.close()
            with contextlib.suppress(OSError):
                source_path.unlink()
            if process.cancelled:
                with contextlib.suppress(OSError):
                    tmp_path.unlink()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        process.cancel()
        # Never publish after timing out, even if Word eventually returns.
        for path in (source_path, tmp_path):
            with contextlib.suppress(OSError):
                path.unlink()
        return f"Word did not finish refreshing the TOC within {timeout:.0f}s"
    try:
        if error:
            raise error[0]
        os.replace(tmp_path, docx_path)
    except Exception as exc:  # noqa: BLE001 - refresh is best-effort
        logger.warning("Word TOC refresh failed for %s: %s", docx_path, exc)
        return f"Word TOC refresh failed: {exc}"
    finally:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
    return None
