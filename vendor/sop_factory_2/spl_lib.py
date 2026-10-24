"""
spl_lib.py — builds VRSI Single Point Lesson (SPL) decks from
template\\Single Point Lesson Template V4.potm.

    from spl_lib import SPLDeck
    deck = SPLDeck(output_pptx=r"...\\output\\X\\X_SPL_v1.0.pptx", author="Ted Hauke")
    deck.title_slide("MFS Job Creation", image="MFS/Auto/001.jpg")
    deck.prerequisites(["Jobscope access", "Tracker edit rights"], image=None)
    deck.step("Create the job", ["Click New", "Type the part number"],
              notes="Do not use Add WO", images=["MFS/Manual/013.jpg"])
    deck.save()   # builds, fits every text box in PowerPoint, exports previews

save() opens the deck in PowerPoint (COM), measures every text frame and shrinks
overflowing text 1pt at a time down to a floor (12pt for body text). Anything that
still does not fit raises TextOverflow naming the slide, so a deck with cut-off text
is never handed over silently: split the steps across slides (step(..., start_at=N)).

Requires: python-pptx, Pillow, pywin32, PowerPoint installed.
"""

import copy
import io
import re
import zipfile
from pathlib import Path
from typing import Optional, Sequence, Union

from PIL import Image
from pptx import Presentation
from pptx.util import Pt

TEMPLATE = Path(__file__).with_name("Single Point Lesson Template V4.potm")
BODY_FLOOR_PT = 12

ImageSpec = Union[str, dict]  # "path" or {"path", "callouts": [(n, x, y)], "verdict"}


class TextOverflow(RuntimeError):
    pass


