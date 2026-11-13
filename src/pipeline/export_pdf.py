"""PDF export: a manifest -> PDF whose structure mirrors the SOP Factory 2
docx (docx_assembler.py) so the two exports read the same -- a title page,
then assembler.document_outline's numbered sections and per-step: an
"A)  ..." heading, the step text as a bullet, any authored extras and
narration, the annotated screenshot with a caption, and a closing
revision-history table. Pure-Python via fpdf2 -- no GTK (weasyprint)
or Word COM automation dependency, both hostile to PyInstaller freezing and a
headless build VM. It won't be pixel-identical to Word's rendering (different
engines), but the formatting *system* -- headings, bullets, captions,
revision history -- matches."""

from pathlib import Path

from fpdf import FPDF

from pipeline import authoring as auth
from pipeline.assembler import document_outline, format_doc_date, step_heading, toc_entries
from pipeline.claim_coverage import parse_verify_line
from pipeline.resource_path import resource_path
from pipeline.review_notes import step_review_notes

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


# Width (mm) of the right-aligned page-number column in the TOC.
_TOC_PAGE_W = 14


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
    # DejaVu (a real Unicode font) has a native glyph for the actual bullet
    # character; the core-font Helvetica fallback only supports Latin-1
    # (0-255), which the bullet's real codepoint (U+2022) is outside of --
    # chr(149) is the historical trick there: byte 0x95 encodes fine within
    # Latin-1's range, and fpdf2's built-in Helvetica glyph table happens to
    # render it as a bullet (same cp1252 mapping Windows uses for that byte).
    bullet_char = "•" if pdf._font_family != "Helvetica" else chr(149)
    pdf.cell(6, 6, bullet_char)
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


def _heading(pdf, text, height):
    text = _safe_text(text, pdf._font_family)
    rows = len(pdf.multi_cell(0, height, text, dry_run=True, output="LINES"))
    if pdf.will_page_break(rows * height):
        pdf.add_page()
    page = pdf.page_no()
    pdf.multi_cell(0, height, text, new_x="LMARGIN", new_y="NEXT")
    return page


def _section_heading(pdf, text):
    pdf.set_font(pdf._font_family, "B", 18)
    pdf.set_text_color(*_ACCENT)
    page = _heading(pdf, text, 12)
    pdf.set_text_color(*_INK)
    pdf.ln(2)
    return page


def _text_line(pdf, text, style="", size=11, color=_INK, h=6, align="L"):
    pdf.set_font(pdf._font_family, style, size)
    pdf.set_text_color(*color)
    pdf.multi_cell(
        0, h, _safe_text(text, pdf._font_family), align=align, new_x="LMARGIN", new_y="NEXT"
    )
    pdf.set_text_color(*_INK)


def _sub_bullet(pdf, text, sub):
    if sub:
        pdf.set_x(pdf.l_margin + 8)
    _bullet(pdf, text)


