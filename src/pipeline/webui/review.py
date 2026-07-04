"""Plain-HTML review page rendering the sidecar report (invariant L5). No
build step, no JS framework, no Node in the runtime (CLAUDE.md: "Plain
HTML/JS is fine; no build step, no Node in the runtime") — the report is
rendered server-side into a single static page."""

import html


def _list_section(title, items):
    if not items:
        return f"<h2>{html.escape(title)}</h2><p>None.</p>"
    lis = "".join(f"<li>{html.escape(str(item))}</li>" for item in items)
    return f"<h2>{html.escape(title)}</h2><ul>{lis}</ul>"


def render_review_page(report):
    verify_items = [
        f"{c['claim_id']}: {c['text']}" if c.get("text") else c["claim_id"]
        for c in report.get("verify_claims", [])
    ]

    body = "".join(
        [
            _list_section("Template-fallback steps", report.get("template_fallback_steps", [])),
            _list_section("Verify claims", verify_items),
            _list_section("Empty-metadata steps", report.get("empty_metadata_steps", [])),
        ]
    )

    return (
        "<!doctype html>"
        '<html><head><meta charset="utf-8"><title>SOPForge Review</title></head>'
        f"<body><h1>Review Report</h1>{body}</body></html>"
    )
