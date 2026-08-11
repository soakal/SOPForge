"""Review web UI pages: library listing (search + upload), per-session
processing/review pages, colored sidecar report, re-render/delete forms, and a
read-only config panel. Plain server-rendered HTML with a single embedded
stylesheet (no build step, no Node, no external assets -- works fully offline).
A shared _shell() wraps every page in the same modern, light/dark-aware chrome."""

import html

from pipeline import __version__

# One embedded stylesheet, shared by every page. System font stack, a centered
# card layout, an accent color, and a dark-mode variant via prefers-color-scheme
# -- no webfonts or external CSS so it renders identically offline.
_STYLE = """
:root{--bg:#f5f6f8;--card:#ffffff;--fg:#1f2328;--muted:#6b7280;--accent:#2563eb;
--border:#e5e7eb;--ok:#16a34a;--warn:#d97706;--bad:#dc2626;--radius:12px}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b20;--fg:#e6e8eb;
--muted:#9aa1ac;--accent:#6795ff;--border:#2a2f37;--ok:#3fb950;--warn:#e3a008;--bad:#f04f4f}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);line-height:1.55;
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:28px 20px 72px}
.brand{display:flex;align-items:center;gap:10px;margin-bottom:22px}
.brand .dot{width:22px;height:22px;border-radius:6px;background:var(--accent);
box-shadow:0 2px 8px rgba(37,99,235,.35)}
.brand b{font-size:1.05rem;letter-spacing:.2px}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
h1{font-size:1.7rem;margin:.1em 0 .4em}
h2{font-size:1.15rem;margin:1.5em 0 .5em}
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);
padding:18px 20px;margin:16px 0;box-shadow:0 1px 3px rgba(0,0,0,.05)}
.muted,small{color:var(--muted)}
input[type=text],input[type=file],select,textarea{font:inherit;padding:9px 11px;border:1px solid var(--border);
border-radius:9px;background:var(--card);color:var(--fg);max-width:440px;width:100%}
textarea{min-height:6em;max-width:100%;resize:vertical;font-family:inherit}
table{border-collapse:collapse;width:100%;font-size:.88em}
th,td{border:1px solid var(--border);padding:6px 10px;text-align:left;white-space:nowrap}
th{background:rgba(0,0,0,.04)}
.field{margin:14px 0}
label{display:block;font-weight:600;margin-bottom:6px}
button{font:inherit;font-weight:600;padding:9px 17px;border:0;border-radius:9px;
background:var(--accent);color:#fff;cursor:pointer}
button:hover{filter:brightness(1.08)}
.actions{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}
.actions form{margin:0}
button.secondary{background:transparent;color:var(--fg);border:1px solid var(--border)}
ul.sessions{list-style:none;padding:0;margin:0}
ul.sessions li{padding:12px 2px;border-bottom:1px solid var(--border)}
ul.sessions li:last-child{border-bottom:0}
ul.dl{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:10px}
ul.dl a{display:inline-block;padding:9px 14px;border:1px solid var(--border);
border-radius:9px;background:var(--card)}
section[data-status]{border-left:4px solid var(--border)}
section[data-status="green"]{border-left-color:var(--ok)}
section[data-status="yellow"]{border-left-color:var(--warn)}
section[data-status="red"]{border-left-color:var(--bad)}
blockquote.narration{margin:8px 0;padding:8px 14px;border-left:3px solid var(--accent);
background:rgba(37,99,235,.06);border-radius:0 8px 8px 0}
iframe{width:100%;height:460px;border:1px solid var(--border);border-radius:var(--radius);background:#fff}
.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:.85em;font-weight:600}
.pill.processing,.pill.queued{background:rgba(217,119,6,.15);color:var(--warn)}
.pill.error{background:rgba(220,38,38,.15);color:var(--bad)}
.spin{display:inline-block;width:13px;height:13px;margin-right:7px;vertical-align:-1px;
border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:s .8s linear infinite}
@keyframes s{to{transform:rotate(360deg)}}
progress{width:100%;height:14px;border-radius:7px;accent-color:var(--accent);margin:10px 0 4px}
footer{margin-top:44px;color:var(--muted);font-size:.85em}
img.shot{cursor:zoom-in}
#lightbox{position:fixed;inset:0;background:rgba(0,0,0,.75);display:flex;
align-items:center;justify-content:center;z-index:100}
#lightbox[hidden]{display:none}
#lightbox img{max-width:95vw;max-height:90vh;object-fit:contain;border-radius:var(--radius)}
#lightbox .close{position:absolute;top:18px;right:24px;color:#fff;font-size:2rem;
line-height:1;cursor:pointer;user-select:none}
.finding{display:flex;gap:14px;align-items:flex-start;padding:10px 0;
border-bottom:1px solid var(--border)}
.finding:last-child{border-bottom:0}
.finding img.shot{max-width:180px;border-radius:8px;border:1px solid var(--border);flex-shrink:0}
details.stepedit{border-bottom:1px solid var(--border);padding:6px 0}
details.stepedit:last-child{border-bottom:0}
details.stepedit summary{cursor:pointer}
.pill.edited{background:rgba(217,119,6,.15);color:var(--warn)}
"""


