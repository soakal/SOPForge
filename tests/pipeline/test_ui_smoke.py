"""Playwright UI smoke test (AC2): a real headless browser drives the
actual running dev server end-to-end — upload a fixture session (task-11's
crafted manifest), poll to done, verify the review page's real rendered
DOM, and download a valid docx. Isolated behind the `ui` pytest marker
(playwright + a real browser are heavy, environment-fragile dependencies)
so the default suite stays fast and browser-free (`pytest -q -m ui
tests/pipeline/test_ui_smoke.py` to run this file).

Step generation is now LLM-backed on the live server (round-trip gated,
per-step template fallback — never a retry loop). This test injects the
shared stub LLM client (tests/pipeline/_stub_llm.py), which deterministically
fails every step's round-trip check, so every step falls back to its
template — genuinely exercising the red "Template-fallback steps" flag,
not just asserting it stays green. "Verify claims" is yellow: the stub
narrative client also fails to cover the synthesized-narration claims, and
since audit fix F-02 those [verify] ids are reported instead of discarded
(the same expectation test_webui_pages.py's
test_session_page_colors_sidecar_sections_correctly asserts; this browser
test was missed when F-02 updated that one)."""

import os
import threading
import time
import zipfile
from io import BytesIO
from pathlib import Path

import httpx
import pytest
import uvicorn
from PIL import Image

from pipeline.manifest import load_manifest
from pipeline.server import create_app

from _stub_llm import stub_llm_client_factory

pytestmark = pytest.mark.ui

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


class _ServerThread:
    """A real uvicorn server bound to an OS-assigned free port, run on a
    background thread — Playwright needs an actual HTTP socket to
    navigate to, which TestClient (used everywhere else in this suite)
    does not provide."""

    def __init__(self, app):
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self):
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("server did not start in time")
            time.sleep(0.02)

    @property
    def base_url(self):
        port = self.server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)


def _launch(p):
    """Headless chromium. PLAYWRIGHT_CHROMIUM_EXECUTABLE points at a
    specific browser build when the installed playwright package and the
    pre-installed browser revision differ (e.g. a CI container); unset, it
    is playwright's own default."""
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    return p.chromium.launch(headless=True, **({"executable_path": exe} if exe else {}))


@pytest.fixture
def running_server(tmp_path):
    app = create_app(
        sessions_root=tmp_path / "sessions",
        llm_client_factory=stub_llm_client_factory,
        narrative_llm_client_factory=stub_llm_client_factory,
    )
    server = _ServerThread(app)
    server.start()
    try:
        yield server.base_url
    finally:
        server.stop()


def _upload_fixture_session(base_url):
    manifest_path = FIXTURES / "review-report-manifest.json"
    manifest = load_manifest(manifest_path)
    with httpx.Client(timeout=10) as client:
        files = []
        buffers = []
        for step in manifest.steps:
            buf = BytesIO()
            Image.new("RGB", (1920, 1080), (255, 255, 255)).save(buf, format="PNG")
            buf.seek(0)
            buffers.append(buf)
            files.append(("files", (step.screenshot, buf, "image/png")))
        resp = client.post(
            f"{base_url}/sessions",
            data={"manifest_json": manifest_path.read_text(encoding="utf-8")},
            files=files,
        )
        resp.raise_for_status()
        return resp.json()["session_id"]


def _wait_until_done(base_url, session_id, timeout=15.0):
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=10) as client:
        while time.monotonic() < deadline:
            status = client.get(f"{base_url}/sessions/{session_id}/status").json()
            if status["status"] == "done":
                return
            if status["status"] == "error":
                raise AssertionError(f"session failed: {status.get('error')}")
            time.sleep(0.1)
    raise AssertionError("session never reached done")


def test_ui_smoke_upload_to_docx_download(running_server):
    base_url = running_server
    session_id = _upload_fixture_session(base_url)
    _wait_until_done(base_url, session_id)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page()
            page.goto(f"{base_url}/ui/sessions/{session_id}", timeout=15000)

            # Report page shows the expected 3 flags, correctly colored.
            # The stub LLM client deterministically fails every step's
            # round-trip check, so every step genuinely falls back to its
            # template -- "Template-fallback steps" is red, not vacuously
            # green. "Verify claims" is yellow since F-02 reports the stub
            # narrative's uncovered claims (see the module docstring).
            sections = page.query_selector_all("section[data-status]")
            assert len(sections) == 3
            status_by_title = {}
            for section in sections:
                title = section.query_selector("h2").inner_text()
                status_by_title[title] = section.get_attribute("data-status")
            assert status_by_title["Template-fallback steps"] == "red"
            assert status_by_title["Verify claims"] == "yellow"
            assert status_by_title["Empty-metadata steps"] == "yellow"

            # docx downloads through the real browser.
            with page.expect_download() as download_info:
                page.click('a[data-download="docx"]')
            download = download_info.value
            download_path = download.path()
            assert Path(download_path).exists()
            with zipfile.ZipFile(download_path) as zf:
                assert "word/document.xml" in zf.namelist()
        finally:
            browser.close()


def test_ui_smoke_library_page_lists_the_session(running_server):
    base_url = running_server
    session_id = _upload_fixture_session(base_url)
    _wait_until_done(base_url, session_id)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page()
            page.goto(f"{base_url}/ui", timeout=15000)
            link = page.query_selector(f'a[href="/ui/sessions/{session_id}"]')
            assert link is not None
        finally:
            browser.close()


