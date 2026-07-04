# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for sopforge.exe (the capture-agent tray app entry point,
src/capture/__main__.py). No console window (console=False).

One-folder (COLLECT), not one-file: measured onefile cold start at 2.2-2.6s,
consistently over acceptance criterion 4's <2s threshold, purely from
onefile's per-launch self-extraction to a temp dir (dev-mode unfrozen
start-to-tray-ready was ~0.8s — the gap is packaging overhead, not app code).
One-folder was measured at ~1.7s cold start with identical code. task-13's
original plan said "onefile"; task-14 (this build's own acceptance test)
is what actually requires <2s, so the packaging mode was revised to meet it
rather than the reverse — phases/01-capture.md's deliverable text only
requires "PyInstaller spec producing sopforge.exe", not onefile specifically.
Users still get one EXE to double-click, in dist/sopforge/sopforge.exe,
alongside its (uncompressed, therefore instant-to-load) support files."""

a = Analysis(
    ["src/capture/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[("config/redaction.toml", "config")],
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
        "unittest",
        "pywinauto.unittests",
        # Not imported at runtime by anything the app actually uses (verified:
        # PIL/pywinauto/pystray/winsdk/comtypes/mss/pynput don't put it in
        # sys.modules) — PyInstaller's static analysis only picks it up via
        # some library's conditional/optional import paths.
        "numpy",
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
    name="sopforge",
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

# UPX-compressing everything (measured) shrinks the footprint but pushes
# cold start to ~3.0s — worse than uncompressed one-folder (~1.7s), because
# decompression cost lands on exactly the DLLs loaded during startup. These
# are excluded from compression (kept fast to load); everything else stays
# UPX-compressed to hit the <40MB budget. Cold-path-only DLLs (winsdk's
# WinRT bindings, pywinauto's win32ui, OpenSSL) are NOT in this list and do
# get compressed, since they only load lazily well after the tray is ready.
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
    name="sopforge",
)