def _shell(title, body):
    """Wrap page body in the shared modern chrome + stylesheet."""
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head><body>"
        '<div class="wrap"><div class="brand"><span class="dot"></span>'
        "<b>SOPForge</b></div>"
        f"{body}"
        f"<footer>SOPForge v{html.escape(__version__)} &middot; Built by CWI AI</footer>"
        "</div></body></html>"
    )


def _color_for(category, count):
    if count == 0:
        return "green"
    return "red" if category == "template_fallback_steps" else "yellow"


def _section(title, category, items):
    color = _color_for(category, len(items))
    if items:
        body = "<ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in items) + "</ul>"
    else:
        body = '<p class="muted">None.</p>'
    # data-status + <h2> immediately after the section open tag is a contract
    # the review tests assert against -- keep the heading first, no nested tag
    # between the attribute and the <h2>.
    return (
        f'<section class="card" data-status="{color}"><h2>{html.escape(title)}</h2>{body}</section>'
    )


def _finding_row(session_id, step_id, entry):
    """One report finding (a flagged step_id) rendered as a thumbnail +
    generated text + a link into the doc preview iframe at that step's
    anchor (render_html's id="{step_id}", task-06), plus a link to that
    step's editor (#edit-{step_id}, task-06's _step_editor) and an "edited"
    badge when the step already carries a manual edit. `entry` is the
    matching steps.json record (or None if there wasn't one for this
    step_id -- degrades to a text-only row rather than a broken <img>)."""
    sid = html.escape(session_id)
    sid_step = html.escape(step_id)
    screenshot = (entry or {}).get("screenshot") or ""
    thumb = (
        f'<img class="shot" loading="lazy" '
        f'src="/sessions/{sid}/{html.escape(screenshot)}" alt="{sid_step}">'
        if screenshot
        else ""
    )
    text = html.escape((entry or {}).get("text") or "")
    text_html = f'<div class="muted">{text}</div>' if text else ""
    edited_badge = (
        '<span class="pill edited">edited</span>' if (entry or {}).get("manually_edited") else ""
    )
    return (
        f'<div class="finding">{thumb}'
        f'<div><a href="/sessions/{sid}/doc.html#{sid_step}" target="docpreview">'
        f"<strong>{sid_step}</strong></a> "
        f'<a class="muted" href="#edit-{sid_step}">Edit</a> {edited_badge}'
        f"{text_html}</div></div>"
    )


def _step_section(title, category, step_ids, session_id, steps_by_id):
    """_section's counterpart for step-id findings: same card/color/heading
    contract (data-status + <h2> first, no nested tag between them -- see
    _section's own comment), but thumbnail rows instead of bare <li>s when
    a per-step index (steps.json, task-05) is available. Falls back to
    _section's plain list for a session generated before steps.json
    existed, or when steps_by_id is None."""
    if steps_by_id is None:
        return _section(title, category, step_ids)
    color = _color_for(category, len(step_ids))
    if step_ids:
        body = "".join(
            _finding_row(session_id, step_id, steps_by_id.get(step_id)) for step_id in step_ids
        )
    else:
        body = '<p class="muted">None.</p>'
    return (
        f'<section class="card" data-status="{color}"><h2>{html.escape(title)}</h2>{body}</section>'
    )


def _step_editor(session_id, step_id, entry, can_regenerate):
    """One collapsed <details> per step: a thumbnail, a textarea holding
    its current text (editable via POST .../steps/{step_id}), and an
    optional "Regenerate with AI" button (POST .../steps/{step_id}/
    regenerate) -- omitted entirely when can_regenerate is False (photo-
    mode sessions have no manifest ground truth to regenerate from)."""
    sid = html.escape(session_id)
    sid_step = html.escape(step_id)
    screenshot = (entry or {}).get("screenshot") or ""
    thumb = (
        f'<img class="shot" loading="lazy" '
        f'src="/sessions/{sid}/{html.escape(screenshot)}" alt="{sid_step}">'
        if screenshot
        else ""
    )
    text = html.escape((entry or {}).get("text") or "")
    summary_text = (entry or {}).get("text") or ""
    summary_preview = html.escape(
        summary_text if len(summary_text) <= 80 else summary_text[:77] + "..."
    )
    regenerate_form = (
        f'<form method="post" action="/ui/sessions/{sid}/steps/{sid_step}/regenerate">'
        '<button type="submit" class="secondary">Regenerate with AI</button></form>'
        if can_regenerate
        else ""
    )
    edited_badge = (
        ' <span class="pill edited">edited</span>' if (entry or {}).get("manually_edited") else ""
    )
    return (
        f'<details class="stepedit" id="edit-{sid_step}">'
        f"<summary><strong>{sid_step}</strong>{edited_badge} "
        f'<span class="muted">{summary_preview}</span></summary>'
        f'<div class="finding">{thumb}'
        '<div style="flex:1">'
        f'<form method="post" action="/ui/sessions/{sid}/steps/{sid_step}">'
        '<div class="field"><label>Step text</label>'
        f'<textarea name="text" rows="4" required>{text}</textarea></div>'
        '<div class="actions"><button type="submit">Save &amp; re-export</button></div>'
        "</form>"
        f'<div class="actions">{regenerate_form}</div>'
        "</div></div></details>"
    )


