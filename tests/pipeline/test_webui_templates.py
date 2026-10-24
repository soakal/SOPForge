"""Review UI on Jinja2 templates: values are autoescaped everywhere, the
templates ship in the frozen bundle, and the step editor's Regenerate button
really posts to the regenerate route (it used to be a <form> nested in the
edit form, which browsers drop, so the click saved the edit instead)."""

import os
from pathlib import Path

import pytest

from pipeline.webui.pages import (
    render_config_page,
    render_library_page,
    render_session_page,
)

REPO = Path(__file__).resolve().parent.parent.parent
EVIL = '<script>alert("x")</script>'


def test_hostile_values_are_escaped_on_every_page():
    rows = [
        {
            "session_id": "s1",
            "title": EVIL,
            "date": "2026-01-01",
            "mode": EVIL,
            "doc_no": EVIL,
            "steps": 1,
            "state": "ready",
            "status_label": EVIL,
            "needs_review": True,
            "exports": [(EVIL, "/x")],
            "thumb": None,
            "updated": EVIL,
        }
    ]
    pages = [
        render_library_page([], query=EVIL, rows=rows),
        render_session_page(
            "sid",
            EVIL,
            EVIL,
            {
                "verify_claims": [{"claim_id": "c1", "text": EVIL}],
                "phases": [{"name": EVIL, "step_ids": ["step-001"]}],
                "spl_text_overflow": [EVIL],
                "document": {"title": EVIL},
            },
            {"steps": {"model": EVIL}},
            steps=[{"step_id": "step-001", "text": EVIL}],
        ),
        render_config_page(
            {
                "steps": {"provider": "ollama", "model": EVIL, "endpoint": EVIL},
                "narrative": {"provider": "ollama", "model": "m", "endpoint": "e"},
                "vision": {"provider": "ollama", "model": "m", "endpoint": "e"},
                "polish": {"provider": "ollama", "model": "m", "endpoint": "e"},
                "document": {"author": EVIL},
            },
            {},
        ),
    ]
    for page in pages:
        assert EVIL not in page
        assert "&lt;script&gt;alert(" in page


def test_templates_are_bundled_in_the_server_exe():
    spec = (REPO / "sopforge-server.spec").read_text(encoding="utf-8")
    assert '("src/pipeline/webui/templates", "src/pipeline/webui/templates")' in spec
    assert (REPO / "src/pipeline/webui/templates/base.html").exists()


@pytest.mark.ui
def test_regenerate_button_posts_to_the_regenerate_route(tmp_path):
    from playwright.sync_api import sync_playwright

    page_html = render_session_page(
        "sid",
        "T",
        "d",
        {},
        {},
        steps=[{"step_id": "step-001", "text": "Click Save."}],
        can_regenerate=True,
    )
    posts = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, executable_path=os.environ.get("SOPFORGE_TEST_CHROMIUM") or None
        )
        page = browser.new_page()

        def handle(route):
            # The sidebar's LLM status probe POSTs too; only form posts count.
            if route.request.method == "POST" and not route.request.url.endswith("/config/test"):
                posts.append(route.request.url)
            route.fulfill(status=200, body="ok")

        page.route("http://sopforge.test/**", handle)
        page.route(
            "http://sopforge.test/ui/sessions/sid",
            lambda route: route.fulfill(status=200, body=page_html, content_type="text/html"),
        )
        page.goto("http://sopforge.test/ui/sessions/sid")
        page.click("#edit-step-001 summary")
        page.click("#edit-step-001 >> text=Regenerate with AI")
        page.wait_for_load_state()
        browser.close()
    assert posts == ["http://sopforge.test/ui/sessions/sid/steps/step-001/regenerate"]
