"""End-to-end through the built EXE, not the dev server (AC5): launches
dist/sopforge-server/sopforge-server.exe, POSTs the golden fixture session
over real HTTP, polls to done, downloads doc.docx, and byte-compares
word/document.xml against fixtures/golden-document.xml using the Phase 2
golden normalizer (task-14). Isolated behind the `exe` pytest marker (needs
a pre-built dist/sopforge-server/, task-10) — skips cleanly if the EXE
hasn't been built yet, rather than failing.

`fixtures/sample-manifest.json` is the exact manifest task-15's
`fixtures/golden-document.xml` was generated from — using it here proves
the same output comes out the other end of the real packaged EXE, not
just the dev-mode Python process every other test exercises."""

import io
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from PIL import Image

from pipeline.golden import compare_document_xml_to_golden_file
from pipeline.manifest import load_manifest

pytestmark = pytest.mark.exe

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DIST_EXE = REPO_ROOT / "dist" / "sopforge-server" / "sopforge-server.exe"
FIXTURES = REPO_ROOT / "fixtures"
GOLDEN_XML = FIXTURES / "golden-document.xml"

READY_TIMEOUT = 15.0
# Step generation is now LLM-backed (round-trip gated, per-step template
# fallback). This test runs the real packaged EXE via subprocess, so unlike
# the in-process tests it cannot inject a stub LLM client — it genuinely
# attempts the configured (unreachable, in this environment) Ollama
# endpoint once per step before falling back, each attempt bounded by
# LLMClient's ~5s connect timeout. sample-manifest.json has 3 steps, so
# budget generously beyond the ~15-20s that alone can take.
DONE_TIMEOUT = 90.0


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def running_exe(tmp_path):
    if not DIST_EXE.exists():
        pytest.skip(f"{DIST_EXE} not built yet — run scripts/build_server_exe.py first")

    port = _find_free_port()
    sessions_root = tmp_path / "sessions"
    proc = subprocess.Popen(
        [str(DIST_EXE), "--port", str(port), "--sessions-root", str(sessions_root)],
        # Explicit redirection required for this console=False build to
        # respond at all — see task-10's DEVIATIONS.md-worthy discovery in
        # scripts/build_server_exe.py's module docstring.
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + READY_TIMEOUT
    with httpx.Client(timeout=5) as client:
        while time.time() < deadline:
            try:
                if client.get(f"{base_url}/").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.1)
        else:
            proc.kill()
            proc.wait(timeout=10)
            raise RuntimeError("built EXE never responded 200 within timeout")

    try:
        yield base_url
    finally:
        try:
            httpx.post(f"{base_url}/shutdown", timeout=2.0)
        except httpx.TransportError:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_golden_fixture_through_built_exe_matches_committed_golden_docx(running_exe, tmp_path):
    base_url = running_exe
    manifest_path = FIXTURES / "sample-manifest.json"
    manifest = load_manifest(manifest_path)

    # A single status poll can occasionally stall well past what the LLM
    # client's own connect_timeout would suggest — this process's GIL is
    # shared between JobRunner's background-generation thread and uvicorn's
    # request handling, and CPU-bound work (docx/pdf assembly, not just the
    # LLM connection attempts) can delay how quickly a concurrent request
    # gets scheduled. Generous per-request timeout here, not just a longer
    # poll-loop deadline.
    with httpx.Client(timeout=60) as client:
        files = []
        buffers = []
        for step in manifest.steps:
            buf = io.BytesIO()
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
        session_id = resp.json()["session_id"]

        deadline = time.time() + DONE_TIMEOUT
        while time.time() < deadline:
            status = client.get(f"{base_url}/sessions/{session_id}/status").json()
            if status["status"] == "done":
                break
            if status["status"] == "error":
                raise AssertionError(f"session failed on the built EXE: {status.get('error')}")
            # A slower poll interval than the in-process tests use: LLM-backed
            # generation on the real EXE makes real (if bounded) network
            # attempts per step, and polling at a slower rate here reduces
            # request-rate contention with that background work.
            time.sleep(1.0)
        else:
            raise AssertionError("session never reached done on the built EXE")

        docx_resp = client.get(f"{base_url}/sessions/{session_id}/doc.docx")
        docx_resp.raise_for_status()

    docx_path = tmp_path / "out.docx"
    docx_path.write_bytes(docx_resp.content)

    match, actual, golden = compare_document_xml_to_golden_file(docx_path, GOLDEN_XML)
    assert match, (
        "document.xml generated by the built EXE no longer matches the "
        f"committed golden fixture ({len(actual)} vs {len(golden)} bytes after normalization)"
    )