def _edit_steps_card(session_id, steps, can_regenerate):
    """A card listing every step as a collapsed editor -- not only flagged
    ones, since "the phrasing is just fine except this one step" is not
    necessarily a flagged condition. Returns "" when steps is None (a
    session generated before steps.json existed)."""
    if steps is None:
        return ""
    rows = "".join(
        _step_editor(session_id, e["step_id"], e, can_regenerate)
        for e in steps
        if isinstance(e, dict) and "step_id" in e
    )
    return (
        '<h2>Edit steps</h2><div class="card" id="edit-steps">'
        '<p class="muted">Edits are saved immediately and survive a later re-render. '
        "Regenerating a step discards its manual edit and asks the AI again."
        + ("" if can_regenerate else " Regenerate isn't available for this build mode.")
        + f"</p>{rows}</div>"
    )


def render_library_page(entries, query=None):
    if entries:
        rows = "".join(
            f'<li><a href="/ui/sessions/{html.escape(e["session_id"])}">'
            f'{html.escape(e["title"])}</a> <span class="muted">({html.escape(e["date"])})</span></li>'
            for e in entries
        )
    else:
        rows = '<li class="muted">No sessions yet.</li>'
    query_value = html.escape(query) if query else ""
    body = (
        '<h1>SOP Library</h1><p><a href="/ui/config">&#9881; Configuration</a></p>'
        '<div class="field"><form method="get" action="/ui">'
        f'<input type="text" name="q" value="{query_value}" placeholder="Search title or date"> '
        '<button type="submit">Search</button></form></div>'
        f'<div class="card"><ul class="sessions">{rows}</ul></div>'
        "<h2>Upload a new session</h2>"
        '<div class="card">'
        '<form method="post" action="/ui/upload" enctype="multipart/form-data">'
        '<div class="field"><label>Manifest (manifest.json)</label>'
        '<input type="file" name="manifest_file" accept=".json" required></div>'
        '<div class="field"><label>Screenshots (select every PNG)</label>'
        '<input type="file" name="files" multiple required></div>'
        '<div class="field"><label>Narration transcript &mdash; optional (.txt or .md)</label>'
        '<input type="file" name="transcript_file" accept=".txt,.md,.json">'
        "<div><small>Label blocks &ldquo;Step 1:&rdquo;, &ldquo;1.&rdquo; or &ldquo;## Step 1&rdquo; "
        "to place each under its step, or write one line (or paragraph) per step, in order.</small></div></div>"
        '<button type="submit">Upload</button></form></div>'
        "<h2>Build from screenshots + transcript (no capture)</h2>"
        '<div class="card">'
        "<p><small>No capture needed &mdash; each image becomes one step, in the "
        "order you select them. With vision captioning on (default), the AI reads "
        "each screenshot plus your narration and writes that step&rsquo;s "
        "instruction; otherwise the transcript supplies the text.</small></p>"
        '<form method="post" action="/ui/build" enctype="multipart/form-data">'
        '<div class="field"><label>Title (optional)</label>'
        '<input type="text" name="title" placeholder="My procedure"></div>'
        '<div class="field"><label>Screenshots / images (in order)</label>'
        '<input type="file" name="files" accept="image/*" multiple required></div>'
        '<div class="field"><label>Transcript &mdash; optional (.txt or .md)</label>'
        '<input type="file" name="transcript_file" accept=".txt,.md,.json"></div>'
        '<button type="submit">Build document</button></form></div>'
    )
    return _shell("SOPForge Library", body)


def render_session_processing_page(session_id, status):
    # While the background job is still running, auto-refresh every few seconds
    # so the page turns into the finished review page on its own the moment
    # generation completes -- without this the user is left staring at a stale
    # "processing" snapshot forever. A terminal "error" status stops refreshing.
    is_pending = status["status"] in ("queued", "processing")
    state = html.escape(status["status"])
    spin = '<span class="spin"></span>' if is_pending else ""
    pending_note = (
        '<p class="muted">Generating your SOP&hellip; this page updates automatically '
        "when it&rsquo;s ready.</p>"
        if is_pending
        else ""
    )
    err = (
        f'<p class="muted">{html.escape(status.get("error", ""))}</p>'
        if status["status"] == "error"
        else ""
    )
    progress = status.get("progress")
    progress_bar = ""
    if is_pending and progress and progress.get("total"):
        current, total = progress["current"], progress["total"]
        pct = round(100 * current / total)
        progress_bar = (
            f'<progress value="{current}" max="{total}"></progress>'
            f'<p class="muted">{current} / {total} steps ({pct}%)</p>'
        )
    body = (
        '<p><a href="/ui">&larr; Back to library</a></p>'
        f"<h1>Session {html.escape(session_id)}</h1>"
        f'<p data-status="{state}"><span class="pill {state}">{spin}Status: {state}</span></p>'
        + progress_bar
        + pending_note
        + err
    )
    refresh_meta = '<meta http-equiv="refresh" content="3">' if is_pending else ""
    # The refresh meta must live in <head>; _shell builds the head, so inject it
    # by wrapping: put the meta at the very start of the body is invalid, so
    # instead build the doc directly here when refreshing.
    if refresh_meta:
        return _shell("SOPForge Review", body).replace("<style>", f"{refresh_meta}<style>", 1)
    return _shell("SOPForge Review", body)


