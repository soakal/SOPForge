"""PDF/DOCX image extraction for the manifest-free photo build, plus EXIF
rotation of uploaded photos (pipeline.source_extract + /ui/build)."""

import io
import json
import os
import shutil

import docx
from docx.shared import Inches
from fastapi.testclient import TestClient
from fpdf import FPDF
from PIL import Image

from pipeline.config import default_config_path
from pipeline.server import create_app
from pipeline.source_extract import extract_images

from _stub_llm import stub_llm_client_factory

RED, GREEN, BLUE = (220, 0, 0), (0, 220, 0), (0, 0, 220)


def _png(color, size=(120, 80)):
    """Left half solid `color` (identifies the image), right half random
    noise so the PNG is well over the 5 KB minimum."""
    w, h = size
    im = Image.new("RGB", size, color)
    im.paste(Image.frombytes("RGB", (w // 2, h), os.urandom(w // 2 * h * 3)), (w // 2, 0))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    assert len(buf.getvalue()) > 5 * 1024
    return buf.getvalue()


def _tiny_png():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, "PNG")
    assert len(buf.getvalue()) < 5 * 1024
    return buf.getvalue()


def _pdf(*images):
    pdf = FPDF()
    for data in images:
        pdf.add_page()
        pdf.image(io.BytesIO(data), x=10, y=10, w=60)
    return bytes(pdf.output())


def _docx(*images):
    d = docx.Document()
    for data in images:
        d.add_paragraph("step")
        d.add_picture(io.BytesIO(data), width=Inches(2))
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _colors(blobs):
    out = []
    for b in blobs:
        with Image.open(io.BytesIO(b)) as im:
            out.append(im.convert("RGB").getpixel((2, 2)))
    return out


def test_pdf_images_in_order_and_small_skipped():
    blobs = extract_images(_pdf(_png(RED), _tiny_png(), _png(GREEN), _png(BLUE)))
    assert _colors(blobs) == [RED, GREEN, BLUE]


def test_docx_images_in_document_order_and_small_skipped(tmp_path):
    path = tmp_path / "src.docx"
    path.write_bytes(_docx(_png(BLUE), _tiny_png(), _png(RED)))
    assert _colors(extract_images(path)) == [BLUE, RED]


def test_cap_and_bad_document():
    assert len(extract_images(_pdf(_png(RED), _png(GREEN)), max_images=1)) == 1
    try:
        extract_images(b"not a document")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def _client(tmp_path):
    cfg = tmp_path / "models.toml"
    shutil.copyfile(default_config_path(), cfg)
    app = create_app(
        sessions_root=tmp_path / "sessions",
        llm_client_factory=stub_llm_client_factory,
        narrative_llm_client_factory=stub_llm_client_factory,
        config_path=cfg,
    )
    return TestClient(app)


def _build(client, files):
    resp = client.post("/ui/build", data={"title": "T"}, files=files, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    return resp.headers["location"].rsplit("/", 1)[-1]


def test_pdf_and_docx_upload_become_steps_in_upload_order(tmp_path):
    client = _client(tmp_path)
    sid = _build(
        client,
        [
            ("files", ("a.png", _png(BLUE), "image/png")),
            ("files", ("doc.pdf", _pdf(_png(RED), _png(GREEN)), "application/pdf")),
            ("files", ("doc.docx", _docx(_png(BLUE)), "application/octet-stream")),
        ],
    )
    session_dir = tmp_path / "sessions" / sid
    manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
    shots = [s["screenshot"] for s in manifest["steps"]]
    assert shots == ["001.png", "002.png", "003.png", "004.png"]
    colors = []
    for name in shots:
        with Image.open(session_dir / "screenshots" / name) as im:
            colors.append(im.convert("RGB").getpixel((2, 2)))
    assert colors == [BLUE, RED, GREEN, BLUE]


def test_pdf_without_images_is_rejected_without_orphan(tmp_path):
    client = _client(tmp_path)
    resp = client.post(
        "/ui/build", data={"title": "T"}, files=[("files", ("e.pdf", _pdf(), "application/pdf"))]
    )
    assert resp.status_code == 400
    assert list((tmp_path / "sessions").iterdir()) == []


def test_exif_rotated_jpeg_is_upright(tmp_path):
    im = Image.new("RGB", (200, 100), RED)
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90 CW to display
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    client = _client(tmp_path)
    sid = _build(client, [("files", ("phone.jpg", buf.getvalue(), "image/jpeg"))])
    with Image.open(tmp_path / "sessions" / sid / "screenshots" / "001.png") as out:
        assert out.size == (100, 200)
