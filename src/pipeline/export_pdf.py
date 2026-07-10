"""PDF export: a manifest -> PDF whose structure mirrors the SOP Factory 2
docx (docx_assembler.py) so the two exports read the same -- a title page,
a "Steps" section heading, then per-step: a "Step N" heading, the step text
as a bullet, any narration, the annotated screenshot with a caption, and a
closing revision-history table. Pure-Python via fpdf2 -- no GTK (weasyprint)
or Word COM automation dependency, both hostile to PyInstaller freezing and a
headless build VM. It won't be pixel-identical to Word's rendering (different
engines), but the formatting *system* -- headings, bullets, captions,
revision history -- matches."""

from pathlib import Path

from fpdf import FPDF

from pipeline.assembler import format_doc_date, step_heading, toc_lines
from pipeline.claim_coverage import parse_verify_line
from pipeline.resource_path import resource_path

_INK = (33, 37, 41)
_MUTED = (110, 116, 124)
_ACCENT = (37, 99, 235)
_VERIFY = (192, 0, 0)

# DejaVu Sans (Bitstream Vera license -- freely redistributable/embeddable,
# see assets/fonts/dejavu-sans/LICENSE) gives a real Unicode font instead of
# fpdf2's core Helvetica, which only supports Latin-1: fine for accented
# Latin letters (é, ñ, ü, ...) but not "smart" typography punctuation
# (curly quotes, en/em dashes, ellipsis), which live outside Latin-1 in
# Unicode's General Punctuation block.
_DEJAVU_STYLES = {
    "": "DejaVuSans.ttf",
    "B": "DejaVuSans-Bold.ttf",
    "I": "DejaVuSans-Oblique.ttf",
    "BI": "DejaVuSans-BoldOblique.ttf",
}

# Only used as a last-resort fallback -- see _safe_text -- if DejaVu
# registration itself fails and Helvetica (Latin-1 only) ends up as the
# active font. Transliterates exactly the characters that break under
# Latin-1 to their closest ASCII equivalent before the final Latin-1
# encode/replace for anything else.
_TRANSLITERATIONS = {
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "--",
    "…": "...",
    "•": "*",
}


def _register_font(pdf):
    """Registers DejaVu Sans (all four styles fpdf2 needs: regular/B/I/BI)
    as a real Unicode font. Falls back to the built-in Helvetica if the
    font files are missing for any reason -- font registration failing
    must never break PDF export, the same "always succeeds" spirit as
    invariant L3's template fallback. Returns the resolved font family
    name every set_font call in this module should use."""
    try:
        font_dir = resource_path("assets", "fonts", "dejavu-sans")
        for style, filename in _DEJAVU_STYLES.items():
            pdf.add_font("DejaVu", style, str(font_dir / filename))
        return "DejaVu"
    except Exception:  # noqa: BLE001
        return "Helvetica"


def _safe_text(text, font_family):
    """Never raises; the export path must always succeed, the same way
    invariant L3's template fallback always succeeds. With DejaVu
    registered, text needs no transformation at all -- it's a real Unicode
    font. Only falls back to Latin-1 + transliteration when _register_font
    couldn't register DejaVu and Helvetica (Latin-1 only) is what's
    actually active."""
    if font_family != "Helvetica":
        return text
    for src, dst in _TRANSLITERATIONS.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", "replace").decode("latin-1")


class _SOPPdf(FPDF):
    def header(self):
        # No header on the title page (page 1); a thin running title after.
        if self.page_no() == 1:
            return
        self.set_font(self._font_family, "", 8)
        self.set_text_color(*_MUTED)
        self.cell(0, 8, self._running_title, align="R")
        self.ln(10)
        self.set_text_color(*_INK)

    def footer(self):
        self.set_y(-12)
        self.set_font(self._font_family, "", 8)
        self.set_text_color(*_MUTED)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")
        self.set_text_color(*_INK)


def _bullet(pdf, text):
    """fpdf2's multi_cell defaults to new_x=XPos.RIGHT -- leaving the cursor
    at the page's right margin, not the left, regardless of how much text
    was actually drawn (even a single short line). Every multi_cell call in
    this module passes new_x="LMARGIN", new_y="NEXT" explicitly so the next
    operation (another multi_cell, a cell(), an image placed at the current
    cursor) never silently starts from the wrong side of the page."""
    pdf.set_font(pdf._font_family, "", 11)
    pdf.set_text_color(*_INK)
    x = pdf.get_x()
    pdf.cell(6, 6, chr(149))  # bullet dot
    pdf.set_x(x + 6)
    pdf.multi_cell(0, 6, _safe_text(text, pdf._font_family), new_x="LMARGIN", new_y="NEXT")


def _narrative_body(pdf, narrative_text):
    """Mirrors docx_assembler.py's _narrative_body: a [verify]-flagged line
    (claim_coverage.parse_verify_line -- the shared single point of truth
    both exporters use to reverse render_verify_blockquote's format) renders
    as a distinct red/italic callout instead of raw debug-looking text; the
    claim id itself is dropped from what's shown (it stays meaningful in the
    sidecar report, not the reader-facing doc)."""
    for line in narrative_text.splitlines():
        claim_text = parse_verify_line(line)
        if claim_text is not None:
            pdf.set_font(pdf._font_family, "BI", 10)
            pdf.set_text_color(*_VERIFY)
            pdf.multi_cell(
                0,
                6,
                _safe_text(f"Needs verification: {claim_text}", pdf._font_family),
                new_x="LMARGIN",
                new_y="NEXT",
            )
            pdf.set_text_color(*_INK)
        elif line.strip():
            pdf.set_font(pdf._font_family, "", 11)
            pdf.multi_cell(0, 6, _safe_text(line, pdf._font_family), new_x="LMARGIN", new_y="NEXT")


