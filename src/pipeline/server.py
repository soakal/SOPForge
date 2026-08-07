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
import logging
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
from starlette.concurrency import run_in_threadpool
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
    PolishConfig,
    SectionConfig,
    VisionConfig,
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
from pipeline.generation import generate_step_text
from pipeline.jobs import JobRunner
from pipeline.library import remove_entry
from pipeline.library import search as library_search
from pipeline.library import upsert_entry
from pipeline.llm_client import LLMClient
from pipeline.manifest import load_manifest, manifest_to_schema_dict, select_manifest_steps
from pipeline.narration_polish import polish_narration
from pipeline.narrative import generate_narrative
from pipeline.polish import generate_polish_fields
from pipeline.preflight import probe_section
from pipeline.render import render_html, render_markdown, render_steps_llm_mode
from pipeline.semantic_align import build_step_contexts, semantic_align
from pipeline.sidecar import build_sidecar_report
from pipeline.summarize import generate_title_and_overview
from pipeline.transcript import _parse_json_segments, align_transcript_to_steps
from pipeline.transcription import Transcriber
from pipeline.vision import caption_images
from pipeline.webui.pages import (
    render_config_page,
    render_library_page,
    render_session_page,
    render_session_processing_page,
    render_steps_review_page,
)
from pipeline.webui.review import render_review_page

logger = logging.getLogger(__name__)


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


def _load_step_state(session_dir):
    """The parsed steps.json sidecar (written by _write_all_exports) as a
    dict, or None if it doesn't exist, isn't valid JSON, or -- even though
    it parses -- isn't a JSON *object* (e.g. a damaged file containing a
    bare list or string). A session generated before this sidecar existed
    (or one whose file somehow got clobbered/truncated) degrades gracefully
    wherever this is consulted, rather than an AttributeError from calling
    .get() on the wrong type escaping into a generation job and failing it."""
    path = Path(session_dir) / "steps.json"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _load_step_index(session_dir):
    """The steps.json sidecar's per-step list, or None -- see
    _load_step_state. Kept as its own helper since most callers (the
    session page's report rows) only need the list, not the wrapping
    version/narrative_text envelope. Also guards against a "steps" value
    that isn't actually a list (same damaged-file class as
    _load_step_state's own guard)."""
    state = _load_step_state(session_dir)
    if not state:
        return None
    steps = state.get("steps")
    return steps if isinstance(steps, list) else None


# Generous but finite per-file caps on ingest -- this is a local,
# single-user tool with no auth, so any process that can reach it (or a
# DNS-rebinding page slipping past the host/CSRF guards) could otherwise
# stream unbounded multipart bodies straight to disk/memory. Screenshots
# are realistically a few MB each; a narration WAV is a single recording,
# not hours of audio. Caught by a repo security audit.
_MAX_UPLOAD_FILE_BYTES = 200 * 1024 * 1024  # 200MB
_MAX_NARRATION_WAV_BYTES = 500 * 1024 * 1024  # 500MB


def _copy_capped(src_fileobj, dest_path, max_bytes, what):
    """Streams src_fileobj into dest_path in chunks, aborting with a 413
    (and removing the partial file) if it exceeds max_bytes -- never
    buffers the whole upload in memory just to check its size."""
    written = 0
    with dest_path.open("wb") as out:
        while True:
            chunk = src_fileobj.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                out.close()
                dest_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"{what} exceeds the {max_bytes // (1024 * 1024)}MB limit",
                )
            out.write(chunk)


def _read_capped(fileobj, max_bytes, what):
    """Reads fileobj into memory in chunks, raising a 413 (never buffering
    past the cap) if it exceeds max_bytes."""
    chunks = []
    total = 0
    while True:
        chunk = fileobj.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"{what} exceeds the {max_bytes // (1024 * 1024)}MB limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _edits_path(session_dir):
    return Path(session_dir) / "edits.json"


