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
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from PIL import Image

from pipeline import __version__
from pipeline.assembler import check_1to1_mapping, doc_number, format_doc_date
from pipeline.claim_coverage import validate_claim_coverage
from pipeline.claims import extract_claims
from pipeline.consistency import canonicalize_terms
from pipeline.photo_build import synthetic_manifest_dict
from pipeline.config import (
    ModelsConfig,
    key_status,
    load_models_config,
    provider_api_key,
    provider_endpoint,
    resolve_polish_config,
    runtime_config_path,
    save_models_config,
)
from pipeline.docx_assembler import assemble_docx
from pipeline.export_html import render_single_file_html
from pipeline.export_md import _slugify, export_markdown_bundle
from pipeline.export_pdf import render_pdf
from pipeline.jobs import JobRunner
from pipeline.library import remove_entry
from pipeline.library import search as library_search
from pipeline.library import upsert_entry
from pipeline.llm_client import LLMClient
from pipeline.manifest import load_manifest, manifest_to_schema_dict, select_manifest_steps
from pipeline.narration_polish import polish_narration
from pipeline.narrative import generate_narrative
from pipeline.polish import generate_polish_fields
from pipeline.render import render_html, render_markdown, render_steps_llm_mode
from pipeline.semantic_align import build_step_contexts, semantic_align
from pipeline.sidecar import build_sidecar_report
from pipeline.summarize import generate_title_and_overview
from pipeline.transcript import _parse_json_segments, align_transcript_to_steps
from pipeline.vision import caption_images
from pipeline.webui.pages import (
    render_config_page,
    render_library_page,
    render_session_page,
    render_session_processing_page,
    render_steps_review_page,
)
from pipeline.webui.review import render_review_page


def _synthesize_narration_from_steps(manifest, step_results):
    """Builds a lightweight text summary of a capture session -- the distinct
    window titles clicked through, plus each step's generated text, in order
    -- so generate_title_and_overview (normally fed a real narration
    transcript) can produce a session title from what the screenshots
    actually show, even when there's no user-supplied narration at all."""
    windows = []
    seen = set()
    for step in manifest.steps:
        title = step.window.title
        if title and title not in seen:
            seen.add(title)
            windows.append(title)
    lines = [f"Windows involved: {', '.join(windows)}."] if windows else []
    lines.extend(result["text"] for result in step_results)
    return "\n".join(lines)


def _narration_to_claims(text):
    """Turns a block of narration text into task-07's claims.py shape (one
    claim per non-empty line, synthetic index-based timestamps) so
    generate_narrative's claim-coverage gate has facts to check the drafted
    stage-2 narrative against. There's no real audio transcription here (no
    per-segment timing) -- extract_claims' own contract only needs stable
    ids in segment order, which a plain line split already gives it, without
    inventing a second extraction path alongside the real transcription.py
    one."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return extract_claims([{"text": ln, "start": float(i)} for i, ln in enumerate(lines)])


def _claims_for_narrative(transcript_path, fallback_text):
    """Builds Stage 2's claim list from whatever narration is available --
    the uploaded transcript if there is one (the richer, human-authored
    source), else the step-synthesized fallback text already computed for
    the title. Dispatches on extension exactly like align_transcript_to_steps:
    a .json transcript is real timestamped segments
    (transcript._parse_json_segments), so its structured "text" fields go
    straight into extract_claims; a .txt/.md transcript (and the synthetic
    fallback, which is always plain prose) has no such structure, so it goes
    through _narration_to_claims' line split instead. Treating the raw JSON
    file as prose would feed extract_claims JSON syntax fragments ("{",
    '"segments": [') as claim text, which then show up verbatim in
    [verify] blockquotes in the rendered doc -- this dispatch is what keeps
    that from happening."""
    if transcript_path is not None and transcript_path.suffix.lower() == ".json":
        segments = _parse_json_segments(transcript_path.read_text(encoding="utf-8"))
        return extract_claims(segments)
    text = transcript_path.read_text(encoding="utf-8") if transcript_path else fallback_text
    return _narration_to_claims(text)


def _assert_1to1_mapping(manifest, step_results):
    """Enforces CLAUDE.md invariant L1 at doc-build time instead of trusting
    step generation to have stayed sequential/order-preserving -- raises so
    the job ends in `error` (jobs.py's worker loop) rather than silently
    shipping a doc whose steps don't match the manifest 1:1."""
    if not check_1to1_mapping(manifest, step_results):
        raise RuntimeError(
            "step generation produced a mismatched step list "
            f"(expected {[s.id for s in manifest.steps]}, "
            f"got {[r['step_id'] for r in step_results]})"
        )


def _download_filename(manifest, ext):
    """The filename a browser saves a downloaded doc as -- derived from the
    session's title (or id, if untitled) so it reads as what the SOP is
    about instead of the generic "doc.docx" every session otherwise shares.
    Reuses export_md.py's own slug so the whole app has one naming
    convention, not two."""
    slug = _slugify(manifest.session.title or manifest.session.id)
    return f"{slug}.{ext}"


def _zip_directory(directory):
    directory = Path(directory)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(directory))
    return buf.getvalue()


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


