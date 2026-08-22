"""Run Windows 7 build imports one-by-one in isolated child processes.

A native extension can terminate the interpreter before Python can raise an
exception.  Keeping every probe in its own process identifies the exact import
that failed and prevents PyInstaller from running after a native crash.
"""

from __future__ import print_function

import importlib.metadata
import os
import struct
import subprocess
import sys
from pathlib import Path


EXPECTED_PYTHON = (3, 8, 10)
EXPECTED_BITS = 32
EVENT_LOG_COMMAND = (
    'wevtutil qe Application /q:"*[System[(EventID=1000)]]" '
    "/c:5 /rd:true /f:text"
)


PROBES = (
    (
        "NumPy",
        "numpy",
        "1.20.3",
        "import numpy; assert numpy.__version__ == '1.20.3'; "
        "print('NumPy OK', numpy.__version__)",
    ),
    (
        "Pillow",
        "Pillow",
        "9.5.0",
        "import PIL; assert PIL.__version__ == '9.5.0'; "
        "print('Pillow OK', PIL.__version__)",
    ),
    (
        "shiboken2",
        "shiboken2",
        "5.15.2.1",
        "import shiboken2; print('shiboken2 OK', "
        "getattr(shiboken2, '__version__', '5.15.2.1'))",
    ),
    (
        "PySide2",
        "PySide2",
        "5.15.2.1",
        "import PySide2; assert PySide2.__version__ == '5.15.2.1'; "
        "print('PySide2 OK', PySide2.__version__)",
    ),
    (
        "QtCore",
        "PySide2",
        "5.15.2.1",
        "from PySide2 import QtCore; assert QtCore.qVersion() == '5.15.2'; "
        "print('QtCore OK', QtCore.qVersion())",
    ),
    (
        "QtGui",
        "PySide2",
        "5.15.2.1",
        "from PySide2 import QtGui; print('QtGui OK', QtGui.__name__)",
    ),
    (
        "QtWidgets",
        "PySide2",
        "5.15.2.1",
        "from PySide2 import QtWidgets; print('QtWidgets OK', QtWidgets.__name__)",
    ),
    (
        "Matplotlib",
        "matplotlib",
        "3.7.5",
        "import matplotlib; assert matplotlib.__version__ == '3.7.5'; "
        "print('Matplotlib OK', matplotlib.__version__)",
    ),
    (
        "HPLC GUI import",
        None,
        None,
        "import hplc_app.gui; print('HPLC GUI import OK')",
    ),
    (
        "HPLC Analyzer GUI smoke test",
        None,
        None,
        None,
    ),
    (
        "PyInstaller",
        "PyInstaller",
        "5.13.2",
        "import PyInstaller; assert PyInstaller.__version__ == '5.13.2'; "
        "print('PyInstaller OK', PyInstaller.__version__)",
    ),
)


def interpreter_description():
    version = ".".join(str(part) for part in sys.version_info[:3])
    return "Python {0} ({1}-bit)".format(version, struct.calcsize("P") * 8)


def installed_version(distribution):
    if not distribution:
        return "application source"
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def print_failure(name, code, distribution, expected):
    unsigned_code = code & 0xFFFFFFFF
    print("[ERROR] Import preflight stopped.")
    print("[ERROR] Test name       : {0}".format(name))
    print("[ERROR] Exit code       : {0} (0x{1:08X})".format(code, unsigned_code))
    print("[ERROR] Interpreter     : {0}".format(interpreter_description()))
    print("[ERROR] Package/version : {0} / {1}".format(
        distribution or "HPLC Analyzer", installed_version(distribution)
    ))
    if expected:
        print("[ERROR] Expected version: {0}".format(expected))
    print("[INFO] If this was a native crash, inspect recent Event ID 1000 entries:")
    print("       {0}".format(EVENT_LOG_COMMAND))


def run_preflight(root=None):
    root = Path(root or Path(__file__).resolve().parents[1]).resolve()
    bits = struct.calcsize("P") * 8
    version = tuple(sys.version_info[:3])
    print("[INFO] Windows 7 isolated-import preflight")
    print("[INFO] {0}".format(interpreter_description()))
    if version != EXPECTED_PYTHON or bits != EXPECTED_BITS:
        print("[ERROR] Python 3.8.10 32-bit is required before import tests.")
        return 1

    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["PYTHONNOUSERSITE"] = "1"
    for name, distribution, expected, source in PROBES:
        actual = installed_version(distribution)
        if expected and actual != expected:
            print_failure(name, 1, distribution, expected)
            return 1
        if source is None:
            command = [sys.executable, str(root / "app.py"), "--startup-smoke-test"]
        else:
            command = [sys.executable, "-c", source]
        print("[TEST] {0} (installed: {1})".format(
            name, actual
        ))
        code = subprocess.call(command, cwd=str(root), env=environment)
        if code != 0:
            print_failure(name, code, distribution, expected)
            return code if 0 < code < 256 else 1
    print("[OK] Every Windows 7 dependency and the HPLC GUI passed in isolation.")
    return 0


if __name__ == "__main__":
    sys.exit(run_preflight())