# One shared lightbox overlay + its click/Escape handlers -- used by both
# the steps-review page (a click on any .shot thumbnail there) and the
# session/report page (task-07's thumbnail report rows). Hoisted to module
# level so both pages emit the exact same markup/script instead of two
# copies drifting apart. preventDefault() on the .shot click is what stops
# a surrounding <label> (steps-review's cards) from also toggling its
# checkbox -- a plain click handler alone wouldn't, since <img> inside a
# <label> activates the label by default; harmless no-op on pages (like the
# session page) where .shot isn't inside a <label>. Inline attribute
# handlers on the overlay itself, one small <script> for the rest -- same
# "considered exception" pattern as /ui/config's datalist JS, not a
# framework or build step.
_LIGHTBOX_HTML = (
    '<div id="lightbox" hidden '
    "onclick=\"if(event.target===this)this.hidden=true,this.querySelector('img').src=''\">"
    "<span class=\"close\" onclick=\"lightbox.hidden=true;lightbox.querySelector('img').src=''\">"
    "&times;</span>"
    '<img alt=""></div>'
)
_LIGHTBOX_SCRIPT = (
    "<script>"
    "document.addEventListener('click',function(e){"
    "if(!e.target.classList.contains('shot'))return;"
    "e.preventDefault();"
    "var lb=document.getElementById('lightbox');"
    "lb.querySelector('img').src=e.target.src;"
    "lb.querySelector('img').alt=e.target.alt;"
    "lb.hidden=false;"
    "});"
    "document.addEventListener('keydown',function(e){"
    "if(e.key==='Escape'){"
    "var lb=document.getElementById('lightbox');"
    "lb.hidden=true;lb.querySelector('img').src='';"
    "}});"
    "</script>"
)


def render_steps_review_page(session_id, manifest):
    # Shown once, right after upload/build and before generation: a checklist
    # of every captured step so the user can drop mis-clicks (wrong element,
    # accidental double-click) before the doc gets built from them. Checked by
    # default -- this is an opt-out ("uncheck the wrong ones"), not opt-in.
    # Each card also carries a position number, editable to reorder steps --
    # decimals are allowed (e.g. "2.5" inserts between 2 and 3), so moving one
    # step never requires renumbering every other card; the server stable-
    # sorts on whatever values get submitted.
    sid = html.escape(session_id)
    cards = []
    for i, step in enumerate(manifest.steps, start=1):
        step_id = html.escape(step.id)
        detail = step.button if step.action == "click" else step.text_summary
        action_line = f"{html.escape(step.action)} ({html.escape(detail or '')})"
        window_line = f"{html.escape(step.window.title)} &middot; {html.escape(step.element.name)}"
        control_type = html.escape(step.element.control_type)
        shot_src = f"/sessions/{sid}/raw/{html.escape(step.screenshot)}"
        cards.append(
            '<label class="card" style="display:flex;gap:14px;align-items:flex-start">'
            f'<input type="checkbox" name="keep" value="{step_id}" checked '
            'style="margin-top:4px">'
            f'<img class="shot" src="{shot_src}" alt="{step_id}" '
            'style="max-width:220px;border-radius:8px;border:1px solid var(--border)">'
            f"<span><strong>{step_id}</strong> &mdash; {action_line}"
            f'<br><span class="muted">{window_line} ({control_type})</span></span>'
            '<span style="margin-left:auto;text-align:center">'
            '<label class="muted" style="display:block;font-weight:normal;font-size:.85em">'
            "Position</label>"
            f'<input type="number" name="pos-{step_id}" value="{i}" step="any" min="0" '
            'style="width:4.5em">'
            "</span>"
            "</label>"
        )
    # One shared lightbox for every card (not one overlay per card) -- a click
    # on any .shot thumbnail sets its src and shows it. Closes via the X,
    # clicking the dimmed backdrop, or Escape; clearing the src on close
    # avoids holding a large decoded image in memory between views.
    body = (
        f"<h1>Review captured steps</h1>"
        '<p class="muted">Uncheck any wrong or accidental clicks before generating the '
        'document. Edit a step\'s position number to move it -- decimals (e.g. "2.5") '
        "insert between two steps without renumbering the rest. Click a screenshot to "
        "see it full size.</p>"
        f'<form method="post" action="/ui/sessions/{sid}/confirm-steps">'
        + "".join(cards)
        + '<div class="actions"><button type="submit">'
        "Keep selected steps &amp; generate document</button></div></form>"
        + _LIGHTBOX_HTML
        + _LIGHTBOX_SCRIPT
    )
    return _shell("SOPForge Review", body)


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
        "openrouter": "anthropic/claude-haiku-4.5",
        "openai": "gpt-5.4-mini",
        "anthropic": "claude-haiku-4-5-20251001",
    },
    "narrative": {
        "ollama": "qwen3.6:27b",
        "openrouter": "anthropic/claude-sonnet-5",
        "openai": "gpt-5.5",
        "anthropic": "claude-sonnet-5",
    },
    "vision": {
        "ollama": "qwen2.5vl:7b",
        "openrouter": "anthropic/claude-sonnet-5",
        "openai": "gpt-4o",
    },
    "polish": {
        "ollama": "gemma3:12b",
        "openrouter": "anthropic/claude-haiku-4.5",
        "openai": "gpt-5.4-mini",
        "anthropic": "claude-haiku-4-5",
    },
}
_MODEL_SUGGESTIONS = {
    "steps": {
        "ollama": ["qwen3:32b", "qwen3:14b"],
        "openrouter": [
            "anthropic/claude-haiku-4.5",
            "anthropic/claude-sonnet-5",
            "anthropic/claude-opus-4.8",
            "openai/gpt-5.4-mini",
        ],
        "openai": ["gpt-5.4-mini", "gpt-5.4-nano"],
        "anthropic": [
            "claude-haiku-4-5-20251001",
            "claude-sonnet-5",
            "claude-opus-4-8",
            "claude-fable-5",
        ],
    },
    "narrative": {
        "ollama": ["qwen3.6:27b", "qwen3:32b"],
        "openrouter": ["anthropic/claude-sonnet-5", "openai/gpt-5.5"],
        "openai": ["gpt-5.5", "gpt-5.4"],
        "anthropic": ["claude-sonnet-5", "claude-opus-4-8", "claude-fable-5"],
    },
    "vision": {
        "ollama": ["qwen2.5vl:7b"],
        "openrouter": ["anthropic/claude-sonnet-5", "openai/gpt-5.5"],
        "openai": ["gpt-5.5", "gpt-4o"],
        # deliberately no "anthropic" key -- vision excludes bare anthropic (see _VISION_PROVIDERS)
    },
    "polish": {
        "ollama": ["gemma3:12b", "gemma3:27b"],
        "openrouter": ["anthropic/claude-haiku-4.5", "anthropic/claude-sonnet-5"],
        "openai": ["gpt-5.4-mini", "gpt-5.4-nano"],
        "anthropic": ["claude-haiku-4-5", "claude-sonnet-5"],
    },
}


