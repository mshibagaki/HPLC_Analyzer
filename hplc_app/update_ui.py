"""Non-launching Qt presentation components for verified update downloads."""

from __future__ import annotations

from threading import Event
from typing import Callable, Dict, Optional

from .i18n import Translator
from .qt_compat import QtCore, QtWidgets
from .updater_download import stage_verified_installer


class UpdateDownloadWorker(QtCore.QObject):
    """Run staging outside the GUI thread and bridge progress/cancellation."""

    progress = QtCore.Signal(str, int, object)
    finished = QtCore.Signal(dict)

    def __init__(
        self,
        stage_arguments: Dict[str, object],
        stage: Optional[Callable] = None,
    ):
        super().__init__()
        self._stage_arguments = dict(stage_arguments)
        self._stage = stage or stage_verified_installer
        self._cancel_event = Event()

    def cancel(self):
        self._cancel_event.set()

    def run(self):
        arguments = dict(self._stage_arguments)
        arguments["progress"] = self._report_progress
        arguments["cancelled"] = self._cancel_event.is_set
        try:
            result = dict(self._stage(**arguments))
        except Exception as exc:
            result = {
                "status": "error",
                "path": "",
                "sha256": "",
                "authenticode": {
                    "status": "not_tested",
                    "thumbprint": "",
                    "reason": "",
                },
                "reason": str(exc),
                "launch_allowed": False,
            }
        # This component is intentionally presentation-only. A staging result
        # must never become an installer execution authorization by crossing it.
        result["launch_allowed"] = False
        self.finished.emit(result)

    def _report_progress(self, asset, received, total):
        self.progress.emit(str(asset), int(received), total)


class UpdateDownloadDialog(QtWidgets.QDialog):
    """Bilingual progress and terminal-state UI with no launch control."""

    cancel_requested = QtCore.Signal()

    def __init__(self, language: str = "ja", parent=None):
        super().__init__(parent)
        self.translator = Translator(language)
        self.result = None
        self.state = "running"
        self.setWindowTitle(self.translator("update_download_title"))
        root = QtWidgets.QVBoxLayout(self)
        self.phase_label = QtWidgets.QLabel(
            self.translator("update_download_preparing")
        )
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.detail_label = QtWidgets.QLabel("")
        self.detail_label.setWordWrap(True)
        self.action_button = QtWidgets.QPushButton(self.translator("cancel"))
        self.action_button.clicked.connect(self._action_clicked)
        root.addWidget(self.phase_label)
        root.addWidget(self.progress_bar)
        root.addWidget(self.detail_label)
        root.addWidget(self.action_button)
        self.setMinimumWidth(430)

    def apply_progress(self, asset: str, received: int, total):
        if self.state != "running":
            return
        key = (
            "update_download_manifest"
            if str(asset) == "manifest"
            else "update_download_installer"
        )
        self.phase_label.setText(self.translator(key))
        received_value = max(0, int(received))
        if total is None:
            self.progress_bar.setRange(0, 0)
            self.detail_label.setText(
                self.translator(
                    "update_download_bytes_unknown", received=received_value
                )
            )
            return
        total_value = max(0, int(total))
        self.progress_bar.setRange(0, max(1, total_value))
        self.progress_bar.setValue(min(received_value, total_value))
        self.detail_label.setText(
            self.translator(
                "update_download_bytes",
                received=received_value,
                total=total_value,
            )
        )

    def bind_worker(self, worker: UpdateDownloadWorker):
        """Wire signals while keeping cancellation callable from the GUI thread."""

        worker.progress.connect(self.apply_progress)
        worker.finished.connect(self.apply_result)
        # A queued slot on a busy worker thread cannot run until its blocking
        # download returns. The lambda executes in the emitting GUI context and
        # only sets a thread-safe Event, so cancellation reaches the read loop.
        self.cancel_requested.connect(lambda: worker.cancel())

    def apply_result(self, result):
        safe_result = dict(result or {})
        safe_result["launch_allowed"] = False
        self.result = safe_result
        status = str(safe_result.get("status", "error"))
        self.state = status if status in ("verified", "held", "canceled") else "error"
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1 if self.state in ("verified", "held") else 0)
        self.phase_label.setText(
            self.translator("update_download_result_" + self.state)
        )
        reason = str(safe_result.get("reason", "") or "")
        self.detail_label.setText(reason)
        self.action_button.setEnabled(True)
        self.action_button.setText(self.translator("close"))

    def _action_clicked(self):
        if self.state == "running":
            self.state = "canceling"
            self.phase_label.setText(self.translator("update_download_canceling"))
            self.action_button.setEnabled(False)
            self.cancel_requested.emit()
            return
        self.accept()
