"""Review web UI pages: library listing (search) and per-session review
page (doc preview, colored sidecar report, re-render form, read-only
config panel). Plain HTML with a standard <form> POST for re-render — no
JS required, no build step, no Node."""

import html


def _color_for(category, count):
    if count == 0:
        return "green"
    return "red" if category == "template_fallback_steps" else "yellow"


def _section(title, category, items):
    color = _color_for(category, len(items))
    if items:
        body = "<ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in items) + "</ul>"
    else:
        body = "<p>None.</p>"
    return (
        f'<section data-status="{color}" '
        f'style="border-left:4px solid {color};padding-left:1em;">'
        f"<h2>{html.escape(title)}</h2>{body}</section>"
    )


def render_library_page(entries, query=None):
    if entries:
        rows = "".join(
            f'<li><a href="/ui/sessions/{html.escape(e["session_id"])}">'
            f"{html.escape(e['title'])}</a> ({html.escape(e['date'])})</li>"
            for e in entries
        )
    else:
        rows = "<li>No sessions yet.</li>"
    query_value = html.escape(query) if query else ""
    return (
        "<!doctype html>"
        '<html><head><meta charset="utf-8"><title>SOPForge Library</title></head><body>'
        "<h1>SOP Library</h1>"
        '<form method="get" action="/ui">'
        f'<input type="text" name="q" value="{query_value}" placeholder="Search title or date">'
        '<button type="submit">Search</button></form>'
        f"<ul>{rows}</ul>"
        "</body></html>"
    )


def render_session_processing_page(session_id, status):
    return (
        "<!doctype html>"
        '<html><head><meta charset="utf-8"><title>SOPForge Review</title></head><body>'
        f"<h1>Session {html.escape(session_id)}</h1>"
        f'<p data-status="{html.escape(status["status"])}">Status: {html.escape(status["status"])}</p>'
        + (f"<p>{html.escape(status.get('error', ''))}</p>" if status["status"] == "error" else "")
        + "</body></html>"
    )


def render_session_page(session_id, report, config):
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
    config_rows = "".join(
        f"<li>{html.escape(section)}: endpoint={html.escape(values['endpoint'])}, "
        f"model={html.escape(values['model'])}, anthropic={values['anthropic']}</li>"
        for section, values in config.items()
    )
    sid = html.escape(session_id)
    downloads = "".join(
        f'<li><a href="/sessions/{sid}/{path}" data-download="{label}">{label}</a></li>'
        for path, label in (
            ("doc.docx", "docx"),
            ("doc.pdf", "pdf"),
            ("doc.single.html", "single-file html"),
            ("export.md.zip", "markdown bundle (zip)"),
        )
    )
    return (
        "<!doctype html>"
        '<html><head><meta charset="utf-8"><title>SOPForge Review</title></head><body>'
        f"<h1>Session {sid}</h1>"
        f'<iframe src="/sessions/{sid}/doc.html" '
        'style="width:100%;height:400px;border:1px solid #ccc;"></iframe>'
        f"{sections}"
        f'<form method="post" action="/sessions/{sid}/rerender">'
        '<button type="submit">Re-render</button></form>'
        f"<h2>Downloads</h2><ul>{downloads}</ul>"
        f"<h2>Config (read-only)</h2><ul>{config_rows}</ul>"
        "</body></html>"
    )
