# -*- mode: python ; coding: utf-8 -*-
import importlib.util
import os

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("matplotlib") + [
    ("assets/app_icon.png", "assets"),
]
version_file = os.environ.get("HPLC_VERSION_FILE", "")
if not version_file or not os.path.isfile(version_file):
    raise RuntimeError("HPLC_VERSION_FILE must name a generated version-resource file")
build_debug = os.environ.get("HPLC_ANALYZER_BUILD_DEBUG", "") == "1"
debug_version_file = os.environ.get("HPLC_DEBUG_VERSION_FILE", "")
if build_debug and (
    not debug_version_file or not os.path.isfile(debug_version_file)
):
    raise RuntimeError(
        "HPLC_DEBUG_VERSION_FILE must name the generated Debug version resource"
    )

hiddenimports = [
    "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.backend_qt5agg",
    "matplotlib.backends.backend_agg",
    "matplotlib.backends.backend_svg",
    "matplotlib.backends.backend_pdf",
]
# The screen preview loads PyQtGraph through importlib, so PyInstaller cannot see
# it.  Bundle it only when the optional Windows 11 pin is installed; the shared
# Windows 7 build never installs it and stays unchanged.
if importlib.util.find_spec("pyqtgraph") is not None:
    hiddenimports += [
        "pyqtgraph",
        "pyqtgraph.Qt.QtCore",
        "pyqtgraph.Qt.QtGui",
        "pyqtgraph.Qt.QtWidgets",
    ]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
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
    version=version_file,
)

if build_debug:
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
        version=debug_version_file,
    )
