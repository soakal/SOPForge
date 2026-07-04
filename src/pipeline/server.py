"""sopforge-server: FastAPI app consuming manifests + screenshot PNGs,
running the Phase 2/3 template-mode pipeline (render.py + sidecar.py +
export_*.py) in the background (jobs.py), and exposing session status,
the sidecar report, generated docs, and a plain-HTML review page.

Generation is queued on a background worker thread (task-05) — POST
/sessions returns as soon as the upload is validated and saved, never
blocking on the actual rendering/export work; status moves
queued -> processing -> done | error. Rendered artifacts are written to
each session's own directory on disk (not duplicated in memory) and read
back on each GET, the same way the manifest/screenshots themselves already
lived on disk rather than in a Python object."""

import io
import json
import mimetypes
import shutil
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from pipeline.config import load_models_config
from pipeline.docx_assembler import assemble_docx
from pipeline.export_html import render_single_file_html
from pipeline.export_md import export_markdown_bundle
from pipeline.export_pdf import render_pdf
from pipeline.jobs import JobRunner
from pipeline.library import search as library_search
from pipeline.library import upsert_entry
from pipeline.manifest import load_manifest
from pipeline.render import render_html, render_markdown, render_steps_template_mode
from pipeline.sidecar import build_sidecar_report
from pipeline.webui.pages import (
    render_library_page,
    render_session_page,
    render_session_processing_page,
)
from pipeline.webui.review import render_review_page


def _zip_directory(directory):
    directory = Path(directory)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(directory))
    return buf.getvalue()


