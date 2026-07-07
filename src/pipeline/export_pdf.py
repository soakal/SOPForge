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

_INK = (33, 37, 41)
_MUTED = (110, 116, 124)
_ACCENT = (37, 99, 235)


def _safe_text(text):
    """fpdf2's core Helvetica font only supports Latin-1; replace anything
    outside that range so a PDF export never crashes on transcribed speech
    text (curly quotes, accented names, etc.) -- the export path must always
    succeed, the same way invariant L3's template fallback always succeeds."""
    return text.encode("latin-1", "replace").decode("latin-1")


class _SOPPdf(FPDF):
    def header(self):
        # No header on the title page (page 1); a thin running title after.
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*_MUTED)
        self.cell(0, 8, self._running_title, align="R")
        self.ln(10)
        self.set_text_color(*_INK)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*_MUTED)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")
        self.set_text_color(*_INK)


def _bullet(pdf, text):
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(*_INK)
    x = pdf.get_x()
    pdf.cell(6, 6, chr(149))  # bullet dot
    pdf.set_x(x + 6)
    pdf.multi_cell(0, 6, _safe_text(text))


def render_pdf(manifest, step_results, annotated_paths, output_path, narrative_text=None):
    """Builds a PDF mirroring the docx layout. step_results may carry an
    optional "narration" key per step (placed under the step, matching the
    docx/markdown/html exports). Returns output_path."""
    pdf = _SOPPdf()
    title = manifest.session.title or manifest.session.id
    pdf._running_title = _safe_text(title)
    pdf.set_auto_page_break(auto=True, margin=18)

    # --- Title page ---
    pdf.add_page()
    pdf.ln(60)
    pdf.set_font("Helvetica", "B", 26)
    pdf.set_text_color(*_INK)
    pdf.multi_cell(0, 14, _safe_text(title.upper()), align="C")
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(*_MUTED)
    pdf.multi_cell(
        0, 7, _safe_text(f"Standard Operating Procedure\n{manifest.session.started_utc}"), align="C"
    )
    if narrative_text:
        pdf.ln(8)
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(*_INK)
        pdf.multi_cell(0, 6, _safe_text(narrative_text))

    # --- Steps section ---
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(*_ACCENT)
    pdf.cell(0, 12, "Steps", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_INK)
    pdf.ln(2)

    for step, result, shot in zip(manifest.steps, step_results, annotated_paths, strict=True):
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(*_INK)
        pdf.cell(0, 10, _safe_text(f"Step {step.id}"), new_x="LMARGIN", new_y="NEXT")

        _bullet(pdf, result["text"])

        if result.get("narration"):
            pdf.ln(1)
            pdf.set_font("Helvetica", "I", 10)
            pdf.set_text_color(*_MUTED)
            pdf.multi_cell(0, 5.5, _safe_text(f"Narration: {result['narration']}"))
            pdf.set_text_color(*_INK)

        if shot is not None and Path(shot).exists():
            pdf.ln(2)
            pdf.image(str(shot), w=130)
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(*_MUTED)
            pdf.multi_cell(0, 5, _safe_text(step.id), align="C")
            pdf.set_text_color(*_INK)
        pdf.ln(6)

    # --- Revision history (mirrors sop.revision_history in the docx) ---
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*_ACCENT)
    pdf.cell(0, 11, "Revision History", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_INK)
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 10)
    widths = (35, 25, 70, 40)
    for label, w in zip(("Date", "Revision", "Description", "Author"), widths):
        pdf.cell(w, 8, label, border=1)
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 10)
    for value, w in zip(
        (manifest.session.started_utc[:10], "1.0", "Initial generation", "SOPForge"), widths
    ):
        pdf.cell(w, 8, _safe_text(value), border=1)
    pdf.ln(8)

    pdf.output(str(output_path))
    return output_path
