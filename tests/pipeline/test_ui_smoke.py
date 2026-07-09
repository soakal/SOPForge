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
not just asserting it stays green. "Verify claims" stays green regardless:
narration/claim-coverage still isn't wired into the live server's request
path at all (see phases/DEVIATIONS.md's "task-09 UI smoke test's expected
sidecar flags" for the full history of this scoping)."""

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
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(f"{base_url}/ui/sessions/{session_id}", timeout=15000)

            # Report page shows the expected 3 flags, correctly colored.
            # The stub LLM client deterministically fails every step's
            # round-trip check, so every step genuinely falls back to its
            # template -- "Template-fallback steps" is red, not vacuously
            # green. "Verify claims" stays green since narration still
            # isn't wired into the live server at all (see the module
            # docstring / DEVIATIONS.md).
            sections = page.query_selector_all("section[data-status]")
            assert len(sections) == 3
            status_by_title = {}
            for section in sections:
                title = section.query_selector("h2").inner_text()
                status_by_title[title] = section.get_attribute("data-status")
            assert status_by_title["Template-fallback steps"] == "red"
            assert status_by_title["Verify claims"] == "green"
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
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(f"{base_url}/ui", timeout=15000)
            link = page.query_selector(f'a[href="/ui/sessions/{session_id}"]')
            assert link is not None
        finally:
            browser.close()
