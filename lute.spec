# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build recipe for Lute.

    python build.py            (or: python -m PyInstaller lute.spec --noconfirm)

Produces one standalone windowed executable, dist/Lute.exe. Songs are not
bundled: the program reads (and creates, on first run) the songs/ folder
beside the exe.
"""

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Not used by the app; excluding them keeps the exe small.
    excludes=["numpy", "matplotlib", "PIL", "pytest", "IPython", "pandas"],
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
    name="Lute",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,              # windowed: no console window
    disable_windowed_traceback=False,   # show a dialog if it crashes
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,                  # drop a lute.ico here and set icon="lute.ico"
)
