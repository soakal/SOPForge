"""Phase 6d (phases/06-factory-parity.md AC4): the click-through walkthrough
has one entry per manifest step in order, hotspots that sit on the clicked
control inside the annotated crop, no external references, and a script that
parses and actually navigates."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from pipeline.annotate import compute_crop_box
from pipeline.export_walkthrough import hotspot_box, render_walkthrough, walkthrough_steps
from pipeline.grouping import Phase
from pipeline.manifest import Manifest, load_manifest
from pipeline.photo_build import synthetic_manifest_dict
from pipeline.render import render_steps_template_mode
from pipeline.review_notes import VALUE_NOT_CAPTURED

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"
_URL_RE = re.compile(r"(?:https?:)?//", re.IGNORECASE)


def _rendered(tmp_path, manifest=None):
    manifest = manifest or load_manifest(FIXTURES / "sample-manifest.json")
    shots = tmp_path / "shots"
    shots.mkdir()
    for step in manifest.steps:
        Image.new("RGB", (1920, 1080), (255, 255, 255)).save(shots / step.screenshot)
    results, paths = render_steps_template_mode(manifest, shots, tmp_path / "annotated")
    return manifest, results, paths, shots


def test_hotspot_matches_element_rect_inside_the_crop():
    step = load_manifest(FIXTURES / "sample-manifest.json").steps[0]
    raw = (1920, 1080)
    box = hotspot_box(step, raw)
    cl, ct, cr, cb = compute_crop_box(
        raw, step.element.bounding_rect, (step.screen.x, step.screen.y)
    )
    left, top, right, bottom = step.element.bounding_rect
    assert box == [
        round((left - cl) / (cr - cl) * 100, 2),
        round((top - ct) / (cb - ct) * 100, 2),
        round((right - left) / (cr - cl) * 100, 2),
        round((bottom - top) / (cb - ct) * 100, 2),
    ]
    assert all(0 <= v <= 100 for v in box)


def test_hotspot_without_rect_centres_on_the_click():
    step = load_manifest(FIXTURES / "sample-manifest.json").steps[2]
    assert step.element.bounding_rect is None or not step.element.name
    step = step.model_copy(
        update={"element": step.element.model_copy(update={"bounding_rect": None})}
    )
    box = hotspot_box(step, (1920, 1080))
    centre_x = (box[0] + box[2] / 2) / 100 * 1920
    centre_y = (box[1] + box[3] / 2) / 100 * 1080
    assert abs(centre_x - step.screen.x) < 2 and abs(centre_y - step.screen.y) < 2


def test_no_hotspot_for_photo_steps_or_unknown_frames():
    data = synthetic_manifest_dict("T", ["a.png"], "2026-10-24T00:00:00.000Z")
    data["session"]["id"] = "s"
    assert hotspot_box(Manifest.model_validate(data).steps[0], (800, 600)) is None
    assert hotspot_box(load_manifest(FIXTURES / "sample-manifest.json").steps[0], None) is None


def test_one_entry_per_step_in_order_with_intro_and_done(tmp_path):
    m, results, paths, shots = _rendered(tmp_path)
    steps = walkthrough_steps(m, results, paths, screenshot_dir=shots)
    assert [s["id"] for s in steps[1:-1]] == m.step_ids()
    assert steps[0]["id"] is None and steps[-1]["title"] == "Done"
    assert all(s["img"].startswith("data:image/") for s in steps[1:-1])
    assert VALUE_NOT_CAPTURED in steps[2]["text"]


def test_omitted_screenshot_reuses_previous_picture(tmp_path):
    m, results, paths, shots = _rendered(tmp_path)
    steps = walkthrough_steps(
        m, results, paths, omitted_screenshots={"step-002"}, screenshot_dir=shots
    )
    assert steps[2]["img"] == steps[1]["img"] and steps[2]["box"] == steps[1]["box"]


def test_page_is_self_contained_and_script_safe(tmp_path):
    m, results, paths, shots = _rendered(tmp_path)
    m.session.title = "Reset </script><script>alert(1)</script>"
    page = render_walkthrough(m, results, paths, screenshot_dir=shots)
    stripped = re.sub(r'src="data:[^"]+"', "", page)
    stripped = re.sub(r'"data:image/[^"]+"', '""', stripped)
    assert not _URL_RE.search(stripped)
    assert page.count("<script>") == 1 and page.count("</script>") == 1


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_script_parses(tmp_path):
    m, results, paths, shots = _rendered(tmp_path)
    phases = [Phase("Sign in", ("step-001",)), Phase("Finish", ("step-002", "step-003"))]
    page = render_walkthrough(m, results, paths, phases=phases, screenshot_dir=shots)
    script = page.split("<script>", 1)[1].split("</script>", 1)[0]
    js = tmp_path / "walk.js"
    js.write_text(script, encoding="utf-8")
    subprocess.run(["node", "--check", str(js)], check=True)


@pytest.mark.ui
def test_walkthrough_navigates_in_a_browser(tmp_path):
    from playwright.sync_api import sync_playwright

    m, results, paths, shots = _rendered(tmp_path)
    phases = [Phase("Sign in", ("step-001",)), Phase("Finish", ("step-002", "step-003"))]
    page_file = tmp_path / "walk.html"
    page_file.write_text(
        render_walkthrough(m, results, paths, phases=phases, screenshot_dir=shots),
        encoding="utf-8",
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, executable_path=os.environ.get("SOPFORGE_TEST_CHROMIUM") or None
        )
        page = browser.new_page()
        page.goto(page_file.as_uri())
        assert page.text_content("#title") == m.session.title or m.session.id
        page.click("#stage [data-next]")  # intro "Start"
        assert page.text_content("#stepno").startswith("Step 1 of 3")
        page.click("#stage .hot")  # the hotspot over the clicked control
        assert page.text_content("#stepno").startswith("Step 2 of 3")
        page.click('[data-phase="Sign in"]')
        assert page.text_content("#stepno").startswith("Step 1 of 3")
        page.keyboard.press("ArrowRight")
        assert "Finish" in page.text_content("#stepno")
        page.goto(page_file.as_uri() + "?shot=3")
        assert "shot" in page.evaluate("document.body.className")
        assert json.loads(page.evaluate("JSON.stringify(steps.length)")) == len(m.steps) + 2
        browser.close()
