"""Per-session rows + stat tiles for the Forge library table (Phase 6h).

Everything here is read back from files a session already has on disk --
report.json (the sidecar review report), steps.json (the per-step sidecar
_write_all_exports writes), the exports themselves -- plus the job status
the server already tracks. No new state is recorded anywhere: a session
generated before any of these files existed degrades to blanks, never an
error, the same "corrupt degrades like missing" rule server.py uses."""

import datetime as dt
import json
from pathlib import Path

from pipeline.assembler import doc_number

# (file on disk, chip label) -- the download routes server.py serves.
EXPORTS = (
    ("doc.docx", "DOCX"),
    ("doc.pdf", "PDF"),
    ("doc.single.html", "HTML"),
    ("export.md.zip", "MD"),
    ("walkthrough.html", "WALK"),  # 6d; absent for sessions generated before it
    ("spl.pptx", "SPL"),  # 6f; absent before it, or when the deck export failed
)


def _read_json(path):
    try:
        parsed = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _ids(report, key):
    value = report.get(key)
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def review_count(report):
    """Things the sidecar report asks a human to look at: each flagged step
    once (template/caption fallback, empty UIA metadata, uncaptured typed
    value) plus each [verify]
    claim. 0 means nothing is flagged."""
    flagged = (
        set(_ids(report, "template_fallback_steps"))
        | set(_ids(report, "caption_fallback_steps"))
        | set(_ids(report, "empty_metadata_steps"))
        | set(_ids(report, "uncaptured_value_steps"))
    )
    claims = report.get("verify_claims")
    return len(flagged) + (len(claims) if isinstance(claims, list) else 0)


def ai_written_count(session_dir, report, step_count):
    """Steps not on template fallback. steps.json's per-step used_fallback
    is the precise source (it is also True for a photo-mode step whose
    caption came verbatim from the transcript rather than vision); a
    session without steps.json falls back to the report's fallback lists."""
    state = _read_json(Path(session_dir) / "steps.json")
    steps = state.get("steps") if state else None
    if isinstance(steps, list):
        entries = [s for s in steps if isinstance(s, dict)]
        return sum(1 for s in entries if not s.get("used_fallback"))
    if step_count is None:
        return None
    fallback = set(_ids(report, "template_fallback_steps")) | set(
        _ids(report, "caption_fallback_steps")
    )
    return max(step_count - len(fallback), 0)


def _updated(session_dir, fallback_iso):
    for name in ("report.json", "manifest.json"):
        try:
            ts = (Path(session_dir) / name).stat().st_mtime
        except OSError:
            continue
        return ts, dt.datetime.fromtimestamp(ts).strftime("%m/%d/%Y")
    try:
        parsed = dt.datetime.fromisoformat(str(fallback_iso).replace("Z", "+00:00"))
    except ValueError:
        return 0.0, ""
    return parsed.timestamp(), parsed.strftime("%m/%d/%Y")


def session_row(session_id, title, date, session_dir, manifest, status, doc_no_prefix=""):
    """One library-table row. `status` is the server's view of the session:
    "staged" | "queued" | "processing" | "done" | "error" | "unknown"."""
    session_dir = Path(session_dir)
    report = _read_json(session_dir / "report.json") or {}
    steps = len(manifest.steps) if manifest is not None else None
    is_photo = (session_dir / "mode.txt").is_file()

    if status == "staged":
        state, label = "staged", "Review steps"
    elif status in ("queued", "processing"):
        state, label = "generating", "Generating" if status == "processing" else "Queued"
    elif status == "error":
        state, label = "error", "Failed"
    else:
        flags = review_count(report)
        state, label = ("review", f"{flags} to review") if flags else ("ready", "Ready")

    thumb = None
    if manifest is not None and manifest.steps:
        shot = manifest.steps[0].screenshot
        if (session_dir / "annotated" / shot).is_file():
            thumb = f"/sessions/{session_id}/{shot}"
        elif (session_dir / "screenshots" / shot).is_file():
            thumb = f"/sessions/{session_id}/raw/{shot}"

    exports = []
    if status == "done":
        exports = [
            (label_, f"/sessions/{session_id}/{name}")
            for name, label_ in EXPORTS
            if (session_dir / name).is_file()
        ]

    sort_ts, updated = _updated(session_dir, date)
    doc_id = manifest.session.id if manifest is not None else session_id
    return {
        "session_id": session_id,
        "title": title or session_id,
        "date": str(date or ""),
        "mode": "Screenshots" if is_photo else "Capture",
        "doc_no": doc_number(doc_no_prefix, doc_id) if doc_id else None,
        "steps": steps,
        "ai_steps": ai_written_count(session_dir, report, steps) if status == "done" else None,
        "state": state,
        "status_label": label,
        "needs_review": state in ("staged", "review", "error"),
        "exports": exports,
        "thumb": thumb,
        "updated": updated,
        "sort_ts": sort_ts,
    }


def library_stats(rows):
    """The four stat tiles. Procedures counts finished documents; the step
    totals cover the same finished documents so the AI-written share is a
    ratio over one population."""
    done = [r for r in rows if r["ai_steps"] is not None]
    steps = sum(r["steps"] or 0 for r in done)
    ai_steps = sum(r["ai_steps"] for r in done)
    return {
        "procedures": len(done),
        "need_review": sum(1 for r in rows if r["needs_review"]),
        "steps": steps,
        "ai_steps": ai_steps,
    }


def matches_query(row, query):
    """Title / date / doc-number substring match, case-insensitive -- the
    library search box. library.search covers title + date for GET /library;
    the UI adds doc number since it is shown in the table."""
    if not query:
        return True
    q = query.lower()
    return any(q in str(row.get(k) or "").lower() for k in ("title", "date", "doc_no", "updated"))