def render_pdf(
    manifest,
    step_results,
    annotated_paths,
    output_path,
    narrative_text=None,
    revision="1.0",
    date=None,
    author="SOPForge",
    doc_no=None,
):
    """Builds a PDF mirroring the docx layout (docx_assembler.py): a title
    page (with doc number/author/revision when given), a table of contents,
    a "Procedure" section with a descriptive heading per step, and a closing
    revision-history table. step_results may carry an optional "narration"
    key per step (placed under the step, matching the docx/markdown/html
    exports). `date`, if not given, is the manifest's own real session date
    (never a hardcoded placeholder). Returns output_path."""
    date = date or format_doc_date(manifest.session.started_utc)

    pdf = _SOPPdf()
    pdf._font_family = _register_font(pdf)
    title = manifest.session.title or manifest.session.id
    pdf._running_title = _safe_text(title, pdf._font_family)
    pdf.set_auto_page_break(auto=True, margin=18)

    # --- Title page ---
    pdf.add_page()
    pdf.ln(50)
    pdf.set_font(pdf._font_family, "B", 26)
    pdf.set_text_color(*_INK)
    pdf.multi_cell(
        0, 14, _safe_text(title.upper(), pdf._font_family), align="C", new_x="LMARGIN", new_y="NEXT"
    )
    pdf.ln(6)
    pdf.set_font(pdf._font_family, "", 12)
    pdf.set_text_color(*_MUTED)
    pdf.multi_cell(0, 7, "Standard Operating Procedure", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)
    meta_lines = []
    if doc_no:
        meta_lines.append(f"Document No: {doc_no}")
    meta_lines.append(f"Revision {revision}  —  Released {date}")
    if author:
        meta_lines.append(f"Author: {author}")
    pdf.multi_cell(
        0,
        7,
        _safe_text("\n".join(meta_lines), pdf._font_family),
        align="C",
        new_x="LMARGIN",
        new_y="NEXT",
    )

    # --- Table of contents ---
    pdf.add_page()
    pdf.set_font(pdf._font_family, "B", 16)
    pdf.set_text_color(*_ACCENT)
    pdf.cell(0, 11, "Table of Contents", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_INK)
    pdf.ln(2)
    # toc_lines (assembler.py) is the single source of truth for the outline
    # itself -- shared with docx_assembler.py so the two TOCs can't drift
    # out of sync. Indented step entries (the "      " prefix that same
    # helper produces) get the smaller/muted styling; numbered section
    # lines get the larger plain one.
    for line in toc_lines(manifest, narrative_text):
        if line.startswith("      "):
            pdf.set_font(pdf._font_family, "", 10)
            pdf.set_text_color(*_MUTED)
            pdf.multi_cell(0, 6, _safe_text(line, pdf._font_family), new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(*_INK)
        else:
            pdf.set_font(pdf._font_family, "", 11)
            pdf.multi_cell(0, 7, _safe_text(line, pdf._font_family), new_x="LMARGIN", new_y="NEXT")

    # --- Overview ---
    if narrative_text:
        pdf.add_page()
        pdf.set_font(pdf._font_family, "B", 16)
        pdf.set_text_color(*_ACCENT)
        pdf.cell(0, 11, "Overview", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_INK)
        pdf.ln(2)
        _narrative_body(pdf, narrative_text)

    # --- Procedure ---
    pdf.add_page()
    pdf.set_font(pdf._font_family, "B", 18)
    pdf.set_text_color(*_ACCENT)
    pdf.cell(0, 12, "Procedure", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_INK)
    pdf.ln(2)

    for n, (step, result, shot) in enumerate(
        zip(manifest.steps, step_results, annotated_paths, strict=True), start=1
    ):
        heading = step_heading(n, step)
        pdf.set_font(pdf._font_family, "BI", 13)
        pdf.set_text_color(*_INK)
        pdf.multi_cell(0, 9, _safe_text(heading, pdf._font_family), new_x="LMARGIN", new_y="NEXT")

        _bullet(pdf, result["text"])

        if result.get("narration"):
            pdf.ln(1)
            pdf.set_font(pdf._font_family, "I", 10)
            pdf.set_text_color(*_MUTED)
            pdf.multi_cell(
                0,
                5.5,
                _safe_text(f"Narration: {result['narration']}", pdf._font_family),
                new_x="LMARGIN",
                new_y="NEXT",
            )
            pdf.set_text_color(*_INK)

        if shot is not None and Path(shot).exists():
            pdf.ln(2)
            pdf.image(str(shot), w=130)
            pdf.set_font(pdf._font_family, "I", 9)
            pdf.set_text_color(*_MUTED)
            pdf.multi_cell(
                0,
                5,
                _safe_text(heading, pdf._font_family),
                align="C",
                new_x="LMARGIN",
                new_y="NEXT",
            )
            pdf.set_text_color(*_INK)
        pdf.ln(6)

    # --- Revision history (mirrors sop.revision_history in the docx) ---
    pdf.add_page()
    pdf.set_font(pdf._font_family, "B", 16)
    pdf.set_text_color(*_ACCENT)
    pdf.cell(0, 11, "Revision History", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_INK)
    pdf.ln(2)
    pdf.set_font(pdf._font_family, "B", 10)
    widths = (35, 25, 70, 40)
    for label, w in zip(("Date", "Revision", "Description", "Author"), widths):
        pdf.cell(w, 8, label, border=1)
    pdf.ln(8)
    pdf.set_font(pdf._font_family, "", 10)
    for value, w in zip((date, revision, "Initial generation", author), widths):
        pdf.cell(w, 8, _safe_text(value, pdf._font_family), border=1)
    pdf.ln(8)

    pdf.output(str(output_path))
    return output_path
