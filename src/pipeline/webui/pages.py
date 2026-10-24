"""Review web UI pages ("Forge", Phase 6h): library table + drop zone,
per-session processing/steps-review/review pages, and the configuration
panel. Server-rendered with Jinja2 templates (webui/templates/, autoescaped
and StrictUndefined) -- no build step, no Node, no CDN. The stylesheet and
the few small scripts live in templates/assets/ and are inlined into each
page, so every page is still one self-contained response. The only external
asset is IBM Plex (OFL), vendored under assets/fonts/ibm-plex/ and served by
this same server (/ui/assets/fonts/...), so every page renders identically
offline. Light/dark follow the OS (prefers-color-scheme).

This module prepares the data each page shows; the templates own the
markup. Every value a template prints is escaped by Jinja, so nothing here
calls html.escape -- the only markup passed in is Markup() built from the
trusted constants below."""

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

from pipeline import __version__
from pipeline.resource_path import resource_path

# The vendored Plex faces, as (family, file stem, weight, style). server.py
# serves exactly these files (FONT_FILES) and sopforge-server.spec bundles
# them -- one list so the three can't drift apart.
_FONT_FACES = [
    ("IBM Plex Sans", "IBMPlexSans-Regular", 400, "normal"),
    ("IBM Plex Sans", "IBMPlexSans-Italic", 400, "italic"),
    ("IBM Plex Sans", "IBMPlexSans-Medium", 500, "normal"),
    ("IBM Plex Sans", "IBMPlexSans-SemiBold", 600, "normal"),
    ("IBM Plex Sans", "IBMPlexSans-Bold", 700, "normal"),
    ("IBM Plex Mono", "IBMPlexMono-Regular", 400, "normal"),
    ("IBM Plex Mono", "IBMPlexMono-Medium", 500, "normal"),
    ("IBM Plex Mono", "IBMPlexMono-SemiBold", 600, "normal"),
]
FONT_FILES = frozenset(f"{stem}.woff2" for _family, stem, _w, _s in _FONT_FACES)
FONT_URL_PREFIX = "/ui/assets/fonts/"

_FONT_CSS = "".join(
    f'@font-face{{font-family:"{family}";font-weight:{weight};font-style:{style};'
    f'font-display:swap;src:url({FONT_URL_PREFIX}{stem}.woff2) format("woff2")}}'
    for family, stem, weight, style in _FONT_FACES
)

# Minimal stroke icons (24-unit viewBox, currentColor) -- inline SVG so the
# page needs no icon font or image requests.
_ICON_PATHS = {
    "library": '<path d="M4 5h16M4 12h16M4 19h10"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "queue": '<path d="M4 13h4l2 3h4l2-3h4"/><path d="M5 13 7 5h10l2 8v6H5z"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1'
    'M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "upload": '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v4h16v-4"/>',
    "image": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 16 5-5 4 4 3-3 6 6"/>',
    "download": '<path d="M12 4v12M7 11l5 5 5-5"/><path d="M4 20h16"/>',
}


def _icon(name):
    return Markup(
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_ICON_PATHS[name]}</svg>'
    )


_NAV_ITEMS = (
    ("library", "/ui", "library", "Library"),
    ("new", "/ui#new", "plus", "New SOP"),
    ("review", "/ui?view=review", "queue", "Review queue"),
    ("config", "/ui/config", "gear", "Configuration"),
)

_ENV = Environment(
    loader=FileSystemLoader(str(resource_path("src", "pipeline", "webui", "templates"))),
    autoescape=True,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)
_ENV.globals.update(
    icon=_icon,
    font_css=Markup(_FONT_CSS),
    version=__version__,
    nav_items=_NAV_ITEMS,
)

_BACK = Markup('<a href="/ui">&larr; Back to library</a>')


def _render(template, *, title, active="library", nav=None, **context):
    """One page: the template plus the shared chrome's context."""
    return _ENV.get_template(template).render(
        title=title,
        active=active,
        review_count=(nav or {}).get("review_count"),
        back=_BACK,
        **context,
    )


def _color_for(category, count):
    if count == 0:
        return "green"
    return "red" if category in ("template_fallback_steps", "caption_fallback_steps") else "yellow"


def _fallback_rows(entries):
    """Minimal table rows straight from library-index entries -- used when
    the caller didn't pass enriched rows (library_view.session_row)."""
    return [
        {
            "session_id": e["session_id"],
            "title": e.get("title") or e["session_id"],
            "date": str(e.get("date") or ""),
            "mode": "",
            "doc_no": None,
            "steps": None,
            "state": "ready",
            "status_label": "Ready",
            "needs_review": False,
            "exports": [],
            "thumb": None,
            "updated": str(e.get("date") or "")[:10],
        }
        for e in entries
    ]


