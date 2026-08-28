from __future__ import annotations

import os
import sys
from pathlib import Path


def resource_path(relative: str) -> str:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(base / relative)


def configure_isolated_test_settings(QtCore) -> None:
    """Redirect smoke-test settings away from real user presets when requested."""

    directory = os.environ.get("HPLC_ANALYZER_TEST_SETTINGS_DIR", "").strip()
    if not directory:
        return
    Path(directory).mkdir(parents=True, exist_ok=True)
    if hasattr(QtCore.QSettings, "Format"):
        ini_format = QtCore.QSettings.Format.IniFormat
        user_scope = QtCore.QSettings.Scope.UserScope
    else:
        ini_format = QtCore.QSettings.IniFormat
        user_scope = QtCore.QSettings.UserScope
    QtCore.QSettings.setDefaultFormat(ini_format)
    QtCore.QSettings.setPath(ini_format, user_scope, directory)


def main() -> int:
    try:
        from hplc_app.qt_compat import QtCore, QtGui, QtWidgets
        from hplc_app.gui import MainWindow
    except ImportError as exc:
        print("GUI dependency is missing: %s" % exc, file=sys.stderr)
        print("Install requirements-win11.txt or requirements-win7.txt first.", file=sys.stderr)
        return 1

    if hasattr(QtCore.Qt, "AA_EnableHighDpiScaling"):
        QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    smoke_test = "--startup-smoke-test" in sys.argv
    configure_isolated_test_settings(QtCore)
    qt_arguments = [arg for arg in sys.argv if arg != "--startup-smoke-test"]
    app = QtWidgets.QApplication(qt_arguments)
    app.setApplicationName("HPLC Analyzer")
    app.setOrganizationName("Research Tools")
    icon_path = resource_path("assets/app_icon.png")
    if Path(icon_path).exists():
        app.setWindowIcon(QtGui.QIcon(icon_path))
    window = MainWindow()
    if Path(icon_path).exists():
        window.setWindowIcon(QtGui.QIcon(icon_path))
    if smoke_test:
        window.show()
        app.processEvents()
        window.close()
        app.processEvents()
        print("[OK] HPLC Analyzer GUI startup smoke test passed.")
        return 0
    window.show()
    return app.exec() if hasattr(app, "exec") else app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
