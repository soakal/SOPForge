# SOP Factory 2 engine (vendored)

`sop_lib.py` and `SOP_TEMPLATE_WITH_PHOTOS.docx` are the reusable docx-assembly
engine from the private repo `soakal/SOP-Factory`, vendored here by explicit
owner request so SOPForge builds and runs with zero external clone
dependency. This is **only** the clean engine — not the SOP-Factory working
project it was extracted from (which has active jobs, per-client archives
with real photos/documents, and its own git history).

`src/pipeline/docx_assembler.py` imports `sop_lib.SOPBuilder` from this
directory via `sys.path` (`sop_factory_2_dir()`), not a normal package
import — see that module's docstring. Do not edit `sop_lib.py` here; treat
it as an external dependency snapshot (CLAUDE.md: "extend it, do not rewrite
it") and update it by re-copying from the source repo, not by hand-patching.

`SOPFORGE_SOP_FACTORY_2_DIR` still overrides this location at runtime, for
anyone testing against a newer/different copy of the engine.

Two things noted by a security/cruft audit of `sop_lib.py`, not acted on here
per the "don't hand-patch" rule above (fix upstream and re-vendor if ever
addressed): `load_secret()` is unused by SOPForge (only `SOPBuilder` is
imported); and `save()` has a `print(f"WARN: ...")` that's redundant for this
integration since `assemble_docx()` already returns the same warnings into
the sidecar report, and the frozen windowed EXE's stdout is `os.devnull`
anyway.