def _stat_tiles(stats):
    steps, ai = stats["steps"], stats["ai_steps"]
    if steps:
        ai_tile = {"value": f"{round(100 * ai / steps)}%", "detail": f"{ai} of {steps}"}
    else:
        ai_tile = {"value": Markup("&mdash;"), "detail": None}
    return [
        {
            "value": stats["procedures"],
            "label": "Finished",
            "detail": f"of {stats.get('total', stats['procedures'])} total",
        },
        {"value": stats["need_review"], "label": "Need review", "detail": None},
        {"value": steps, "label": "Steps captured", "detail": None},
        {"label": "Steps AI-written", **ai_tile},
    ]


def render_library_page(entries, query=None, rows=None, stats=None, nav=None, view=None):
    """entries: library-index entries (library.search). rows/stats: the
    enriched table rows + stat tiles from webui/library_view.py; without
    them the table degrades to title/date from `entries`. view="review"
    renders the review queue (rows needing attention) instead."""
    if rows is None:
        rows = _fallback_rows(entries)
    review = view == "review"
    if review:
        rows = [r for r in rows if r.get("needs_review")]
    rows = [
        {**r, "sub": " \u00b7 ".join(p for p in (r.get("mode") or "", r.get("date", "")[:10]) if p)}
        for r in rows
    ]
    return _render(
        "library.html",
        title="SOPForge Review queue" if review else "SOPForge Library",
        active="review" if review else "library",
        nav=nav,
        rows=rows,
        review=review,
        query=query,
        tiles=_stat_tiles(stats) if stats else None,
    )


def render_session_processing_page(session_id, status, nav=None, title=None):
    # While the background job is still running, auto-refresh every few seconds
    # so the page turns into the finished review page on its own the moment
    # generation completes -- without this the user is left staring at a stale
    # "processing" snapshot forever. A terminal "error" status stops refreshing.
    pending = status["status"] in ("queued", "processing")
    progress = status.get("progress")
    progress_view = None
    if pending and progress and progress.get("total"):
        current, total = progress["current"], progress["total"]
        progress_view = {"current": current, "total": total, "pct": round(100 * current / total)}
    return _render(
        "processing.html",
        title="SOPForge Review",
        nav=nav,
        session_id=session_id,
        heading="Generation failed" if status["status"] == "error" else "Generating document",
        page_title=title,
        state=status["status"],
        pending=pending,
        progress=progress_view,
        error=status.get("error", ""),
    )


def render_steps_review_page(session_id, manifest, nav=None):
    # Shown once, right after upload/build and before generation: a checklist
    # of every captured step so the user can drop mis-clicks (wrong element,
    # accidental double-click) before the doc gets built from them. Checked by
    # default -- this is an opt-out ("uncheck the wrong ones"), not opt-in.
    # Each card also carries a position number, editable to reorder steps --
    # decimals are allowed (e.g. "2.5" inserts between 2 and 3), so moving one
    # step never requires renumbering every other card; the server stable-
    # sorts on whatever values get submitted.
    cards = []
    for i, step in enumerate(manifest.steps, start=1):
        # Empty UIA fields (common for Chrome/Electron) are left out rather
        # than rendered as a dangling "·" or "()".
        window_line = " \u00b7 ".join(p for p in (step.window.title, step.element.name) if p)
        if step.element.control_type:
            window_line += f" ({step.element.control_type})"
        cards.append(
            {
                "step_id": step.id,
                "screenshot": step.screenshot,
                "action": step.action,
                "detail": (step.button if step.action == "click" else step.text_summary) or "",
                "window_line": window_line or "No window or element metadata captured",
                "position": i,
            }
        )
    lede = Markup(
        "Uncheck any wrong or accidental clicks before generating the document. Edit a "
        "step's position number to move it &mdash; decimals (e.g. &ldquo;2.5&rdquo;) insert "
        "between two steps without renumbering the rest. Click a screenshot to see it full size."
    )
    return _render(
        "steps_review.html",
        title="SOPForge Review",
        nav=nav,
        sid=session_id,
        session_title=manifest.session.title or "",
        cards=cards,
        total=len(manifest.steps),
        lede=lede,
    )


