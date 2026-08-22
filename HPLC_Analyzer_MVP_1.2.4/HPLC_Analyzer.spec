# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("matplotlib") + [
    ("assets/app_icon.png", "assets"),
]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "matplotlib.backends.backend_qtagg",
        "matplotlib.backends.backend_qt5agg",
        "matplotlib.backends.backend_agg",
        "matplotlib.backends.backend_svg",
        "matplotlib.backends.backend_pdf",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="HPLC_Analyzer",
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
    icon="assets/app_icon.ico",
)

if os.environ.get("HPLC_ANALYZER_BUILD_DEBUG", "") == "1":
    debug_exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="HPLC_Analyzer_Debug",
        debug=True,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon="assets/app_icon.ico",
    )