def _provider_select(name, current, key, providers=None):
    opts = "".join(
        f'<option value="{p}"{" selected" if p == current else ""}>{p}</option>'
        for p in (providers or _PROVIDERS)
    )
    # Swap the canonical datalist's contents to the newly-selected provider's
    # per-provider datalist. Defensive null-check: every field's providers
    # currently have a per-provider datalist entry, but a missing one should
    # no-op instead of throwing on a null getElementById.
    onchange = (
        "(function(s){"
        f"var t=document.getElementById('{key}_model_suggestions');"
        f"var d=document.getElementById('{key}_model_suggestions_'+s.value);"
        "if(d)t.innerHTML=d.innerHTML;"
        "})(this)"
    )
    return f'<select name="{name}" onchange="{onchange}">{opts}</select>'


def _model_datalist(key, current_provider):
    canonical_id = f"{key}_model_suggestions"
    per_provider = _MODEL_SUGGESTIONS.get(key, {})
    canonical_options = "".join(
        f'<option value="{html.escape(m)}">' for m in per_provider.get(current_provider, [])
    )
    extra_datalists = "".join(
        f'<datalist id="{key}_model_suggestions_{p}">'
        + "".join(f'<option value="{html.escape(m)}">' for m in models)
        + "</datalist>"
        for p, models in per_provider.items()
    )
    return (
        canonical_id,
        f'<datalist id="{canonical_id}">{canonical_options}</datalist>{extra_datalists}',
    )


def _config_row(key, heading, values, extra="", providers=None):
    suggestions_id, datalist = _model_datalist(key, values["provider"])
    return (
        f'<div class="card"><h2>{heading}</h2>'
        f'<div class="field"><label>Provider</label>'
        f"{_provider_select(f'{key}_provider', values['provider'], key, providers)}</div>"
        f'<div class="field"><label>Model</label>'
        f'<input type="text" name="{key}_model" value="{html.escape(values["model"])}" '
        f'list="{suggestions_id}" '
        f"onfocus=\"this.dataset.prev=this.value;this.value=''\" "
        f"onblur=\"if(!this.value)this.value=this.dataset.prev||''\">{datalist}</div>"
        f'<div class="field"><label>Endpoint <small>(Ollama / custom only)</small></label>'
        f'<input type="text" name="{key}_endpoint" value="{html.escape(values["endpoint"])}"></div>'
        f'<div class="field"><button type="button" class="secondary" '
        f'data-test-section="{key}">Test connection</button> '
        f'<span class="muted" id="{key}_test_result"></span></div>'
        f"{extra}</div>"
    )