_PROVIDERS = ["ollama", "openrouter", "openai", "anthropic"]
# Vision goes through the OpenAI-compatible image path, which excludes anthropic
# (see config.VisionProvider) -- so the vision row offers only these three.
_VISION_PROVIDERS = ["ollama", "openrouter", "openai"]
_KEY_ENV = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}
_RECOMMENDED = {
    "steps": {
        "ollama": "qwen3:32b",
        "openrouter": "anthropic/claude-sonnet-5.5",
        "openai": "gpt-5.4-mini",
        "anthropic": "claude-sonnet-5-5",
    },
    "narrative": {
        "ollama": "qwen3.6:27b",
        "openrouter": "anthropic/claude-opus-5.5",
        "openai": "gpt-5.5",
        "anthropic": "claude-opus-5-5",
    },
    "vision": {
        "ollama": "qwen2.5vl:7b",
        "openrouter": "anthropic/claude-sonnet-5.5",
        "openai": "gpt-4o",
    },
    "polish": {
        "ollama": "gemma3:12b",
        "openrouter": "anthropic/claude-sonnet-5.5",
        "openai": "gpt-5.4-mini",
        "anthropic": "claude-sonnet-5-5",
    },
}
_MODEL_SUGGESTIONS = {
    "steps": {
        "ollama": ["qwen3:32b", "qwen3.6:27b", "qwen3:14b"],
        "openrouter": [
            "anthropic/claude-sonnet-5.5",
            "anthropic/claude-haiku-5.5",
            "anthropic/claude-opus-5.5",
            "openai/gpt-5.4-mini",
        ],
        "openai": ["gpt-5.4-mini", "gpt-5.4-nano"],
        "anthropic": [
            "claude-sonnet-5-5",
            "claude-haiku-5-5",
            "claude-opus-5-5",
            "claude-fable-5-1",
        ],
    },
    "narrative": {
        "ollama": ["qwen3.6:27b", "qwen3:32b"],
        "openrouter": [
            "anthropic/claude-opus-5.5",
            "anthropic/claude-sonnet-5.5",
            "openai/gpt-5.5",
        ],
        "openai": ["gpt-5.5", "gpt-5.4"],
        "anthropic": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1"],
    },
    "vision": {
        "ollama": ["qwen2.5vl:7b", "qwen3-vl:8b", "qwen3.6:27b"],
        "openrouter": [
            "anthropic/claude-sonnet-5.5",
            "anthropic/claude-opus-5.5",
            "openai/gpt-5.5",
        ],
        "openai": ["gpt-5.5", "gpt-4o"],
        # deliberately no "anthropic" key -- vision excludes bare anthropic (see _VISION_PROVIDERS)
    },
    "polish": {
        "ollama": ["gemma3:12b", "gemma3:27b"],
        "openrouter": ["anthropic/claude-sonnet-5.5", "anthropic/claude-haiku-5.5"],
        "openai": ["gpt-5.4-mini", "gpt-5.4-nano"],
        "anthropic": ["claude-sonnet-5-5", "claude-haiku-5-5"],
    },
}


def render_config_page(config, keystatus, saved=False, nav=None):
    return _render(
        "config.html",
        title="SOPForge Configuration",
        active="config",
        nav=nav,
        saved=saved,
        steps=config["steps"],
        narrative=config["narrative"],
        vision=config["vision"],
        polish=config["polish"],
        document=config.get("document", {}),
        transcription=config.get("transcription", {}),
        providers=_PROVIDERS,
        vision_providers=_VISION_PROVIDERS,
        suggestions=_MODEL_SUGGESTIONS,
        recommended=_RECOMMENDED,
        key_rows=[(_KEY_ENV.get(p, p), ok) for p, ok in sorted(keystatus.items())],
    )


_META_FIELDS = (
    ("title", "Title"),
    ("subtitle", "Subtitle"),
    ("doc_no", "Document no."),
    ("author", "Author"),
    ("revision", "Revision"),
)
_DOC_DOWNLOADS = (
    ("doc.docx", "docx"),
    ("doc.pdf", "pdf"),
    ("doc.single.html", "single-file html"),
    ("export.md.zip", "markdown bundle (zip)"),
)
_PREFLIGHT_COLORS = {"ok": "--ok", "warn": "--warn", "error": "--bad"}


def _fmt_config(values):
    # Render any section shape (steps/narrative carry anthropic/passes;
    # vision carries enabled) -- endpoint/model first, then the rest.
    ordered = [k for k in ("endpoint", "model") if k in values]
    ordered += [k for k in values if k not in ("endpoint", "model")]
    return ", ".join(f"{k}={values[k]}" for k in ordered)