def _png_bytes(color=(255, 255, 255)):
    buf = BytesIO()
    Image.new("RGB", (1280, 720), color).save(buf, format="PNG")
    return buf.getvalue()


def _finish_from_steps_review(page, base_url):
    """From the steps-review page a drop-zone upload lands on: keep every
    step, generate, and wait for the finished review page."""
    assert "Review captured steps" in page.content()
    page.click("text=Keep selected steps")
    page.wait_for_selector('a[data-download="docx"]', timeout=20000)
    session_id = page.url.rstrip("/").rsplit("/", 1)[-1]
    with httpx.Client(timeout=10) as client:
        docx = client.get(f"{base_url}/sessions/{session_id}/doc.docx")
    assert docx.status_code == 200
    assert docx.content[:2] == b"PK"
    return session_id


def _assert_local_only(requests, base_url):
    external = [
        u for u in requests if not (u.startswith(base_url) or u.startswith(("data:", "blob:")))
    ]
    assert not external, f"page made non-local requests: {external}"


def test_ui_drop_zone_capture_path_reaches_a_built_document(running_server, tmp_path):
    """manifest.json + PNGs through the one drop zone -> /ui/upload ->
    steps review -> generated document (AC8, capture path)."""
    base_url = running_server
    manifest_path = FIXTURES / "review-report-manifest.json"
    manifest = load_manifest(manifest_path)
    files = [
        {
            "name": "manifest.json",
            "mimeType": "application/json",
            "buffer": manifest_path.read_bytes(),
        }
    ]
    files += [
        {"name": s.screenshot, "mimeType": "image/png", "buffer": _png_bytes()}
        for s in manifest.steps
    ]

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page()
            requests = []
            page.on("request", lambda r: requests.append(r.url))
            page.goto(f"{base_url}/ui", timeout=15000)
            page.set_input_files("#drop-files", files)
            assert page.inner_text("#new [data-kind]").startswith("Capture session")
            assert page.is_hidden("#new [data-title]")
            with page.expect_navigation(timeout=15000):
                page.click("#new [data-go]")
            assert "/ui/sessions/" in page.url
            session_id = _finish_from_steps_review(page, base_url)
            page.goto(f"{base_url}/ui", timeout=15000)
            assert page.query_selector(f'a[href="/ui/sessions/{session_id}"]') is not None
            _assert_local_only(requests, base_url)
        finally:
            browser.close()


def test_ui_drop_zone_screenshot_path_reaches_a_built_document(running_server):
    """Images + a transcript, dropped (a real drop event carrying files),
    with no manifest -> /ui/build -> steps review -> generated document
    (AC8, screenshots + transcript path)."""
    base_url = running_server

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page()
            requests = []
            page.on("request", lambda r: requests.append(r.url))
            page.goto(f"{base_url}/ui", timeout=15000)
            # Build a DataTransfer in the page (two PNGs, deliberately named
            # out of natural order, plus a transcript) and drop it on the zone.
            png = list(_png_bytes())
            page.evaluate(
                """([png]) => {
                  const dt = new DataTransfer();
                  const bytes = new Uint8Array(png);
                  dt.items.add(new File([bytes], 'shot-10.png', {type: 'image/png'}));
                  dt.items.add(new File([bytes], 'shot-2.png', {type: 'image/png'}));
                  dt.items.add(new File(['1. Open the app.\\n2. Click Save.'], 'notes.txt',
                                        {type: 'text/plain'}));
                  const zone = document.getElementById('new');
                  zone.dispatchEvent(new DragEvent('dragover', {dataTransfer: dt, bubbles: true}));
                  zone.dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true}));
                }""",
                [png],
            )
            page.wait_for_selector("#new .plan:not([hidden])")
            assert page.inner_text("#new [data-kind]").startswith("Screenshots build")
            chips = page.inner_text("#new [data-files]")
            assert "2 screenshots" in chips and "notes.txt" in chips
            page.fill("#drop-title", "Drop zone photo build")
            with page.expect_navigation(timeout=15000):
                page.click("#new [data-go]")
            session_id = _finish_from_steps_review(page, base_url)
            with httpx.Client(timeout=10) as client:
                entries = client.get(f"{base_url}/library").json()
            entry = next(e for e in entries if e["session_id"] == session_id)
            assert entry["title"] == "Drop zone photo build"
            _assert_local_only(requests, base_url)
        finally:
            browser.close()


def test_ui_pages_load_only_local_resources_and_plex_fonts(running_server):
    """Every rebuilt page makes no external network request, and the
    vendored IBM Plex faces actually load from this server (AC8)."""
    base_url = running_server
    session_id = _upload_fixture_session(base_url)
    _wait_until_done(base_url, session_id)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page()
            requests, font_responses = [], []
            page.on("request", lambda r: requests.append(r.url))
            page.on(
                "response",
                lambda r: font_responses.append(r.status) if "/ui/assets/fonts/" in r.url else None,
            )
            for path in ("/ui", "/ui?view=review", f"/ui/sessions/{session_id}", "/ui/config"):
                page.goto(f"{base_url}{path}", timeout=15000)
                page.evaluate("document.fonts.ready")
                assert page.evaluate("document.fonts.check('600 16px \"IBM Plex Sans\"')"), path
                assert page.evaluate("document.fonts.check('16px \"IBM Plex Mono\"')"), path
                family = page.evaluate("getComputedStyle(document.body).fontFamily")
                assert family.startswith('"IBM Plex Sans"'), family
            assert font_responses and all(s == 200 for s in font_responses)
            _assert_local_only(requests, base_url)
        finally:
            browser.close()
