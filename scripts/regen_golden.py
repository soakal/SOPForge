"""Regenerates fixtures/golden-document.xml (AC5's committed golden
reference, see tests/pipeline/test_golden_docx.py) from the same
fixtures/sample-manifest.json build the golden test itself uses. Run this
after any intentional change to docx_assembler.py/docx_fields.py's output
and review the diff — it should only ever move by exactly what the change
was meant to do.

Requires SOPFORGE_SOP_FACTORY_2_DIR pointed at a real SOP Factory 2 engine
copy (see CLAUDE.md's "SOP_Factory_2 engine (sop_lib) is external" note).

Usage: python scripts/regen_golden.py
"""

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from PIL import Image  # noqa: E402

from pipeline.docx_assembler import assemble_docx  # noqa: E402
from pipeline.golden import normalize_document_xml, extract_document_xml  # noqa: E402
from pipeline.manifest import load_manifest  # noqa: E402
from pipeline.render import render_steps_template_mode  # noqa: E402

FIXTURES = REPO_ROOT / "fixtures"
GOLDEN_XML = FIXTURES / "golden-document.xml"


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        manifest = load_manifest(FIXTURES / "sample-manifest.json")
        screenshots = tmp_path / "screenshots"
        annotated = tmp_path / "annotated"
        screenshots.mkdir(parents=True)
        for step in manifest.steps:
            Image.new("RGB", (1920, 1080), (255, 255, 255)).save(screenshots / step.screenshot)

        step_results, _annotated_paths = render_steps_template_mode(
            manifest, screenshots, annotated
        )
        output_path = tmp_path / "out.docx"
        out, warnings = assemble_docx(manifest, step_results, annotated, output_path)
        if warnings:
            print(f"WARNING: assemble_docx reported warnings: {warnings}", file=sys.stderr)

        normalized = normalize_document_xml(extract_document_xml(out))
        GOLDEN_XML.write_bytes(normalized)
        print(f"Wrote {GOLDEN_XML} ({len(normalized)} bytes)")


if __name__ == "__main__":
    main()