# One delegated click listener for every "Test connection" button, appended
# once to render_config_page's body. Reads the section's own provider/
# endpoint/model inputs at click time -- the model field clears on focus and
# restores on blur (see _config_row's onfocus/onblur above), and blur always
# fires before this click handler, so .value is the field's real content by
# the time this reads it.
_CONFIG_TEST_SCRIPT = """<script>
document.addEventListener('click', function(e) {
  var btn = e.target.closest('[data-test-section]');
  if (!btn) return;
  var key = btn.getAttribute('data-test-section');
  var result = document.getElementById(key + '_test_result');
  var field = function(suffix) {
    var el = document.querySelector('[name="' + key + suffix + '"]');
    return el ? el.value : '';
  };
  result.textContent = 'Testing...';
  var body = new URLSearchParams({
    section: key,
    provider: field('_provider'),
    endpoint: field('_endpoint'),
    model: field('_model')
  });
  fetch('/ui/config/test', {method: 'POST', body: body})
    .then(function(r) { return r.json().then(function(d) { return {ok: r.ok, d: d}; }); })
    .then(function(res) {
      var d = res.d;
      if (!res.ok) {
        result.textContent = 'error: ' + (d.detail || 'request rejected');
        return;
      }
      var latency = (d.latency_ms !== null && d.latency_ms !== undefined) ?
        ' (' + d.latency_ms + 'ms)' : '';
      result.textContent = d.status + ': ' + d.detail + latency;
    })
    .catch(function() { result.textContent = 'Test failed'; });
});
</script>"""


def render_config_page(config, keystatus, saved=False):
    steps, narr, vis, polish = (
        config["steps"],
        config["narrative"],
        config["vision"],
        config["polish"],
    )
    doc = config.get("document", {})
    transcription = config.get("transcription", {})
    saved_note = (
        '<div class="card" data-status="green" style="border-left:4px solid var(--ok)">'
        "<p><strong>Saved.</strong> Changes take effect on the next generation.</p></div>"
        if saved
        else ""
    )
    checked = " checked" if vis.get("enabled") else ""
    vision_extra = (
        '<div class="field"><label><input type="checkbox" name="vision_enabled"'
        f"{checked}> Enable vision captioning</label></div>"
        '<div class="field"><label>Max concurrency <small>(concurrent vision calls)</small>'
        "</label>"
        f'<input type="text" name="vision_max_concurrency" value="{vis.get("max_concurrency", 4)}"></div>'
    )
    polish_checked = " checked" if polish.get("enabled") else ""
    polish_extra = (
        '<div class="field"><label><input type="checkbox" name="polish_enabled"'
        f"{polish_checked}> Enable polish pass</label>"
        '<p class="muted">A single formatting/tone pass over the finished document. '
        "Off by default: the local backend only produces a usable rewrite roughly a "
        "quarter to half of the time (falls back to the original text otherwise, never "
        "wrong, just unchanged) — review its output on a real document before relying "
        "on it. Covers all six export formats identically. Can also be overridden "
        "per-job via <code>?polish=off|local|haiku</code> on the rerender endpoint.</p></div>"
    )
    document_card = (
        '<div class="card"><h2>Document</h2>'
        '<div class="field"><label>Author <small>(shown on the title page / revision '
        "table)</small></label>"
        f'<input type="text" name="document_author" value="{html.escape(doc.get("author", "SOPForge"))}"></div>'
        '<div class="field"><label>Document number prefix <small>(e.g. "SOP" — blank omits '
        "the document number entirely)</small></label>"
        f'<input type="text" name="document_doc_no_prefix" value="{html.escape(doc.get("doc_no_prefix", ""))}"></div>'
        "</div>"
    )
    transcription_checked = " checked" if transcription.get("enabled") else ""
    transcription_device = transcription.get("device", "cpu")
    device_opts = "".join(
        f'<option value="{d}"{" selected" if d == transcription_device else ""}>{d}</option>'
        for d in ("cpu", "cuda", "auto")
    )
    transcription_card = (
        '<div class="card"><h2>Narration transcription</h2>'
        '<div class="field"><label><input type="checkbox" name="transcription_enabled"'
        f"{transcription_checked}> Transcribe recorded narration audio</label>"
        '<p class="muted">On by default. When a capture session\'s optional tray '
        '"Record narration (mic)" toggle produced a narration.wav, and no transcript '
        "was uploaded by hand, enabling this runs it through a local speech-to-text "
        "model (faster-whisper) before generation — nothing leaves your network. A "
        "missing model or unavailable hardware just skips transcription for that "
        "session; the document still generates from the steps alone.</p></div>"
        '<div class="field"><label>Model size</label>'
        f'<input type="text" name="transcription_model_size" '
        f'value="{html.escape(transcription.get("model_size", "base"))}"></div>'
        '<div class="field"><label>Device</label>'
        f'<select name="transcription_device">{device_opts}</select></div>'
        '<div class="field"><label>Compute type</label>'
        f'<input type="text" name="transcription_compute_type" '
        f'value="{html.escape(transcription.get("compute_type", "int8"))}"></div>'
        "</div>"
    )
    passes_extra = (
        f'<div class="field"><label>Passes</label>'
        f'<input type="text" name="narrative_passes" value="{narr.get("passes", 1)}"></div>'
    )
    steps_extra = (
        f'<div class="field"><label>Max concurrency <small>(concurrent LLM calls — see '
        "config/models.toml's comment; raising this only helps against an Ollama server "
        "tuned for parallel requests)</small></label>"
        f'<input type="text" name="steps_max_concurrency" value="{steps.get("max_concurrency", 1)}"></div>'
    )

    key_rows = "".join(
        f"<li>{html.escape(_KEY_ENV.get(p, p))}: "
        + ("<strong>set</strong>" if ok else '<span class="muted">not set</span>')
        + "</li>"
        for p, ok in sorted(keystatus.items())
    )
    key_panel = (
        f'<h2>API keys</h2><div class="card"><p class="muted">Keys are read from '
        "environment variables and never stored in the config — this page can only show "
        "whether one is set, never edit or reveal it. To set one: PowerShell "
        "<code>setx ANTHROPIC_API_KEY &quot;sk-ant-...&quot;</code> (swap in the variable name "
        "below for your provider), or Windows Settings → search &quot;Environment Variables&quot; "
        "→ Edit environment variables for your account → New. Then restart the server."
        f"</p><ul>{key_rows or '<li class="muted">All chosen providers are local (Ollama) — no key needed.</li>'}</ul></div>"
    )

    rec_rows = "".join(
        f"<tr><td>{html.escape(task)}</td>"
        + "".join(f"<td>{html.escape(_RECOMMENDED[task].get(p, '—'))}</td>" for p in _PROVIDERS)
        + "</tr>"
        for task in ("steps", "narrative", "vision", "polish")
    )
    rec_table = (
        '<h2>Recommended models</h2><div class="card" style="overflow-x:auto"><table>'
        "<tr><th>Task</th>"
        + "".join(f"<th>{p}</th>" for p in _PROVIDERS)
        + f"</tr>{rec_rows}</table></div>"
    )

    body = (
        '<p><a href="/ui">&larr; Back to library</a></p>'
        "<h1>Configuration</h1>"
        f"{saved_note}"
        '<p class="muted">Pick the AI provider and model for each task. '
        "<strong>Ollama</strong> is local and private (no key, nothing leaves your network). "
        "Other providers use an API key from an environment variable.</p>"
        '<form method="post" action="/ui/config">'
        f"{_config_row('steps', 'Steps', steps, extra=steps_extra)}"
        f"{_config_row('narrative', 'Narration', narr, extra=passes_extra)}"
        f"{_config_row('vision', 'Vision (screenshot captions)', vis, extra=vision_extra, providers=_VISION_PROVIDERS)}"
        f"{_config_row('polish', 'Polish (optional 4th stage)', polish, extra=polish_extra)}"
        f"{document_card}"
        f"{transcription_card}"
        '<button type="submit">Save configuration</button></form>'
        f"{key_panel}{rec_table}"
        f"{_CONFIG_TEST_SCRIPT}"
    )
    return _shell("SOPForge Configuration", body)


