"""SOP library store (Phase 3): a persistent JSON index of every completed
session — title, session date, formats rendered, sidecar summary counts —
updated whenever a generation job finishes. Backs GET /library's
title/date substring search."""

import json
import os
import time
from pathlib import Path

_FORMATS = ["docx", "pdf", "html", "single_html", "md"]

# On Windows, any file open (read OR write/rename) can transiently raise
# PermissionError if an AV real-time scanner has the file momentarily
# locked — the same cold-file-open cost Phase 1 measured
# (phases/DEVIATIONS.md). Retrying a plain PermissionError for a few
# seconds absorbs that without weakening any correctness guarantee; the
# concurrent read/write test in test_library.py reproduces it under a
# tight read/write loop against library.json.
_RETRY_ATTEMPTS = 100
_RETRY_DELAY_SECONDS = 0.05


def _retrying(fn):
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return fn()
        except PermissionError:
            if attempt == _RETRY_ATTEMPTS - 1:
                raise
            time.sleep(_RETRY_DELAY_SECONDS)


def _index_path(sessions_root):
    return Path(sessions_root) / "library.json"


def load_index(sessions_root):
    path = _index_path(sessions_root)
    if not path.exists():
        return []
    return _retrying(lambda: json.loads(path.read_text(encoding="utf-8")))


def _save_index(sessions_root, entries):
    """Writes via a temp file + os.replace, which is an atomic rename on
    both Windows and POSIX — GET /library reads this file from request
    threads while a background job's upsert_entry can be rewriting it at
    the same moment, and a plain write_text() truncates before writing,
    so a concurrent reader can observe an empty or partially-written file.
    Safe only for a single writer at a time (true today: JobRunner has
    exactly one worker thread) — a future multi-worker upgrade would need
    a lock around the read-modify-write in upsert_entry, not just this."""
    path = _index_path(sessions_root)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    _retrying(lambda: os.replace(tmp_path, path))


def upsert_entry(sessions_root, session_id, manifest, report):
    """Adds or updates this session's library entry after a completed job
    (re-render overwrites the existing entry for the same session_id, it
    never duplicates). Returns the updated entry."""
    entry = {
        "session_id": session_id,
        "title": manifest.session.title or manifest.session.id,
        "date": manifest.session.started_utc,
        "formats": list(_FORMATS),
        "template_fallback_count": len(report.get("template_fallback_steps", [])),
        "verify_claims_count": len(report.get("verify_claims", [])),
        "empty_metadata_count": len(report.get("empty_metadata_steps", [])),
    }
    entries = [e for e in load_index(sessions_root) if e["session_id"] != session_id]
    entries.append(entry)
    _save_index(sessions_root, entries)
    return entry


def remove_entry(sessions_root, session_id):
    """Removes this session's library entry, if present. No-op if it isn't
    (deleting an already-removed or never-indexed session)."""
    entries = [e for e in load_index(sessions_root) if e["session_id"] != session_id]
    _save_index(sessions_root, entries)


def search(sessions_root, query=None):
    """Returns library entries whose title or date contains `query`
    (case-insensitive substring), or all entries if query is empty/None."""
    entries = load_index(sessions_root)
    if not query:
        return entries
    q = query.lower()
    return [e for e in entries if q in e["title"].lower() or q in e["date"].lower()]
