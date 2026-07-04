# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for sopforge.exe (the capture-agent tray app entry point,
src/capture/__main__.py). Onefile, no console window (console=False)."""

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
    a.binaries,
    a.datas,
    [],
    name="sopforge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