def render_session_page(session_id, title, date, report, config, steps=None, can_regenerate=True):
    """steps: the steps.json sidecar's list (task-05), or None for a
    session generated before it existed -- report rows for
    template-fallback/empty-metadata steps show a thumbnail + the step's
    actual text, deep-linked into the doc preview, when available; None
    degrades to the original plain step-id list (_step_section). Also
    drives the "Edit steps" card (task-06) -- every step gets a collapsed
    editor there, not only flagged ones.

    can_regenerate: whether the per-step "Regenerate with AI" button
    appears -- False for screenshots+transcript ("photo mode") sessions,
    which have no manifest ground truth to regenerate a step from."""
    # Guard against a malformed entry (not a dict, or missing "step_id") in
    # a damaged steps.json -- "corrupt degrades like missing" is the
    # contract everywhere else this file is read (server.py's
    # _load_step_state/_load_step_index), so a bad individual entry is
    # dropped here rather than raising and 500ing the whole review page.
    steps_by_id = (
        {e["step_id"]: e for e in steps if isinstance(e, dict) and "step_id" in e}
        if steps is not None
        else None
    )
    verify_items = [
        f"{c['claim_id']}: {c['text']}" if c.get("text") else c["claim_id"]
        for c in report.get("verify_claims", [])
    ]
    sections = "".join(
        [
            _step_section(
                "Template-fallback steps",
                "template_fallback_steps",
                report.get("template_fallback_steps", []),
                session_id,
                steps_by_id,
            ),
            _section("Verify claims", "verify_claims", verify_items),
            _step_section(
                "Empty-metadata steps",
                "empty_metadata_steps",
                report.get("empty_metadata_steps", []),
                session_id,
                steps_by_id,
            ),
        ]
    )

    def _fmt_config(values):
        # Render any section shape (steps/narrative carry anthropic/passes;
        # vision carries enabled) -- endpoint/model first, then the rest.
        ordered = [k for k in ("endpoint", "model") if k in values]
        ordered += [k for k in values if k not in ("endpoint", "model")]
        return ", ".join(f"{k}={values[k]}" for k in ordered)

    config_rows = "".join(
        f"<li>{html.escape(section)}: {html.escape(_fmt_config(values))}</li>"
        for section, values in config.items()
    )
    sid = html.escape(session_id)
    transcript_text = report.get("transcript") or ""
    if "WARNING" in transcript_text:
        transcript_note = (
            '<section class="card" data-status="yellow"><h2>Transcript placement</h2>'
            f"<p>{html.escape(transcript_text)}</p></section>"
        )
    elif transcript_text:
        transcript_note = f'<p class="muted">Transcript: {html.escape(transcript_text)}</p>'
    else:
        transcript_note = ""
    narration_transcription_text = report.get("narration_transcription") or ""
    if "could not be transcribed" in narration_transcription_text:
        narration_transcription_note = (
            '<section class="card" data-status="yellow"><h2>Narration transcription</h2>'
            f"<p>{html.escape(narration_transcription_text)}</p></section>"
        )
    elif narration_transcription_text:
        narration_transcription_note = (
            f'<p class="muted">{html.escape(narration_transcription_text)}</p>'
        )
    else:
        narration_transcription_note = ""
    preflight = report.get("llm_preflight")
    if preflight:
        # A div, deliberately NOT a <section data-status=...> -- the review
        # page's UI-smoke test asserts an exact count of those for the three
        # fixed sidecar-report categories, and this isn't one of them.
        _preflight_color = {"ok": "--ok", "warn": "--warn", "error": "--bad"}.get(
            preflight.get("status"), "--warn"
        )
        latency = preflight.get("latency_ms")
        latency_text = f" ({latency}ms)" if latency is not None else ""
        preflight_note = (
            f'<div class="card" style="border-left:4px solid var({_preflight_color})">'
            "<strong>LLM preflight</strong> &mdash; "
            f"{html.escape(str(preflight.get('provider', '')))}/"
            f"{html.escape(str(preflight.get('model', '')))}: "
            f"{html.escape(str(preflight.get('status', '')))}{latency_text} "
            f"&mdash; {html.escape(str(preflight.get('detail', '')))}</div>"
        )
    else:
        preflight_note = ""
    manually_edited_ids = report.get("manually_edited_steps") or []
    if manually_edited_ids:
        # A div, deliberately NOT a <section data-status=...> -- same reason
        # as preflight_note above: the fixed three sidecar-report categories
        # keep their exact section count, new info is added alongside them.
        manually_edited_note = (
            '<div class="card" style="border-left:4px solid var(--warn)">'
            "<strong>Manually edited steps</strong> &mdash; "
            "these steps carry human-written text, not gated by the round-trip "
            f"check: {html.escape(', '.join(manually_edited_ids))}</div>"
        )
    else:
        manually_edited_note = ""
    regenerate_declined_ids = report.get("regenerate_declined_steps") or []
    if regenerate_declined_ids:
        # Same "div, not a data-status section" reasoning as preflight_note/
        # manually_edited_note above.
        regenerate_declined_note = (
            '<div class="card" style="border-left:4px solid var(--warn)">'
            "<strong>Regenerate didn&rsquo;t produce a result</strong> &mdash; "
            "the AI attempt fell back to the template wording, so your manual "
            f"edit was kept instead: {html.escape(', '.join(regenerate_declined_ids))}</div>"
        )
    else:
        regenerate_declined_note = ""
    downloads = "".join(
        f'<li><a href="/sessions/{sid}/{path}" data-download="{label}">{label}</a></li>'
        for path, label in (
            ("doc.docx", "docx"),
            ("doc.pdf", "pdf"),
            ("doc.single.html", "single-file html"),
            ("export.md.zip", "markdown bundle (zip)"),
        )
    )
    body = (
        '<p><a href="/ui">&larr; Back to library</a></p>'
        f"<h1>{html.escape(title)}</h1>"
        f'<p class="muted">{html.escape(date)} &mdash; {sid}</p>'
        f'<iframe name="docpreview" src="/sessions/{sid}/doc.html"></iframe>'
        "<p>Every recorded step is included &mdash; the document has one step per "
        "captured action, nothing skipped. The report below only flags steps worth "
        "a second look: <em>template-fallback</em> steps are still complete and "
        "factually correct, just written from the captured data rather than the "
        "language model.</p>"
        f"{preflight_note}"
        f"{manually_edited_note}"
        f"{regenerate_declined_note}"
        f"{transcript_note}"
        f"{narration_transcription_note}"
        f"{sections}"
        f"{_edit_steps_card(session_id, steps, can_regenerate)}"
        "<h2>Narration transcript</h2>"
        '<div class="card">'
        f'<form method="post" action="/ui/sessions/{sid}/transcript" enctype="multipart/form-data">'
        '<div class="field"><label>Add or replace a transcript (.txt or .md), then re-render</label>'
        '<input type="file" name="transcript_file" accept=".txt,.md,.json" required></div>'
        '<button type="submit">Add transcript &amp; re-render</button></form>'
        "<div><small>Label blocks &ldquo;Step 1:&rdquo;, &ldquo;1.&rdquo; or &ldquo;## Step 1&rdquo; "
        "to place each under its step, or one line (or paragraph) per step, in order.</small></div></div>"
        '<div class="actions">'
        f'<form method="post" action="/ui/sessions/{sid}/rerender">'
        '<button type="submit">Re-render</button></form>'
        + (
            f'<form method="post" action="/ui/sessions/{sid}/rerender?discard_edits=1">'
            '<button type="submit" class="secondary">Re-render from scratch '
            "(discard my edits)</button></form>"
            if manually_edited_ids
            else ""
        )
        + f'<form method="post" action="/ui/sessions/{sid}/delete">'
        '<button type="submit" class="secondary">Delete</button></form>'
        "</div>"
        f'<h2>Downloads</h2><ul class="dl">{downloads}</ul>'
        f'<h2>Config (read-only)</h2><div class="card"><ul class="sessions">{config_rows}</ul></div>'
        + _LIGHTBOX_HTML
        + _LIGHTBOX_SCRIPT
    )
    return _shell("SOPForge Review", body)