def create_app(sessions_root: Path) -> FastAPI:
    app = FastAPI()
    sessions_root.mkdir(parents=True, exist_ok=True)
    jobs = JobRunner()
    # session_id -> (manifest, screenshots_dir, annotated_dir, session_dir)
    sessions = {}

    def _generate(session_id):
        manifest, screenshots_dir, annotated_dir, session_dir = sessions[session_id]

        step_results, annotated_paths = render_steps_template_mode(
            manifest, screenshots_dir, annotated_dir
        )
        report_step_results = [{**result, "used_fallback": False} for result in step_results]
        report = build_sidecar_report(manifest, report_step_results, [], {})

        md = render_markdown(manifest, step_results, annotated_paths, base_dir=annotated_dir)
        (session_dir / "doc.md").write_text(md, encoding="utf-8")

        html_doc = render_html(manifest, step_results, annotated_paths, base_dir=annotated_dir)
        (session_dir / "doc.html").write_text(html_doc, encoding="utf-8")

        docx_path = session_dir / "doc.docx"
        _out, docx_warnings = assemble_docx(manifest, step_results, annotated_dir, docx_path)
        report["docx_warnings"] = docx_warnings

        pdf_path = session_dir / "doc.pdf"
        render_pdf(manifest, step_results, annotated_paths, pdf_path)

        single_html = render_single_file_html(manifest, step_results, annotated_paths)
        (session_dir / "doc.single.html").write_text(single_html, encoding="utf-8")

        md_bundle_dir = session_dir / "md_bundle"
        export_markdown_bundle(manifest, step_results, annotated_paths, md_bundle_dir)
        (session_dir / "export.md.zip").write_bytes(_zip_directory(md_bundle_dir))

        (session_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
        upsert_entry(sessions_root, session_id, manifest, report)

    @app.post("/sessions")
    def create_session(manifest_json: str = Form(...), files: list[UploadFile] = File(default=[])):
        try:
            manifest = load_manifest(json.loads(manifest_json))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid manifest: {exc}") from exc

        session_id = str(uuid.uuid4())
        session_dir = sessions_root / session_id
        screenshots_dir = session_dir / "screenshots"
        annotated_dir = session_dir / "annotated"
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        try:
            for upload in files:
                # Wire-supplied filename must never be trusted as a path —
                # Path(...).name strips any directory components (including
                # "../" traversal), and resolving under screenshots_dir with
                # a containment check catches anything that survives that.
                name = Path(upload.filename or "").name
                if not name:
                    raise HTTPException(status_code=400, detail="uploaded file has no filename")
                dest = (screenshots_dir / name).resolve()
                if screenshots_dir.resolve() not in dest.parents:
                    raise HTTPException(status_code=400, detail=f"invalid filename: {name!r}")
                with dest.open("wb") as out:
                    shutil.copyfileobj(upload.file, out)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid upload: {exc}") from exc

        sessions[session_id] = (manifest, screenshots_dir, annotated_dir, session_dir)
        jobs.submit(session_id, lambda: _generate(session_id))
        return {"session_id": session_id, "status": jobs.status(session_id)["status"]}

    @app.post("/sessions/{session_id}/rerender")
    def rerender(session_id: str):
        """Re-runs generation + all exports for an already-uploaded session
        against the current config/models.toml (a no-op on template-mode
        output today, since no LLM call reads it yet — the hook exists so
        LLM-backed generation can be re-triggered after a config change
        without re-uploading the manifest/screenshots)."""
        _require_known_session(session_id)
        jobs.submit(session_id, lambda: _generate(session_id))
        return {"session_id": session_id, "status": jobs.status(session_id)["status"]}

    def _require_known_session(session_id):
        if session_id not in sessions:
            raise HTTPException(status_code=404, detail="session not found")

    def _require_done(session_id):
        _require_known_session(session_id)
        status = jobs.status(session_id)
        if status["status"] != "done":
            raise HTTPException(
                status_code=409, detail=f"session not ready (status: {status['status']})"
            )
        return sessions[session_id][3]  # session_dir

    @app.get("/sessions/{session_id}/status")
    def get_status(session_id: str):
        _require_known_session(session_id)
        status = jobs.status(session_id)
        body = {"status": status["status"]}
        if status["status"] == "error":
            body["error"] = status["error"]
        return body

    @app.get("/sessions/{session_id}/report")
    def get_report(session_id: str):
        session_dir = _require_done(session_id)
        return json.loads((session_dir / "report.json").read_text(encoding="utf-8"))

    @app.get("/sessions/{session_id}/doc.md")
    def get_doc_md(session_id: str):
        session_dir = _require_done(session_id)
        return PlainTextResponse(
            (session_dir / "doc.md").read_text(encoding="utf-8"), media_type="text/markdown"
        )

    @app.get("/sessions/{session_id}/doc.html")
    def get_doc_html(session_id: str):
        session_dir = _require_done(session_id)
        return HTMLResponse((session_dir / "doc.html").read_text(encoding="utf-8"))

    @app.get("/sessions/{session_id}/review")
    def get_review(session_id: str):
        session_dir = _require_done(session_id)
        report = json.loads((session_dir / "report.json").read_text(encoding="utf-8"))
        return HTMLResponse(render_review_page(report))

    @app.get("/sessions/{session_id}/doc.docx")
    def get_doc_docx(session_id: str):
        session_dir = _require_done(session_id)
        return Response(
            (session_dir / "doc.docx").read_bytes(),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": 'attachment; filename="doc.docx"'},
        )

    @app.get("/sessions/{session_id}/doc.pdf")
    def get_doc_pdf(session_id: str):
        session_dir = _require_done(session_id)
        return Response(
            (session_dir / "doc.pdf").read_bytes(),
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="doc.pdf"'},
        )

    @app.get("/sessions/{session_id}/doc.single.html")
    def get_doc_single_html(session_id: str):
        session_dir = _require_done(session_id)
        return Response(
            (session_dir / "doc.single.html").read_text(encoding="utf-8"),
            media_type="text/html",
            headers={"Content-Disposition": 'attachment; filename="doc.single.html"'},
        )

    @app.get("/sessions/{session_id}/export.md.zip")
    def get_export_md_zip(session_id: str):
        session_dir = _require_done(session_id)
        return Response(
            (session_dir / "export.md.zip").read_bytes(),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="export.md.zip"'},
        )

    @app.get("/library")
    def get_library(q: str | None = None):
        return library_search(sessions_root, q)

    @app.get("/config")
    def get_config():
        return load_models_config().model_dump()

    @app.get("/ui")
    def ui_library(q: str | None = None):
        return HTMLResponse(render_library_page(library_search(sessions_root, q), q))

    @app.get("/ui/sessions/{session_id}")
    def ui_session(session_id: str):
        _require_known_session(session_id)
        status = jobs.status(session_id)
        if status["status"] != "done":
            return HTMLResponse(render_session_processing_page(session_id, status))
        session_dir = sessions[session_id][3]
        report = json.loads((session_dir / "report.json").read_text(encoding="utf-8"))
        config = load_models_config().model_dump()
        return HTMLResponse(render_session_page(session_id, report, config))

    @app.get("/sessions/{session_id}/{filename}")
    def get_annotated_image(session_id: str, filename: str):
        """Serves annotated screenshots that doc.html references by bare
        relative filename (task-12's base_dir=annotated_dir) — without
        this, the /ui doc-preview iframe's <img> tags 404 and every
        screenshot in the preview shows broken. Registered last so it
        never shadows the specific routes above (doc.md, doc.pdf, etc.)."""
        _require_known_session(session_id)
        annotated_dir = sessions[session_id][2]
        try:
            name = Path(filename).name
            dest = (annotated_dir / name).resolve()
        except (ValueError, OSError) as exc:
            # e.g. a URL-encoded null byte reaching Path(...).resolve() as
            # "embedded null character in path" — a malformed request, not
            # a server error.
            raise HTTPException(status_code=404, detail="file not found") from exc
        if annotated_dir.resolve() not in dest.parents or not dest.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        mime, _ = mimetypes.guess_type(str(dest))
        return Response(dest.read_bytes(), media_type=mime or "application/octet-stream")

    return app