def create_app(
    sessions_root: Path,
    llm_client_factory=None,
    config_path=None,
    narrative_llm_client_factory=None,
    polish_llm_client_factory=None,
) -> FastAPI:
    """llm_client_factory: zero-arg callable returning an object with a
    .chat(messages) method (matching LLMClient's interface), called fresh
    for every generation. Defaults to a real LLMClient built from the
    CURRENT config/models.toml (loaded fresh each call, not cached at
    app-creation time, so /rerender's promise to reflect config edits is
    actually true). Tests override this to a fast, deterministic stub —
    without it, every session creation would make a real network attempt
    to the (usually unreachable in a dev/test environment) configured
    endpoint before falling back, adding real seconds per step.

    narrative_llm_client_factory: same shape, but built from config/models.toml's
    `[narrative]` section (previously unused by any live code) -- used only for
    the semantic transcript-placement/polish path (_apply_transcript), when a
    transcript has no structure for the deterministic placement to split on.

    polish_llm_client_factory: same shape, but built from config/models.toml's
    `[polish]` section -- used only for the optional stage-4 polish pass
    (_write_all_exports), gated on `[polish].enabled`. Only doc.md's,
    doc.html's, and the md-bundle's exports reflect this pass so far
    (per-field, via generate_polish_fields)."""
    app = FastAPI()

    _LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

    @app.middleware("http")
    async def _csrf_guard(request, call_next):
        """CSRF guard for EVERY state-changing request. The server is
        localhost-only, but a page on another site could still POST to it in the
        user's browser (e.g. auto-submit a form to /shutdown or /ui/config).
        Browsers attach an Origin header on cross-origin requests -- reject any
        whose host isn't this local server. Programmatic clients (the capture
        agent's auto-upload) send no Origin and are allowed. The host is matched
        exactly (not a prefix), so 'http://127.0.0.1.evil.com' is rejected."""
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).hostname not in _LOCAL_HOSTS:
                return JSONResponse({"detail": "cross-site request rejected"}, status_code=403)
        return await call_next(request)

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
    # The editable runtime config lives in a per-user writable file (seeded from
    # the bundled default). Tests pass an isolated config_path so the editor
    # never writes to the real user config.
    resolved_config_path = config_path or runtime_config_path()
    make_llm_client = llm_client_factory or (
        lambda: LLMClient(load_models_config(resolved_config_path).steps)
    )
    make_narrative_llm_client = narrative_llm_client_factory or (
        lambda: LLMClient(load_models_config(resolved_config_path).narrative)
    )

    def make_polish_llm_client(section=None):
        """section: a resolved PolishConfig to build the client from --
        passed by _write_all_exports when a per-job `polish` override
        (resolve_polish_config) is in play. Defaults to the current
        [polish] section from config/models.toml, loaded fresh (not
        cached), matching every other make_*_llm_client factory's promise
        to reflect config edits without a server restart."""
        if section is None:
            section = load_models_config(resolved_config_path).polish
        if polish_llm_client_factory is not None:
            return polish_llm_client_factory(section)
        return LLMClient(section)

    # session_id -> (manifest, screenshots_dir, annotated_dir, session_dir)
    sessions = {}
    # session_ids awaiting step-review confirmation -- staged by _ingest_session
    # /_ingest_photo_session instead of being submitted to jobs immediately, so
    # the user can drop mis-captured steps before generation ever runs.
    staged = set()
    _restore_sessions_from_disk(sessions_root, sessions, jobs)

    def _status_of(session_id):
        """Like jobs.status(session_id)["status"], but safe for a staged
        session that was never submitted to jobs (status() returns {} for
        those) -- reports "staged" instead of KeyError-ing."""
        if session_id in staged:
            return "staged"
        return jobs.status(session_id).get("status", "unknown")

    def _is_photo_mode(session_dir):
        mode = session_dir / "mode.txt"
        return mode.exists() and mode.read_text(encoding="utf-8").strip() == "photo"

    def _generate(session_id, polish_override=None):
        manifest, screenshots_dir, annotated_dir, session_dir = sessions[session_id]

        # Manifest-free "screenshots + transcript" sessions take a different
        # path: no LLM step phrasing, no click-marker annotation.
        if _is_photo_mode(session_dir):
            _generate_photo(session_id, polish_override=polish_override)
            return

        # One generation attempt per step, round-trip-gated with a
        # template fallback (task-06) -- if the configured endpoint is
        # unreachable, or Anthropic routing is on with no API key, or the
        # reply doesn't hold up, that step just falls back; nothing here
        # ever retries.
        llm_client = make_llm_client()
        try:
            step_results, annotated_paths = render_steps_llm_mode(
                manifest,
                screenshots_dir,
                annotated_dir,
                llm_client,
                on_progress=lambda i, n: jobs.set_progress(session_id, i, n),
                max_concurrency=load_models_config(resolved_config_path).steps.max_concurrency,
            )
        finally:
            close = getattr(llm_client, "close", None)
            if callable(close):
                close()
        _assert_1to1_mapping(manifest, step_results)

        # Place an uploaded transcript's narration under each step (by step
        # label / order for .txt/.md, by timestamp for .json, or -- when
        # neither gives the deterministic splitter anything to work with --
        # the semantic LLM placement + polish pipeline). Best-effort: a
        # transcript is validated at upload time, but a problem here must never
        # break generation -- the doc is complete from the steps alone.
        transcript_note, placement_meta = _apply_transcript(session_dir, manifest, step_results)

        # A real capture session's manifest almost never has a title (nothing
        # in the capture flow asks the user for one), so without this the
        # library page shows the raw session id -- a timestamp+uuid blob,
        # not what the SOP is about. Auto-title from what the screenshots
        # actually show: the window titles clicked through plus each step's
        # generated text, the same generate_title_and_overview call
        # manifest-free photo builds already use for narration-based titles.
        # Fills the title only if one isn't already set, so a manifest that
        # DOES carry a title (or a previous run's title) is never overwritten.
        synthetic_narration = _synthesize_narration_from_steps(manifest, step_results)
        if not manifest.session.title and synthetic_narration.strip():
            title_llm = make_llm_client()
            try:
                gen_title, _overview = generate_title_and_overview(synthetic_narration, title_llm)
            finally:
                close = getattr(title_llm, "close", None)
                if callable(close):
                    close()
            if gen_title:
                manifest.session.title = gen_title

        # Stage 2: a multi-pass narrative paragraph (task-09) from whatever
        # narration is available -- the uploaded transcript if there is one
        # (the richer, human-authored source), else the step-synthesized
        # narration already computed above for the title. Best-effort like
        # everything else in this path: any failure just leaves
        # narrative_text unset, and the doc ships with steps alone, no
        # different from before this was wired up.
        narrative_text = None
        transcript_matches = sorted(session_dir.glob("transcript.*"))
        transcript_path = transcript_matches[0] if transcript_matches else None
        try:
            claims = _claims_for_narrative(transcript_path, synthetic_narration)
        except Exception:  # noqa: BLE001 - a bad transcript must never block Stage 2
            claims = []
        if claims:
            narrative_llm = make_narrative_llm_client()
            try:
                narrative_text, _covered, _verify_ids = generate_narrative(
                    claims,
                    narrative_llm,
                    passes=load_models_config(resolved_config_path).narrative.passes,
                )
            except Exception:  # noqa: BLE001 - narrative is best-effort, never blocks the doc
                narrative_text = None
            finally:
                close = getattr(narrative_llm, "close", None)
                if callable(close):
                    close()

        # narration_polish's minted verify_claims (dropped clauses from a
        # polished rewrite) finally give this hardcoded-empty parameter real
        # content, the same [verify]-blockquote accounting the sidecar report
        # has always supported for the (still-unwired) audio narration path.
        verify_claims = (placement_meta or {}).get("verify_claims", [])
        verify_claim_ids = [c["claim_id"] for c in verify_claims]
        claims_by_id = {c["claim_id"]: c for c in verify_claims}
        report = build_sidecar_report(manifest, step_results, verify_claim_ids, claims_by_id)
        if transcript_note:
            report["transcript"] = transcript_note
        if placement_meta:
            report["transcript_placement"] = {
                k: v for k, v in placement_meta.items() if k != "verify_claims"
            }

        _write_all_exports(
            session_id,
            manifest,
            step_results,
            annotated_paths,
            annotated_dir,
            session_dir,
            report,
            narrative_text=narrative_text,
            polish_override=polish_override,
            claims=claims,
        )

    def _generate_photo(session_id, polish_override=None):
        """Manifest-free mode: one step per uploaded image, in order. When
        [vision] is enabled, a vision model captions each screenshot from the
        image + narration; otherwise the transcript's own placement supplies the
        text. A title + short overview are generated from the narration.

        This manifest is SYNTHETIC (photo_build.py: one step per image,
        placeholder click, empty window/element) -- there's no real UIA
        context for the semantic transcript pipeline to use, so it uses each
        step's own vision caption as context instead (see below); that's also
        why captioning must run BEFORE any semantic-placement attempt."""
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

        # Copy each image through and collect its path in order.
        annotated_paths = []
        for step in manifest.steps:
            out = annotated_dir / step.screenshot
            shutil.copyfile(screenshots_dir / step.screenshot, out)
            annotated_paths.append(out)

        # Vision captioning: a vision model looks at each screenshot + the
        # narration and writes that step's instruction (in parallel). The steps
        # are still the images (one each, in order) -- the model only phrases
        # them. Any failed caption falls back to the transcript placement.
        # Progress is reported per completed caption -- with vision disabled
        # there's nothing per-step to count, so the processing page just shows
        # its plain spinner (same as before this feature existed).
        captions = [None] * len(manifest.steps)
        vision_note = None
        vision_cfg = load_models_config(resolved_config_path).vision
        if vision_cfg.enabled:
            captions = caption_images(
                annotated_paths,
                narration,
                provider_endpoint(vision_cfg.provider, vision_cfg.endpoint),
                vision_cfg.model,
                api_key=provider_api_key(vision_cfg.provider),
                on_progress=lambda i, n: jobs.set_progress(session_id, i, n),
                max_workers=vision_cfg.max_concurrency,
            )
            vision_note = (
                f"vision-captioned {sum(1 for c in captions if c)}/{len(captions)} "
                f"screenshots ({vision_cfg.model})"
            )

        # If the deterministic placement above collapsed the transcript onto a
        # single step, try the same semantic LLM pipeline the real-capture flow
        # uses -- fed each step's own caption (the closest thing to real
        # per-step context this synthetic manifest has) instead of window/
        # element metadata, which doesn't exist here.
        placement_meta = None
        if transcript_note and "WARNING" in transcript_note:
            step_contexts = build_step_contexts(
                manifest,
                [
                    {"step_id": step.id, "text": caption or ""}
                    for step, caption in zip(manifest.steps, captions)
                ],
            )
            per_step, transcript_note, placement_meta = _run_semantic_pipeline(
                narration, manifest, step_contexts, per_step, transcript_note
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
        _assert_1to1_mapping(manifest, step_results)

        # A title + one-line overview from the narration (best-effort). Fill the
        # title only if the user didn't provide one, so a UUID never shows.
        user_title = manifest.session.title
        on_screen_texts = [c for c in captions if c]
        narrative_text = None
        if narration.strip():
            llm = make_llm_client()
            try:
                gen_title, narrative_text = generate_title_and_overview(
                    narration, llm, on_screen_texts=on_screen_texts
                )
            finally:
                close = getattr(llm, "close", None)
                if callable(close):
                    close()
            if gen_title and not manifest.session.title:
                manifest.session.title = gen_title

        # Photo-mode has no manifest ground truth (element/window names) to
        # round-trip-gate step text against -- a raw narration transcript is
        # placed verbatim, and ASR can transcribe the same out-of-vocabulary
        # proper noun differently each time it's spoken. This can't fix
        # spelling to be *correct* (no ground truth for that exists here),
        # only internally *consistent* -- see consistency.py. A user-typed
        # title is the one real ground truth available, so it anchors the
        # canonical spelling when one of its words matches a variant; a
        # vision caption (on_screen_texts -- reading the actual screenshot
        # pixels) is weaker ground truth, preferred over raw frequency.
        fields = [manifest.session.title or "", narrative_text or ""] + [
            r["text"] for r in step_results
        ]
        canonicalized, consistency_actions = canonicalize_terms(
            fields, anchor_text=user_title or None, preferred_texts=on_screen_texts
        )
        manifest.session.title = canonicalized[0]
        if narrative_text is not None:
            narrative_text = canonicalized[1]
        for step_result, canonical_text in zip(step_results, canonicalized[2:]):
            step_result["text"] = canonical_text

        verify_claims = (placement_meta or {}).get("verify_claims", [])
        report = {
            "template_fallback_steps": [],
            "verify_claims": [
                {"claim_id": c["claim_id"], "text": c.get("text")} for c in verify_claims
            ],
            "empty_metadata_steps": [],
            "docx_warnings": [],
        }
        if transcript_note:
            report["transcript"] = transcript_note
        if placement_meta:
            report["transcript_placement"] = {
                k: v for k, v in placement_meta.items() if k != "verify_claims"
            }
        if vision_note:
            report["vision"] = vision_note
        if consistency_actions:
            report["consistency"] = consistency_actions
        _write_all_exports(
            session_id,
            manifest,
            step_results,
            annotated_paths,
            annotated_dir,
            session_dir,
            report,
            narrative_text=narrative_text,
            polish_override=polish_override,
        )

    def _write_all_exports(
        session_id,
        manifest,
        step_results,
        annotated_paths,
        annotated_dir,
        session_dir,
        report,
        narrative_text=None,
        polish_override=None,
        claims=(),
    ):
        """Render every output format + the review report from finished
        step_results and annotated images. Shared by both generation modes.

        polish_override: a PolishMode ("off"/"local"/"haiku") for this job
        only, resolved via resolve_polish_config -- e.g. from a `polish`
        query param on /rerender. None (the default, used by every caller
        except the rerender routes) means "no override": fall back to
        whatever [polish].enabled/provider/model already say in
        config/models.toml, byte-for-byte the same as before this param
        existed.

        claims: the Stage 2 narrative claim list (claims.py shape) the
        assembled markdown was built to cover, checked against the polish
        pass's output below -- an empty default (the photo-build call site,
        which has no narrative claims) makes that check a no-op, byte-for-
        byte unchanged from before this param existed."""
        # Optional stage 4: a per-field formatting/tone pass over
        # narrative_text and each step's text/present narration
        # (generate_polish_fields, polish.py) -- narration_polish.py's
        # per-unit pattern, so a bad rewrite of one field can never discard a
        # good rewrite of another. With no override, gated on
        # [polish].enabled (default off -- see PolishConfig); an explicit
        # override bypasses that toggle for this job (resolve_polish_config's
        # "off" always skips, "local"/"haiku" always run). doc.md, doc.html,
        # doc.single.html, the md-bundle (export.md.zip), doc.docx, and
        # doc.pdf all reflect this pass -- all six rendered from the
        # polished fields below.
        current_cfg = load_models_config(resolved_config_path)
        if polish_override is not None:
            polish_section = resolve_polish_config(polish_override, current_cfg)
        elif current_cfg.polish.enabled:
            polish_section = current_cfg.polish
        else:
            polish_section = None

        md_narrative_text = narrative_text
        md_step_results = step_results
        if polish_section is not None:
            polish_llm = make_polish_llm_client(polish_section)
            try:
                md_narrative_text, md_step_results, _polish_meta = generate_polish_fields(
                    narrative_text, step_results, polish_llm
                )
            finally:
                close = getattr(polish_llm, "close", None)
                if callable(close):
                    close()
            # Safety net for invariant L4 (CLAUDE.md): generate_polish_fields's
            # own per-field gate (_field_gate) only rejects a rewrite that
            # ADDS unsupported content -- it has no way to know a claim's
            # exact text is load-bearing, so nothing stops an otherwise-
            # faithful rephrase of narrative_text from making a claim's text
            # (and any [verify] blockquote covering it -- which lives inside
            # narrative_text itself, see narrative.py's ensure_claim_coverage)
            # vanish. Re-run the same coverage check doc.md must already
            # satisfy pre-polish, against the polished narrative specifically
            # (claims never live in step text/narration); if it broke
            # coverage, discard just the narrative rewrite and keep the
            # known-good original narrative_text rather than ship a doc with
            # a silently dropped claim. Step text/narration polish carries no
            # such risk and is kept either way.
            ok, missing = validate_claim_coverage(md_narrative_text, claims)
            if not ok:
                report["polish_rejected_claim_coverage"] = missing
                md_narrative_text = narrative_text

        md = render_markdown(
            manifest,
            md_step_results,
            annotated_paths,
            narrative_text=md_narrative_text,
            base_dir=annotated_dir,
        )
        (session_dir / "doc.md").write_text(md, encoding="utf-8")

        html_doc = render_html(
            manifest,
            md_step_results,
            annotated_paths,
            narrative_text=md_narrative_text,
            base_dir=annotated_dir,
        )
        (session_dir / "doc.html").write_text(html_doc, encoding="utf-8")

        doc_cfg = load_models_config(resolved_config_path).document
        doc_date = format_doc_date(manifest.session.started_utc)
        doc_no = doc_number(doc_cfg.doc_no_prefix, manifest.session.id)

        docx_path = session_dir / "doc.docx"
        _out, docx_warnings = assemble_docx(
            manifest,
            md_step_results,
            annotated_dir,
            docx_path,
            date=doc_date,
            author=doc_cfg.author,
            doc_no=doc_no,
            narrative_text=md_narrative_text,
        )
        report["docx_warnings"] = docx_warnings

        pdf_path = session_dir / "doc.pdf"
        render_pdf(
            manifest,
            md_step_results,
            annotated_paths,
            pdf_path,
            narrative_text=md_narrative_text,
            date=doc_date,
            author=doc_cfg.author,
            doc_no=doc_no,
        )

        single_html = render_single_file_html(
            manifest, md_step_results, annotated_paths, narrative_text=md_narrative_text
        )
        (session_dir / "doc.single.html").write_text(single_html, encoding="utf-8")

        md_bundle_dir = session_dir / "md_bundle"
        export_markdown_bundle(
            manifest,
            md_step_results,
            annotated_paths,
            md_bundle_dir,
            narrative_text=md_narrative_text,
        )
        (session_dir / "export.md.zip").write_bytes(_zip_directory(md_bundle_dir))

        (session_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
        upsert_entry(sessions_root, session_id, manifest, report)

    def _run_semantic_pipeline(content, manifest, step_contexts, per_step, note):
        """Shared by the real-capture flow (_apply_transcript) and the photo
        build flow (_generate_photo): when the deterministic placement
        (transcript.py) collapses onto a single step despite there being
        multiple real steps -- its own loud WARNING signature -- this tries
        the semantic LLM pipeline instead: stage 1 (semantic_align) picks
        verbatim split points from the full transcript + step contexts;
        stage 2 (polish_narration) then rewrites each resulting segment for
        readability, gated so nothing invented or dropped is ever trusted
        silently. Either stage failing just falls back to what came before
        it -- semantic_align failing keeps the original single-block
        deterministic result (with its warning); polish_narration failing
        keeps stage 1's verbatim placement. Returns (per_step, note,
        placement_meta) -- placement_meta is None if semantic_align declined
        or "WARNING" wasn't in `note` to begin with (nothing to try)."""
        if "WARNING" not in note:
            return per_step, note, None

        placement_meta = None
        narrative_llm = make_narrative_llm_client()
        try:
            aligned = semantic_align(content, manifest, step_contexts, narrative_llm)
        finally:
            close = getattr(narrative_llm, "close", None)
            if callable(close):
                close()
        if aligned:
            per_step, placement_meta = aligned
            polish_llm = make_narrative_llm_client()
            try:
                per_step, polish_meta = polish_narration(
                    per_step, manifest, step_contexts, polish_llm
                )
            finally:
                close = getattr(polish_llm, "close", None)
                if callable(close):
                    close()
            placement_meta.update(polish_meta)
            note = (
                f"{len(per_step)} of {len(manifest.steps)} step(s) narrated "
                "(semantic placement + polish)"
            )
        return per_step, note, placement_meta

    def _apply_transcript(session_dir, manifest, step_results):
        """If a transcript was uploaded (saved as transcript.<ext>), place its
        narration onto each step_result's "narration" key and return
        (placement_note, placement_meta) for the report. Returns (None, None)
        when there's no transcript or it can't be parsed -- never raises. See
        _run_semantic_pipeline for what happens when the deterministic
        placement collapses onto a single step."""
        matches = sorted(session_dir.glob("transcript.*"))
        if not matches:
            return None, None
        tpath = matches[0]
        content = tpath.read_text(encoding="utf-8")
        try:
            per_step, note = align_transcript_to_steps(tpath.name, content, manifest)
        except Exception:  # noqa: BLE001 - a bad transcript must never break generation
            return None, None

        step_contexts = build_step_contexts(manifest, step_results)
        per_step, note, placement_meta = _run_semantic_pipeline(
            content, manifest, step_contexts, per_step, note
        )

        for result in step_results:
            narration = per_step.get(result["step_id"])
            if narration:
                result["narration"] = narration
        return note, placement_meta

    def _ingest_session(manifest_json, files, transcript=None, stage=False):
        """Shared by the JSON API (POST /sessions) and the browser upload
        form (POST /ui/upload). `transcript`, if given, is a (filename,
        text) tuple. When `stage` is True, the session is registered but not
        submitted for generation -- it's left in `staged` so the user can
        drop mis-captured steps via the steps-review page before generation
        runs (see POST .../confirm-steps). Returns the new session_id, or
        raises HTTPException on bad input."""
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
        if stage:
            staged.add(session_id)
        else:
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
        stage: bool = Form(False),
    ):
        session_id = _ingest_session(
            manifest_json, files, _read_transcript(transcript_file), stage=stage
        )
        return {"session_id": session_id, "status": _status_of(session_id)}

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
        session_id = _ingest_session(
            manifest_json, files, _read_transcript(transcript_file), stage=True
        )
        return RedirectResponse(f"/ui/sessions/{session_id}", status_code=303)

    _IMG_EXTS = {"png", "jpg", "jpeg", "gif", "bmp", "webp"}

    def _ingest_photo_session(title, files, transcript=None, stage=False):
        """Manifest-free build: each uploaded image becomes one step (in upload
        order), with the transcript supplying each step's text. Synthesizes a
        schema-valid manifest so the rest of the pipeline works unchanged.
        When `stage` is True, the session is registered but not submitted for
        generation -- see _ingest_session. Returns the new session_id or
        raises HTTPException on bad input."""
        images = [u for u in files if u.filename]
        if not images:
            raise HTTPException(status_code=400, detail="upload at least one screenshot/image")

        session_id = str(uuid.uuid4())
        session_dir = sessions_root / session_id
        screenshots_dir = session_dir / "screenshots"
        annotated_dir = session_dir / "annotated"
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        # If any validation below rejects the input, remove the just-created
        # session directory so a bad request never leaves an orphan on disk
        # (unregistered, never restored, never cleaned).
        try:
            # Normalize every image to NNN.png (upload order) via PIL -- fixes
            # ordering/naming and validates each file really is an image.
            names = []
            for i, upload in enumerate(images, start=1):
                ext = Path(upload.filename or "").suffix.lower().lstrip(".")
                if ext not in _IMG_EXTS:
                    raise HTTPException(
                        status_code=400, detail=f"not a supported image: {upload.filename!r}"
                    )
                name = f"{i:03d}.png"
                try:
                    with Image.open(upload.file) as im:
                        im.convert("RGB").save(screenshots_dir / name)
                except HTTPException:
                    raise
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
                    raise HTTPException(
                        status_code=400, detail=f"invalid transcript: {exc}"
                    ) from exc
                transcript_ext = (t_name or "").lower().rsplit(".", 1)[-1]

            (session_dir / "manifest.json").write_text(json.dumps(manifest_dict), encoding="utf-8")
            (session_dir / "mode.txt").write_text("photo", encoding="utf-8")
            if transcript is not None:
                (session_dir / f"transcript.{transcript_ext}").write_text(
                    transcript[1], encoding="utf-8"
                )
        except HTTPException:
            shutil.rmtree(session_dir, ignore_errors=True)
            raise

        sessions[session_id] = (manifest, screenshots_dir, annotated_dir, session_dir)
        if stage:
            staged.add(session_id)
        else:
            jobs.submit(session_id, lambda: _generate(session_id))
        return session_id

    @app.post("/ui/build")
    def ui_build(
        title: str = Form(""),
        files: list[UploadFile] = File(default=[]),
        transcript_file: UploadFile | None = File(default=None),
    ):
        session_id = _ingest_photo_session(
            title, files, _read_transcript(transcript_file), stage=True
        )
        return RedirectResponse(f"/ui/sessions/{session_id}", status_code=303)

    @app.post("/sessions/{session_id}/rerender")
    def rerender(session_id: str, polish: Literal["off", "local", "haiku"] | None = None):
        """Re-runs generation + all exports for an already-uploaded session
        against the current config/models.toml -- genuinely meaningful now
        that step generation is LLM-backed: e.g. after editing the config to
        point at a different model/endpoint, or setting up Anthropic
        routing, without re-uploading the manifest/screenshots.

        `polish` (optional query param) overrides the polish stage for this
        job only, via resolve_polish_config -- "off" forces it skipped
        even if [polish].enabled=true, "local" forces the local ollama
        provider, "haiku" forces Anthropic's Claude Haiku 4.5. Omitted
        (the default) leaves the current [polish].enabled/provider/model
        behavior untouched."""
        _require_known_session(session_id)
        jobs.submit(session_id, lambda: _generate(session_id, polish_override=polish))
        return {"session_id": session_id, "status": jobs.status(session_id)["status"]}

    @app.post("/ui/sessions/{session_id}/rerender")
    def ui_rerender(session_id: str, polish: Literal["off", "local", "haiku"] | None = None):
        """Same effect as POST /sessions/{id}/rerender, but redirects back
        to the session page instead of returning JSON -- the JSON route
        stays as-is for API/script callers, since a plain HTML <form> POST
        would otherwise navigate the browser to a raw JSON blob. See
        rerender() above for what `polish` does."""
        _require_known_session(session_id)
        jobs.submit(session_id, lambda: _generate(session_id, polish_override=polish))
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
        # Refuse to delete a session whose generation job is still running --
        # rmtree racing the worker thread mid-export would crash the job and
        # could leave a half-deleted directory.
        if _status_of(session_id) in ("queued", "processing"):
            raise HTTPException(
                status_code=409, detail="session is still generating; try again once it's done"
            )
        session_dir = sessions[session_id][3]
        staged.discard(session_id)
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
        status = _status_of(session_id)
        if status != "done":
            raise HTTPException(status_code=409, detail=f"session not ready (status: {status})")
        return sessions[session_id][3]  # session_dir

    @app.get("/sessions/{session_id}/status")
    def get_status(session_id: str):
        _require_known_session(session_id)
        status = _status_of(session_id)
        body = {"status": status}
        job_status = jobs.status(session_id)
        if status == "error":
            body["error"] = job_status["error"]
        # Only surface progress while genuinely mid-run -- the last reported
        # {current, total} lingers in the job dict after "done", and adding
        # it there would be redundant (100% is implied) as well as changing
        # this endpoint's shape for callers who only expect {"status": ...}.
        if status == "processing" and job_status.get("progress"):
            body["progress"] = job_status["progress"]
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
        filename = _download_filename(sessions[session_id][0], "docx")
        return Response(
            (session_dir / "doc.docx").read_bytes(),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/sessions/{session_id}/doc.pdf")
    def get_doc_pdf(session_id: str):
        session_dir = _require_done(session_id)
        filename = _download_filename(sessions[session_id][0], "pdf")
        return Response(
            (session_dir / "doc.pdf").read_bytes(),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/sessions/{session_id}/doc.single.html")
    def get_doc_single_html(session_id: str):
        session_dir = _require_done(session_id)
        filename = _download_filename(sessions[session_id][0], "html")
        return Response(
            (session_dir / "doc.single.html").read_text(encoding="utf-8"),
            media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/sessions/{session_id}/export.md.zip")
    def get_export_md_zip(session_id: str):
        session_dir = _require_done(session_id)
        filename = _download_filename(sessions[session_id][0], "zip")
        return Response(
            (session_dir / "export.md.zip").read_bytes(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/library")
    def get_library(q: str | None = None):
        return library_search(sessions_root, q)

    @app.get("/config")
    def get_config():
        return load_models_config(resolved_config_path).model_dump()

    @app.get("/ui/config")
    def ui_config(saved: str | None = None):
        cfg = load_models_config(resolved_config_path)
        return HTMLResponse(
            render_config_page(cfg.model_dump(), key_status(cfg), saved=bool(saved))
        )

    @app.post("/ui/config")
    async def ui_config_save(request: Request):
        form = await request.form()
        # The Model fields clear on focus so the browser shows the full
        # suggestion list (see webui/pages.py's onfocus/onblur handlers) --
        # an empty submission (a stray Enter mid-clear, or similar race)
        # must not blank out the saved model, so fall back to the currently
        # saved value for any field submitted empty.
        existing = load_models_config(resolved_config_path)

        def _model_or_existing(form_value, existing_model):
            return form_value if form_value else existing_model

        data = {
            "steps": {
                "provider": form.get("steps_provider", "ollama"),
                "endpoint": form.get("steps_endpoint", ""),
                "model": _model_or_existing(form.get("steps_model", ""), existing.steps.model),
                "max_concurrency": form.get("steps_max_concurrency")
                or str(existing.steps.max_concurrency),
            },
            "narrative": {
                "provider": form.get("narrative_provider", "ollama"),
                "endpoint": form.get("narrative_endpoint", ""),
                "model": _model_or_existing(
                    form.get("narrative_model", ""), existing.narrative.model
                ),
                "passes": form.get("narrative_passes") or "1",
            },
            "vision": {
                "enabled": form.get("vision_enabled") == "on",
                "provider": form.get("vision_provider", "ollama"),
                "endpoint": form.get("vision_endpoint", ""),
                "model": _model_or_existing(form.get("vision_model", ""), existing.vision.model),
                "max_concurrency": form.get("vision_max_concurrency")
                or str(existing.vision.max_concurrency),
            },
            "document": {
                "author": _model_or_existing(
                    form.get("document_author", ""), existing.document.author
                ),
                "doc_no_prefix": form.get("document_doc_no_prefix", ""),
            },
        }
        try:
            # pydantic coerces "passes" from str and enforces ge=1; a non-numeric
            # value is a clean 400 here, not an int() crash before validation.
            cfg = ModelsConfig.model_validate(data)
        except Exception as exc:  # noqa: BLE001 - pydantic ValidationError -> clear 400
            raise HTTPException(status_code=400, detail=f"invalid config: {exc}") from exc
        try:
            save_models_config(cfg, resolved_config_path)
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"could not save config to {resolved_config_path}: {exc}"
            ) from exc
        return RedirectResponse("/ui/config?saved=1", status_code=303)

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
        if session_id in staged:
            manifest = sessions[session_id][0]
            return HTMLResponse(render_steps_review_page(session_id, manifest))
        status = jobs.status(session_id)
        if status["status"] != "done":
            return HTMLResponse(render_session_processing_page(session_id, status))
        manifest = sessions[session_id][0]
        session_dir = sessions[session_id][3]
        report = json.loads((session_dir / "report.json").read_text(encoding="utf-8"))
        config = load_models_config(resolved_config_path).model_dump()
        title = manifest.session.title or manifest.session.id
        date = manifest.session.started_utc
        return HTMLResponse(render_session_page(session_id, title, date, report, config))

    @app.post("/ui/sessions/{session_id}/confirm-steps")
    async def ui_confirm_steps(session_id: str, request: Request):
        """Drops any steps the user unchecked on the steps-review page, and
        reorders the rest by each step's submitted position number (decimals
        allowed, so moving one step never requires renumbering the others --
        the server just stable-sorts on whatever values were submitted),
        then submits the session for generation. A `pos-{step_id}` field is
        OPTIONAL per step (not just per request) -- a plain `keep`-only POST
        (the documented curl API, and every pre-reorder-feature caller)
        keeps working exactly as before, with steps ordered however they
        were submitted; only a field that's PRESENT but not a number is
        rejected, since that can only come from a malformed request, never
        the real review page or the documented API shape. The manifest on
        disk is rewritten to the selected/reordered subset BEFORE
        jobs.submit runs, so _generate/assemble_steps never sees a dropped
        or out-of-order step -- the 1:1 manifest<->doc mapping invariant
        holds by construction, the same way it would for a hand-edited
        manifest."""
        _require_known_session(session_id)
        if session_id not in staged:
            raise HTTPException(status_code=409, detail="session already submitted for generation")
        form = await request.form()
        keep_ids = form.getlist("keep")
        if not keep_ids:
            raise HTTPException(status_code=400, detail="select at least one step to keep")

        positions = {}
        for index, step_id in enumerate(keep_ids):
            raw = form.get(f"pos-{step_id}")
            if raw is None:
                positions[step_id] = (float(index), index)  # no position given -> submission order
                continue
            try:
                positions[step_id] = (float(raw), index)
            except ValueError:
                raise HTTPException(
                    status_code=400, detail=f"invalid position for step {step_id!r}"
                ) from None
        ordered_ids = sorted(keep_ids, key=positions.__getitem__)

        manifest, screenshots_dir, annotated_dir, session_dir = sessions[session_id]
        try:
            selected = select_manifest_steps(manifest, ordered_ids)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        (session_dir / "manifest.json").write_text(
            json.dumps(manifest_to_schema_dict(selected)), encoding="utf-8"
        )
        sessions[session_id] = (selected, screenshots_dir, annotated_dir, session_dir)
        staged.discard(session_id)
        jobs.submit(session_id, lambda: _generate(session_id))
        return RedirectResponse(f"/ui/sessions/{session_id}", status_code=303)

    @app.get("/sessions/{session_id}/raw/{filename}")
    def get_raw_screenshot(session_id: str, filename: str):
        """Serves pre-generation screenshots for the steps-review page's
        thumbnails -- annotated_dir (get_annotated_image, below) is empty
        until generation actually runs, so a staged session's only images
        live in screenshots_dir. Registered before the single-segment
        catch-all below regardless, since the extra /raw/ segment means the
        two routes can never collide."""
        _require_known_session(session_id)
        screenshots_dir = sessions[session_id][1]
        try:
            name = Path(filename).name
            dest = (screenshots_dir / name).resolve()
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=404, detail="file not found") from exc
        if screenshots_dir.resolve() not in dest.parents or not dest.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        mime, _ = mimetypes.guess_type(str(dest))
        return Response(dest.read_bytes(), media_type=mime or "application/octet-stream")

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