def _open_potm(path: Path) -> Presentation:
    """python-pptx can't open a macro-enabled template: relabel it as a .pptx and
    drop the VBA (its one macro only writes the file path, which save() does)."""
    src = zipfile.ZipFile(path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "ppt/vbaProject.bin":
                continue
            if item.filename == "[Content_Types].xml":
                data = data.replace(b"application/vnd.ms-powerpoint.template.macroEnabled.main+xml",
                                    b"application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml")
            if item.filename == "ppt/_rels/presentation.xml.rels":
                data = re.sub(rb'<Relationship [^>]*vbaProject[^>]*/>', b"", data)
            out.writestr(item, data)
    return Presentation(io.BytesIO(buf.getvalue()))


class SPLDeck:
    def __init__(self, output_pptx: str, author: str,
                 active_dir: Optional[str] = None, template: Path = TEMPLATE):
        self.output = Path(output_pptx)
        self.author = author
        self.active_dir = Path(active_dir) if active_dir else Path(__file__).parent.parent / "active"
        self.prs = _open_potm(Path(template))
        self.layouts = {l.name.strip(): l for l in self.prs.slide_layouts}
        # Callout / verdict shapes live on the template's "Template Shapes" slide.
        self.shapes = {s.name: copy.deepcopy(s._element) for s in self.prs.slides[-1].shapes}
        sld_ids = self.prs.slides._sldIdLst
        for sld_id in list(sld_ids):  # start from an empty deck
            self.prs.part.drop_rel(sld_id.rId)
            sld_ids.remove(sld_id)

    # ------------------------------------------------------------- slide types
    def title_slide(self, title: str, image: Optional[ImageSpec] = None):
        s = self._new("Work Instruction Title")
        s.shapes.title.text = title
        self._fill(s, 1, image)
        return self._finish(s)

    def prerequisites(self, items: Sequence[str], image: Optional[ImageSpec] = None,
                      title: str = "Prerequisites"):
        s = self._new("Tools Required")
        s.shapes.title.text = title
        self._lines(self._ph(s, 14), items)
        self._fill(s, 15, image)
        return self._finish(s)

    def components(self, title: str = "Components Required", image: Optional[ImageSpec] = None,
                   items: Optional[Sequence[str]] = None):
        s = self._new("Components Required")
        s.shapes.title.text = title
        if items:
            self._lines(self._ph(s, 13), items)
        else:
            self._fill(s, 13, image)
        return self._finish(s)

    def step(self, task: str, steps: Sequence[str], notes: str = "",
             images: Sequence[ImageSpec] = (), start_at: int = 1):
        """One task slide: 1 or 2 images on top, task label, numbered steps, notes.
        start_at continues the numbering when a task is split across slides."""
        if len(images) > 2:
            raise ValueError("step(): at most 2 images per slide")
        s = self._new("Assembly Instruction x2 Images" if len(images) == 2
                      else "Assembly Instruction x1 Image")
        self._ph(s, 2).text_frame.text = task
        body = self._ph(s, 13)
        self._lines(body, steps)
        if start_at != 1:
            for p in body.text_frame.paragraphs:
                p._p.get_or_add_pPr().insert(0, _autonum(start_at))
        if notes:
            self._ph(s, 14).text_frame.text = notes
        for idx, img in zip((16, 17) if len(images) == 2 else (15,), images):
            self._fill(s, idx, img)
        return self._finish(s)

    def single_component(self, part_number: str, description: str, qty: Union[int, str],
                         image: Optional[ImageSpec] = None):
        s = self._new("Single Component")
        self._ph(s, 2).text_frame.text = part_number
        self._ph(s, 13).text_frame.text = description
        self._ph(s, 14).text_frame.text = f"Qty: {qty}"
        self._fill(s, 15, image)
        return self._finish(s)

    def prints(self, image: ImageSpec):
        s = self._new("Wiring Diagrams/Build Prints")
        self._fill(s, 13, image)
        return self._finish(s)

    # ---------------------------------------------------------------- helpers
    def _new(self, layout: str):
        return self.prs.slides.add_slide(self.layouts[layout])

    @staticmethod
    def _ph(slide, idx: int):
        return slide.placeholders[idx]

    @staticmethod
    def _lines(ph, items: Sequence[str]):
        tf = ph.text_frame
        tf.text = items[0] if items else ""
        for line in items[1:]:
            tf.add_paragraph().text = line

    def _fill(self, slide, idx: int, img: Optional[ImageSpec]):
        """Fit the image inside placeholder idx (no cropping), then drop the placeholder."""
        if img is None:
            return
        spec = {"path": img} if isinstance(img, str) else img
        ph = self._ph(slide, idx)
        path = Path(spec["path"])
        if not path.is_absolute():
            path = self.active_dir / path
        w, h = Image.open(path).size
        scale = min(ph.width / w, ph.height / h)
        pw, ph_h = int(w * scale), int(h * scale)
        left, top = ph.left + (ph.width - pw) // 2, ph.top + (ph.height - ph_h) // 2
        slide.shapes.add_picture(str(path), left, top, pw, ph_h)
        ph._element.getparent().remove(ph._element)
        for n, fx, fy in spec.get("callouts", ()):
            self._clone(slide, "Oval 6", left + int(pw * fx), top + int(ph_h * fy), str(n))
        verdict = spec.get("verdict")
        if verdict:
            label, mark = {"correct": ("Rectangle 8", "Rectangle 11"),
                           "incorrect": ("Rectangle 9", "Rectangle 10")}[verdict]
            lab = self._clone(slide, label, left + Pt(4), top + Pt(4))
            self._clone(slide, mark, lab.left + lab.width + Pt(6), top + Pt(2))

    def _clone(self, slide, name: str, left: int, top: int, text: Optional[str] = None):
        el = copy.deepcopy(self.shapes[name])
        slide.shapes._spTree.append(el)
        shp = slide.shapes[-1]
        shp.left, shp.top = left, top
        if text is not None:
            shp.text_frame.paragraphs[0].runs[0].text = text
        return shp

    def _finish(self, slide):
        """Remove unused placeholders and add the layout's date/footer/number boxes
        (python-pptx doesn't copy those)."""
        for ph in list(slide.placeholders):
            if ph.has_text_frame and not ph.text_frame.text.strip():
                ph._element.getparent().remove(ph._element)
        for lph in slide.slide_layout.placeholders:
            if lph.placeholder_format.type is not None and \
                    lph.placeholder_format.type.name in ("DATE", "FOOTER", "SLIDE_NUMBER"):
                el = copy.deepcopy(lph._element)
                slide.shapes._spTree.append(el)
                if lph.placeholder_format.type.name == "FOOTER":
                    slide.shapes[-1].text_frame.text = f"Last Edited By: {self.author}"
        return slide

    # ------------------------------------------------------------------ save
    def save(self, preview_dir: Optional[str] = None) -> Path:
        for shp in self.prs.slide_master.shapes:
            if shp.name == "FilePath":  # what the template's macro would write
                # One line at 8pt holds ~120 characters; past that show the file name.
                path = str(self.output)
                shp.text_frame.paragraphs[0].runs[0].text = path if len(path) <= 120 else self.output.name
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.prs.save(self.output)
        problems = fit_text(self.output, preview_dir)
        if problems:
            raise TextOverflow("Text does not fit even at the floor size:\n  " +
                               "\n  ".join(problems) +
                               "\nShorten it or split the steps across slides (start_at=).")
        return self.output


def _autonum(start: int):
    from pptx.oxml.ns import qn
    from lxml import etree
    el = etree.Element(qn("a:buAutoNum"))
    el.set("type", "arabicPeriod")
    el.set("startAt", str(start))
    return el


def fit_text(pptx_path: Path, preview_dir: Optional[str] = None) -> list:
    """Open in PowerPoint, shrink overflowing text to its floor, save, and return the
    shapes that still overflow. Optionally export one PNG per slide."""
    import win32com.client
    app = win32com.client.DispatchEx("PowerPoint.Application")
    pres = app.Presentations.Open(str(Path(pptx_path).resolve()), False, False, False)
    problems = []
    try:
        sw, sh = pres.PageSetup.SlideWidth, pres.PageSetup.SlideHeight
        for slide in pres.Slides:
            for shp in slide.Shapes:
                # Placeholders only: callout/verdict badges are fixed template shapes
                # whose short text already sits tight in its box.
                if shp.Type != 14 or not shp.HasTextFrame or not shp.TextFrame.HasText:
                    continue
                tf2, rng = shp.TextFrame2, shp.TextFrame2.TextRange
                if tf2.AutoSize == 1:  # shape grows to fit its text; check the slide edge only
                    if shp.Top + shp.Height > sh + 0.5 or shp.Left + shp.Width > sw + 0.5:
                        problems.append(f"slide {slide.SlideIndex} '{shp.Name}': runs off the slide")
                    continue
                avail_h = shp.Height - tf2.MarginTop - tf2.MarginBottom
                avail_w = shp.Width - tf2.MarginLeft - tf2.MarginRight
                size = rng.Font.Size if rng.Font.Size > 0 else rng.Runs(1).Font.Size
                floor = BODY_FLOOR_PT if size >= BODY_FLOOR_PT else size * 0.75
                over = lambda: rng.BoundHeight > avail_h + 0.5 or rng.BoundWidth > avail_w + 0.5
                while over() and size - 1 >= floor:
                    size -= 1
                    rng.Font.Size = size
                if over():
                    problems.append(f"slide {slide.SlideIndex} '{shp.Name}': "
                                    f"{rng.Text[:60]!r}")
        pres.Save()
        if preview_dir:
            Path(preview_dir).mkdir(parents=True, exist_ok=True)
            for slide in pres.Slides:
                slide.Export(str(Path(preview_dir).resolve() / f"slide{slide.SlideIndex:02d}.png"),
                             "PNG", 1280, 960)
    finally:
        pres.Close()
        app.Quit()
    return problems


if __name__ == "__main__":
    # Self-check: one short step must fit untouched; a wall of text must raise.
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    d = SPLDeck(str(tmp / "ok.pptx"), author="Test")
    d.title_slide("Self-check deck")
    d.step("Short task", ["One", "Two"], notes="Note")
    d.save(preview_dir=str(tmp / "png"))
    d = SPLDeck(str(tmp / "bad.pptx"), author="Test")
    d.step("Long task", ["word " * 60] * 8)
    try:
        d.save()
        raise SystemExit("FAIL: overflow not detected")
    except TextOverflow as e:
        print("overflow detected as expected:", str(e).splitlines()[1])
    print("self-check passed:", tmp)
