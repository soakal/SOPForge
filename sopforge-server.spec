# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for sopforge-server.exe (the FastAPI pipeline server,
src/pipeline/__main__.py). No console window.

One-folder (COLLECT), matching sopforge.spec's established pattern and
reasoning (phases/DEVIATIONS.md's "Criterion 4 packaging mode" entry) —
steady-state repeat launches are what matter for a server a user starts
once per session/boot, not the one-time AV-scan cost every unique binary
pays on its very first launch after a build.

Bundles config/models.toml (read at runtime via
pipeline.resource_path.resource_path(), task-08) and the SOP Factory 2
docx-assembly engine's two actual files — sop_lib.py and
SOP_TEMPLATE_WITH_PHOTOS.docx, the only two files docx_assembler.py reads
— into a "sop_factory_2" folder inside the bundle (task-08's frozen
sop_factory_2_dir() expects exactly this name). This is NOT the whole
external SOP_Factory_2 working project (which has active jobs, per-client
archives with real photos/documents, and its own git history) — only the
clean, reusable engine files, matching the "extend it, do not rewrite it,
never vendor it into this repo" rule that already governs the source-level
import in dev mode."""

import os

_SOP_FACTORY_2_TEMPLATE_DIR = os.path.expandvars(
    r"%USERPROFILE%\Documents\SOP_Factory_2\template"
)

a = Analysis(
    ["src/pipeline/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[
        ("config/models.toml", "config"),
        # pipeline.manifest's JSON Schema (ground truth per CLAUDE.md) is
        # read at import time via resource_path() — must be bundled or
        # the frozen EXE fails on startup (caught by actually running the
        # first build attempt, not assumed).
        ("fixtures/manifest.schema.json", "fixtures"),
        (
            os.path.join(_SOP_FACTORY_2_TEMPLATE_DIR, "sop_lib.py"),
            "sop_factory_2",
        ),
        (
            os.path.join(_SOP_FACTORY_2_TEMPLATE_DIR, "SOP_TEMPLATE_WITH_PHOTOS.docx"),
            "sop_factory_2",
        ),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "pandas",
        "scipy",
        "IPython",
        "jupyter",
        "pytest",
        # NOT excluding "unittest" here (unlike sopforge.spec) — fpdf2's
        # fpdf/sign.py imports it at module level; excluding it makes the
        # frozen EXE fail on startup with ModuleNotFoundError. Confirmed
        # by actually running the built EXE, not assumed.
        "pywinauto",
        "pywinauto.unittests",
        "numpy",
        "playwright",
        # Not on server.py's import graph today (no narration/LLM wiring
        # into the live app yet — see phases/DEVIATIONS.md's task-09
        # entry) but present in the pipeline package/venv; excluding them
        # keeps the server EXE from pulling in faster-whisper's heavy
        # transitive deps (ctranslate2, av, onnxruntime) for code paths
        # this EXE never reaches. Revisit if/when narration gets wired in.
        "faster_whisper",
        "ctranslate2",
        "av",
        "onnxruntime",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="sopforge-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# Same UPX exclusion list as sopforge.spec (Phase 1), for the same reason:
# a small, measured steady-state-launch improvement on the hot-import-path
# files, at negligible size cost. See phases/DEVIATIONS.md's "UPX
# compression" entry for the original measured comparison this list is
# based on (capture agent, but the same DLLs are on this server's hot path
# too — pywintypes/pythoncom/win32api/win32gui are pulled in by pywin32,
# a transitive dependency of python-docx's Windows-specific bits and the
# shared pipeline package).
_UPX_EXCLUDE = [
    "python312.dll",
    "pywintypes312.dll",
    "pythoncom312.dll",
    "win32api.pyd",
    "win32gui.pyd",
]

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=_UPX_EXCLUDE,
    name="sopforge-server",
)
