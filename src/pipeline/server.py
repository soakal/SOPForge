"""sopforge-server: FastAPI app consuming manifests + screenshot PNGs,
running the Phase 2 template-mode pipeline (render.py + sidecar.py), and
exposing session status, the sidecar report, generated docs, and a
plain-HTML review page for a human reviewer.

Everything here runs in template mode (task-12) — invariant L3 guarantees
that's always available and always factually correct; no LLM call happens
server-side yet. Sessions are processed synchronously and kept in an
in-memory dict; a session id is only ever handed back once processing
actually succeeded, so there's nothing orphaned or unreachable to track."""

import json
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse

from pipeline.manifest import load_manifest
from pipeline.render import render_html, render_markdown, render_steps_template_mode
from pipeline.sidecar import build_sidecar_report
from pipeline.webui.review import render_review_page


def create_app(sessions_root: Path) -> FastAPI:
    app = FastAPI()
    sessions_root.mkdir(parents=True, exist_ok=True)
    sessions = {}

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

        try:
            step_results, annotated_paths = render_steps_template_mode(
                manifest, screenshots_dir, annotated_dir
            )
            # Pure template mode never attempts an LLM call, so nothing here
            # "fell back" from anything — used_fallback is False for every
            # step (build_sidecar_report's shape, not assemble_steps' own).
            report_step_results = [{**result, "used_fallback": False} for result in step_results]
            report = build_sidecar_report(manifest, report_step_results, [], {})
            md = render_markdown(manifest, step_results, annotated_paths, base_dir=annotated_dir)
            html_doc = render_html(manifest, step_results, annotated_paths, base_dir=annotated_dir)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"generation failed: {exc}") from exc

        sessions[session_id] = {"status": "done", "report": report, "md": md, "html": html_doc}
        return {"session_id": session_id, "status": "done"}

    def _session_or_404(session_id):
        session = sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        return session

    @app.get("/sessions/{session_id}/status")
    async def get_status(session_id: str):
        return {"status": _session_or_404(session_id)["status"]}

    @app.get("/sessions/{session_id}/report")
    async def get_report(session_id: str):
        return _session_or_404(session_id)["report"]

    @app.get("/sessions/{session_id}/doc.md")
    async def get_doc_md(session_id: str):
        return PlainTextResponse(_session_or_404(session_id)["md"], media_type="text/markdown")

    @app.get("/sessions/{session_id}/doc.html")
    async def get_doc_html(session_id: str):
        return HTMLResponse(_session_or_404(session_id)["html"])

    @app.get("/sessions/{session_id}/review")
    async def get_review(session_id: str):
        return HTMLResponse(render_review_page(_session_or_404(session_id)["report"]))

    return app
