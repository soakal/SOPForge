"""Per-document metadata (Phase 6g): title, subtitle, document number, author
and revision for the title page, revision table, SPL footer and download
names. Every field is inferred so a document builds with nothing entered
(zero-config rule); a reviewer can override any of them per session, and the
overrides live in the session's metadata.json.

Inferred defaults:
- title: the capture's title (auto-generated from the steps when blank).
- author: config/models.toml [document].author when someone set it, else the
  Windows user running SOPForge, else "SOPForge".
- doc_no: "<prefix>-0001", "<prefix>-0002", ... from a per-prefix counter in
  the sessions folder (assign_doc_number), "SOP" standing in when no prefix
  is configured. Each session keeps the number it was first given.
- revision: "1.0"; subtitle: none."""

import getpass
import json
import re
import threading
from dataclasses import asdict, dataclass

from pipeline.assembler import doc_number

DEFAULT_AUTHOR = "SOPForge"
DEFAULT_DOC_PREFIX = "SOP"
DEFAULT_REVISION = "1.0"
FIELDS = ("title", "subtitle", "doc_no", "author", "revision")
MAX_FIELD_LEN = 120
_REVISION_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){0,2}$")
DOC_NO_FILE = "doc_no.txt"
COUNTER_FILE = "doc_numbers.json"
# Export jobs run on worker threads in one server process; the lock keeps two
# jobs from taking the same number.
_COUNTER_LOCK = threading.Lock()


@dataclass(frozen=True)
class DocMeta:
    title: str
    subtitle: str | None
    doc_no: str
    author: str
    revision: str


def _os_user():
    try:
        return getpass.getuser() or None
    except Exception:  # noqa: BLE001 - no usable user name is just "no default"
        return None


def assign_doc_number(session_dir, prefix):
    """This session's document number: the one it was given before
    (session_dir/doc_no.txt), else the next "<prefix>-NNNN" from the counter
    in the sessions folder (session_dir's parent), saved for next time.
    Sequential per prefix, so no two documents share a number -- unlike
    the old session-id suffix, which repeats once there are a few hundred."""
    own = session_dir / DOC_NO_FILE
    try:
        existing = own.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    counter_path = session_dir.parent / COUNTER_FILE
    with _COUNTER_LOCK:
        try:
            counters = json.loads(counter_path.read_text(encoding="utf-8"))
            if not isinstance(counters, dict):
                counters = {}
        except (OSError, ValueError):
            counters = {}
        last = counters.get(prefix)
        number = (last if isinstance(last, int) else 0) + 1
        counters[prefix] = number
        counter_path.write_text(json.dumps(counters, indent=2), encoding="utf-8")
        doc_no = f"{prefix}-{number:04d}"
        own.write_text(doc_no, encoding="utf-8")
    return doc_no


def inferred_meta(manifest, doc_cfg, doc_no=None):
    """Inferred metadata. doc_no comes from assign_doc_number when there is
    a session folder; without one, the session-id form stands in."""
    author = doc_cfg.author if doc_cfg.author and doc_cfg.author != DEFAULT_AUTHOR else None
    prefix = doc_cfg.doc_no_prefix or DEFAULT_DOC_PREFIX
    return DocMeta(
        title=manifest.session.title or manifest.session.id,
        subtitle=None,
        doc_no=doc_no or doc_number(prefix, manifest.session.id),
        author=author or _os_user() or DEFAULT_AUTHOR,
        revision=DEFAULT_REVISION,
    )


def load_overrides(session_dir):
    try:
        data = json.loads((session_dir / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in FIELDS and isinstance(v, str) and v.strip()}


def resolve_meta(manifest, doc_cfg, session_dir):
    """Inferred metadata with this session's overrides applied."""
    prefix = doc_cfg.doc_no_prefix or DEFAULT_DOC_PREFIX
    doc_no = assign_doc_number(session_dir, prefix)
    meta = asdict(inferred_meta(manifest, doc_cfg, doc_no=doc_no))
    overrides = load_overrides(session_dir)
    overrides.pop("title", None)
    meta.update(overrides)
    return DocMeta(**meta)


def validate_overrides(values):
    """Cleaned {field: value} from user input, or raises ValueError naming
    the bad field. Blank values clear an override (back to the inferred
    default); whitespace is collapsed."""
    cleaned = {}
    for field in FIELDS:
        if field not in values:
            continue
        value = " ".join(str(values[field] or "").split())
        if len(value) > MAX_FIELD_LEN:
            raise ValueError(f"{field} is longer than {MAX_FIELD_LEN} characters")
        if field == "revision" and value and not _REVISION_RE.match(value):
            raise ValueError("revision must look like 1.0 or 2.1.3")
        cleaned[field] = value
    return cleaned


def save_overrides(session_dir, values):
    """Merges validated values into metadata.json (blank removes a field).
    The title is not stored here: the caller writes it to the manifest, so
    the library and every format read one title."""
    current = load_overrides(session_dir)
    current.pop("title", None)
    cleaned = validate_overrides(values)
    cleaned.pop("title", None)
    for field, value in cleaned.items():
        if value:
            current[field] = value
        else:
            current.pop(field, None)
    (session_dir / "metadata.json").write_text(json.dumps(current, indent=2), encoding="utf-8")
    return current


def download_basename(meta):
    """SOP Factory-style file name: the title's words joined by underscores,
    case kept, then the revision -- "Create_a_SmartDeploy_Answer_File_v1.0".
    Only letters, digits, '-' survive, so it's safe on Windows and in a
    Content-Disposition header."""
    words = re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", meta.title)
    name = "_".join(words)[:80].rstrip("_") or "SOP"
    return f"{name}_v{meta.revision}"