def _phases_view(phases, steps_by_id):
    """report.json's "phases" (6c) as rows for the Phases card: letter,
    name, and each step with its text as a hover hint."""
    if not isinstance(phases, list):
        return []
    view = []
    for index, phase in enumerate(phases):
        if not isinstance(phase, dict):
            continue
        view.append(
            {
                "index": index,
                "letter": chr(65 + index) if index < 26 else index + 1,
                "name": str(phase.get("name") or ""),
                "steps": [
                    (str(i), ((steps_by_id or {}).get(i) or {}).get("text") or "")
                    for i in phase.get("step_ids") or []
                ],
            }
        )
    return view


def render_session_page(
    session_id,
    title,
    date,
    report,
    config,
    steps=None,
    can_regenerate=True,
    nav=None,
    walkthrough=False,
    spl_deck=False,
):
    """steps: the steps.json sidecar's list (task-05), or None for a
    session generated before it existed -- report rows for
    template-fallback/empty-metadata steps show a thumbnail + the step's
    actual text, deep-linked into the doc preview, when available; None
    degrades to the original plain step-id list. Also drives the "Edit
    steps" card (task-06) -- every step gets a collapsed editor there, not
    only flagged ones.

    can_regenerate: whether the per-step "Regenerate with AI" button
    appears -- False for screenshots+transcript ("photo mode") sessions,
    which have no manifest ground truth to regenerate a step from."""
    # Guard against a malformed entry (not a dict, or missing "step_id") in
    # a damaged steps.json -- "corrupt degrades like missing" is the
    # contract everywhere else this file is read (server.py's
    # _load_step_state/_load_step_index), so a bad individual entry is
    # dropped here rather than raising and 500ing the whole review page.
    if steps is not None:
        steps = [e for e in steps if isinstance(e, dict) and "step_id" in e]
        steps = [
            {**e, "preview": t if len(t) <= 80 else t[:77] + "..."}
            for e in steps
            for t in [e.get("text") or ""]
        ]
    steps_by_id = {e["step_id"]: e for e in steps} if steps is not None else None
    template_fallback = report.get("template_fallback_steps", [])
    empty_metadata = report.get("empty_metadata_steps", [])
    verify_items = [
        f"{c['claim_id']}: {c['text']}" if c.get("text") else c["claim_id"]
        for c in report.get("verify_claims", [])
    ]
    # Photo builds only (their report always carries the key, even empty);
    # capture sessions keep the fixed three sections.
    caption_fallback = report.get("caption_fallback_steps")
    preflight = report.get("llm_preflight")
    if preflight:
        preflight = {
            "color": _PREFLIGHT_COLORS.get(preflight.get("status"), "--warn"),
            "provider": str(preflight.get("provider", "")),
            "model": str(preflight.get("model", "")),
            "status": str(preflight.get("status", "")),
            "latency": preflight.get("latency_ms"),
            "detail": str(preflight.get("detail", "")),
        }
    transcript = report.get("transcript") or ""
    narration = report.get("narration_transcription") or ""
    document = report.get("document")
    return _render(
        "session.html",
        title="SOPForge Review",
        nav=nav,
        sid=session_id,
        page_title=title,
        date=date,
        doc_downloads=_DOC_DOWNLOADS,
        walkthrough=walkthrough,
        spl_deck=spl_deck,
        document=document if isinstance(document, dict) else None,
        meta_fields=_META_FIELDS,
        preflight=preflight,
        manually_edited=report.get("manually_edited_steps") or [],
        regenerate_declined=report.get("regenerate_declined_steps") or [],
        transcript=transcript,
        transcript_warning="WARNING" in transcript,
        narration_transcription=narration,
        narration_warning="could not be transcribed" in narration,
        uncaptured=report.get("uncaptured_value_steps") or [],
        spl_error=str(report["spl_error"]) if report.get("spl_error") else None,
        spl_overflow=[str(t) for t in report.get("spl_text_overflow") or []],
        template_fallback=template_fallback,
        caption_fallback=caption_fallback,
        verify_items=verify_items,
        empty_metadata=empty_metadata,
        colors={
            "template": _color_for("template_fallback_steps", len(template_fallback)),
            "caption": _color_for("caption_fallback_steps", len(caption_fallback or [])),
            "verify": _color_for("verify_claims", len(verify_items)),
            "empty": _color_for("empty_metadata_steps", len(empty_metadata)),
        },
        steps_by_id=steps_by_id,
        phases=_phases_view(report.get("phases"), steps_by_id),
        omitted=[str(i) for i in report.get("omitted_screenshot_steps") or []],
        steps=steps,
        can_regenerate=can_regenerate,
        config_rows=[(section, _fmt_config(values)) for section, values in config.items()],
    )
