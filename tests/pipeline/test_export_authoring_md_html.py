"""Phase 7 authored content in the md / html / single-file html / md bundle
exports: prerequisites, step extras, blocks, revision history, params --
and no authoring (None or EMPTY) leaves every output unchanged."""

from pathlib import Path

from PIL import Image

from pipeline import authoring as au
from pipeline.export_html import render_single_file_html
from pipeline.export_md import export_markdown_bundle
from pipeline.manifest import load_manifest
from pipeline.render import render_html, render_markdown, render_steps_template_mode

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"
REV = {"revision": "2.0", "date": "11/13/2026", "author": "QA"}


def _setup(tmp_path):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    shots = tmp_path / "screenshots"
    shots.mkdir()
    for step in manifest.steps:
        Image.new("RGB", (64, 48), (255, 255, 255)).save(shots / step.screenshot)
    results, paths = render_steps_template_mode(manifest, shots, tmp_path / "annotated")
    return manifest, results, paths


def _authoring(manifest):
    first = manifest.steps[0].id
    return au.validate(
        {
            "prerequisites": ["Admin rights on {{host}}"],
            "params": [{"name": "host", "label": "Host", "default": "srv01"}],
            "paths": ["Win"],
            "revisions": [{"date": "01/01/2026", "revision": "1.0", "comment": "First"}],
            "steps": {
                first: {
                    "bullets": ["Pick **Advanced**", "<script>alert(1)</script>"],
                    "commands": ["ping {{host}}"],
                    "warning": "Do not unplug",
                    "table": {"headers": ["Scenario", "Value"], "rows": [["a|b", "c"]]},
                    "path": "Win",
                }
            },
            "blocks": [
                {"after": "", "heading": "Intro block", "text": "Para one\n\nPara two"},
                {"after": first, "section": "Cleanup", "placeholder": "router photo"},
            ],
        },
        [s.id for s in manifest.steps],
    )


def _outputs(tmp_path, manifest, results, paths, authoring, **kw):
    bundle = export_markdown_bundle(
        manifest, results, paths, tmp_path / "b", authoring=authoring, **kw
    ).read_text(encoding="utf-8")
    return {
        "md": render_markdown(manifest, results, paths, authoring=authoring, **kw),
        "html": render_html(manifest, results, paths, authoring=authoring, **kw),
        "single": render_single_file_html(manifest, results, paths, authoring=authoring, **kw),
        "bundle": bundle,
    }


def test_no_authoring_output_unchanged(tmp_path):
    manifest, results, paths = _setup(tmp_path)
    md = render_markdown(manifest, results, paths)
    html_doc = render_html(manifest, results, paths)
    single = render_single_file_html(manifest, results, paths)
    for a in (None, au.EMPTY):
        assert render_markdown(manifest, results, paths, authoring=a, **REV) == md
        assert render_html(manifest, results, paths, authoring=a, **REV) == html_doc
        assert render_single_file_html(manifest, results, paths, authoring=a, **REV) == single


def test_authoring_appears_in_every_output(tmp_path):
    manifest, results, paths = _setup(tmp_path)
    docs = _outputs(tmp_path, manifest, results, paths, _authoring(manifest), **REV)
    for name, doc in docs.items():
        for text in (
            "Before You Begin",
            "Admin rights on srv01",
            "ping srv01",
            "Do not unplug",
            "Intro block",
            "Para two",
            "Cleanup",
            "[IMAGE PLACEHOLDER: router photo]",
            "Win only",
            "Revision History",
            "Initial generation",
        ):
            assert text in doc, (name, text)
        assert doc.index("Intro block") < doc.index("Cleanup")
    md = docs["md"]
    assert "```\nping srv01\n```" in md
    assert "**Warning:** Do not unplug" in md
    assert r"| a\|b | c |" in md
    assert "***— Win only***" in md
    assert "<pre><code>ping srv01</code></pre>" in docs["html"]
    assert "<strong>Advanced</strong>" in docs["single"]
    assert '<p class="warning"><strong>Warning:</strong> Do not unplug</p>' in docs["html"]
    assert '<em class="only">— Win only</em>' in docs["single"]


def test_revision_table_needs_revision_kwargs(tmp_path):
    manifest, results, paths = _setup(tmp_path)
    docs = _outputs(tmp_path, manifest, results, paths, _authoring(manifest))
    assert all("Revision History" not in d for d in docs.values())


def test_html_escapes_authored_script(tmp_path):
    manifest, results, paths = _setup(tmp_path)
    docs = _outputs(tmp_path, manifest, results, paths, _authoring(manifest))
    for name in ("html", "single"):
        assert "<script>" not in docs[name]
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in docs[name]
