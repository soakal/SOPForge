"""Phase 6h ("Forge" UI): the vendored IBM Plex fonts (served locally, also
from a frozen bundle), the library table's stat tiles and review queue, and
the "no new settings" rule (AC9) for the rebuilt configuration page. The
browser-driven checks (both drop-zone paths, no external requests, fonts
actually applied) live in test_ui_smoke.py behind the `ui` marker."""

import json
import re
import shutil
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from pipeline.manifest import load_manifest
from pipeline.server import create_app
from pipeline.webui.library_view import ai_written_count, library_stats, review_count
from pipeline.webui.pages import FONT_FILES, render_library_page

from _stub_llm import stub_llm_client_factory

REPO = Path(__file__).resolve().parent.parent.parent
FIXTURES = REPO / "fixtures"
FONT_DIR = REPO / "assets" / "fonts" / "ibm-plex"

# Every form field the configuration page posted before 6h. AC9: the rebuilt
# page may restyle these, never add to them.
CONFIG_FIELDS = {
    "steps_provider",
    "steps_model",
    "steps_endpoint",
    "steps_max_concurrency",
    "narrative_provider",
    "narrative_model",
    "narrative_endpoint",
    "narrative_passes",
    "vision_enabled",
    "vision_provider",
    "vision_model",
    "vision_endpoint",
    "vision_max_concurrency",
    "polish_enabled",
    "polish_provider",
    "polish_model",
    "polish_endpoint",
    "document_author",
    "document_doc_no_prefix",
    "transcription_enabled",
    "transcription_model_size",
    "transcription_device",
    "transcription_compute_type",
}


def _make_client(tmp_path):
    app = create_app(
        sessions_root=tmp_path / "sessions",
        llm_client_factory=stub_llm_client_factory,
        narrative_llm_client_factory=stub_llm_client_factory,
    )
    return TestClient(app)


def _create_and_wait(client, tmp_path):
    manifest_path = FIXTURES / "sample-manifest.json"
    manifest = load_manifest(manifest_path)
    files = []
    for step in manifest.steps:
        p = tmp_path / step.screenshot
        Image.new("RGB", (320, 200), (255, 255, 255)).save(p)
        files.append(("files", (step.screenshot, p.open("rb"), "image/png")))
    resp = client.post(
        "/sessions", data={"manifest_json": manifest_path.read_text(encoding="utf-8")}, files=files
    )
    session_id = resp.json()["session_id"]
    deadline = time.monotonic() + 10
    while client.get(f"/sessions/{session_id}/status").json()["status"] != "done":
        assert time.monotonic() < deadline, "session never reached done"
        time.sleep(0.05)
    return session_id, len(manifest.steps)


def test_vendored_plex_fonts_and_license_are_present():
    for name in FONT_FILES:
        assert (FONT_DIR / name).read_bytes()[:4] == b"wOF2", name
    license_text = (FONT_DIR / "LICENSE").read_text(encoding="utf-8")
    assert "SIL Open Font License" in license_text


def test_server_spec_bundles_the_plex_fonts():
    spec = (REPO / "sopforge-server.spec").read_text(encoding="utf-8")
    assert '("assets/fonts/ibm-plex/*.woff2", "assets/fonts/ibm-plex")' in spec
    assert '("assets/fonts/ibm-plex/LICENSE", "assets/fonts/ibm-plex")' in spec


def test_font_route_serves_only_the_declared_faces(tmp_path):
    client = _make_client(tmp_path)
    for name in FONT_FILES:
        resp = client.get(f"/ui/assets/fonts/{name}")
        assert resp.status_code == 200, name
        assert resp.headers["content-type"] == "font/woff2"
        assert resp.content == (FONT_DIR / name).read_bytes()
    for bad in ("LICENSE", "..%2F..%2Fconfig%2Fmodels.toml", "IBMPlexSans-Thin.woff2"):
        assert client.get(f"/ui/assets/fonts/{bad}").status_code == 404, bad