def _command(pdf, text):
    """Mirrors sop.code_block: a shaded, bordered monospace box, indented.
    Core Courier is Latin-1 only, hence the Helvetica-mode _safe_text."""
    pdf.set_font("Courier", "", 10)
    pdf.set_fill_color(0xF2, 0xF2, 0xF2)
    pdf.set_draw_color(0xBF, 0xBF, 0xBF)
    pdf.set_x(pdf.l_margin + 12)
    pdf.multi_cell(
        pdf.epw - 12,
        5.5,
        _safe_text(text, "Helvetica"),
        border=1,
        fill=True,
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.set_draw_color(0, 0, 0)
    pdf.ln(1)


def _table(pdf, headers, rows, col_widths=None):
    """A bordered table, header row bold (sop.scenario_table / revision_history)."""
    pdf.set_font(pdf._font_family, "", 10)
    pdf.set_text_color(*_INK)
    with pdf.table(col_widths=col_widths, text_align="LEFT") as table:
        for row in [headers, *rows]:
            cells = table.row()
            for value in row:
                cells.cell(_safe_text(value, pdf._font_family))
    pdf.ln(2)


def _authored(pdf, item, values, sub):
    """An authored step extra's / block's bullets, commands and warning
    (docx_assembler._authored's counterpart; inline markup shown plain)."""
    for text in item["bullets"]:
        _sub_bullet(pdf, auth.plain(auth.substitute(text, values)), sub)
    for command in item["commands"]:
        _command(pdf, auth.substitute(command, values))
    if item["warning"]:
        warning = auth.plain(auth.substitute(item["warning"], values))
        _text_line(pdf, f"Warning: {warning}", "B", 10, _VERIFY, 5.5)


def _authored_table(pdf, table, values):
    sub = [[auth.substitute(c, values) for c in row] for row in table["rows"]]
    _table(pdf, [auth.substitute(h, values) for h in table["headers"]], sub)


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
    phases=None,
    omitted_screenshots=(),
    subtitle=None,
    authoring=None,
    version_stamp=None,
):
    """Builds a PDF mirroring the docx layout (docx_assembler.py): a title
    page (with doc number/author/revision/version stamp when given), a table
    of contents, then assembler.document_outline -- numbered sections,
    lettered step headings with the step text as a bullet, any authored
    content (authoring.py) -- and a closing revision-history table.
    step_results may carry an optional "narration" key per step (placed
    under the step, matching the docx/markdown/html exports). `date`, if not
    given, is the manifest's own real session date (never a hardcoded
    placeholder); an authored date overrides both. Returns output_path."""
    a = authoring or auth.EMPTY
    values = auth.param_values(a)
    date = a["date"] or date or format_doc_date(manifest.session.started_utc)
    outline = document_outline(manifest, step_results, narrative_text, phases, a)
    shot_of = dict(zip(manifest.step_ids(), annotated_paths, strict=True))

    def _build(pages):
        page_of = {}
        pdf = _SOPPdf()
        pdf._font_family = _register_font(pdf)
        title = manifest.session.title or manifest.session.id
        pdf._running_title = _safe_text(title, pdf._font_family)
        pdf.set_auto_page_break(auto=True, margin=18)

        # --- Title page ---
        pdf.add_page()
        pdf.ln(50)
        _text_line(pdf, title.upper(), "B", 26, h=14, align="C")
        line2 = auth.substitute(a["title_line2"], values).upper()
        if line2:
            _text_line(pdf, a["title_connector"], "I", 20, h=11, align="C")
            _text_line(pdf, line2, "B", 26, h=14, align="C")
        pdf.ln(6)
        _text_line(pdf, subtitle or "Standard Operating Procedure", "", 12, _MUTED, 7, "C")
        pdf.ln(10)
        meta_lines = []
        if doc_no:
            meta_lines.append(f"Document No: {doc_no}")
        meta_lines.append(f"Revision {revision}  —  Released {date}")
        if author:
            meta_lines.append(f"Author: {author}")
        _text_line(pdf, "\n".join(meta_lines), "", 12, _MUTED, 7, "C")
        if version_stamp:
            _text_line(pdf, version_stamp, "I", 9, _MUTED, 5, "C")

        # --- Table of contents ---
        pdf.add_page()
        pdf.set_font(pdf._font_family, "B", 16)
        pdf.set_text_color(*_ACCENT)
        pdf.cell(0, 11, "Table of Contents", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_INK)
        pdf.ln(2)
        # toc_entries (assembler.py) is the single source of truth for the outline
        # itself -- shared with docx_assembler.py so the two TOCs can't drift
        # out of sync. Indented step entries (the "      " prefix that same
        # helper produces) get the smaller/muted styling; numbered section
        # lines get the larger plain one. Each line reserves a fixed-width page
        # number slot, so pass 1 (no numbers yet) lays out exactly like pass 2.
        for key, line in toc_entries(manifest, narrative_text, phases, step_results, a):
            step_line = line.startswith("      ")
            pdf.set_font(pdf._font_family, "", 10 if step_line else 11)
            if step_line:
                pdf.set_text_color(*_MUTED)
            h = 6 if step_line else 7
            w = pdf.epw - _TOC_PAGE_W
            text = _safe_text(line, pdf._font_family)
            # Break before a (possibly wrapped) line, never inside it, so the
            # page number can sit on the line's first row.
            rows = len(pdf.multi_cell(w, h, text, dry_run=True, output="LINES"))
            if pdf.will_page_break(rows * h):
                pdf.add_page()
            y = pdf.get_y()
            pdf.multi_cell(w, h, text, new_x="LMARGIN", new_y="NEXT")
            end_y = pdf.get_y()
            pdf.set_xy(pdf.l_margin + w, y)
            pdf.cell(_TOC_PAGE_W, h, str(pages.get(key, "")), align="R")
            pdf.set_xy(pdf.l_margin, end_y)
            pdf.set_text_color(*_INK)

        # --- Body: the shared outline. Overview, the first content section
        # and Revision History each start a page; later sections flow on. ---
        first_body = True
        for item in outline:
            if item[0] == "section":
                _, key, text = item
                if key in ("overview", "revision") or first_body:
                    pdf.add_page()
                    first_body = key == "overview"
                else:
                    pdf.ln(4)
                page_of[key] = _section_heading(pdf, text)
                if key == "overview":
                    _narrative_body(pdf, narrative_text)
                elif key == "prereq":
                    for prereq in a["prerequisites"]:
                        _bullet(pdf, auth.plain(auth.substitute(prereq, values)))
                elif key == "revision":
                    _table(
                        pdf,
                        ("Date", "Revision", "Description", "Author"),
                        auth.revision_rows(a, date, revision, author),
                        col_widths=(35, 25, 70, 40),
                    )
                continue

            if item[0] == "block":
                _, block, heading = item
                if heading:
                    pdf.set_font(pdf._font_family, "BI", 13)
                    _heading(pdf, auth.substitute(heading, values), 9)
                    if auth.tag_of(block):
                        _text_line(pdf, f"— {auth.tag_of(block)}", "BI", 10, _VERIFY, 5.5)
                for para in block["text"].split("\n\n") if block["text"] else ():
                    _text_line(pdf, auth.plain(auth.substitute(para.strip(), values)))
                    pdf.ln(1)
                _authored(pdf, block, values, sub=False)
                if block["table"]:
                    _authored_table(pdf, block["table"], values)
                if block["placeholder"]:
                    placeholder = auth.substitute(block["placeholder"], values)
                    _text_line(pdf, f"[IMAGE PLACEHOLDER: {placeholder}]", "I", 10, _MUTED, 6, "C")
                pdf.ln(4)
                continue

            _, n, step, result, heading = item
            extra = auth.step_extra(a, step.id) or {}
            pdf.set_font(pdf._font_family, "BI", 13)
            pdf.set_text_color(*_INK)
            page_of[step.id] = _heading(pdf, heading, 9)

            _bullet(pdf, result["text"])
            if auth.tag_of(extra):
                _text_line(pdf, f"— {auth.tag_of(extra)}", "BI", 10, _VERIFY, 5.5)
            if extra:
                _authored(pdf, extra, values, sub=True)

            for note in step_review_notes(step):
                _text_line(pdf, note, "B", 10, _VERIFY, 5.5)

            if result.get("narration"):
                pdf.ln(1)
                _text_line(pdf, f"Narration: {result['narration']}", "I", 10, _MUTED, 5.5)

            shot = shot_of[step.id]
            if step.id not in omitted_screenshots and shot is not None and Path(shot).exists():
                pdf.ln(2)
                width = min(extra["width_in"] * 25.4, pdf.epw) if extra.get("width_in") else 130
                pdf.image(str(shot), w=width)
                _text_line(pdf, step_heading(n, step, result), "I", 9, _MUTED, 5, "C")
            if extra.get("table"):
                pdf.ln(2)
                _authored_table(pdf, extra["table"], values)
            pdf.ln(6)

        return pdf, page_of

    # Two passes: the first finds the page each TOC entry lands on, the
    # second prints those numbers (same layout, so the same pages).
    _pdf, page_of = _build({})
    pdf, _ = _build(page_of)
    pdf.output(str(output_path))
    return output_path
