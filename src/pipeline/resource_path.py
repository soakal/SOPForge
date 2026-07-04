"""Frozen-bundle resource resolution (Phase 3, AC3/AC5 prerequisite): a
single sys._MEIPASS-aware helper for locating resources bundled inside the
repo (config/models.toml today; any future webui static assets — none
exist yet, src/pipeline/webui/ is pure server-rendered Python strings).
Dev/test mode resolves relative to the repo root; a frozen PyInstaller
build resolves relative to its own extraction directory. Every in-repo
resource path must go through this rather than __file__ math, so a single
fix here covers every call site instead of each one needing its own
frozen-aware logic.

This does NOT cover the SOP Factory 2 engine (external to this repo,
never vendored — see docx_assembler.py), which has its own frozen/dev
resolution for that reason."""

import sys
from pathlib import Path

# src/pipeline/resource_path.py -> src/pipeline -> src -> repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def resource_path(*parts):
    """Returns an absolute path to a resource inside this repo (or its
    frozen bundle), joining `parts` onto the resolved base directory."""
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)
    else:
        base = _REPO_ROOT
    return base.joinpath(*parts)
