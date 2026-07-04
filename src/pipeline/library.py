"""SOP library store (Phase 3): a persistent JSON index of every completed
session — title, session date, formats rendered, sidecar summary counts —
updated whenever a generation job finishes. Backs GET /library's
title/date substring search."""

import json
from pathlib import Path

_FORMATS = ["docx", "pdf", "html", "single_html", "md"]


def _index_path(sessions_root):
    return Path(sessions_root) / "library.json"


def load_index(sessions_root):
    path = _index_path(sessions_root)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _save_index(sessions_root, entries):
    _index_path(sessions_root).write_text(json.dumps(entries, indent=2), encoding="utf-8")


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


def search(sessions_root, query=None):
    """Returns library entries whose title or date contains `query`
    (case-insensitive substring), or all entries if query is empty/None."""
    entries = load_index(sessions_root)
    if not query:
        return entries
    q = query.lower()
    return [e for e in entries if q in e["title"].lower() or q in e["date"].lower()]
