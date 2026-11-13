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

from pipeline import authoring as auth
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


# ---- Phase 7: branding, authoring, paths, params, browser frame ----------


def _authoring(m):
    ids = m.step_ids()
    return auth.validate(
        {
            "paths": ["Local", "Remote"],
            "params": [{"name": "ip", "label": "Server IP", "default": "10.0.0.5"}],
            "steps": {
                ids[0]: {
                    "bullets": ["Pick **Advanced** <b>now</b>", "then `ipconfig`"],
                    "commands": ["ping {{ip}}"],
                    "warning": "Do **not** reboot",
                    "table": {"headers": ["Scenario", "Value"], "rows": [["A", "<x>"]]},
                },
                ids[1]: {"path": "Remote"},
            },
            "blocks": [
                {"after": "", "heading": "Open PowerShell", "commands": ["Get-Host {{ip}}"]},
                {
                    "after": ids[1],
                    "section": "Verify",
                    "heading": "Check it",
                    "text": "Line one\n\nPara **two**",
                    "placeholder": "screenshot goes here",
                    "path": "Local",
                },
            ],
        },
        ids,
    )


def test_blocks_are_extra_entries_steps_unchanged(tmp_path):
    m, results, paths, shots = _rendered(tmp_path)
    steps = walkthrough_steps(m, results, paths, screenshot_dir=shots, authoring=_authoring(m))
    assert [s["id"] for s in steps if s["id"]] == m.step_ids()
    assert [s["title"] for s in steps if s["stage"]] == ["Open PowerShell", "Check it"]
    assert steps[1]["stage"] and steps[1]["id"] is None  # "" block right after the intro
    assert steps[4]["title"] == "Check it" and steps[3]["id"] == m.step_ids()[1]
    assert "PS C:\&gt;" in steps[1]["stage"] and "{{ip}}" in steps[1]["stage"]
    assert 'class="ph"' in steps[4]["stage"] and "<strong>two</strong>" in steps[4]["stage"]
    # a block's section relabels the phase for what follows it
    assert steps[4]["phase"] == "Verify" and steps[5]["phase"] == "Verify"


def test_step_extras_rendered_and_escaped(tmp_path):
    m, results, paths, shots = _rendered(tmp_path)
    steps = walkthrough_steps(m, results, paths, screenshot_dir=shots, authoring=_authoring(m))
    a = steps[2]["atext"]
    assert "<strong>Advanced</strong> &lt;b&gt;now&lt;/b&gt;" in a
    assert "<code>ipconfig</code>" in a
    assert '<div class="cmd"><code>ping {{ip}}</code><button type="button" data-copy>' in a
    assert '<div class="warn">Do <strong>not</strong> reboot</div>' in a
    assert "<th>Scenario</th>" in a and "<td>&lt;x&gt;</td>" in a


def test_path_filtering_data(tmp_path):
    m, results, paths, shots = _rendered(tmp_path)
    steps = walkthrough_steps(m, results, paths, screenshot_dir=shots, authoring=_authoring(m))
    by_title = {s["title"]: s for s in steps}
    assert steps[3]["path"] == "Remote" and steps[3]["tag"] == "Remote only"
    assert by_title["Check it"]["path"] == "Local"
    assert steps[2]["path"] == "" and by_title["Open PowerShell"]["path"] == ""
    intro = steps[0]["text"]
    assert 'data-path="Local"' in intro and 'data-path="Remote"' in intro
    assert 'data-param="ip" value="10.0.0.5"' in intro


def test_no_authoring_keeps_plain_entries(tmp_path):
    m, results, paths, shots = _rendered(tmp_path)
    plain = walkthrough_steps(m, results, paths, screenshot_dir=shots)
    same = walkthrough_steps(m, results, paths, screenshot_dir=shots, authoring=auth.EMPTY)
    assert plain == same and len(plain) == len(m.steps) + 2
    assert not any(s["atext"] or s["stage"] or s["path"] for s in plain)


def test_browser_frame_for_browser_steps(tmp_path):
    m, results, paths, shots = _rendered(tmp_path)
    steps = walkthrough_steps(m, results, paths, screenshot_dir=shots)
    by_id = {s["id"]: s for s in steps if s["id"]}
    for step in m.steps:
        expected = step.window.title if step.window.process == "chrome.exe" else None
        assert by_id[step.id]["browser"] == expected
    assert any(s["browser"] for s in by_id.values())


def test_header_logo_and_subtitle(tmp_path, monkeypatch):
    m, results, paths, shots = _rendered(tmp_path)
    page = render_walkthrough(
        m,
        results,
        paths,
        screenshot_dir=shots,
        doc_no="SOP-IT-1",
        revision="2.0",
        version_stamp="SOPForge v1.11.1",
    )
    assert '<div class="brand"><img src="data:image/png;base64,' in page
    assert "SOP-IT-1 · Rev 2.0 · SOPForge v1.11.1" in page
    page = render_walkthrough(m, results, paths, screenshot_dir=shots, revision="3")
    assert '<div class="sub">Rev 3</div>' in page
    monkeypatch.setenv("SOPFORGE_SOP_FACTORY_2_DIR", str(tmp_path / "missing"))
    assert 'class="brand"' not in render_walkthrough(m, results, paths, screenshot_dir=shots)


def test_authored_page_self_contained_and_parses(tmp_path):
    m, results, paths, shots = _rendered(tmp_path)
    page = render_walkthrough(m, results, paths, screenshot_dir=shots, authoring=_authoring(m))
    stripped = re.sub(r'src="data:[^"]+"', "", page)
    stripped = re.sub(r'"data:image/[^"]+"', '""', stripped)
    assert not _URL_RE.search(stripped)
    assert 'const PATHS = ["Local", "Remote"]' in page and '"name": "ip"' in page
    assert page.count("<script>") == 1 and page.count("</script>") == 1
    if shutil.which("node"):
        js = tmp_path / "walk.js"
        js.write_text(page.split("<script>", 1)[1].split("</script>", 1)[0], encoding="utf-8")
        subprocess.run(["node", "--check", str(js)], check=True)


@pytest.mark.ui
def test_authored_walkthrough_in_a_browser(tmp_path):
    from playwright.sync_api import sync_playwright

    m, results, paths, shots = _rendered(tmp_path)
    page_file = tmp_path / "walk.html"
    page_file.write_text(
        render_walkthrough(m, results, paths, screenshot_dir=shots, authoring=_authoring(m)),
        encoding="utf-8",
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, executable_path=os.environ.get("SOPFORGE_TEST_CHROMIUM") or None
        )
        page = browser.new_page()
        page.goto(page_file.as_uri())
        page.fill('[data-param="ip"]', "192.168.1.9")
        page.click('[data-path="Local"]')
        page.keyboard.press("Enter")  # plain Enter advances
        assert page.text_content("#stepno").startswith("Step 1 of 4")  # Remote step hidden
        assert "Get-Host 192.168.1.9" in page.text_content("#stage .term")
        page.keyboard.press("Enter")
        assert "ping 192.168.1.9" in page.text_content("#text .cmd code")
        page.goto(page_file.as_uri() + "?path=Remote&shot=4")  # step-003, a chrome.exe step
        assert page.evaluate("vis().length") == len(m.steps) + 3  # intro, block, 3 steps, done
        assert "Google Chrome" in page.text_content("#stage .browser .tab")
        assert page.query_selector("#stage .browser .snap > img + .hot")
        browser.close()
