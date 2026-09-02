"""Small PySide6/PySide2 compatibility layer.

PySide2 is used for the Windows 7 build. PySide6 is the preferred Windows 11
build. Keeping imports here allows the application code to remain identical.
"""

try:
    from PySide6 import QtCore, QtGui, QtPrintSupport, QtWidgets

    QT_API = 6
    QAction = QtGui.QAction
    QActionGroup = QtGui.QActionGroup
    CHECKED = QtCore.Qt.CheckState.Checked
    UNCHECKED = QtCore.Qt.CheckState.Unchecked
    USER_ROLE = QtCore.Qt.ItemDataRole.UserRole
    ITEM_IS_EDITABLE = QtCore.Qt.ItemFlag.ItemIsEditable
    HORIZONTAL = QtCore.Qt.Orientation.Horizontal
    WINDOW_MODAL = QtCore.Qt.WindowModality.WindowModal
    SHIFT_MODIFIER = QtCore.Qt.KeyboardModifier.ShiftModifier
    KEEP_ASPECT_RATIO = QtCore.Qt.AspectRatioMode.KeepAspectRatio
    STANDARD_SAVE_SHORTCUT = QtGui.QKeySequence.StandardKey.Save
except ImportError:
    from PySide2 import QtCore, QtGui, QtPrintSupport, QtWidgets

    QT_API = 5
    QAction = QtWidgets.QAction
    QActionGroup = QtWidgets.QActionGroup
    CHECKED = QtCore.Qt.Checked
    UNCHECKED = QtCore.Qt.Unchecked
    USER_ROLE = QtCore.Qt.UserRole
    ITEM_IS_EDITABLE = QtCore.Qt.ItemIsEditable
    HORIZONTAL = QtCore.Qt.Horizontal
    WINDOW_MODAL = QtCore.Qt.WindowModal
    SHIFT_MODIFIER = QtCore.Qt.ShiftModifier
    KEEP_ASPECT_RATIO = QtCore.Qt.KeepAspectRatio
    STANDARD_SAVE_SHORTCUT = QtGui.QKeySequence.Save


def dialog_exec(dialog):
    return dialog.exec() if QT_API == 6 else dialog.exec_()


def qt_enum(owner, group, member):
    """Return one scoped Qt 6 enum or its flat Qt 5 equivalent."""

    return getattr(getattr(owner, group, owner), member)


EVENT_LEAVE = qt_enum(QtCore.QEvent, "Type", "Leave")
EVENT_FOCUS_OUT = qt_enum(QtCore.QEvent, "Type", "FocusOut")
EVENT_RESIZE = qt_enum(QtCore.QEvent, "Type", "Resize")
EVENT_KEY_PRESS = qt_enum(QtCore.QEvent, "Type", "KeyPress")
KEY_ESCAPE = qt_enum(QtCore.Qt, "Key", "Key_Escape")
KEY_DELETE = qt_enum(QtCore.Qt, "Key", "Key_Delete")
MOUSE_FOCUS_REASON = qt_enum(QtCore.Qt, "FocusReason", "MouseFocusReason")
