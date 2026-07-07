"""sopforge-server: FastAPI app consuming manifests + screenshot PNGs,
running the LLM-backed generation pipeline (render.py's render_steps_llm_mode
+ sidecar.py + export_*.py) in the background (jobs.py), and exposing
session status, the sidecar report, generated docs, and a plain-HTML review
page. Step text comes from the LLM configured in config/models.toml (Ollama
or Anthropic), round-trip-gated with a per-step template fallback — never a
retry loop.

Generation is queued on a background worker thread (task-05) — POST
/sessions returns as soon as the upload is validated and saved, never
blocking on the actual rendering/export work; status moves
queued -> processing -> done | error. Rendered artifacts are written to
each session's own directory on disk (not duplicated in memory) and read
back on each GET, the same way the manifest/screenshots themselves already
lived on disk rather than in a Python object.

The in-memory `sessions` index is rebuilt from disk at startup
(_restore_sessions_from_disk) -- without this, a session becomes
permanently inaccessible via the API/UI the moment the process restarts,
even though the persistent library index (library.py) still lists it and
its generated docs are still sitting on disk. Each upload's raw manifest
JSON is persisted to session_dir/manifest.json specifically so this
restore is possible."""

import io
import json
import mimetypes
import os
import shutil
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from PIL import Image

from pipeline import __version__
from pipeline.photo_build import synthetic_manifest_dict
from pipeline.config import load_models_config
from pipeline.docx_assembler import assemble_docx
from pipeline.export_html import render_single_file_html
from pipeline.export_md import export_markdown_bundle
from pipeline.export_pdf import render_pdf
from pipeline.jobs import JobRunner
from pipeline.library import remove_entry
from pipeline.library import search as library_search
from pipeline.library import upsert_entry
from pipeline.llm_client import LLMClient
from pipeline.manifest import load_manifest
from pipeline.render import render_html, render_markdown, render_steps_llm_mode
from pipeline.sidecar import build_sidecar_report
from pipeline.transcript import align_transcript_to_steps
from pipeline.vision import caption_images
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


def _default_llm_client_factory():
    return LLMClient(load_models_config().steps)


def _restore_sessions_from_disk(sessions_root, sessions, jobs):
    """Rebuilds the in-memory `sessions` index from session directories a
    previous server run left on disk. Only restores sessions that finished
    cleanly (manifest.json AND report.json both present) -- a session that
    was mid-upload or mid-generation when the previous process died has no
    completed output to serve and was never added to the library index
    either (upsert_entry only runs after report.json is written), so it's
    left alone rather than guessed at."""
    if not sessions_root.exists():
        return
    for session_dir in sessions_root.iterdir():
        if not session_dir.is_dir():
            continue
        manifest_path = session_dir / "manifest.json"
        report_path = session_dir / "report.json"
        if not (manifest_path.exists() and report_path.exists()):
            continue
        try:
            manifest = load_manifest(manifest_path)
        except Exception:  # noqa: BLE001 - a corrupt leftover must never crash startup
            continue
        session_id = session_dir.name
        sessions[session_id] = (
            manifest,
            session_dir / "screenshots",
            session_dir / "annotated",
            session_dir,
        )
        jobs.seed_done(session_id)