def test_font_route_reads_from_the_frozen_bundle(tmp_path, monkeypatch):
    """In the frozen EXE the fonts come from sys._MEIPASS (where the spec's
    datas put them), not the source tree."""
    client = _make_client(tmp_path)
    bundle = tmp_path / "bundle"
    shutil.copytree(FONT_DIR, bundle / "assets" / "fonts" / "ibm-plex")
    marker = bundle / "assets" / "fonts" / "ibm-plex" / "IBMPlexSans-Regular.woff2"
    marker.write_bytes(b"wOF2-from-bundle")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    resp = client.get("/ui/assets/fonts/IBMPlexSans-Regular.woff2")
    assert resp.status_code == 200
    assert resp.content == b"wOF2-from-bundle"


def test_pages_reference_no_external_resources(tmp_path):
    client = _make_client(tmp_path)
    session_id, _ = _create_and_wait(client, tmp_path)
    for path in ("/ui", "/ui?view=review", f"/ui/sessions/{session_id}", "/ui/config"):
        page = client.get(path).text
        for pattern in (r'src="(?:https?:)?//', r'href="(?:https?:)?//', r"url\((?:https?:)?//"):
            assert not re.search(pattern, page), (path, pattern)
        assert "@import" not in page, path
        assert "fonts.googleapis" not in page and "cdn" not in page.lower(), path


def test_library_shows_table_stat_tiles_and_review_queue(tmp_path):
    client = _make_client(tmp_path)
    session_id, step_count = _create_and_wait(client, tmp_path)

    page = client.get("/ui").text
    for header in ("Procedure", "Doc no.", "Steps", "Status", "Exports", "Updated"):
        assert f'<th scope="col">{header}</th>' in page
    for label in ("Procedures", "Need review", "Steps captured", "Steps AI-written"):
        assert label in page
    # The stub LLM fails every round-trip -> every step is template fallback,
    # so 0% AI-written and the session needs review.
    assert f"(0 of {step_count})" in page
    assert '<span class="count" aria-label="1 need review">1</span>' in page
    for path in ("doc.docx", "doc.pdf", "doc.single.html", "export.md.zip"):
        assert f'href="/sessions/{session_id}/{path}"' in page
    assert 'id="new"' in page  # the drop zone

    queue = client.get("/ui", params={"view": "review"}).text
    assert f"/ui/sessions/{session_id}" in queue
    assert 'aria-current="page"><svg' in queue and "Review queue" in queue


def test_ai_written_prefers_steps_json_and_falls_back_to_report(tmp_path):
    report = {"template_fallback_steps": ["step-002"], "caption_fallback_steps": []}
    assert ai_written_count(tmp_path, report, 3) == 2
    (tmp_path / "steps.json").write_text(
        json.dumps(
            {
                "version": 2,
                "steps": [
                    {"step_id": "step-001", "used_fallback": False},
                    {"step_id": "step-002", "used_fallback": True},
                    {"step_id": "step-003", "used_fallback": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert ai_written_count(tmp_path, report, 3) == 1


def test_review_count_counts_each_flagged_step_once_plus_claims():
    report = {
        "template_fallback_steps": ["step-001", "step-002"],
        "empty_metadata_steps": ["step-002"],
        "verify_claims": [{"claim_id": "c1"}],
    }
    assert review_count(report) == 3
    assert review_count({}) == 0


def test_library_stats_ratio_covers_finished_documents_only():
    rows = [
        {"steps": 10, "ai_steps": 9, "needs_review": True},
        {"steps": 4, "ai_steps": 4, "needs_review": False},
        {"steps": 7, "ai_steps": None, "needs_review": True},  # still generating
    ]
    assert library_stats(rows) == {
        "procedures": 2,
        "need_review": 2,
        "steps": 14,
        "ai_steps": 13,
    }
    page = render_library_page([], rows=[], stats=library_stats(rows))
    assert "93%" in page and "(13 of 14)" in page


def test_config_page_posts_exactly_the_existing_fields(tmp_path):
    client = _make_client(tmp_path)
    page = client.get("/ui/config").text
    form = page.split('<form method="post" action="/ui/config"', 1)[1].split("</form>", 1)[0]
    assert set(re.findall(r'name="([^"]+)"', form)) == CONFIG_FIELDS
