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
input[type=text],input[type=file],select{font:inherit;padding:9px 11px;border:1px solid var(--border);
border-radius:9px;background:var(--card);color:var(--fg);max-width:440px;width:100%}
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


def render_steps_review_page(session_id, manifest):
    # Shown once, right after upload/build and before generation: a checklist
    # of every captured step so the user can drop mis-clicks (wrong element,
    # accidental double-click) before the doc gets built from them. Checked by
    # default -- this is an opt-out ("uncheck the wrong ones"), not opt-in.
    sid = html.escape(session_id)
    cards = []
    for step in manifest.steps:
        step_id = html.escape(step.id)
        detail = step.button if step.action == "click" else step.text_summary
        action_line = f"{html.escape(step.action)} ({html.escape(detail or '')})"
        window_line = f"{html.escape(step.window.title)} &middot; {html.escape(step.element.name)}"
        control_type = html.escape(step.element.control_type)
        cards.append(
            '<label class="card" style="display:flex;gap:14px;align-items:flex-start">'
            f'<input type="checkbox" name="keep" value="{step_id}" checked '
            'style="margin-top:4px">'
            f'<img src="/sessions/{sid}/raw/{html.escape(step.screenshot)}" '
            'style="max-width:220px;border-radius:8px;border:1px solid var(--border)">'
            f"<span><strong>{step_id}</strong> &mdash; {action_line}"
            f'<br><span class="muted">{window_line} ({control_type})</span></span>'
            "</label>"
        )
    body = (
        f"<h1>Review captured steps</h1>"
        '<p class="muted">Uncheck any wrong or accidental clicks before generating the '
        "document.</p>"
        f'<form method="post" action="/ui/sessions/{sid}/confirm-steps">'
        + "".join(cards)
        + '<div class="actions"><button type="submit">'
        "Keep selected steps &amp; generate document</button></div></form>"
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
        "ollama": "qwen3:32b",
        "openrouter": "anthropic/claude-sonnet-5",
        "openai": "gpt-5.5",
        "anthropic": "claude-sonnet-5",
    },
    "vision": {
        "ollama": "qwen2.5vl:7b",
        "openrouter": "anthropic/claude-sonnet-5",
        "openai": "gpt-4o",
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
        "ollama": ["qwen3:32b"],
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
        f"{extra}</div>"
    )


def render_config_page(config, keystatus, saved=False):
    steps, narr, vis = config["steps"], config["narrative"], config["vision"]
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
    )
    passes_extra = (
        f'<div class="field"><label>Passes</label>'
        f'<input type="text" name="narrative_passes" value="{narr.get("passes", 1)}"></div>'
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
        for task in ("steps", "narrative", "vision")
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
        f"{_config_row('steps', 'Steps', steps)}"
        f"{_config_row('narrative', 'Narration', narr, extra=passes_extra)}"
        f"{_config_row('vision', 'Vision (screenshot captions)', vis, extra=vision_extra, providers=_VISION_PROVIDERS)}"
        '<button type="submit">Save configuration</button></form>'
        f"{key_panel}{rec_table}"
    )
    return _shell("SOPForge Configuration", body)


def render_session_page(session_id, title, date, report, config):
    verify_items = [
        f"{c['claim_id']}: {c['text']}" if c.get("text") else c["claim_id"]
        for c in report.get("verify_claims", [])
    ]
    sections = "".join(
        [
            _section(
                "Template-fallback steps",
                "template_fallback_steps",
                report.get("template_fallback_steps", []),
            ),
            _section("Verify claims", "verify_claims", verify_items),
            _section(
                "Empty-metadata steps",
                "empty_metadata_steps",
                report.get("empty_metadata_steps", []),
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
        f'<iframe src="/sessions/{sid}/doc.html"></iframe>'
        "<p>Every recorded step is included &mdash; the document has one step per "
        "captured action, nothing skipped. The report below only flags steps worth "
        "a second look: <em>template-fallback</em> steps are still complete and "
        "factually correct, just written from the captured data rather than the "
        "language model.</p>"
        f"{transcript_note}"
        f"{sections}"
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
        f'<form method="post" action="/ui/sessions/{sid}/delete">'
        '<button type="submit" class="secondary">Delete</button></form>'
        "</div>"
        f'<h2>Downloads</h2><ul class="dl">{downloads}</ul>'
        f'<h2>Config (read-only)</h2><div class="card"><ul class="sessions">{config_rows}</ul></div>'
    )
    return _shell("SOPForge Review", body)