def create_app(sessions_root: Path, llm_client_factory=None) -> FastAPI:
    """llm_client_factory: zero-arg callable returning an object with a
    .chat(messages) method (matching LLMClient's interface), called fresh
    for every generation. Defaults to a real LLMClient built from the
    CURRENT config/models.toml (loaded fresh each call, not cached at
    app-creation time, so /rerender's promise to reflect config edits is
    actually true). Tests override this to a fast, deterministic stub —
    without it, every session creation would make a real network attempt
    to the (usually unreachable in a dev/test environment) configured
    endpoint before falling back, adding real seconds per step."""
    app = FastAPI()

    @app.middleware("http")
    async def _no_store_html(request, call_next):
        """Never let the browser cache the HTML pages -- otherwise a stale
        library/review page keeps showing an old version footer or session
        list after an upgrade (the "still shows the old version" trap)."""
        response = await call_next(request)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        return response

    sessions_root.mkdir(parents=True, exist_ok=True)
    # Fail loudly at startup if sessions_root isn't actually writable, rather
    # than letting every upload blow up later with a bare 500 from deep inside
    # the ingest path (a real bug: installing under Program Files pointed
    # --sessions-root at an admin-only location while the server ran
    # unelevated, so mkdir on each upload raised PermissionError). mkdir with
    # exist_ok=True above succeeds on an already-existing dir even when it's
    # unwritable, so an explicit write probe is what actually catches it.
    _probe = sessions_root / ".sopforge-write-test"
    try:
        _probe.write_text("ok", encoding="utf-8")
        _probe.unlink()
    except OSError as exc:
        raise RuntimeError(
            f"sessions-root {sessions_root} is not writable ({exc}). Point "
            "--sessions-root at a directory this process can write to (e.g. a "
            "per-user location, not Program Files when running unelevated)."
        ) from exc
    jobs = JobRunner()
    make_llm_client = llm_client_factory or _default_llm_client_factory
    # session_id -> (manifest, screenshots_dir, annotated_dir, session_dir)
    sessions = {}
    _restore_sessions_from_disk(sessions_root, sessions, jobs)

    def _is_photo_mode(session_dir):
        mode = session_dir / "mode.txt"
        return mode.exists() and mode.read_text(encoding="utf-8").strip() == "photo"

    def _generate(session_id):
        manifest, screenshots_dir, annotated_dir, session_dir = sessions[session_id]

        # Manifest-free "screenshots + transcript" sessions take a different
        # path: no LLM step phrasing, no click-marker annotation.
        if _is_photo_mode(session_dir):
            _generate_photo(session_id)
            return

        # One generation attempt per step, round-trip-gated with a
        # template fallback (task-06) -- if the configured endpoint is
        # unreachable, or Anthropic routing is on with no API key, or the
        # reply doesn't hold up, that step just falls back; nothing here
        # ever retries.
        llm_client = make_llm_client()
        try:
            step_results, annotated_paths = render_steps_llm_mode(
                manifest, screenshots_dir, annotated_dir, llm_client
            )
        finally:
            close = getattr(llm_client, "close", None)
            if callable(close):
                close()

        # Place an uploaded transcript's narration under each step (by step
        # label / order for .txt/.md, by timestamp for .json). Best-effort: a
        # transcript is validated at upload time, but a problem here must never
        # break generation -- the doc is complete from the steps alone.
        transcript_note = _apply_transcript(session_dir, manifest, step_results)

        report = build_sidecar_report(manifest, step_results, [], {})
        if transcript_note:
            report["transcript"] = transcript_note

        _write_all_exports(
            session_id, manifest, step_results, annotated_paths, annotated_dir, session_dir, report
        )

    def _generate_photo(session_id):
        """Manifest-free mode: one step per uploaded image, in order, with the
        transcript's text placed verbatim under each. Images are copied through
        without a click marker (there was no recorded click), and no LLM runs
        (there's no recorded action to phrase)."""
        manifest, screenshots_dir, annotated_dir, session_dir = sessions[session_id]
        annotated_dir.mkdir(parents=True, exist_ok=True)

        # Raw narration (vision context) + order/label placement (the fallback
        # when vision is off or a caption fails).
        narration = ""
        per_step, transcript_note = {}, None
        matches = sorted(session_dir.glob("transcript.*"))
        if matches:
            narration = matches[0].read_text(encoding="utf-8")
            try:
                per_step, transcript_note = align_transcript_to_steps(
                    matches[0].name, narration, manifest
                )
            except Exception:  # noqa: BLE001 - a bad transcript must never break generation
                per_step, transcript_note = {}, None

        # Copy each image through (no click marker) and collect its path in order.
        annotated_paths = []
        for step in manifest.steps:
            out = annotated_dir / step.screenshot
            shutil.copyfile(screenshots_dir / step.screenshot, out)
            annotated_paths.append(out)

        # Optional vision captioning: a vision model looks at each screenshot +
        # the narration and writes that step's instruction. The steps are still
        # the images (one each, in order) -- the model only phrases them. Any
        # failed caption falls back to the transcript's order/label placement.
        captions = [None] * len(manifest.steps)
        vision_note = None
        vision_cfg = load_models_config().vision
        if vision_cfg.enabled:
            captions = caption_images(
                annotated_paths, narration, vision_cfg.endpoint, vision_cfg.model
            )
            vision_note = (
                f"vision-captioned {sum(1 for c in captions if c)}/{len(captions)} "
                f"screenshots ({vision_cfg.model})"
            )

        step_results = []
        for step, caption in zip(manifest.steps, captions):
            step_results.append(
                {
                    "step_id": step.id,
                    "text": caption or per_step.get(step.id) or "(no description provided)",
                    "used_fallback": caption is None,
                }
            )

        report = {
            "template_fallback_steps": [],
            "verify_claims": [],
            "empty_metadata_steps": [],
            "docx_warnings": [],
        }
        if transcript_note:
            report["transcript"] = transcript_note
        if vision_note:
            report["vision"] = vision_note
        _write_all_exports(
            session_id, manifest, step_results, annotated_paths, annotated_dir, session_dir, report
        )

    def _write_all_exports(
        session_id, manifest, step_results, annotated_paths, annotated_dir, session_dir, report
    ):
        """Render every output format + the review report from finished
        step_results and annotated images. Shared by both generation modes."""
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

    def _apply_transcript(session_dir, manifest, step_results):
        """If a transcript was uploaded (saved as transcript.<ext>), place its
        narration onto each step_result's "narration" key and return a short
        placement note for the report. Returns None when there's no transcript
        or it can't be parsed -- never raises."""
        matches = sorted(session_dir.glob("transcript.*"))
        if not matches:
            return None
        tpath = matches[0]
        try:
            per_step, note = align_transcript_to_steps(
                tpath.name, tpath.read_text(encoding="utf-8"), manifest
            )
        except Exception:  # noqa: BLE001 - a bad transcript must never break generation
            return None
        for result in step_results:
            narration = per_step.get(result["step_id"])
            if narration:
                result["narration"] = narration
        return note

    def _ingest_session(manifest_json, files, transcript=None):
        """Shared by the JSON API (POST /sessions) and the browser upload
        form (POST /ui/upload). `transcript`, if given, is a (filename,
        text) tuple. Returns the new session_id, or raises HTTPException on
        bad input."""
        try:
            manifest = load_manifest(json.loads(manifest_json))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid manifest: {exc}") from exc

        # Validate the transcript up front (parse + placement) so a bad one is
        # a clear 400 here, not a silently-dropped narration later.
        transcript_ext = None
        if transcript is not None:
            t_name, t_content = transcript
            try:
                align_transcript_to_steps(t_name, t_content, manifest)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"invalid transcript: {exc}") from exc
            transcript_ext = (t_name or "").lower().rsplit(".", 1)[-1]

        # Fail loudly and early if the upload doesn't include every screenshot
        # the manifest references. Without this, a missing screenshot only
        # surfaces later as a cryptic FileNotFoundError from the background
        # annotation step (rendered as "Status: error" on the session page) --
        # the "internal error" a user hits when they miss a PNG in the upload
        # form's multi-select. The filenames are reduced to basenames to match
        # exactly how the write loop below stores them (Path(...).name).
        provided = {Path(u.filename or "").name for u in files}
        required = {step.screenshot for step in manifest.steps}
        missing = sorted(required - provided)
        if missing:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"missing screenshots: {', '.join(missing)} -- upload every PNG "
                    "from the capture folder alongside manifest.json"
                ),
            )

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

        # Persisted so a server restart can rebuild `sessions` from disk
        # (_restore_sessions_from_disk) -- the manifest otherwise only ever
        # exists as an in-memory object.
        (session_dir / "manifest.json").write_text(manifest_json, encoding="utf-8")
        if transcript is not None:
            (session_dir / f"transcript.{transcript_ext}").write_text(
                transcript[1], encoding="utf-8"
            )

        sessions[session_id] = (manifest, screenshots_dir, annotated_dir, session_dir)
        jobs.submit(session_id, lambda: _generate(session_id))
        return session_id

    def _read_transcript(upload):
        """Turn an optional transcript UploadFile into a (filename, text)
        tuple, or None if none was provided. Raises a clear 400 if it isn't
        UTF-8 text."""
        if upload is None or not upload.filename:
            return None
        try:
            content = upload.file.read().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=400, detail=f"transcript must be UTF-8 text: {exc}"
            ) from exc
        return (upload.filename, content)

    @app.post("/sessions")
    def create_session(
        manifest_json: str = Form(...),
        files: list[UploadFile] = File(default=[]),
        transcript_file: UploadFile | None = File(default=None),
    ):
        session_id = _ingest_session(manifest_json, files, _read_transcript(transcript_file))
        return {"session_id": session_id, "status": jobs.status(session_id)["status"]}

    @app.post("/ui/upload")
    def ui_upload(
        manifest_file: UploadFile = File(...),
        files: list[UploadFile] = File(default=[]),
        transcript_file: UploadFile | None = File(default=None),
    ):
        try:
            manifest_json = manifest_file.file.read().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=400, detail=f"invalid manifest encoding: {exc}"
            ) from exc
        session_id = _ingest_session(manifest_json, files, _read_transcript(transcript_file))
        return RedirectResponse(f"/ui/sessions/{session_id}", status_code=303)

    _IMG_EXTS = {"png", "jpg", "jpeg", "gif", "bmp", "webp"}

    def _ingest_photo_session(title, files, transcript=None):
        """Manifest-free build: each uploaded image becomes one step (in upload
        order), with the transcript supplying each step's text. Synthesizes a
        schema-valid manifest so the rest of the pipeline works unchanged.
        Returns the new session_id or raises HTTPException on bad input."""
        images = [u for u in files if u.filename]
        if not images:
            raise HTTPException(status_code=400, detail="upload at least one screenshot/image")

        session_id = str(uuid.uuid4())
        session_dir = sessions_root / session_id
        screenshots_dir = session_dir / "screenshots"
        annotated_dir = session_dir / "annotated"
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        # Normalize every image to NNN.png (upload order) via PIL -- this both
        # fixes the ordering/naming and validates each file really is an image
        # (PIL raises on junk), so downstream export never trips on a bad file.
        names = []
        for i, upload in enumerate(images, start=1):
            ext = Path(upload.filename or "").suffix.lower().lstrip(".")
            if ext not in _IMG_EXTS:
                raise HTTPException(
                    status_code=400, detail=f"not a supported image: {upload.filename!r}"
                )
            name = f"{i:03d}.png"
            try:
                Image.open(upload.file).convert("RGB").save(screenshots_dir / name)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=400, detail=f"unreadable image {upload.filename!r}: {exc}"
                ) from exc
            names.append(name)

        started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        manifest_dict = synthetic_manifest_dict(title, names, started)
        manifest_dict["session"]["id"] = session_id
        manifest = load_manifest(manifest_dict)

        transcript_ext = None
        if transcript is not None:
            t_name, t_content = transcript
            try:
                align_transcript_to_steps(t_name, t_content, manifest)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"invalid transcript: {exc}") from exc
            transcript_ext = (t_name or "").lower().rsplit(".", 1)[-1]

        (session_dir / "manifest.json").write_text(json.dumps(manifest_dict), encoding="utf-8")
        (session_dir / "mode.txt").write_text("photo", encoding="utf-8")
        if transcript is not None:
            (session_dir / f"transcript.{transcript_ext}").write_text(
                transcript[1], encoding="utf-8"
            )

        sessions[session_id] = (manifest, screenshots_dir, annotated_dir, session_dir)
        jobs.submit(session_id, lambda: _generate(session_id))
        return session_id

    @app.post("/ui/build")
    def ui_build(
        title: str = Form(""),
        files: list[UploadFile] = File(default=[]),
        transcript_file: UploadFile | None = File(default=None),
    ):
        session_id = _ingest_photo_session(title, files, _read_transcript(transcript_file))
        return RedirectResponse(f"/ui/sessions/{session_id}", status_code=303)

    @app.post("/sessions/{session_id}/rerender")
    def rerender(session_id: str):
        """Re-runs generation + all exports for an already-uploaded session
        against the current config/models.toml -- genuinely meaningful now
        that step generation is LLM-backed: e.g. after editing the config to
        point at a different model/endpoint, or setting up Anthropic
        routing, without re-uploading the manifest/screenshots."""
        _require_known_session(session_id)
        jobs.submit(session_id, lambda: _generate(session_id))
        return {"session_id": session_id, "status": jobs.status(session_id)["status"]}

    @app.post("/ui/sessions/{session_id}/rerender")
    def ui_rerender(session_id: str):
        """Same effect as POST /sessions/{id}/rerender, but redirects back
        to the session page instead of returning JSON -- the JSON route
        stays as-is for API/script callers, since a plain HTML <form> POST
        would otherwise navigate the browser to a raw JSON blob."""
        _require_known_session(session_id)
        jobs.submit(session_id, lambda: _generate(session_id))
        return RedirectResponse(f"/ui/sessions/{session_id}", status_code=303)

    @app.post("/ui/sessions/{session_id}/transcript")
    def ui_add_transcript(session_id: str, transcript_file: UploadFile = File(...)):
        """Attach (or replace) a narration transcript on an already-uploaded
        session and re-render, so a user can add narration after the fact from
        the review page -- not only at initial upload. _generate picks the
        saved transcript.<ext> up automatically."""
        _require_known_session(session_id)
        manifest, _screens, _annot, session_dir = sessions[session_id]
        transcript = _read_transcript(transcript_file)
        if transcript is None:
            raise HTTPException(status_code=400, detail="no transcript file provided")
        t_name, t_content = transcript
        try:
            align_transcript_to_steps(t_name, t_content, manifest)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid transcript: {exc}") from exc
        # Only one transcript per session -- drop any prior (possibly different
        # extension) before writing the new one.
        for old in session_dir.glob("transcript.*"):
            old.unlink()
        ext = (t_name or "").lower().rsplit(".", 1)[-1]
        (session_dir / f"transcript.{ext}").write_text(t_content, encoding="utf-8")
        jobs.submit(session_id, lambda: _generate(session_id))
        return RedirectResponse(f"/ui/sessions/{session_id}", status_code=303)

    @app.post("/ui/sessions/{session_id}/delete")
    def ui_delete(session_id: str):
        """Removes a session entirely: its directory on disk, its library
        index entry, and its in-memory registration. Irreversible -- there
        is no undo/trash, matching how uninstall.ps1 -RemoveData works."""
        _require_known_session(session_id)
        session_dir = sessions[session_id][3]
        del sessions[session_id]
        # report.json's absence is what _restore_sessions_from_disk actually
        # checks for -- delete it first (best-effort, ignoring a Windows
        # file-lock PermissionError) so a partially-locked rmtree below can
        # never leave enough behind to resurrect this session on the next
        # server restart.
        try:
            (session_dir / "report.json").unlink(missing_ok=True)
        except OSError:
            pass
        shutil.rmtree(session_dir, ignore_errors=True)
        remove_entry(sessions_root, session_id)
        return RedirectResponse("/ui", status_code=303)

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

    @app.get("/version")
    def get_version():
        return {"version": __version__}

    @app.get("/")
    @app.get("/ui")
    def ui_library(q: str | None = None):
        return HTMLResponse(render_library_page(library_search(sessions_root, q), q))

    @app.get("/ui/sessions/{session_id}")
    def ui_session(session_id: str):
        _require_known_session(session_id)
        status = jobs.status(session_id)
        if status["status"] != "done":
            return HTMLResponse(render_session_processing_page(session_id, status))
        manifest = sessions[session_id][0]
        session_dir = sessions[session_id][3]
        report = json.loads((session_dir / "report.json").read_text(encoding="utf-8"))
        config = load_models_config().model_dump()
        title = manifest.session.title or manifest.session.id
        date = manifest.session.started_utc
        return HTMLResponse(render_session_page(session_id, title, date, report, config))

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

    @app.post("/shutdown")
    def shutdown():
        """Stops the server process. A console=False (windowed-subsystem)
        PyInstaller build does not reliably receive Windows console control
        events (CTRL_BREAK_EVENT/CTRL_C_EVENT) the way a console-subsystem
        process does — confirmed empirically while building task-10's
        verify script, not assumed — so an HTTP-triggered stop is the
        reliable mechanism for both scripts/build_server_exe.py and the
        eventual install.ps1/uninstall.ps1 (task-12). Assumes this server
        is only ever bound to localhost, matching its default; there is no
        auth on this endpoint. A short delay lets this response flush
        before the process actually exits."""

        def _stop():
            time.sleep(0.2)
            os._exit(0)

        threading.Thread(target=_stop, daemon=True).start()
        return {"status": "shutting down"}

    return app