def _load_edits(session_dir):
    """{step_id: {"text", "edited_utc"}} of every manual override on this
    session, or {} if none exist / the file is missing or corrupt --
    including "corrupt" in the sense of parsing as valid JSON that isn't
    the expected shape (e.g. a bare list/string, or a "steps" value that
    isn't itself an object), which must degrade the same way a missing
    file does rather than raise mid-generation. A per-step edit route
    writes here; steps.json (the derived, LLM-pipeline output) is never
    the source of truth for an edit -- it gets rewritten wholesale by
    every _write_all_exports call, so an override that must survive a
    from-scratch rerender has to live in a file the pipeline only ever
    reads, never overwrites."""
    try:
        parsed = json.loads(_edits_path(session_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    edits = parsed.get("steps")
    return edits if isinstance(edits, dict) else {}


def _save_edit(session_dir, step_id, text):
    edits = _load_edits(session_dir)
    edits[step_id] = {"text": text, "edited_utc": datetime.now(timezone.utc).isoformat()}
    _edits_path(session_dir).write_text(json.dumps({"steps": edits}), encoding="utf-8")


def _clear_edit(session_dir, step_id):
    """Removes one step's override, if any. Returns whether one existed."""
    edits = _load_edits(session_dir)
    if step_id not in edits:
        return False
    del edits[step_id]
    _edits_path(session_dir).write_text(json.dumps({"steps": edits}), encoding="utf-8")
    return True


def _clear_all_edits(session_dir):
    _edits_path(session_dir).unlink(missing_ok=True)


def _apply_manual_edits(session_dir, step_results):
    """Overwrites step_results[i]["text"] in place for any step_id present
    in edits.json, and marks it used_fallback=False, manually_edited=True.
    Never adds, removes, or reorders entries -- an override for a step_id
    not present in step_results (e.g. a step later dropped via the
    steps-review page) is silently ignored, so invariant L1's 1:1 mapping
    can never be perturbed by this. Returns the list of step_ids actually
    applied, in step_results order."""
    edits = _load_edits(session_dir)
    if not edits:
        return []
    applied = []
    for result in step_results:
        override = edits.get(result["step_id"])
        # A malformed per-step entry (not a dict, or missing "text") is
        # treated the same as "no edit for this step" rather than raising --
        # matches _load_edits' own "corrupt degrades like missing" contract.
        if not isinstance(override, dict) or not isinstance(override.get("text"), str):
            continue
        result["text"] = override["text"]
        result["used_fallback"] = False
        result["manually_edited"] = True
        applied.append(result["step_id"])
    return applied


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
    transcriber_factory=None,
    probe_section_fn=None,
    preflight_enabled=None,
    bind_host="127.0.0.1",
    extra_allowed_hosts=(),
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
    (per-field, via generate_polish_fields).

    transcriber_factory: cfg -> object with a .transcribe(audio_path) method
    (matching transcription.Transcriber's interface), called once per
    generation job that has a narration.wav and no explicit transcript
    (see _maybe_transcribe_narration). Defaults to a real Transcriber built
    from config/models.toml's `[transcription]` section. Tests override
    this to a stub so a run never downloads/loads real Whisper weights.

    probe_section_fn: section-config -> dict (matching preflight.probe_section's
    interface), used by POST /ui/config/test and, best-effort, once per
    generation job. Defaults to the real probe_section. Tests override this
    to a stub/recorder so the suite never makes a real network call.

    preflight_enabled: whether _generate probes the [steps] endpoint once
    before generation and records the result on the sidecar report.
    Defaults to `llm_client_factory is None` -- i.e. only when this app
    actually talks to the real configured endpoint. An injected
    llm_client_factory (every test, and any live-proof script) means
    generation never touches that endpoint at all, so probing it would
    both report on something the job doesn't use AND add a real network
    round-trip to every test job; pass True explicitly to test the
    preflight wiring itself against a stub probe.

    bind_host: the --host value the server is (or will be) bound to
    (__main__.py). Bound to a real loopback address (the default),
    Host/Origin-header checking stays strict against the fixed local
    allowlist (DNS-rebinding/CSRF protection). Bound to anything else --
    0.0.0.0, a LAN IP -- the operator has deliberately exposed this
    server beyond loopback (USER_MANUAL.md's "server on another machine"
    setup, driven by --host + the capture agent's SOPFORGE_SERVER_URL),
    so there's no fixed set of legitimate Host/Origin values to allowlist
    in advance: _host_guard steps aside entirely (its DNS-rebinding
    threat model has no meaningful fixed allowlist once arbitrary
    addresses are expected to reach the server by design), while
    _csrf_guard switches to a self-referential check -- an Origin must
    match the SAME request's own Host header, not a fixed set -- so CSRF
    protection stays in force (still rejects a request forged from a
    different site's Origin) without needing to know the LAN address in
    advance. Caught by automated PR review, three times over two rounds:
    the guard's first version hard-coded loopback-only and broke that
    setup; the fix only touched _host_guard and missed that _csrf_guard
    needed relaxing too; and that relaxation initially disabled
    _csrf_guard entirely rather than switching to the self-referential
    check, which would have turned off CSRF protection for the whole,
    unauthenticated app (including /shutdown, /ui/config, and session
    delete) the moment anyone ran this documented setup.

    extra_allowed_hosts: additional hostnames _host_guard/_csrf_guard
    trust alongside the real loopback names, ONLY when bind_host is
    loopback (the strict case) -- never set by production code
    (__main__.py). Exists solely so the test suite can trust Starlette
    TestClient's fixed "testserver" Host/Origin without that string being
    baked into every real, shipped build. Caught by automated PR review:
    a prior version hard-coded "testserver" into the production allowlist
    itself."""
    app = FastAPI()
    probe = probe_section_fn or probe_section
    run_preflight = (
        preflight_enabled if preflight_enabled is not None else (llm_client_factory is None)
    )

    # "testserver" (Starlette TestClient's fixed Host/Origin default) is
    # trusted ONLY when pytest is actually the one running -- gated on the
    # PYTEST_CURRENT_TEST env var pytest itself sets for the duration of
    # each test, never present in a real installed/frozen build -- so this
    # can never widen a shipped server's trust. Caught by automated PR
    # review: a prior version hard-coded "testserver" unconditionally.
    _test_hosts = {"testserver"} if os.environ.get("PYTEST_CURRENT_TEST") else set()
    _LOCAL_HOSTS = (
        frozenset({"127.0.0.1", "localhost", "::1"})
        | frozenset(extra_allowed_hosts)
        | frozenset(_test_hosts)
    )
    _ALLOWED_HOSTS = _LOCAL_HOSTS
    _host_guard_strict = bind_host in {"127.0.0.1", "localhost", "::1"}

    @app.middleware("http")
    async def _host_guard(request, call_next):
        """Blocks DNS-rebinding: the CSRF guard below only checks the Origin
        header, which browsers only send on cross-origin requests -- but a
        page that DNS-rebinds its own hostname to 127.0.0.1 can still issue
        same-origin-looking GETs (no Origin header required for e.g. a
        top-level navigation or certain request modes) whose Host header is
        the attacker's rebound domain, not this server's. Checking Host
        directly closes that gap regardless of method or Origin presence.
        A no-op (see bind_host's docstring) when this server was bound to
        anything beyond loopback -- there, "any Host but ours" isn't a
        meaningful allowlist to begin with.

        Host is parsed with urlsplit, not a raw ":"-split, so a bracketed
        IPv6 literal (`Host: [::1]:8420`) resolves to `::1` rather than the
        mangled `[` a naive split produces. Caught by automated PR review."""
        if not _host_guard_strict:
            return await call_next(request)
        host = (urlsplit(f"//{request.headers.get('host') or ''}").hostname or "").lower()
        if host not in _ALLOWED_HOSTS:
            return JSONResponse({"detail": "invalid host header"}, status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def _csrf_guard(request, call_next):
        """CSRF guard for EVERY state-changing request. The server is
        localhost-only BY DEFAULT, but a page on another site could still
        POST to it in the user's browser (e.g. auto-submit a form to
        /shutdown or /ui/config). Browsers attach an Origin header on
        every non-GET/HEAD request, including same-origin ones -- reject
        any whose host isn't this local server. Programmatic clients (the
        capture agent's auto-upload) send no Origin and are allowed. The
        host is matched exactly (not a prefix), so
        'http://127.0.0.1.evil.com' is rejected.

        Bound beyond loopback (the documented "server on another machine"
        setup), there's no fixed allowlist of legitimate Origin values --
        a browser loading the UI from that LAN address legitimately sends
        an Origin with that same LAN hostname on every form POST. This
        guard must still reject an actually cross-origin request, though
        -- silently disabling it entirely in that mode (a prior version
        of this fix, caught by automated PR review as a real security
        regression: it turned off CSRF protection for the whole,
        unauthenticated app, including /shutdown, /ui/config, and session
        delete, the moment anyone ran the documented remote setup) would
        let ANY website the operator's browser visits forge requests to
        this server. Instead, when not in the strict/loopback case, the
        check is self-referential: Origin's hostname must match THIS
        request's own Host header (also urlsplit-parsed) rather than a
        fixed set -- exactly the same test a same-origin request from the
        real UI naturally passes, whatever LAN address it's served from,
        while a request forged from a different site's Origin still
        fails it."""
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            if origin:
                origin_host = urlsplit(origin).hostname
                if _host_guard_strict:
                    allowed = origin_host in _LOCAL_HOSTS
                else:
                    own_host = (
                        urlsplit(f"//{request.headers.get('host') or ''}").hostname or ""
                    ).lower()
                    allowed = bool(origin_host) and origin_host == own_host
                if not allowed:
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

    def make_transcriber(cfg):
        if transcriber_factory is not None:
            return transcriber_factory(cfg)
        return Transcriber(
            model_size=cfg.model_size, device=cfg.device, compute_type=cfg.compute_type
        )

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

    def _render_transcript_md(segments):
        """Render whisper-shaped segments ([{"text", "start", ...}, ...]) as
        readable Markdown: one timestamped line per segment. Mirrors
        transcript.json 1:1 -- pure formatting, no content decisions."""
        lines = [
            f"**[{seg.get('start', 0.0):.1f}s]** {seg.get('text', '').strip()}" for seg in segments
        ]
        return "# Narration transcript\n\n" + "\n\n".join(lines) + "\n"

    def _maybe_transcribe_narration(session_dir):
        """Best-effort: if this session has a narration.wav (opt-in mic
        recording, see capture/narration.py) and no transcript.* has been
        uploaded already -- an explicit human-supplied transcript always
        wins over a derived one -- and [transcription].enabled is on,
        transcribes it locally and writes transcript.json in exactly the
        segment shape align_transcript_to_steps' .json branch already
        parses, so _apply_transcript picks it up completely unchanged. Also
        writes transcript.md alongside it -- a human-readable sidecar (not
        consumed by any pipeline code) so a user can open the transcript
        directly instead of parsing JSON.
        Runs on the background job thread (never the HTTP request path) --
        CPU transcription can take real seconds and must never risk
        upload_session's own timeout or make "stop recording" feel slow.
        Never raises: a disabled toggle, missing model, or unavailable
        hardware must degrade to "no transcript" for this session, not fail
        the whole generation job. Returns a short report note, or None if
        nothing happened."""
        wav_path = session_dir / "narration.wav"
        if not wav_path.exists():
            return None
        if any(session_dir.glob("transcript.*")):
            return None
        try:
            transcription_cfg = load_models_config(resolved_config_path).transcription
        except Exception:  # noqa: BLE001 - a bad config must not break generation
            logger.exception("could not load [transcription] config for %s", session_dir)
            return None
        if not transcription_cfg.enabled:
            return None
        try:
            segments = make_transcriber(transcription_cfg).transcribe(wav_path)
        except Exception:  # noqa: BLE001 - missing model/hardware: skip, don't fail the job
            logger.exception("narration transcription failed for %s", session_dir)
            return "narration.wav could not be transcribed (see server log) -- doc generated from steps only"
        if not segments:
            return "narration.wav produced no speech segments"
        (session_dir / "transcript.json").write_text(
            json.dumps({"segments": segments}), encoding="utf-8"
        )
        (session_dir / "transcript.md").write_text(
            _render_transcript_md(segments), encoding="utf-8"
        )
        return "narration.wav transcribed locally and placed onto steps"

    def _generate(session_id, polish_override=None):
        manifest, screenshots_dir, annotated_dir, session_dir = sessions[session_id]

        # Manifest-free "screenshots + transcript" sessions take a different
        # path: no LLM step phrasing, no click-marker annotation.
        if _is_photo_mode(session_dir):
            _generate_photo(session_id, polish_override=polish_override)
            return

        narration_transcription_note = _maybe_transcribe_narration(session_dir)

        # One generation attempt per step, round-trip-gated with a
        # template fallback (task-06) -- if the configured endpoint is
        # unreachable, or Anthropic routing is on with no API key, or the
        # reply doesn't hold up, that step just falls back; nothing here
        # ever retries.
        models_cfg = load_models_config(resolved_config_path)

        preflight_result = None
        if run_preflight:
            try:
                preflight_result = probe(models_cfg.steps)
            except Exception:  # noqa: BLE001 - preflight is diagnostics, never a gate
                logger.exception("preflight probe failed for session %s", session_id)

        llm_client = make_llm_client()
        try:
            step_results, annotated_paths = render_steps_llm_mode(
                manifest,
                screenshots_dir,
                annotated_dir,
                llm_client,
                on_progress=lambda i, n: jobs.set_progress(session_id, i, n),
                max_concurrency=models_cfg.steps.max_concurrency,
                use_vision=models_cfg.steps.use_vision,
            )
        finally:
            close = getattr(llm_client, "close", None)
            if callable(close):
                close()
        _assert_1to1_mapping(manifest, step_results)
        _apply_manual_edits(session_dir, step_results)

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
                # Persisted immediately (not just left on the in-memory
                # Manifest) -- caught by automated PR review: without this,
                # a server restart reloads the on-disk manifest, which still
                # has an empty title, and _reexport_session (the per-step
                # edit/regenerate path, which makes no LLM call and so can
                # never re-derive a title) would then silently revert the
                # document/library entry to the raw session id.
                (session_dir / "manifest.json").write_text(
                    json.dumps(manifest_to_schema_dict(manifest)), encoding="utf-8"
                )

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
                    passes=models_cfg.narrative.passes,
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
        if narration_transcription_note:
            report["narration_transcription"] = narration_transcription_note
        if preflight_result:
            report["llm_preflight"] = preflight_result

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
                # NOT persisted here -- canonicalize_terms below can still
                # rewrite manifest.session.title's spelling, and persisting
                # before that would save a title the document itself never
                # actually uses. Persisted once, after canonicalization,
                # right below.

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
        if manifest.session.title != user_title:
            # Compared against user_title (the ORIGINAL on-disk value,
            # captured before this function touched it at all) -- not
            # against gen_title, so this fires whenever the title actually
            # changed end to end, whether or not canonicalize_terms itself
            # altered it further. Persist the FINAL (post-canonicalization)
            # title -- see _generate's identical comment: without this, a
            # restart or a later no-LLM edit re-export would revert the
            # document/library entry to the raw session id, or (before this
            # fix) to a title whose spelling doesn't match what the
            # document actually shows.
            (session_dir / "manifest.json").write_text(
                json.dumps(manifest_to_schema_dict(manifest)), encoding="utf-8"
            )

        # Applied AFTER canonicalize_terms, deliberately -- a human's exact
        # wording (including spelling choices) must never get "corrected"
        # by the consistency pass, the same reasoning as _write_all_exports'
        # polish-restore below.
        manually_edited = _apply_manual_edits(session_dir, step_results)

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
        if manually_edited:
            report["manually_edited_steps"] = manually_edited
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
            # A human's exact wording must never be silently reworded by the
            # polish pass -- generate_polish_fields has no concept of "this
            # text is authoritative," it treats every step's text as equally
            # eligible for rewriting. Restore the original (edited) text for
            # any step flagged manually_edited; md_step_results is already a
            # list of shallow copies (generate_polish_fields' own contract),
            # so mutating it here doesn't touch step_results.
            manual_text_by_id = {
                r["step_id"]: r["text"] for r in step_results if r.get("manually_edited")
            }
            for r in md_step_results:
                if r["step_id"] in manual_text_by_id:
                    r["text"] = manual_text_by_id[r["step_id"]]
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

        # Per-step text + screenshot sidecar, for the session page's report
        # rows (webui/pages.py's _finding_row) to show a thumbnail and the
        # actual generated text next to a flagged step instead of a bare
        # step id. This is NOT the sidecar report (report.json, invariant
        # L5) -- it's presentational data derived from the same md_step_results
        # that already shipped in the doc, so it always agrees with what the
        # user is reading.
        #
        # version 2 additionally persists narrative_text and each step's
        # narration/manually_edited -- everything _reexport_session (the
        # per-step edit/regenerate feature) needs to rebuild all six export
        # formats from disk without an LLM call. A step-edit route refuses to
        # run against a version 1 (or missing) file rather than silently
        # dropping narration/narrative from every format on re-export.
        screenshot_by_id = {s.id: s.screenshot for s in manifest.steps}
        (session_dir / "steps.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "narrative_text": md_narrative_text,
                    "steps": [
                        {
                            "step_id": r["step_id"],
                            "text": r.get("text", ""),
                            "used_fallback": bool(r.get("used_fallback")),
                            "manually_edited": bool(r.get("manually_edited")),
                            "narration": r.get("narration"),
                            "screenshot": screenshot_by_id.get(r["step_id"], ""),
                        }
                        for r in md_step_results
                    ],
                }
            ),
            encoding="utf-8",
        )

        (session_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
        upsert_entry(sessions_root, session_id, manifest, report)

    def _load_reexportable_state(session_id):
        """Read-only precondition check + load for _reexport_session:
        raises HTTPException(409) if the session's persisted steps.json (or
        an annotated screenshot it references) is too old/missing/damaged
        to re-export faithfully, otherwise returns (step_results,
        annotated_paths, state). Never mutates anything, so it's safe to
        call synchronously from a route BEFORE jobs.submit -- a refused
        edit/regenerate then 409s immediately instead of poisoning an
        already-"done" session's status to "error" (via JobRunner's generic
        any-exception-fails-the-job handling) and hiding its still-intact
        finished documents behind _require_done. _reexport_session calls
        this again itself when the job actually runs, in case the on-disk
        state changed between the route's check and the worker picking up
        the job."""
        manifest, _screenshots_dir, annotated_dir, session_dir = sessions[session_id]
        state = _load_step_state(session_dir)
        steps_list = state.get("steps") if state else None
        if state is None or state.get("version") != 2 or not isinstance(steps_list, list):
            raise HTTPException(
                status_code=409,
                detail=(
                    "this session predates per-step editing (steps.json is missing, "
                    "an older version, or damaged) -- rerender it once before editing steps"
                ),
            )
        try:
            by_id = {e["step_id"]: e for e in steps_list}
        except (TypeError, KeyError) as exc:
            raise HTTPException(
                status_code=409, detail=f"steps.json is damaged ({exc}) -- rerender first"
            ) from exc
        step_results = []
        for step in manifest.steps:
            entry = by_id.get(step.id)
            if entry is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"steps.json is missing an entry for {step.id} -- rerender first",
                )
            result = {
                "step_id": step.id,
                "text": entry.get("text", ""),
                "used_fallback": bool(entry.get("used_fallback")),
                "manually_edited": bool(entry.get("manually_edited")),
            }
            if entry.get("narration"):
                result["narration"] = entry["narration"]
            step_results.append(result)

        annotated_paths = []
        for step in manifest.steps:
            path = annotated_dir / step.screenshot
            if not path.exists():
                raise HTTPException(
                    status_code=409,
                    detail=f"annotated screenshot missing for {step.id} -- rerender first",
                )
            annotated_paths.append(path)
        return step_results, annotated_paths, state

    def _reexport_session(
        session_id, text_overrides=None, preflight_result=None, regenerate_declined_step=None
    ):
        """Re-renders all six export formats + report.json + steps.json +
        the library entry from the session's persisted steps.json state --
        no LLM call of any kind. Used by the per-step edit route (a pure
        text swap) and the regenerate route (text_overrides carries the one
        freshly-generated step). Raises HTTPException(409) if the session's
        persisted state is too old/incomplete to re-export faithfully --
        refusing is the honest choice; silently re-exporting from a v1 (or
        missing) steps.json would drop narration/narrative from every
        format.

        text_overrides: optional {step_id: (text, used_fallback)} applied
        AFTER edits.json's manual-edit overrides -- lets the regenerate
        route's fresh LLM result win over a (now-superseded, and by then
        already-deleted) manual edit for that one step.

        preflight_result: optional preflight.probe_section()-shaped dict to
        attach to report["llm_preflight"], so the regenerate route's own
        diagnostic probe is visible on the session page exactly like a full
        generation's.

        regenerate_declined_step: optional step_id to record in
        report["regenerate_declined_steps"] -- set by _regenerate_step when
        it deliberately skipped applying a fallback result over an existing
        manual edit (see that function's docstring), so the session page
        can still tell the reviewer their regenerate click didn't produce
        anything, even though nothing else about the step changed.

        The polish stage is force-skipped (polish_override="off") --
        steps.json already holds the text that shipped (polished, if
        [polish] was on for the original generation), and re-polishing
        polished text would compound rewrites the reviewer never asked
        for. A full /rerender is the correct way to get a fresh polish
        pass."""
        manifest, _screenshots_dir, annotated_dir, session_dir = sessions[session_id]
        step_results, annotated_paths, state = _load_reexportable_state(session_id)

        _apply_manual_edits(session_dir, step_results)
        for step_id, (text, used_fallback) in (text_overrides or {}).items():
            for result in step_results:
                if result["step_id"] == step_id:
                    result["text"] = text
                    result["used_fallback"] = used_fallback
                    result["manually_edited"] = False
                    break
        _assert_1to1_mapping(manifest, step_results)

        existing_report = {}
        report_path = session_dir / "report.json"
        try:
            existing_report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        existing_report.pop("manually_edited_steps", None)
        manually_edited = [r["step_id"] for r in step_results if r.get("manually_edited")]
        if manually_edited:
            existing_report["manually_edited_steps"] = manually_edited
        existing_report.pop("regenerate_declined_steps", None)
        if regenerate_declined_step:
            existing_report["regenerate_declined_steps"] = [regenerate_declined_step]
        if _is_photo_mode(session_dir):
            # Photo-mode sessions have a SYNTHETIC manifest (every step's
            # window/element is deliberately empty, photo_build.py) and
            # used_fallback there means "no vision caption", not "template
            # fallback" -- build_sidecar_report's manifest-derived
            # empty_metadata_steps/used_fallback-derived template_fallback_steps
            # would therefore flag every single step. _generate_photo always
            # hardcodes both to [] for exactly this reason; match that here
            # rather than corrupting the report on the first edit.
            existing_report["template_fallback_steps"] = []
            existing_report["empty_metadata_steps"] = []
        else:
            rebuilt = build_sidecar_report(manifest, step_results, [])
            rebuilt.pop("verify_claims", None)  # preserve the existing narrative-derived list
            rebuilt.pop("manually_edited_steps", None)  # already handled above
            existing_report.pop("template_fallback_steps", None)
            existing_report.pop("empty_metadata_steps", None)
            existing_report.update(rebuilt)
        if preflight_result:
            existing_report["llm_preflight"] = preflight_result

        _write_all_exports(
            session_id,
            manifest,
            step_results,
            annotated_paths,
            annotated_dir,
            session_dir,
            existing_report,
            narrative_text=state.get("narrative_text"),
            polish_override="off",
            claims=(),
        )

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

    def _ingest_session(
        manifest_json, files, transcript=None, stage=False, narration_wav_upload=None
    ):
        """Shared by the JSON API (POST /sessions) and the browser upload
        form (POST /ui/upload). `transcript`, if given, is a (filename,
        text) tuple. `narration_wav_upload`, if given, is the raw
        UploadFile (not pre-read bytes -- streamed straight to
        session_dir/narration.wav via _copy_capped, same as a screenshot,
        instead of buffering the whole file in a Python bytes object
        first; caught by automated PR review, a prior version read it
        fully into memory via _read_capped before this function ever saw
        it). Its validity (real audio, transcribable) is only checked
        later at generation time (_maybe_transcribe_narration), never a
        400 at ingest -- an unreadable/malformed WAV is still written and
        only size, not content, is validated here. When `stage` is True,
        the session is registered but not submitted for generation -- it's
        left in `staged` so the user can drop mis-captured steps via the
        steps-review page before generation runs (see POST
        .../confirm-steps). Returns the new session_id, or raises
        HTTPException on bad input."""
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
                    _copy_capped(upload.file, dest, _MAX_UPLOAD_FILE_BYTES, f"screenshot {name!r}")
                if narration_wav_upload is not None and narration_wav_upload.filename:
                    _copy_capped(
                        narration_wav_upload.file,
                        session_dir / "narration.wav",
                        _MAX_NARRATION_WAV_BYTES,
                        "narration audio",
                    )
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"invalid upload: {exc}") from exc
        except HTTPException:
            # A rejected upload (bad filename, oversized file, any other
            # failure mid-copy) must not leave a session directory behind:
            # it's never registered in `sessions` (that happens below, only
            # on success), has no report.json, so _restore_sessions_from_disk
            # never picks it up and ui_delete can never reach it -- an
            # unreachable, permanent orphan otherwise. Same pattern
            # _ingest_photo_session already uses. Caught by automated PR
            # review (this became much more reachable once _copy_capped's
            # 413 was added -- previously only the invalid-filename 400 hit
            # it).
            shutil.rmtree(session_dir, ignore_errors=True)
            raise

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
        narration_wav_file: UploadFile | None = File(default=None),
        stage: bool = Form(False),
    ):
        session_id = _ingest_session(
            manifest_json,
            files,
            _read_transcript(transcript_file),
            stage=stage,
            narration_wav_upload=narration_wav_file,
        )
        return {"session_id": session_id, "status": _status_of(session_id)}

    @app.post("/ui/upload")
    def ui_upload(
        manifest_file: UploadFile = File(...),
        files: list[UploadFile] = File(default=[]),
        transcript_file: UploadFile | None = File(default=None),
        narration_wav_file: UploadFile | None = File(default=None),
    ):
        try:
            manifest_json = manifest_file.file.read().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=400, detail=f"invalid manifest encoding: {exc}"
            ) from exc
        session_id = _ingest_session(
            manifest_json,
            files,
            _read_transcript(transcript_file),
            stage=True,
            narration_wav_upload=narration_wav_file,
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
                    # Capped the same way _ingest_session's screenshots are
                    # -- this path had no size limit at all before, on the
                    # same unauthenticated server. Caught by automated PR
                    # review. PIL needs a seekable buffer, so this reads
                    # the (capped) image into memory rather than streaming
                    # straight to disk like _copy_capped; acceptable at the
                    # same 200MB ceiling as a single screenshot.
                    image_bytes = _read_capped(
                        upload.file, _MAX_UPLOAD_FILE_BYTES, f"image {upload.filename!r}"
                    )
                    with Image.open(io.BytesIO(image_bytes)) as im:
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
    def rerender(
        session_id: str,
        polish: Literal["off", "local", "haiku"] | None = None,
        discard_edits: bool = False,
    ):
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
        behavior untouched.

        `discard_edits` (optional query param, default False): any manual
        per-step edits (POST .../steps/{step_id}) are PRESERVED across an
        ordinary rerender by default -- a rerender is the documented remedy
        for "I changed my config/model" or "I added a transcript", and
        destroying a reviewer's hand-written fix as a side effect of that
        would be a data-loss bug, not a feature. Pass discard_edits=true to
        explicitly wipe every edit on this session and regenerate from
        scratch."""
        _require_known_session(session_id)
        if discard_edits:
            _clear_all_edits(sessions[session_id][3])
        jobs.submit(session_id, lambda: _generate(session_id, polish_override=polish))
        return {"session_id": session_id, "status": jobs.status(session_id)["status"]}

    @app.post("/ui/sessions/{session_id}/rerender")
    def ui_rerender(
        session_id: str,
        polish: Literal["off", "local", "haiku"] | None = None,
        discard_edits: bool = False,
    ):
        """Same effect as POST /sessions/{id}/rerender, but redirects back
        to the session page instead of returning JSON -- the JSON route
        stays as-is for API/script callers, since a plain HTML <form> POST
        would otherwise navigate the browser to a raw JSON blob. See
        rerender() above for what `polish`/`discard_edits` do."""
        _require_known_session(session_id)
        if discard_edits:
            _clear_all_edits(sessions[session_id][3])
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

    @app.post("/ui/sessions/{session_id}/steps/{step_id}")
    async def ui_edit_step(session_id: str, step_id: str, request: Request):
        """Replaces one step's text with human-written text, and re-exports
        all six formats from disk -- no LLM call, so this is fast even on a
        many-step session. The edit is durable: it's written to edits.json
        BEFORE the fast re-export, so it survives a later full /rerender
        (see rerender()'s docstring) rather than only lasting until the
        next from-scratch generation."""
        session_dir = _require_done(session_id)
        manifest = sessions[session_id][0]
        if step_id not in manifest.step_ids():
            raise HTTPException(status_code=404, detail=f"unknown step: {step_id!r}")
        form = await request.form()
        text = (form.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="step text must not be empty")
        if len(text) > 20000:
            raise HTTPException(status_code=400, detail="step text is too long (max 20000 chars)")
        # Validate synchronously, before the edit is even saved, so a
        # session too old/damaged to re-export 409s immediately instead of
        # reaching jobs.submit and flipping an already-"done" session to
        # "error" (server.py's _load_reexportable_state docstring).
        _load_reexportable_state(session_id)
        _save_edit(session_dir, step_id, text)
        jobs.submit(session_id, lambda: _reexport_session(session_id))
        return RedirectResponse(f"/ui/sessions/{session_id}", status_code=303)

    @app.post("/ui/sessions/{session_id}/steps/{step_id}/regenerate")
    def ui_regenerate_step(session_id: str, step_id: str):
        """One fresh LLM call for exactly this step (generate_step_text --
        the identical round-trip-gated/template-fallback path the original
        generation used, invariants L2/L3), then a no-LLM re-export of all
        six formats. Supersedes (and deletes) any manual edit on this step
        with a genuine AI result -- the freshly-generated text wins. If the
        attempt instead fell back to render_step_template (used_fallback,
        e.g. the LLM was unreachable or its reply failed the round-trip
        gate) and this step already has a manual edit, the edit is left
        untouched instead: the generic template text would silently and
        irreversibly overwrite hand-written wording with nothing better in
        its place. Caught by automated PR review. Not available for screenshots+
        transcript ("photo mode") builds: there's no manifest ground truth
        (real element/window names) to phrase a step from there, only a
        synthetic placeholder manifest -- regenerating would just produce
        garbage like "Click the position (0, 0) in the current window."."""
        session_dir = _require_done(session_id)
        if _is_photo_mode(session_dir):
            raise HTTPException(
                status_code=409,
                detail=(
                    "regenerate isn't available for screenshots+transcript builds "
                    "(no manifest ground truth to phrase from)"
                ),
            )
        manifest = sessions[session_id][0]
        if step_id not in manifest.step_ids():
            raise HTTPException(status_code=404, detail=f"unknown step: {step_id!r}")
        # Same synchronous check as ui_edit_step, before the LLM call and
        # before jobs.submit -- see _load_reexportable_state's docstring.
        _load_reexportable_state(session_id)
        jobs.submit(session_id, lambda: _regenerate_step(session_id, step_id))
        return RedirectResponse(f"/ui/sessions/{session_id}", status_code=303)

    def _regenerate_step(session_id, step_id):
        manifest, screenshots_dir, _annotated_dir, session_dir = sessions[session_id]
        step = next(s for s in manifest.steps if s.id == step_id)
        cfg = load_models_config(resolved_config_path)
        preflight_result = None
        if run_preflight:
            try:
                preflight_result = probe(cfg.steps)
            except Exception:  # noqa: BLE001 - preflight is diagnostics, never a gate
                logger.exception("preflight probe failed regenerating %s/%s", session_id, step_id)
        llm = make_llm_client()
        try:
            text, used_fallback = generate_step_text(
                step, llm, use_vision=cfg.steps.use_vision, screenshot_dir=screenshots_dir
            )
        finally:
            close = getattr(llm, "close", None)
            if callable(close):
                close()

        if used_fallback:
            # The AI attempt produced nothing real (render_step_template's
            # deterministic manifest interpolation, not a generated reply).
            # Only let it overwrite text that was ALREADY a fallback --
            # never a manual edit or a previously-successful generation,
            # both persisted with used_fallback=False (_apply_manual_edits
            # always sets it False; a step that round-trip-passed on its
            # last real generation/regenerate does too). Checking the
            # step's own persisted flag (not just "is there a manual
            # edit") makes this symmetric: a transient LLM outage must
            # never silently downgrade EITHER kind of good text to
            # boilerplate with no way back short of a full /rerender.
            # Caught by automated PR review -- the first version of this
            # guard only protected a manual edit, missing the identical
            # risk to a prior successful generation.
            #
            # _apply_manual_edits folds in any PENDING edits.json override
            # (saved via the edit route but not yet re-exported into
            # steps.json) the same way _reexport_session itself would --
            # without it, a manual edit saved but never re-exported would
            # still show steps.json's stale used_fallback=True and this
            # guard would wrongly let the fallback through.
            step_results, _annotated_paths, _state = _load_reexportable_state(session_id)
            _apply_manual_edits(session_dir, step_results)
            persisted = next((r for r in step_results if r["step_id"] == step_id), None)
            if persisted is not None and not persisted.get("used_fallback"):
                # Re-export with no override so the existing (good) text
                # stays fully applied; report the decline instead of
                # silently discarding it.
                _reexport_session(
                    session_id,
                    preflight_result=preflight_result,
                    regenerate_declined_step=step_id,
                )
                return

        # _reexport_session BEFORE clearing the edit -- text_overrides always
        # wins over a stale edits.json entry for this same step_id (applied
        # after _apply_manual_edits inside _reexport_session), so ordering
        # this way is safe, and it means a re-export that fails (409: the
        # session's persisted state is too old/damaged) leaves the manual
        # edit intact rather than deleting it with nothing to show for it.
        _reexport_session(
            session_id,
            text_overrides={step_id: (text, used_fallback)},
            preflight_result=preflight_result,
        )
        _clear_edit(session_dir, step_id)

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

    _CONFIG_TEST_SECTIONS = {
        "steps": SectionConfig,
        "narrative": SectionConfig,
        "vision": VisionConfig,
        "polish": PolishConfig,
    }

    @app.post("/ui/config/test")
    async def ui_config_test(request: Request):
        """Cheap reachability probe for one config section (the "Test
        connection" button on /ui/config) -- probes the values currently in
        the form, not necessarily the saved config, so a user can check a
        change before saving it. Always 200 with the probe result in the
        body; a probe failure is data for the page to display, not an HTTP
        error. 400 only for a malformed request (unknown section, or an
        invalid provider override).

        The probe itself runs via run_in_threadpool: probe_section is a
        synchronous httpx call with up to a ~6s combined connect/read
        timeout, and this route is `async def` (it awaits request.form()),
        so calling it directly would block the single event loop for that
        long, stalling every other request to the server for the duration
        of the probe. Caught by automated PR review."""
        form = await request.form()
        name = form.get("section")
        section_cls = _CONFIG_TEST_SECTIONS.get(name)
        if section_cls is None:
            raise HTTPException(status_code=400, detail=f"unknown section: {name!r}")
        existing = load_models_config(resolved_config_path)
        saved_section = getattr(existing, name)
        overrides = {k: form.get(k) for k in ("provider", "endpoint", "model") if form.get(k)}
        base = saved_section.model_dump()
        if "provider" in overrides:
            # Drop the legacy `anthropic = true` flag before validating: a
            # config saved back when that flag was the only way to route to
            # Anthropic still carries it, and SectionConfig's after-validator
            # (config.py's _legacy_anthropic) unconditionally flips
            # provider back to "anthropic" whenever it's set -- silently
            # reverting an explicit provider="ollama" override and probing
            # the wrong service entirely. Caught by automated PR review.
            base.pop("anthropic", None)
        try:
            # model_validate (not model_copy) so an invalid provider override
            # from the form is caught here as a 400, never handed to probe_section.
            section = section_cls.model_validate({**base, **overrides})
        except Exception as exc:  # noqa: BLE001 - pydantic ValidationError -> clear 400
            raise HTTPException(status_code=400, detail=f"invalid section: {exc}") from exc
        result = await run_in_threadpool(probe, section)
        return JSONResponse({**result, "section": name})

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
                "use_vision": form.get("steps_use_vision") == "on",
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
            "polish": {
                "enabled": form.get("polish_enabled") == "on",
                "provider": form.get("polish_provider", "ollama"),
                "endpoint": form.get("polish_endpoint", ""),
                "model": _model_or_existing(form.get("polish_model", ""), existing.polish.model),
            },
            "document": {
                "author": _model_or_existing(
                    form.get("document_author", ""), existing.document.author
                ),
                "doc_no_prefix": form.get("document_doc_no_prefix", ""),
            },
            "transcription": {
                "enabled": form.get("transcription_enabled") == "on",
                "model_size": _model_or_existing(
                    form.get("transcription_model_size", ""), existing.transcription.model_size
                ),
                "device": form.get("transcription_device") or existing.transcription.device,
                "compute_type": _model_or_existing(
                    form.get("transcription_compute_type", ""),
                    existing.transcription.compute_type,
                ),
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
        return HTMLResponse(
            render_session_page(
                session_id,
                title,
                date,
                report,
                config,
                steps=_load_step_index(session_dir),
                can_regenerate=not _is_photo_mode(session_dir),
            )
        )

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
