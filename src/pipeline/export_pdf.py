"""PDF export (AC1 part 1): a manifest -> PDF with one section per step,
annotated screenshots embedded, [verify] blockquotes rendered. Pure-Python
via fpdf2 — no GTK (weasyprint) or Word COM automation dependency, both of
which are hostile to PyInstaller freezing and to a headless build VM."""

from pathlib import Path

from fpdf import FPDF


def _safe_text(text):
    """fpdf2's core Helvetica font only supports Latin-1; replace anything
    outside that range so a PDF export never crashes on transcribed speech
    text (which can realistically contain curly quotes, accented names,
    etc.) — the export path must always succeed, the same way invariant
    L3's template fallback always succeeds."""
    return text.encode("latin-1", "replace").decode("latin-1")


def render_pdf(manifest, step_results, annotated_paths, output_path, narrative_text=None):
    """Builds a PDF: title (+ optional narrative/verify-blockquote text) on
    its own page, then one full page per step (heading, text, embedded
    annotated screenshot) — guaranteeing page count > step count regardless
    of content length. Returns output_path."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    pdf.add_page()
    title = manifest.session.title or manifest.session.id
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, _safe_text(title), new_x="LMARGIN", new_y="NEXT")

    if narrative_text:
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 6, _safe_text(narrative_text))

    for step, result, shot in zip(manifest.steps, step_results, annotated_paths, strict=True):
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 10, _safe_text(f"Step {step.id}"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 6, _safe_text(result["text"]))
        if shot is not None and Path(shot).exists():
            pdf.ln(4)
            pdf.image(str(shot), w=120)

    pdf.output(str(output_path))
    return output_path
