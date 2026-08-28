from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
import math
from pathlib import Path
from typing import Dict, Optional

from .analysis import validate_gradient
from .database import (
    database_section_titles,
    database_sections,
    export_database_csvs,
)
from .import_batch import discover_chromatogram_files
from .models import (
    AnalysisMethod,
    Dataset,
    GradientPoint,
    LEGEND_COMPONENTS,
    PeakRegion,
    Project,
    Solvent,
    TextAnnotation,
    WorkDirectory,
    new_id,
    sanitize_condition_presets,
)
from .naming import build_project_filename, normalize_analysis_date
from .preset_store import (
    apply_preset_operation,
    build_preset_package,
    filter_preset_names,
    merge_preset_package,
    normalize_preset_metadata,
    record_preset_deleted,
    record_preset_saved,
    record_preset_used,
    stable_preset_names,
)
from .rendering import HIGH_QUALITY, LIGHTWEIGHT, normalize_render_quality
from .qt_compat import CHECKED, ITEM_IS_EDITABLE, UNCHECKED, USER_ROLE, QtGui, QtWidgets, dialog_exec


def optional_float(text: str) -> Optional[float]:
    stripped = text.strip()
    return None if not stripped else float(stripped)


def format_optional(value: Optional[float]) -> str:
    return "" if value is None else "%g" % value


def _preset_display(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return "%g" % value
    return str(value)


class PresetManagerDialog(QtWidgets.QDialog):
    """Manage application-level condition and gradient preset names."""

    def __init__(self, conditions, gradients, metadata, language="ja", parent=None):
        super().__init__(parent)
        self.language = language
        self.conditions = deepcopy(conditions)
        self.gradients = deepcopy(gradients)
        self.metadata = deepcopy(metadata)
        self.setWindowTitle("プリセット管理" if language == "ja" else "Preset manager")
        self.resize(620, 480)
        root = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        self.condition_list = QtWidgets.QListWidget()
        self.gradient_list = QtWidgets.QListWidget()
        self.tabs.addTab(self.condition_list, "条件" if language == "ja" else "Conditions")
        self.tabs.addTab(self.gradient_list, "グラジエント" if language == "ja" else "Gradients")
        root.addWidget(self.tabs, 1)
        actions = QtWidgets.QHBoxLayout()
        self.rename_button = QtWidgets.QPushButton("名前変更…" if language == "ja" else "Rename…")
        self.duplicate_button = QtWidgets.QPushButton("複製…" if language == "ja" else "Duplicate…")
        self.delete_button = QtWidgets.QPushButton("削除" if language == "ja" else "Delete")
        self.import_button = QtWidgets.QPushButton("Import…")
        self.export_button = QtWidgets.QPushButton("Export…")
        for button in (self.rename_button, self.duplicate_button, self.delete_button):
            actions.addWidget(button)
        actions.addStretch(1)
        actions.addWidget(self.import_button)
        actions.addWidget(self.export_button)
        root.addLayout(actions)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        root.addWidget(buttons)
        self.rename_button.clicked.connect(lambda: self._rename_or_duplicate("rename"))
        self.duplicate_button.clicked.connect(lambda: self._rename_or_duplicate("duplicate"))
        self.delete_button.clicked.connect(self._delete)
        self.import_button.clicked.connect(self._import_package)
        self.export_button.clicked.connect(self._export_package)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.tabs.currentChanged.connect(lambda _index: self._update_buttons())
        self.condition_list.currentRowChanged.connect(lambda _row: self._update_buttons())
        self.gradient_list.currentRowChanged.connect(lambda _row: self._update_buttons())
        self._refresh()

    def _current(self):
        if self.tabs.currentIndex() == 0:
            return "conditions", self.conditions, self.condition_list
        return "gradients", self.gradients, self.gradient_list

    def _refresh(self, selected=""):
        for presets, widget in (
            (self.conditions, self.condition_list),
            (self.gradients, self.gradient_list),
        ):
            widget.clear()
            for name in sorted(presets, key=lambda value: (value.casefold(), value)):
                widget.addItem(name)
                if name == selected:
                    widget.setCurrentRow(widget.count() - 1)
        self._update_buttons()

    def _update_buttons(self):
        _kind, _presets, widget = self._current()
        enabled = widget.currentItem() is not None
        self.rename_button.setEnabled(enabled)
        self.duplicate_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)

    def _rename_or_duplicate(self, action):
        kind, presets, widget = self._current()
        item = widget.currentItem()
        if item is None:
            return
        source = item.text()
        initial = source if action == "rename" else source + " copy"
        target, accepted = QtWidgets.QInputDialog.getText(
            self,
            "プリセット名" if self.language == "ja" else "Preset name",
            "新しい名前" if self.language == "ja" else "New name",
            text=initial,
        )
        if not accepted:
            return
        try:
            updated, self.metadata = apply_preset_operation(
                presets, self.metadata, kind, action, source, target
            )
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, self.windowTitle(), str(exc))
            return
        if kind == "conditions":
            self.conditions = updated
        else:
            self.gradients = updated
        self._refresh(str(target).strip())

    def _delete(self):
        kind, presets, widget = self._current()
        item = widget.currentItem()
        if item is None:
            return
        source = item.text()
        message = (
            "「%s」を削除しますか？" % source
            if self.language == "ja"
            else "Delete '%s'?" % source
        )
        if QtWidgets.QMessageBox.question(self, self.windowTitle(), message) != QtWidgets.QMessageBox.Yes:
            return
        updated, self.metadata = apply_preset_operation(
            presets, self.metadata, kind, "delete", source
        )
        if kind == "conditions":
            self.conditions = updated
        else:
            self.gradients = updated
        self._refresh()

    def _export_package(self):
        kind, _presets, widget = self._current()
        current = widget.currentItem().text() if widget.currentItem() else ""
        choices = (
            ["すべて", "選択中のみ"]
            if self.language == "ja"
            else ["All presets", "Selected preset only"]
        )
        choice, accepted = QtWidgets.QInputDialog.getItem(
            self,
            self.windowTitle(),
            "出力範囲" if self.language == "ja" else "Export scope",
            choices,
            0,
            False,
        )
        if not accepted:
            return False
        names = None
        if choice == choices[1]:
            if not current:
                return False
            names = {"conditions": [], "gradients": [], kind: [current]}
        path, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "プリセットを書き出す" if self.language == "ja" else "Export presets",
            "hplc-presets.json",
            "JSON (*.json)",
        )
        if not path:
            return False
        if not Path(path).suffix:
            path += ".json"
        package = build_preset_package(
            self.conditions, self.gradients, self.metadata, names
        )
        try:
            Path(path).write_text(
                json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (OSError, TypeError, ValueError) as exc:
            QtWidgets.QMessageBox.warning(self, self.windowTitle(), str(exc))
            return False
        return True

    def _import_package(self):
        path, _selected_filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "プリセットを読み込む" if self.language == "ja" else "Import presets",
            "",
            "JSON (*.json)",
        )
        if not path:
            return False
        try:
            package = json.loads(Path(path).read_text(encoding="utf-8"))
            incoming = package.get("presets", {}) if isinstance(package, dict) else {}
            validation_conflicts = {}
            if isinstance(incoming, dict):
                for kind, existing in (
                    ("conditions", self.conditions),
                    ("gradients", self.gradients),
                ):
                    values = incoming.get(kind, {})
                    if isinstance(values, dict):
                        validation_conflicts.update(
                            {
                                (kind, name): "skip"
                                for name in values
                                if name in existing
                            }
                        )
            merge_preset_package(
                self.conditions,
                self.gradients,
                self.metadata,
                package,
                validation_conflicts,
            )
            conflicts = {}
            labels = (
                ["スキップ", "置換", "別名で両方保持"]
                if self.language == "ja"
                else ["Skip", "Replace", "Keep both with a new name"]
            )
            policies = ("skip", "replace", "keep_both")
            for kind, existing in (
                ("conditions", self.conditions),
                ("gradients", self.gradients),
            ):
                values = incoming.get(kind, {})
                if not isinstance(values, dict):
                    raise ValueError("Invalid %s presets" % kind)
                for name in values:
                    if name not in existing:
                        continue
                    choice, accepted = QtWidgets.QInputDialog.getItem(
                        self,
                        self.windowTitle(),
                        ("同名プリセット「%s」" if self.language == "ja" else "Preset '%s' already exists") % name,
                        labels,
                        0,
                        False,
                    )
                    if not accepted:
                        return False
                    conflicts[(kind, name)] = policies[labels.index(choice)]
            conditions, gradients, metadata, imported = merge_preset_package(
                self.conditions,
                self.gradients,
                self.metadata,
                package,
                conflicts,
            )
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            QtWidgets.QMessageBox.warning(self, self.windowTitle(), str(exc))
            return False
        self.conditions = conditions
        self.gradients = gradients
        self.metadata = metadata
        selected = (imported["conditions"] + imported["gradients"])
        self._refresh(selected[-1] if selected else "")
        return True


def _populate_preset_sort_combo(combo, language: str):
    options = (
        ("新しい順", "Newest created", "created"),
        ("最近使った順", "Recently used", "used"),
        ("最近更新した順", "Recently updated", "updated"),
        ("名前順", "Name", "name"),
    )
    for japanese, english, key in options:
        combo.addItem(japanese if language == "ja" else english, key)


class ReportOptionsDialog(QtWidgets.QDialog):
    """Select session-only details included in the next report operation."""

    OPTION_LABELS = (
        ("integration_range", "積分範囲", "Integration ranges"),
        ("baseline", "ベースライン／積分方法", "Baselines / integration method"),
        ("retention_time", "保持時間", "Retention times"),
        ("gradient_b", "B %", "B %"),
        ("gradient_conditions", "グラジエント曲線", "Gradient curve"),
        ("quantitation", "定量値", "Quantitation values"),
    )

    def __init__(self, language="ja", parent=None):
        super().__init__(parent)
        self.language = language
        self.setWindowTitle(
            "レポート出力項目" if language == "ja" else "Report contents"
        )
        root = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel(
            "今回のレポートに含める項目を選択してください。この設定は解析値を変更しません。"
            if language == "ja"
            else "Choose content for this report. These choices do not change analysis values."
        )
        note.setWordWrap(True)
        root.addWidget(note)
        self.checkboxes = {}
        for key, japanese, english in self.OPTION_LABELS:
            checkbox = QtWidgets.QCheckBox(
                japanese if language == "ja" else english
            )
            checkbox.setChecked(True)
            root.addWidget(checkbox)
            self.checkboxes[key] = checkbox
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def option_values(self):
        return {
            key: checkbox.isChecked()
            for key, checkbox in self.checkboxes.items()
        }


class ReportScopeDialog(QtWidgets.QDialog):
    """Choose which chromatograms are included in a report operation."""

    def __init__(
        self,
        total_count,
        visible_count,
        selected_count,
        language="ja",
        parent=None,
    ):
        super().__init__(parent)
        self.language = language
        self.setWindowTitle("レポート対象" if language == "ja" else "Report scope")
        root = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel(
            "解析レポートへ出力するクロマトグラムを選択してください。"
            if language == "ja"
            else "Choose which chromatograms to include in the analysis report."
        )
        note.setWordWrap(True)
        root.addWidget(note)
        self.all_radio = QtWidgets.QRadioButton(
            "すべて (%d)" % total_count
            if language == "ja"
            else "All (%d)" % total_count
        )
        self.visible_radio = QtWidgets.QRadioButton(
            "現在表示中 (%d)" % visible_count
            if language == "ja"
            else "Currently visible (%d)" % visible_count
        )
        self.selected_radio = QtWidgets.QRadioButton(
            "現在選択中 (%d)" % selected_count
            if language == "ja"
            else "Currently selected (%d)" % selected_count
        )
        for radio in (self.all_radio, self.visible_radio, self.selected_radio):
            root.addWidget(radio)
        self.visible_radio.setEnabled(visible_count > 0)
        self.selected_radio.setEnabled(selected_count > 0)
        if visible_count > 0:
            self.visible_radio.setChecked(True)
        else:
            self.all_radio.setChecked(True)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def scope(self):
        if self.selected_radio.isChecked():
            return "selected"
        if self.visible_radio.isChecked():
            return "visible"
        return "all"


class PresetPreviewDialog(QtWidgets.QDialog):
    """Read-only current/preset/result comparison shown before applying."""

    def __init__(self, title: str, rows, language: str = "ja", parent=None):
        super().__init__(parent)
        self.language = language
        self.setWindowTitle(title)
        self.resize(780, 520)
        root = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel(
            "この画面では内容を確認するだけで、値は変更されません。"
            if language == "ja"
            else "This preview does not change any values."
        )
        note.setWordWrap(True)
        root.addWidget(note)
        self.table = QtWidgets.QTableWidget(len(rows), 4)
        self.table.setHorizontalHeaderLabels(
            (
                "項目" if language == "ja" else "Field",
                "現在値" if language == "ja" else "Current",
                "プリセット" if language == "ja" else "Preset",
                "適用後" if language == "ja" else "Result",
            )
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        for row, (field, current, preset, result) in enumerate(rows):
            values = (field, current, preset, result)
            changed = current != result
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if changed and column == 3:
                    item.setBackground(QtGui.QBrush(QtGui.QColor("#fff2b2")))
                self.table.setItem(row, column, item)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.resizeColumnsToContents()
        root.addWidget(self.table, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)


class DirectoryImportDialog(QtWidgets.QDialog):
    """Choose and preview one directory batch before importing it."""

    def __init__(self, start_directory="", language="ja", parent=None):
        super().__init__(parent)
        self.language = language
        self.files = []
        self.setWindowTitle(
            "ディレクトリ一括読み込み"
            if language == "ja"
            else "Import directory"
        )
        self.resize(700, 520)
        root = QtWidgets.QVBoxLayout(self)
        directory_row = QtWidgets.QHBoxLayout()
        self.directory_edit = QtWidgets.QLineEdit(start_directory)
        self.browse_button = QtWidgets.QPushButton(
            "参照…" if language == "ja" else "Browse…"
        )
        directory_row.addWidget(self.directory_edit, 1)
        directory_row.addWidget(self.browse_button)
        root.addLayout(directory_row)
        self.recursive_checkbox = QtWidgets.QCheckBox(
            "サブフォルダーも検索"
            if language == "ja"
            else "Include subfolders"
        )
        root.addWidget(self.recursive_checkbox)
        form = QtWidgets.QFormLayout()
        self.group_edit = QtWidgets.QLineEdit()
        form.addRow(
            "ディレクトリラベル" if language == "ja" else "Directory label",
            self.group_edit,
        )
        root.addLayout(form)
        self.summary_label = QtWidgets.QLabel()
        root.addWidget(self.summary_label)
        self.preview_list = QtWidgets.QListWidget()
        root.addWidget(self.preview_list, 1)
        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self.import_button = self.buttons.button(QtWidgets.QDialogButtonBox.Ok)
        self.import_button.setText("読み込み" if language == "ja" else "Import")
        self.import_button.setEnabled(False)
        root.addWidget(self.buttons)
        self.browse_button.clicked.connect(self._browse)
        self.directory_edit.editingFinished.connect(self.refresh_preview)
        self.recursive_checkbox.toggled.connect(self.refresh_preview)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        self.refresh_preview()

    def _browse(self):
        selected = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "ディレクトリを選択" if self.language == "ja" else "Select directory",
            self.directory_edit.text().strip(),
        )
        if selected:
            self.directory_edit.setText(selected)
            self.group_edit.setText(Path(selected).name)
            self.refresh_preview()

    def refresh_preview(self):
        directory = self.directory_edit.text().strip()
        self.preview_list.clear()
        try:
            self.files = discover_chromatogram_files(
                directory, self.recursive_checkbox.isChecked()
            )
        except (OSError, ValueError):
            self.files = []
        root = Path(directory) if directory else None
        for path in self.files:
            self.preview_list.addItem(path.relative_to(root).as_posix())
        if directory and not self.group_edit.text().strip():
            self.group_edit.setText(Path(directory).name)
        count = len(self.files)
        self.summary_label.setText(
            "%d件のTXT/GCDが見つかりました。" % count
            if self.language == "ja"
            else "%d TXT/GCD file(s) found." % count
        )
        self.import_button.setEnabled(bool(self.files))

    def _accept(self):
        if not self.files:
            QtWidgets.QMessageBox.warning(
                self,
                "読み込み" if self.language == "ja" else "Import",
                "対象のTXT/GCDがありません。"
                if self.language == "ja"
                else "No TXT/GCD files were found.",
            )
            return
        self.accept()

    @property
    def group_label(self):
        return self.group_edit.text().strip() or Path(
            self.directory_edit.text().strip()
        ).name

    @property
    def directory_path(self):
        return Path(self.directory_edit.text().strip())


class WorkDirectoriesDialog(QtWidgets.QDialog):
    """Edit the project-owned list of directories used by explicit reload."""

    def __init__(self, directories, language="ja", parent=None):
        super().__init__(parent)
        self.language = language
        self.directories = deepcopy(list(directories))
        self.setWindowTitle(
            "作業ディレクトリ" if language == "ja" else "Work directories"
        )
        self.resize(760, 420)
        root = QtWidgets.QVBoxLayout(self)
        explanation = QtWidgets.QLabel(
            "登録したディレクトリから新規TXT/GCDだけを再読み込みします。"
            if language == "ja"
            else "Reload imports only new TXT/GCD files from registered directories."
        )
        explanation.setWordWrap(True)
        root.addWidget(explanation)
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ("有効", "ラベル", "ディレクトリ", "再帰")
            if language == "ja"
            else ("Enabled", "Label", "Directory", "Recursive")
        )
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(2, 440)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        root.addWidget(self.table, 1)
        row = QtWidgets.QHBoxLayout()
        self.add_button = QtWidgets.QPushButton("追加…" if language == "ja" else "Add…")
        self.remove_button = QtWidgets.QPushButton("削除" if language == "ja" else "Remove")
        self.up_button = QtWidgets.QPushButton("↑" if language == "ja" else "Up")
        self.down_button = QtWidgets.QPushButton("↓" if language == "ja" else "Down")
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        row.addWidget(self.up_button)
        row.addWidget(self.down_button)
        row.addStretch(1)
        root.addLayout(row)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText(
            "保存" if language == "ja" else "Save"
        )
        root.addWidget(buttons)
        self.add_button.clicked.connect(self._add)
        self.remove_button.clicked.connect(self._remove)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        self._refresh()

    def _refresh(self):
        self.table.setRowCount(len(self.directories))
        for row, entry in enumerate(self.directories):
            enabled = QtWidgets.QTableWidgetItem()
            enabled.setFlags(enabled.flags() | ITEM_IS_EDITABLE)
            enabled.setCheckState(CHECKED if entry.enabled else UNCHECKED)
            self.table.setItem(row, 0, enabled)
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(entry.label))
            path = QtWidgets.QTableWidgetItem(entry.path)
            path.setFlags(path.flags() & ~ITEM_IS_EDITABLE)
            self.table.setItem(row, 2, path)
            recursive = QtWidgets.QTableWidgetItem()
            recursive.setCheckState(CHECKED if entry.recursive else UNCHECKED)
            self.table.setItem(row, 3, recursive)

    def _add(self):
        self._sync_table()
        selected = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "作業ディレクトリを選択" if self.language == "ja" else "Select work directory",
            "",
        )
        if not selected:
            return
        normalized = str(Path(selected).resolve())
        if any(str(Path(item.path).resolve()).casefold() == normalized.casefold() for item in self.directories):
            QtWidgets.QMessageBox.warning(
                self,
                "作業ディレクトリ" if self.language == "ja" else "Work directories",
                "同じディレクトリは既に登録されています。"
                if self.language == "ja"
                else "That directory is already registered.",
            )
            return
        self.directories.append(WorkDirectory(path=normalized, label=Path(normalized).name))
        self._refresh()
        self.table.selectRow(len(self.directories) - 1)

    def _remove(self):
        self._sync_table()
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()}, reverse=True)
        for row in rows:
            del self.directories[row]
        self._refresh()

    def _move(self, offset):
        self._sync_table()
        row = self.table.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= len(self.directories):
            return
        self.directories[row], self.directories[target] = (
            self.directories[target],
            self.directories[row],
        )
        self._refresh()
        self.table.selectRow(target)

    def _sync_table(self):
        for row, entry in enumerate(self.directories):
            entry.label = self.table.item(row, 1).text().strip() or Path(entry.path).name
            entry.recursive = self.table.item(row, 3).checkState() == CHECKED
            entry.enabled = self.table.item(row, 0).checkState() == CHECKED

    def _accept(self):
        self._sync_table()
        updated = []
        for row, entry in enumerate(self.directories):
            label = self.table.item(row, 1).text().strip()
            updated.append(
                WorkDirectory(
                    path=entry.path,
                    label=label or Path(entry.path).name,
                    recursive=self.table.item(row, 3).checkState() == CHECKED,
                    enabled=self.table.item(row, 0).checkState() == CHECKED,
                )
            )
        self.directories = updated
        self.accept()


class TextAnnotationDialog(QtWidgets.QDialog):
    """Create or edit one movable text box on the chromatogram."""

    def __init__(
        self,
        annotation: TextAnnotation,
        datasets,
        language: str = "ja",
        parent=None,
        allow_delete: bool = False,
    ):
        super().__init__(parent)
        self.annotation = annotation
        self.language = language
        self.delete_requested = False
        self.setWindowTitle(
            "テキストラベル" if language == "ja" else "Text label"
        )
        self.resize(520, 410)
        root = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        self.text_edit = QtWidgets.QPlainTextEdit(annotation.text)
        self.text_edit.setMaximumHeight(100)
        form.addRow("文字" if language == "ja" else "Text", self.text_edit)

        self.dataset_combo = QtWidgets.QComboBox()
        self.dataset_combo.addItem(
            "全体" if language == "ja" else "All chromatograms", ""
        )
        for dataset in datasets:
            self.dataset_combo.addItem(dataset.legend_label(), dataset.id)
        dataset_index = self.dataset_combo.findData(annotation.dataset_id)
        self.dataset_combo.setCurrentIndex(max(0, dataset_index))
        form.addRow(
            "対象クロマトグラム" if language == "ja" else "Chromatogram",
            self.dataset_combo,
        )

        self.x_spin = QtWidgets.QDoubleSpinBox()
        self.y_spin = QtWidgets.QDoubleSpinBox()
        for spin in (self.x_spin, self.y_spin):
            spin.setRange(-1.0e12, 1.0e12)
            spin.setDecimals(6)
        self.x_spin.setValue(float(annotation.x_min))
        self.y_spin.setValue(float(annotation.y_value))
        form.addRow("X位置 (min)" if language == "ja" else "X position (min)", self.x_spin)
        form.addRow("Y位置" if language == "ja" else "Y position", self.y_spin)

        self.y_axis_combo = QtWidgets.QComboBox()
        self.y_axis_combo.addItem("第1縦軸" if language == "ja" else "Y axis 1", 1)
        self.y_axis_combo.addItem("第2縦軸" if language == "ja" else "Y axis 2", 2)
        self.y_axis_combo.setCurrentIndex(
            max(0, self.y_axis_combo.findData(int(annotation.y_axis)))
        )
        form.addRow("基準軸" if language == "ja" else "Reference axis", self.y_axis_combo)

        self.font_combo = QtWidgets.QFontComboBox()
        self.font_combo.setCurrentFont(
            QtGui.QFont(annotation.font_family or "Arial")
        )
        self.font_size = QtWidgets.QDoubleSpinBox()
        self.font_size.setRange(4.0, 72.0)
        self.font_size.setDecimals(1)
        self.font_size.setSingleStep(0.5)
        self.font_size.setValue(float(annotation.font_size))
        form.addRow("フォント" if language == "ja" else "Font", self.font_combo)
        form.addRow("サイズ" if language == "ja" else "Size", self.font_size)

        self.color_button = QtWidgets.QPushButton()
        self.color_button.color_name = annotation.color or "#000000"
        self._update_color_button()
        self.color_button.clicked.connect(self._pick_color)
        form.addRow("文字色" if language == "ja" else "Text color", self.color_button)
        root.addLayout(form)

        hint = QtWidgets.QLabel(
            "配置後はドラッグで移動、ダブルクリックで再編集できます。"
            "ズームしても文字とボックスの表示サイズは変わりません。"
            if language == "ja"
            else "After placement, drag to move and double-click to edit again. "
            "The text and box stay the same screen size while zooming."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        button_row = QtWidgets.QHBoxLayout()
        if allow_delete:
            self.delete_button = QtWidgets.QPushButton(
                "このラベルを削除" if language == "ja" else "Delete this label"
            )
            self.delete_button.clicked.connect(self._delete)
            button_row.addWidget(self.delete_button)
        button_row.addStretch(1)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        button_row.addWidget(buttons)
        root.addLayout(button_row)

    def _update_color_button(self):
        color = QtGui.QColor(self.color_button.color_name)
        if not color.isValid():
            color = QtGui.QColor("#000000")
            self.color_button.color_name = color.name()
        foreground = "#ffffff" if color.lightness() < 128 else "#000000"
        self.color_button.setText(self.color_button.color_name)
        self.color_button.setStyleSheet(
            "QPushButton { background-color: %s; color: %s; }"
            % (self.color_button.color_name, foreground)
        )

    def _pick_color(self):
        color = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(self.color_button.color_name), self
        )
        if color.isValid():
            self.color_button.color_name = color.name()
            self._update_color_button()

    def _delete(self):
        self.delete_requested = True
        self.accept()

    def _accept(self):
        text = self.text_edit.toPlainText().strip()
        if not text:
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid label",
                "文字を入力してください。"
                if self.language == "ja"
                else "Enter label text.",
            )
            return
        self.annotation.text = text
        self.annotation.dataset_id = self.dataset_combo.currentData() or ""
        self.annotation.x_min = self.x_spin.value()
        self.annotation.y_value = self.y_spin.value()
        self.annotation.y_axis = int(self.y_axis_combo.currentData() or 1)
        self.annotation.font_family = self.font_combo.currentFont().family() or "Arial"
        self.annotation.font_size = self.font_size.value()
        self.annotation.color = self.color_button.color_name
        self.accept()


class MetadataDialog(QtWidgets.QDialog):
    def __init__(self, dataset: Dataset, language: str = "ja", parent=None):
        super().__init__(parent)
        self.dataset = dataset
        self.original_label = dataset.label
        self.original_short_label = dataset.short_label
        self.language = language
        self.setWindowTitle("詳細・定量条件" if language == "ja" else "Details and quantitation")
        self.resize(720, 760)
        root = QtWidgets.QVBoxLayout(self)
        meta = dataset.measurement

        self.fields = {}

        def add_section(title, definitions):
            group = QtWidgets.QGroupBox(title)
            form = QtWidgets.QFormLayout(group)
            for key, label, value in definitions:
                widget = QtWidgets.QLineEdit(str(value))
                self.fields[key] = widget
                form.addRow(label, widget)
            root.addWidget(group)
            return form

        sample_title = "サンプル" if language == "ja" else "Sample"
        add_section(
            sample_title,
            (
                ("label", "表示ラベル" if language == "ja" else "Display label", dataset.label),
                ("short_label", "短縮ラベル" if language == "ja" else "Short label", dataset.short_label),
                ("sample_name", "サンプル名" if language == "ja" else "Sample name", meta.sample_name),
                ("sample_id", "ID", meta.sample_id),
                ("group", "グループ" if language == "ja" else "Group", meta.group),
                ("replicate", "反復" if language == "ja" else "Replicate", meta.replicate),
                ("tags", "タグ（カンマ区切り）" if language == "ja" else "Tags (comma-separated)", ", ".join(meta.tags)),
            ),
        )

        measurement_form = add_section(
            "測定条件" if language == "ja" else "Measurement conditions",
            (
                ("wavelength", "測定波長 (nm)" if language == "ja" else "Wavelength (nm)", format_optional(meta.wavelength_nm)),
                ("flow", "流量 (mL/min)" if language == "ja" else "Flow rate (mL/min)", format_optional(meta.flow_rate_ml_min)),
                ("cell", "セル光路長 (cm)" if language == "ja" else "Cell path length (cm)", format_optional(meta.cell_path_length_cm if meta.cell_path_length_cm is not None else 1.0)),
                ("column", "カラム" if language == "ja" else "Column", meta.column_name),
                ("temperature", "カラム温度 (℃)" if language == "ja" else "Column temperature (°C)", format_optional(meta.column_temperature_c)),
            ),
        )
        self.aux_edit = QtWidgets.QLineEdit(format_optional(meta.aux_range_au_per_v))
        validator = QtGui.QDoubleValidator(0.0, 1.0e12, 9, self.aux_edit)
        validator.setNotation(QtGui.QDoubleValidator.StandardNotation)
        self.aux_edit.setValidator(validator)
        self.aux_edit.setPlaceholderText("e.g. 0.5, 1, 2.5")
        measurement_form.insertRow(1, "AU/V", self.aux_edit)

        add_section(
            "試料情報" if language == "ja" else "Analyte information",
            (
                ("analyte", "分析対象物" if language == "ja" else "Analyte", meta.analyte_name),
                ("analyte_id", "分析対象物ID" if language == "ja" else "Analyte ID", meta.analyte_id),
                ("analyte_aliases", "別名（カンマ区切り）" if language == "ja" else "Aliases (comma-separated)", ", ".join(meta.analyte_aliases)),
                ("analyte_source", "データ出典" if language == "ja" else "Data source", meta.analyte_source),
                ("epsilon_unit", "吸光係数の単位" if language == "ja" else "Extinction coefficient unit", meta.extinction_coefficient_unit),
                ("injection", "注入量 (µL)" if language == "ja" else "Injection volume (µL)", format_optional(meta.injection_volume_ul)),
                ("eps214", "ε214 (M⁻¹ cm⁻¹)", format_optional(meta.molar_absorptivity_214)),
                ("eps280", "ε280 (M⁻¹ cm⁻¹)", format_optional(meta.molar_absorptivity_280)),
                ("mw", "分子量 (g/mol)" if language == "ja" else "Molecular weight (g/mol)", format_optional(meta.molecular_weight_g_mol)),
            ),
        )

        other_group = QtWidgets.QGroupBox("コメント・由来" if language == "ja" else "Comments and source")
        other_form = QtWidgets.QFormLayout(other_group)
        self.comments = QtWidgets.QPlainTextEdit(meta.comments)
        self.comments.setMaximumHeight(80)
        other_form.addRow("コメント" if language == "ja" else "Comments", self.comments)
        origin = QtWidgets.QLineEdit(dataset.original_path)
        origin.setReadOnly(True)
        other_form.addRow("元のパス" if language == "ja" else "Original path", origin)
        sha = QtWidgets.QLineEdit(dataset.sha256)
        sha.setReadOnly(True)
        other_form.addRow("SHA-256", sha)
        root.addWidget(other_group)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _accept(self):
        try:
            numeric = {
                key: optional_float(self.fields[key].text())
                for key in ("wavelength", "flow", "cell", "temperature", "injection", "eps214", "eps280", "mw")
            }
            aux = optional_float(self.aux_edit.text())
            if aux is not None and aux <= 0:
                raise ValueError("AU/V must be positive")
            for key in ("flow", "cell", "eps214", "eps280", "mw"):
                if numeric[key] is not None and numeric[key] <= 0:
                    raise ValueError("%s must be positive" % key)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid value", str(exc))
            return
        meta = self.dataset.measurement
        self.dataset.label = self.fields["label"].text().strip() or self.dataset.original_filename
        entered_short = self.fields["short_label"].text().strip()
        if not entered_short or (
            entered_short == self.original_short_label
            and self.original_short_label == self.original_label
        ):
            self.dataset.short_label = self.dataset.label
        else:
            self.dataset.short_label = entered_short
        meta.sample_name = self.fields["sample_name"].text().strip()
        meta.sample_id = self.fields["sample_id"].text().strip()
        meta.group = self.fields["group"].text().strip()
        meta.replicate = self.fields["replicate"].text().strip()
        meta.tags = [item.strip() for item in self.fields["tags"].text().split(",") if item.strip()]
        meta.wavelength_nm = numeric["wavelength"]
        meta.aux_range_au_per_v = aux
        meta.flow_rate_ml_min = numeric["flow"]
        meta.cell_path_length_cm = numeric["cell"] if numeric["cell"] is not None else 1.0
        meta.column_name = self.fields["column"].text().strip()
        meta.column_temperature_c = numeric["temperature"]
        meta.injection_volume_ul = numeric["injection"]
        meta.analyte_name = self.fields["analyte"].text().strip()
        meta.analyte_id = self.fields["analyte_id"].text().strip()
        if meta.analyte_name and not meta.analyte_id:
            meta.analyte_id = "analyte-" + new_id()
        meta.analyte_aliases = [
            item.strip()
            for item in self.fields["analyte_aliases"].text().split(",")
            if item.strip()
        ]
        meta.analyte_source = self.fields["analyte_source"].text().strip()
        meta.extinction_coefficient_unit = (
            self.fields["epsilon_unit"].text().strip() or "M^-1 cm^-1"
        )
        meta.molar_absorptivity_214 = numeric["eps214"]
        meta.molar_absorptivity_280 = numeric["eps280"]
        meta.molecular_weight_g_mol = numeric["mw"]
        meta.comments = self.comments.toPlainText().strip()
        self.accept()


PRESET_FIELDS = (
    "wavelength_nm",
    "aux_range_au_per_v",
    "flow_rate_ml_min",
    "cell_path_length_cm",
    "column_name",
    "column_temperature_c",
    "injection_volume_ul",
    "analyte_name",
    "molar_absorptivity_214",
    "molar_absorptivity_280",
    "molecular_weight_g_mol",
)

CONDITION_PRESET_LABELS = {
    "wavelength_nm": ("波長 (nm)", "Wavelength (nm)"),
    "aux_range_au_per_v": ("AU/V", "AU/V"),
    "flow_rate_ml_min": ("流量 (mL/min)", "Flow (mL/min)"),
    "cell_path_length_cm": ("セル長 (cm)", "Cell length (cm)"),
    "column_name": ("カラム", "Column"),
    "column_temperature_c": ("カラム温度 (°C)", "Column temp. (°C)"),
    "injection_volume_ul": ("注入量 (µL)", "Injection (µL)"),
    "analyte_name": ("分析対象物", "Analyte"),
    "molar_absorptivity_214": ("ε214", "ε214"),
    "molar_absorptivity_280": ("ε280", "ε280"),
    "molecular_weight_g_mol": ("分子量 (g/mol)", "Molecular weight (g/mol)"),
}


def condition_preset_preview_rows(current, preset, language="ja"):
    rows = []
    for field in PRESET_FIELDS:
        current_text = _preset_display(current.get(field))
        preset_value = preset.get(field)
        applies = preset_value is not None and not (
            isinstance(preset_value, str) and not preset_value.strip()
        )
        if applies:
            preset_text = _preset_display(preset_value)
            result_text = preset_text
        else:
            preset_text = "（維持）" if language == "ja" else "(keep)"
            result_text = current_text
        labels = CONDITION_PRESET_LABELS[field]
        rows.append(
            (labels[0] if language == "ja" else labels[1], current_text, preset_text, result_text)
        )
    return rows


def _gradient_point_text(point) -> str:
    if point is None:
        return ""
    source = asdict(point) if isinstance(point, GradientPoint) else point
    if not isinstance(source, dict):
        return _preset_display(source)
    return "A={0}; B={1}; C={2}; D={3}; Flow={4}".format(
        _preset_display(source.get("a_pct")),
        _preset_display(source.get("b_pct")),
        _preset_display(source.get("c_pct")),
        _preset_display(source.get("d_pct")),
        _preset_display(source.get("flow_ml_min")),
    )


def _gradient_point_time(point) -> str:
    if isinstance(point, GradientPoint):
        source = asdict(point)
    else:
        source = point if isinstance(point, dict) else {}
    return _preset_display(source.get("time_min"))


def _solvent_text(solvent) -> str:
    if isinstance(solvent, Solvent):
        source = asdict(solvent)
    else:
        source = solvent if isinstance(solvent, dict) else {}
    name = str(source.get("name", "") or "")
    composition = str(source.get("composition", "") or "")
    return " / ".join(value for value in (name, composition) if value)


def gradient_preset_preview_rows(current, preset, language="ja"):
    current = current if isinstance(current, dict) else {}
    preset = preset if isinstance(preset, dict) else {}
    rows = []
    current_solvents = current.get("solvents", {}) or {}
    preset_solvents = preset.get("solvents", {}) or {}
    for line in "ABCD":
        current_text = _solvent_text(current_solvents.get(line, {}))
        preset_text = _solvent_text(preset_solvents.get(line, {}))
        rows.append(("Solvent " + line, current_text, preset_text, preset_text))
    current_points = current.get("gradient", []) or []
    preset_points = preset.get("gradient", []) or []
    for index in range(max(len(current_points), len(preset_points))):
        current_point = current_points[index] if index < len(current_points) else None
        preset_point = preset_points[index] if index < len(preset_points) else None
        current_time = _gradient_point_time(current_point)
        preset_time = _gradient_point_time(preset_point)
        field = (
            "時間点 {0}".format(index + 1)
            if language == "ja"
            else "Time point {0}".format(index + 1)
        )
        current_text = (
            "{0} min: {1}".format(
                current_time, _gradient_point_text(current_point)
            )
            if current_point is not None
            else ""
        )
        preset_text = (
            "{0} min: {1}".format(
                preset_time, _gradient_point_text(preset_point)
            )
            if preset_point is not None
            else ""
        )
        rows.append((field, current_text, preset_text, preset_text))
    return rows


class BatchCellError(ValueError):
    """One invalid editable cell in the batch conditions table."""

    def __init__(self, row: int, column: int, reason: str):
        super().__init__(reason)
        self.row = row
        self.column = column
        self.reason = reason


class BatchConditionTable(QtWidgets.QTableWidget):
    """Condition table that routes standard clipboard shortcuts to its dialog."""

    def __init__(self, rows: int, columns: int, parent=None):
        super().__init__(rows, columns, parent)
        self.copy_callback = None
        self.paste_callback = None

    def keyPressEvent(self, event):
        if event.matches(QtGui.QKeySequence.Copy) and self.copy_callback:
            self.copy_callback()
            event.accept()
            return
        if event.matches(QtGui.QKeySequence.Paste) and self.paste_callback:
            self.paste_callback()
            event.accept()
            return
        super().keyPressEvent(event)


class BatchMetadataDialog(QtWidgets.QDialog):
    """Editable condition overview with atomic validation and preset support."""

    FIELD_COLUMNS = {
        "wavelength_nm": 4,
        "aux_range_au_per_v": 5,
        "flow_rate_ml_min": 6,
        "cell_path_length_cm": 7,
        "column_name": 8,
        "column_temperature_c": 9,
        "injection_volume_ul": 10,
        "analyte_name": 11,
        "molar_absorptivity_214": 12,
        "molar_absorptivity_280": 13,
        "molecular_weight_g_mol": 14,
    }
    COLUMN_FIELDS = {column: field for field, column in FIELD_COLUMNS.items()}
    GRADIENT_COLUMN = 15
    EDITABLE_COLUMNS = frozenset(range(1, GRADIENT_COLUMN))
    RUN_SHARED_COLUMNS = frozenset((1, 2, 6, 7, 8, 9, 10, 11, 12, 13, 14))
    POSITIVE_FIELDS = frozenset(
        (
            "wavelength_nm",
            "aux_range_au_per_v",
            "flow_rate_ml_min",
            "cell_path_length_cm",
            "injection_volume_ul",
            "molar_absorptivity_214",
            "molar_absorptivity_280",
            "molecular_weight_g_mol",
        )
    )

    def __init__(
        self,
        project: Project,
        selected_dataset_id: str = "",
        language: str = "ja",
        parent=None,
        preset_metadata=None,
    ):
        super().__init__(parent)
        self.project = project
        self.language = language
        self.selected_dataset_id = selected_dataset_id
        self.presets = sanitize_condition_presets(project.condition_presets)
        self.gradient_presets = deepcopy(project.gradient_presets)
        self.preset_metadata = normalize_preset_metadata(
            self.presets, self.gradient_presets, deepcopy(preset_metadata)
        )
        self.loaded_condition_preset_name = ""
        self.gradient_assignments = {}
        self.detail_overrides = {}
        self._syncing_table = False
        self.setWindowTitle("条件の一括入力・プリセット" if language == "ja" else "Batch conditions and presets")
        self.resize(1250, 620)
        root = QtWidgets.QVBoxLayout(self)

        preset_row = QtWidgets.QHBoxLayout()
        preset_row.addWidget(QtWidgets.QLabel("条件プリセット" if language == "ja" else "Condition preset"))
        self.preset_combo = QtWidgets.QComboBox()
        preset_row.addWidget(self.preset_combo, 1)
        self.condition_preset_filter = QtWidgets.QLineEdit()
        self.condition_preset_filter.setPlaceholderText(
            "名前で絞り込み" if language == "ja" else "Filter by name"
        )
        self.condition_preset_filter.setMaximumWidth(150)
        self.condition_preset_sort = QtWidgets.QComboBox()
        _populate_preset_sort_combo(self.condition_preset_sort, language)
        preset_row.addWidget(self.condition_preset_filter)
        preset_row.addWidget(self.condition_preset_sort)
        self.preview_preset_button = QtWidgets.QPushButton(
            "内容・差分…" if language == "ja" else "Preview / diff…"
        )
        self.save_preset_button = QtWidgets.QPushButton(
            "選択中データから名前を付けて保存" if language == "ja" else "Save selected dataset as preset"
        )
        self.delete_preset_button = QtWidgets.QPushButton("プリセット削除" if language == "ja" else "Delete preset")
        self.apply_preset_button = QtWidgets.QPushButton("チェック行へ適用" if language == "ja" else "Apply to checked rows")
        preset_row.addWidget(self.preview_preset_button)
        preset_row.addWidget(self.save_preset_button)
        preset_row.addWidget(self.delete_preset_button)
        preset_row.addWidget(self.apply_preset_button)
        root.addLayout(preset_row)

        gradient_preset_row = QtWidgets.QHBoxLayout()
        gradient_preset_row.addWidget(
            QtWidgets.QLabel("グラジエントプリセット" if language == "ja" else "Gradient preset")
        )
        self.gradient_preset_combo = QtWidgets.QComboBox()
        self.gradient_preset_filter = QtWidgets.QLineEdit()
        self.gradient_preset_filter.setPlaceholderText(
            "名前で絞り込み" if language == "ja" else "Filter by name"
        )
        self.gradient_preset_filter.setMaximumWidth(150)
        self.gradient_preset_sort = QtWidgets.QComboBox()
        _populate_preset_sort_combo(self.gradient_preset_sort, language)
        self.apply_gradient_button = QtWidgets.QPushButton(
            "チェック行へ適用" if language == "ja" else "Apply to checked rows"
        )
        self.preview_gradient_preset_button = QtWidgets.QPushButton(
            "内容・差分…" if language == "ja" else "Preview / diff…"
        )
        gradient_preset_row.addWidget(self.gradient_preset_combo, 1)
        gradient_preset_row.addWidget(self.gradient_preset_filter)
        gradient_preset_row.addWidget(self.gradient_preset_sort)
        gradient_preset_row.addWidget(self.preview_gradient_preset_button)
        gradient_preset_row.addWidget(self.apply_gradient_button)
        root.addLayout(gradient_preset_row)

        selection_row = QtWidgets.QHBoxLayout()
        self.check_all_button = QtWidgets.QPushButton("全データをチェック" if language == "ja" else "Check all")
        self.check_group_button = QtWidgets.QPushButton("同じグループをチェック" if language == "ja" else "Check same group")
        self.clear_checks_button = QtWidgets.QPushButton("チェック解除" if language == "ja" else "Clear checks")
        self.edit_details_button = QtWidgets.QPushButton(
            "選択行の詳細設定…" if language == "ja" else "Edit selected row details…"
        )
        self.edit_gradient_button = QtWidgets.QPushButton(
            "選択行のグラジエント…"
            if language == "ja"
            else "Edit selected row gradient…"
        )
        selection_row.addWidget(self.check_all_button)
        selection_row.addWidget(self.check_group_button)
        selection_row.addWidget(self.clear_checks_button)
        selection_row.addWidget(self.edit_details_button)
        selection_row.addWidget(self.edit_gradient_button)
        selection_row.addStretch(1)
        root.addLayout(selection_row)

        note = QtWidgets.QLabel(
            "条件セルは直接編集でき、Shift/Ctrlで複数選択、Ctrl+C/Ctrl+Vで矩形範囲をコピー／貼り付けできます。Run単位の値は同じRunの行へ同期され、OK時に全行を検証してから一括適用します。"
            if language == "ja"
            else "Edit condition cells directly, use Shift/Ctrl for multi-selection, and use Ctrl+C/Ctrl+V for rectangular clipboard ranges. Run-level values are synchronized across the same Run, and every row is validated before changes are applied on OK."
        )
        note.setWordWrap(True)
        root.addWidget(note)

        headers = (
            "対象" if language == "ja" else "Use",
            "ラベル" if language == "ja" else "Label",
            "グループ" if language == "ja" else "Group",
            "縦軸" if language == "ja" else "Y axis",
            "波長 (nm)" if language == "ja" else "Wavelength (nm)",
            "AU/V",
            "流量" if language == "ja" else "Flow",
            "セル長" if language == "ja" else "Cell length",
            "カラム" if language == "ja" else "Column",
            "カラム温度" if language == "ja" else "Column temp.",
            "注入量" if language == "ja" else "Injection",
            "分析対象物" if language == "ja" else "Analyte",
            "ε214",
            "ε280",
            "分子量" if language == "ja" else "Molecular weight",
            "グラジエント" if language == "ja" else "Gradient",
        )
        self.table = BatchConditionTable(len(project.datasets), len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectItems)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
            | QtWidgets.QAbstractItemView.SelectedClicked
        )
        self.table.verticalHeader().setVisible(False)
        root.addWidget(self.table, 1)
        for row, dataset in enumerate(project.datasets):
            use = QtWidgets.QTableWidgetItem("")
            use.setFlags(use.flags() & ~ITEM_IS_EDITABLE)
            use.setCheckState(CHECKED if dataset.id == selected_dataset_id else UNCHECKED)
            self.table.setItem(row, 0, use)
            values = (
                dataset.label,
                dataset.measurement.group,
                str(dataset.y_axis),
                format_optional(dataset.measurement.wavelength_nm),
                format_optional(dataset.measurement.aux_range_au_per_v),
                format_optional(dataset.measurement.flow_rate_ml_min),
                format_optional(dataset.measurement.cell_path_length_cm),
                dataset.measurement.column_name,
                format_optional(dataset.measurement.column_temperature_c),
                format_optional(dataset.measurement.injection_volume_ul),
                dataset.measurement.analyte_name,
                format_optional(dataset.measurement.molar_absorptivity_214),
                format_optional(dataset.measurement.molar_absorptivity_280),
                format_optional(dataset.measurement.molecular_weight_g_mol),
                dataset.effective_gradient_preset_name(),
            )
            for column, value in enumerate(values, start=1):
                item = QtWidgets.QTableWidgetItem(value)
                if column not in self.EDITABLE_COLUMNS:
                    item.setFlags(item.flags() & ~ITEM_IS_EDITABLE)
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()

        self.save_preset_button.clicked.connect(self._save_preset)
        self.delete_preset_button.clicked.connect(self._delete_preset)
        self.preview_preset_button.clicked.connect(self._preview_condition_preset)
        self.apply_preset_button.clicked.connect(self._apply_preset)
        self.preview_gradient_preset_button.clicked.connect(
            self._preview_gradient_preset
        )
        self.apply_gradient_button.clicked.connect(self._apply_gradient_preset)
        self.condition_preset_filter.textChanged.connect(
            lambda _value=None: self._refresh_presets(
                self.preset_combo.currentText()
            )
        )
        self.condition_preset_sort.currentIndexChanged.connect(
            lambda _value=None: self._refresh_presets(
                self.preset_combo.currentText()
            )
        )
        self.gradient_preset_filter.textChanged.connect(
            lambda _value=None: self._refresh_gradient_presets(
                self.gradient_preset_combo.currentText()
            )
        )
        self.gradient_preset_sort.currentIndexChanged.connect(
            lambda _value=None: self._refresh_gradient_presets(
                self.gradient_preset_combo.currentText()
            )
        )
        self.check_all_button.clicked.connect(lambda: self._set_all_checks(True))
        self.clear_checks_button.clicked.connect(lambda: self._set_all_checks(False))
        self.check_group_button.clicked.connect(self._check_same_group)
        self.edit_details_button.clicked.connect(self._edit_selected_details)
        self.edit_gradient_button.clicked.connect(self._edit_selected_gradient)
        self.table.itemChanged.connect(self._table_item_changed)
        self.table.cellDoubleClicked.connect(self._cell_double_clicked)
        self.table.copy_callback = self._copy_selected_cells
        self.table.paste_callback = self._paste_clipboard
        self._refresh_presets()
        self._refresh_gradient_presets()
        selected_row = next(
            (
                row
                for row, dataset in enumerate(project.datasets)
                if dataset.id == selected_dataset_id
            ),
            0,
        )
        if project.datasets:
            self.table.selectRow(selected_row)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _selected_dataset(self) -> Optional[Dataset]:
        if hasattr(self, "table"):
            row = self.table.currentRow()
            if 0 <= row < len(self.project.datasets):
                dataset = self.project.datasets[row]
                return self.detail_overrides.get(dataset.id, dataset)
        for dataset in self.project.datasets:
            if dataset.id == self.selected_dataset_id:
                return self.detail_overrides.get(dataset.id, dataset)
        return None

    def _refresh_row_from_dataset(self, row: int, dataset: Dataset):
        values = (
            dataset.label,
            dataset.measurement.group,
            str(dataset.y_axis),
            format_optional(dataset.measurement.wavelength_nm),
            format_optional(dataset.measurement.aux_range_au_per_v),
            format_optional(dataset.measurement.flow_rate_ml_min),
            format_optional(dataset.measurement.cell_path_length_cm),
            dataset.measurement.column_name,
            format_optional(dataset.measurement.column_temperature_c),
            format_optional(dataset.measurement.injection_volume_ul),
            dataset.measurement.analyte_name,
            format_optional(dataset.measurement.molar_absorptivity_214),
            format_optional(dataset.measurement.molar_absorptivity_280),
            format_optional(dataset.measurement.molecular_weight_g_mol),
            dataset.effective_gradient_preset_name(),
        )
        for column, value in enumerate(values, start=1):
            self.table.item(row, column).setText(value)

    def _cell_double_clicked(self, row: int, column: int):
        if column == self.GRADIENT_COLUMN:
            self._edit_selected_gradient(row)
        elif column not in self.EDITABLE_COLUMNS:
            self._edit_selected_details(row)

    def _table_item_changed(self, item):
        if self._syncing_table or item.column() not in self.EDITABLE_COLUMNS:
            return
        self._syncing_table = True
        try:
            item.setBackground(QtGui.QBrush())
            if item.column() not in self.RUN_SHARED_COLUMNS:
                return
            source = self.project.datasets[item.row()]
            for row, dataset in enumerate(self.project.datasets):
                if row == item.row() or dataset.run_id != source.run_id:
                    continue
                related = self.table.item(row, item.column())
                if related is not None and related.text() != item.text():
                    related.setText(item.text())
                    related.setBackground(QtGui.QBrush())
        finally:
            self._syncing_table = False

    def _cell_error(self, row: int, column: int, ja: str, en: str):
        raise BatchCellError(row, column, ja if self.language == "ja" else en)

    def _validate_cell_value(self, row: int, column: int, text: str):
        normalized = str(text).strip()
        if column == 3:
            try:
                axis = int(normalized)
            except ValueError:
                self._cell_error(row, column, "1または2を入力してください。", "Enter 1 or 2.")
            if axis not in (1, 2):
                self._cell_error(row, column, "1または2を入力してください。", "Enter 1 or 2.")
            return axis

        field = self.COLUMN_FIELDS.get(column)
        if field is None or field in ("column_name", "analyte_name"):
            return normalized
        try:
            value = optional_float(normalized)
        except ValueError:
            self._cell_error(
                row,
                column,
                "数値または空欄を入力してください。",
                "Enter a number or leave the cell empty.",
            )
        if value is not None and not math.isfinite(value):
            self._cell_error(
                row,
                column,
                "有限の数値を入力してください。",
                "Enter a finite number.",
            )
        if field in self.POSITIVE_FIELDS and value is not None and value <= 0:
            self._cell_error(
                row,
                column,
                "0より大きい値を入力してください。",
                "Enter a value greater than zero.",
            )
        return value

    def _parse_row(self, row: int):
        axis = self._validate_cell_value(row, 3, self._cell_text(row, 3))
        numeric = {}
        for field, column in self.FIELD_COLUMNS.items():
            if field in ("column_name", "analyte_name"):
                continue
            numeric[field] = self._validate_cell_value(
                row, column, self._cell_text(row, column)
            )
        return axis, numeric

    def _clipboard_warning(self, ja: str, en: str):
        QtWidgets.QMessageBox.warning(
            self,
            "コピー／貼り付け" if self.language == "ja" else "Copy / paste",
            ja if self.language == "ja" else en,
        )

    def _copy_selected_cells(self):
        indexes = self.table.selectedIndexes()
        if not indexes:
            return
        rows = sorted({index.row() for index in indexes})
        columns = sorted({index.column() for index in indexes})
        selected = {(index.row(), index.column()) for index in indexes}
        rectangle_rows = range(rows[0], rows[-1] + 1)
        rectangle_columns = range(columns[0], columns[-1] + 1)
        rectangle = {
            (row, column)
            for row in rectangle_rows
            for column in rectangle_columns
        }
        if selected != rectangle:
            self._clipboard_warning(
                "コピーするセルは1つの矩形範囲で選択してください。",
                "Select one rectangular cell range to copy.",
            )
            return
        lines = []
        for row in rectangle_rows:
            values = []
            for column in rectangle_columns:
                item = self.table.item(row, column)
                if column == 0:
                    values.append("1" if item and item.checkState() == CHECKED else "0")
                else:
                    values.append(item.text() if item is not None else "")
            lines.append("\t".join(values))
        QtWidgets.QApplication.clipboard().setText("\n".join(lines))

    def _paste_clipboard(self):
        text = QtWidgets.QApplication.clipboard().text()
        normalized_text = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
        if not normalized_text:
            return
        values = [line.split("\t") for line in normalized_text.split("\n")]
        width = len(values[0])
        if not width or any(len(row_values) != width for row_values in values):
            self._clipboard_warning(
                "列数が揃ったTSVを貼り付けてください。",
                "Paste TSV rows with the same number of columns.",
            )
            return
        start_row = self.table.currentRow()
        start_column = self.table.currentColumn()
        if start_row < 0 or start_column < 0:
            return
        if (
            start_row + len(values) > self.table.rowCount()
            or start_column + width > self.table.columnCount()
        ):
            self._clipboard_warning(
                "貼り付け範囲が表の外にはみ出します。",
                "The paste range extends beyond the table.",
            )
            return

        proposed = []
        shared_values = {}
        try:
            for row_offset, row_values in enumerate(values):
                row = start_row + row_offset
                dataset = self.project.datasets[row]
                for column_offset, text_value in enumerate(row_values):
                    column = start_column + column_offset
                    if column not in self.EDITABLE_COLUMNS:
                        self._cell_error(
                            row,
                            column,
                            "この列には貼り付けできません。",
                            "This column is not editable.",
                        )
                    parsed = self._validate_cell_value(row, column, text_value)
                    if column in self.RUN_SHARED_COLUMNS:
                        key = (dataset.run_id, column)
                        if key in shared_values and shared_values[key] != parsed:
                            self._cell_error(
                                row,
                                column,
                                "同じRunの同じ項目に異なる値は貼り付けできません。",
                                "Conflicting values cannot be pasted into the same Run field.",
                            )
                        shared_values[key] = parsed
                    proposed.append((row, column, str(text_value).strip()))
        except BatchCellError as exc:
            self._show_cell_error(exc)
            return

        for row, column, text_value in proposed:
            self.table.item(row, column).setText(text_value)
        self.table.clearSelection()
        self.table.setCurrentCell(start_row, start_column)
        for row, column, _text_value in proposed:
            self.table.item(row, column).setSelected(True)

    def _show_cell_error(self, error: BatchCellError):
        item = self.table.item(error.row, error.column)
        if item is not None:
            self._syncing_table = True
            try:
                item.setBackground(QtGui.QBrush(QtGui.QColor("#ffd9d9")))
            finally:
                self._syncing_table = False
            self.table.setCurrentCell(error.row, error.column)
            self.table.scrollToItem(item)
        header = self.table.horizontalHeaderItem(error.column)
        field = header.text() if header is not None else str(error.column + 1)
        message = (
            "%d行目「%s」: %s" % (error.row + 1, field, error.reason)
            if self.language == "ja"
            else "Row %d, %s: %s" % (error.row + 1, field, error.reason)
        )
        QtWidgets.QMessageBox.warning(
            self,
            "入力エラー" if self.language == "ja" else "Invalid value",
            message,
        )
        if item is not None:
            self.table.editItem(item)

    def _apply_row_to_dataset(self, row: int, dataset: Dataset):
        axis, numeric = self._parse_row(row)
        old_label = dataset.label
        dataset.label = self._cell_text(row, 1) or dataset.original_filename
        if not dataset.short_label or dataset.short_label == old_label:
            dataset.short_label = dataset.label
        dataset.measurement.group = self._cell_text(row, 2)
        dataset.y_axis = axis
        for field, value in numeric.items():
            setattr(dataset.measurement, field, value)
        dataset.measurement.column_name = self._cell_text(
            row, self.FIELD_COLUMNS["column_name"]
        )
        dataset.measurement.analyte_name = self._cell_text(
            row, self.FIELD_COLUMNS["analyte_name"]
        )
        return axis, numeric

    def _edit_selected_details(self, row=None):
        if isinstance(row, bool) or row is None:
            row = self.table.currentRow()
        if not (0 <= int(row) < len(self.project.datasets)):
            return
        row = int(row)
        original = self.project.datasets[row]
        working = deepcopy(self.detail_overrides.get(original.id, original))
        try:
            self._apply_row_to_dataset(row, working)
        except BatchCellError as exc:
            self._show_cell_error(exc)
            return
        dialog = MetadataDialog(working, self.language, self)
        if dialog_exec(dialog):
            self.detail_overrides[original.id] = working
            for related_row, dataset in enumerate(self.project.datasets):
                if dataset.run_id == original.run_id:
                    self.table.item(related_row, 1).setText(working.label)
            self._refresh_row_from_dataset(row, working)
            self.table.selectRow(row)

    @staticmethod
    def _apply_gradient_payload(dataset: Dataset, name: str, payload):
        dataset.measurement.gradient = [
            GradientPoint(**point)
            for point in deepcopy(payload.get("gradient", []))
        ]
        solvents = payload.get("solvents", {}) or {}
        dataset.measurement.solvents = {
            line: Solvent(**deepcopy(solvents.get(line, {}) or {}))
            for line in "ABCD"
        }
        dataset.gradient_preset_name = name

    def _edit_selected_gradient(self, row=None):
        if isinstance(row, bool) or row is None:
            row = self.table.currentRow()
        if not (0 <= int(row) < len(self.project.datasets)):
            return
        row = int(row)
        original = self.project.datasets[row]
        working = deepcopy(self.detail_overrides.get(original.id, original))
        try:
            self._apply_row_to_dataset(row, working)
        except BatchCellError as exc:
            self._show_cell_error(exc)
            return
        assigned_name = self.gradient_assignments.get(original.run_id)
        if assigned_name:
            self._apply_gradient_payload(
                working,
                assigned_name,
                self.gradient_presets[assigned_name],
            )
        dialog = GradientDialog(
            working,
            self.language,
            self,
            presets=self.gradient_presets,
            preset_metadata=self.preset_metadata,
        )
        if not dialog_exec(dialog):
            return
        self.gradient_presets = dialog.presets
        self.preset_metadata = dialog.preset_metadata
        self._refresh_gradient_presets(working.effective_gradient_preset_name())
        self.detail_overrides[original.id] = working
        self.gradient_assignments.pop(original.run_id, None)
        gradient_name = working.effective_gradient_preset_name()
        for related_row, dataset in enumerate(self.project.datasets):
            if dataset.run_id == original.run_id:
                self.table.item(related_row, self.GRADIENT_COLUMN).setText(
                    gradient_name
                )
        self.table.setCurrentCell(row, self.GRADIENT_COLUMN)

    def _refresh_presets(self, selected_name: str = ""):
        selected_name = selected_name or self.preset_combo.currentText()
        sort_by = self.condition_preset_sort.currentData() or "created"
        names = stable_preset_names(
            self.presets, self.preset_metadata, "conditions", sort_by
        )
        names = filter_preset_names(
            names, self.condition_preset_filter.text()
        )
        self.preset_combo.clear()
        self.preset_combo.addItems(names)
        if selected_name:
            index = self.preset_combo.findText(selected_name)
            if index >= 0:
                self.preset_combo.setCurrentIndex(index)

    def _refresh_gradient_presets(self, selected_name: str = ""):
        selected_name = selected_name or self.gradient_preset_combo.currentText()
        sort_by = self.gradient_preset_sort.currentData() or "created"
        names = stable_preset_names(
            self.gradient_presets,
            self.preset_metadata,
            "gradients",
            sort_by,
        )
        names = filter_preset_names(
            names, self.gradient_preset_filter.text()
        )
        self.gradient_preset_combo.clear()
        self.gradient_preset_combo.addItems(names)
        if selected_name:
            index = self.gradient_preset_combo.findText(selected_name)
            if index >= 0:
                self.gradient_preset_combo.setCurrentIndex(index)

    def _preview_condition_preset(self):
        name = self.preset_combo.currentText()
        preset = self.presets.get(name)
        row = self.table.currentRow()
        if preset is None or not (0 <= row < len(self.project.datasets)):
            return
        current = {
            field: self._cell_text(row, column)
            for field, column in self.FIELD_COLUMNS.items()
        }
        title = (
            "条件プリセットの内容・差分: {0}".format(name)
            if self.language == "ja"
            else "Condition preset preview / diff: {0}".format(name)
        )
        dialog_exec(
            PresetPreviewDialog(
                title,
                condition_preset_preview_rows(current, preset, self.language),
                self.language,
                self,
            )
        )

    def _preview_gradient_preset(self):
        name = self.gradient_preset_combo.currentText()
        preset = self.gradient_presets.get(name)
        row = self.table.currentRow()
        if preset is None or not (0 <= row < len(self.project.datasets)):
            return
        original = self.project.datasets[row]
        working = deepcopy(self.detail_overrides.get(original.id, original))
        try:
            self._apply_row_to_dataset(row, working)
        except BatchCellError as exc:
            self._show_cell_error(exc)
            return
        assigned_name = self.gradient_assignments.get(original.run_id)
        if assigned_name:
            self._apply_gradient_payload(
                working,
                assigned_name,
                self.gradient_presets[assigned_name],
            )
        current = {
            "gradient": [asdict(point) for point in working.measurement.gradient],
            "solvents": {
                line: asdict(solvent)
                for line, solvent in working.measurement.solvents.items()
            },
        }
        title = (
            "グラジエントプリセットの内容・差分: {0}".format(name)
            if self.language == "ja"
            else "Gradient preset preview / diff: {0}".format(name)
        )
        dialog_exec(
            PresetPreviewDialog(
                title,
                gradient_preset_preview_rows(current, preset, self.language),
                self.language,
                self,
            )
        )

    def _save_preset(self):
        dataset = self._selected_dataset()
        if dataset is None:
            return
        initial_name = (
            self.loaded_condition_preset_name or self.preset_combo.currentText()
        )
        name, accepted = QtWidgets.QInputDialog.getText(
            self,
            "条件プリセット" if self.language == "ja" else "Condition preset",
            "プリセット名" if self.language == "ja" else "Preset name",
            QtWidgets.QLineEdit.Normal,
            initial_name,
        )
        name = name.strip()
        if not accepted or not name:
            return
        if initial_name and initial_name != name:
            self.presets.pop(initial_name, None)
        self.presets[name] = {
            field: deepcopy(getattr(dataset.measurement, field)) for field in PRESET_FIELDS
        }
        record_preset_saved(
            self.preset_metadata, "conditions", initial_name, name
        )
        self.loaded_condition_preset_name = name
        self._refresh_presets(name)

    def _delete_preset(self):
        name = self.preset_combo.currentText()
        if name in self.presets:
            del self.presets[name]
            record_preset_deleted(self.preset_metadata, "conditions", name)
            if self.loaded_condition_preset_name == name:
                self.loaded_condition_preset_name = ""
            self._refresh_presets()

    def _checked_rows(self):
        return [
            row
            for row in range(self.table.rowCount())
            if self.table.item(row, 0) and self.table.item(row, 0).checkState() == CHECKED
        ]

    def _set_all_checks(self, checked: bool):
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(CHECKED if checked else UNCHECKED)

    def _check_same_group(self):
        selected = self._selected_dataset()
        if selected is None:
            return
        selected_row = next(
            (row for row, dataset in enumerate(self.project.datasets) if dataset.id == selected.id),
            -1,
        )
        selected_group = self._cell_text(selected_row, 2) if selected_row >= 0 else ""
        for row, dataset in enumerate(self.project.datasets):
            matches = self._cell_text(row, 2) == selected_group
            if not selected_group:
                matches = dataset.id == selected.id
            self.table.item(row, 0).setCheckState(CHECKED if matches else UNCHECKED)

    def _apply_preset(self):
        name = self.preset_combo.currentText()
        preset = self.presets.get(name)
        if not preset:
            return
        checked_rows = self._checked_rows()
        for row in checked_rows:
            for field, column in self.FIELD_COLUMNS.items():
                value = preset.get(field)
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                text = value if isinstance(value, str) else format_optional(value)
                self.table.item(row, column).setText(text)
        self.loaded_condition_preset_name = name
        if checked_rows:
            record_preset_used(self.preset_metadata, "conditions", name)
            self._refresh_presets(name)

    def _apply_gradient_preset(self):
        name = self.gradient_preset_combo.currentText()
        if name not in self.gradient_presets:
            return
        checked_rows = self._checked_rows()
        for row in checked_rows:
            dataset = self.project.datasets[row]
            self.gradient_assignments[dataset.run_id] = name
            for related_row, related in enumerate(self.project.datasets):
                if related.run_id == dataset.run_id:
                    self.table.item(related_row, self.GRADIENT_COLUMN).setText(name)
        if checked_rows:
            record_preset_used(self.preset_metadata, "gradients", name)
            self._refresh_gradient_presets(name)

    def _cell_text(self, row: int, column: int) -> str:
        item = self.table.item(row, column)
        return item.text().strip() if item else ""

    def _accept(self):
        parsed_rows = []
        try:
            for row, dataset in enumerate(self.project.datasets):
                axis, numeric = self._parse_row(row)
                parsed_rows.append((dataset, axis, numeric))
        except BatchCellError as exc:
            self._show_cell_error(exc)
            return

        for row, (dataset, axis, numeric) in enumerate(parsed_rows):
            override = self.detail_overrides.get(dataset.id)
            if override is not None:
                self.project.replace_dataset_measurement(
                    dataset, deepcopy(override.measurement)
                )
                dataset.short_label = override.short_label
                dataset.gradient_preset_name = override.gradient_preset_name
            old_label = dataset.label
            dataset.label = self._cell_text(row, 1) or dataset.original_filename
            if not dataset.short_label or dataset.short_label == old_label:
                dataset.short_label = dataset.label
            dataset.measurement.group = self._cell_text(row, 2)
            dataset.y_axis = axis
            for field, value in numeric.items():
                setattr(dataset.measurement, field, value)
            dataset.measurement.column_name = self._cell_text(row, self.FIELD_COLUMNS["column_name"])
            dataset.measurement.analyte_name = self._cell_text(row, self.FIELD_COLUMNS["analyte_name"])
            gradient_name = self.gradient_assignments.get(dataset.run_id)
            if gradient_name:
                self._apply_gradient_payload(
                    dataset,
                    gradient_name,
                    self.gradient_presets[gradient_name],
                )
        self.project.condition_presets = self.presets
        self.project.gradient_presets = self.gradient_presets
        self.accept()


class PreferencesDialog(QtWidgets.QDialog):
    """Application path and automatic peak-detection preferences."""

    def __init__(
        self,
        method: AnalysisMethod,
        import_directory: str = "",
        language: str = "ja",
        parent=None,
        save_directory: str = "",
        database_path: str = "",
        render_quality: str = HIGH_QUALITY,
        automatic_update_check: bool = True,
    ):
        super().__init__(parent)
        self.language = language
        self.setWindowTitle("環境設定" if language == "ja" else "Preferences")
        self.resize(700, 720)
        root = QtWidgets.QVBoxLayout(self)

        path_group = QtWidgets.QGroupBox(
            "既定フォルダ" if language == "ja" else "Default folders"
        )
        path_form = QtWidgets.QFormLayout(path_group)
        import_path_row = QtWidgets.QHBoxLayout()
        self.import_directory = QtWidgets.QLineEdit(import_directory)
        self.import_browse_button = QtWidgets.QPushButton(
            "参照…" if language == "ja" else "Browse…"
        )
        import_path_row.addWidget(self.import_directory, 1)
        import_path_row.addWidget(self.import_browse_button)
        path_form.addRow(
            "読み込み開始フォルダ" if language == "ja" else "Default import folder",
            import_path_row,
        )
        save_path_row = QtWidgets.QHBoxLayout()
        self.save_directory = QtWidgets.QLineEdit(save_directory)
        self.save_browse_button = QtWidgets.QPushButton(
            "参照…" if language == "ja" else "Browse…"
        )
        save_path_row.addWidget(self.save_directory, 1)
        save_path_row.addWidget(self.save_browse_button)
        path_form.addRow(
            "データ保存先" if language == "ja" else "Default save folder",
            save_path_row,
        )
        database_path_row = QtWidgets.QHBoxLayout()
        self.database_path = QtWidgets.QLineEdit(database_path)
        self.database_browse_button = QtWidgets.QPushButton(
            "参照…" if language == "ja" else "Browse…"
        )
        database_path_row.addWidget(self.database_path, 1)
        database_path_row.addWidget(self.database_browse_button)
        path_form.addRow(
            "研究室共通データベース"
            if language == "ja"
            else "Shared lab database",
            database_path_row,
        )
        path_note = QtWidgets.QLabel(
            "空欄の場合、読み込みは最後に使用したフォルダ、保存は最後に保存したフォルダを使用します。"
            " データベースは研究室の共有フォルダ上にある同じSQLiteファイルを全員が指定してください。"
            if language == "ja"
            else "When blank, the most recently used import or save folder is used. "
            "Point every lab computer to the same SQLite file in a shared folder."
        )
        path_note.setWordWrap(True)
        path_form.addRow(path_note)
        root.addWidget(path_group)

        rendering_group = QtWidgets.QGroupBox(
            "描画品質" if language == "ja" else "Screen rendering quality"
        )
        rendering_layout = QtWidgets.QVBoxLayout(rendering_group)
        self.high_quality_radio = QtWidgets.QRadioButton(
            "高品質" if language == "ja" else "High quality"
        )
        self.lightweight_radio = QtWidgets.QRadioButton(
            "軽量" if language == "ja" else "Lightweight"
        )
        rendering_layout.addWidget(self.high_quality_radio)
        rendering_layout.addWidget(self.lightweight_radio)
        rendering_note = QtWidgets.QLabel(
            "軽量は画面表示だけをピーク保持型で間引きます。積分、保持時間、FWHM、%Area、"
            "CSV、PNG、SVG、PDF、A4レポートは常に元データを使用します。"
            if language == "ja"
            else "Lightweight mode decimates only the interactive screen while preserving peaks. "
            "Integration, retention time, FWHM, %Area, CSV, PNG, SVG, PDF and reports always use full data."
        )
        rendering_note.setWordWrap(True)
        rendering_layout.addWidget(rendering_note)
        selected_quality = normalize_render_quality(render_quality)
        self.high_quality_radio.setChecked(selected_quality == HIGH_QUALITY)
        self.lightweight_radio.setChecked(selected_quality == LIGHTWEIGHT)
        root.addWidget(rendering_group)

        update_group = QtWidgets.QGroupBox(
            "ソフトウェア更新" if language == "ja" else "Software updates"
        )
        update_layout = QtWidgets.QVBoxLayout(update_group)
        self.automatic_update_checkbox = QtWidgets.QCheckBox(
            "起動後にStable版の更新を自動確認する"
            if language == "ja"
            else "Automatically check for Stable updates after startup"
        )
        self.automatic_update_checkbox.setChecked(bool(automatic_update_check))
        update_layout.addWidget(self.automatic_update_checkbox)
        update_note = QtWidgets.QLabel(
            "公開GitHub Releasesの情報だけを確認します。ダウンロードやインストールは自動実行しません。"
            if language == "ja"
            else "Only public GitHub Release metadata is checked. Downloads and installation never start automatically."
        )
        update_note.setWordWrap(True)
        update_layout.addWidget(update_note)
        root.addWidget(update_group)

        detection_group = QtWidgets.QGroupBox(
            "自動ピーク検出" if language == "ja" else "Automatic peak detection"
        )
        form = QtWidgets.QFormLayout(detection_group)

        def double_spin(value, minimum, maximum, decimals=3, step=0.1):
            widget = QtWidgets.QDoubleSpinBox()
            widget.setRange(minimum, maximum)
            widget.setDecimals(decimals)
            widget.setSingleStep(step)
            widget.setValue(float(value))
            return widget

        self.snr = double_spin(method.auto_peak_snr_threshold, 0.0, 1000.0, 2, 0.5)
        self.min_prominence = double_spin(
            method.auto_peak_min_prominence_uv, 0.0, 1.0e12, 3, 10.0
        )
        self.smoothing = double_spin(method.auto_peak_smoothing_min, 0.0, 100.0, 4, 0.01)
        self.min_width = double_spin(method.auto_peak_min_width_min, 0.0, 100.0, 4, 0.01)
        self.max_width = double_spin(method.auto_peak_max_width_min, 0.0001, 1000.0, 4, 0.1)
        self.min_distance = double_spin(method.auto_peak_min_distance_min, 0.0, 100.0, 4, 0.01)
        self.boundary_percent = double_spin(
            method.auto_peak_boundary_percent, 0.0, 50.0, 2, 0.5
        )
        self.max_count = QtWidgets.QSpinBox()
        self.max_count.setRange(1, 10000)
        self.max_count.setValue(int(method.auto_peak_max_count))

        labels = (
            ("S/Nしきい値" if language == "ja" else "S/N threshold", self.snr),
            ("最小プロミネンス (µV)" if language == "ja" else "Minimum prominence (µV)", self.min_prominence),
            ("平滑化幅 (min)" if language == "ja" else "Smoothing width (min)", self.smoothing),
            ("最小ピーク幅 (min)" if language == "ja" else "Minimum peak width (min)", self.min_width),
            ("最大ピーク幅 (min)" if language == "ja" else "Maximum peak width (min)", self.max_width),
            ("最小ピーク間隔 (min)" if language == "ja" else "Minimum peak distance (min)", self.min_distance),
            ("積分境界 (%高さ)" if language == "ja" else "Integration boundary (% height)", self.boundary_percent),
            ("最大検出数" if language == "ja" else "Maximum candidates", self.max_count),
        )
        for label, widget in labels:
            form.addRow(label, widget)
        if language == "ja":
            tooltips = (
                (self.snr, "局所プロミネンスを、隣接点差から推定したノイズ標準偏差で割った下限です。"),
                (self.min_prominence, "周囲の谷からピーク頂点までに必要な最小高さです。S/N条件との厳しい方を採用します。"),
                (self.smoothing, "検出判定だけに使う平滑化幅です。元データと積分値は変更しません。"),
                (self.min_width, "半値幅がこれより狭い候補を除外します。"),
                (self.max_width, "探索範囲と許容する半値幅の上限です。"),
                (self.min_distance, "これより近い候補がある場合、プロミネンスの大きい方を優先します。"),
                (self.boundary_percent, "局所ベースラインからピーク高さの何%まで下がった点を積分境界とするかを指定します。"),
                (self.max_count, "誤検出時に候補数が際限なく増えるのを防ぐ上限です。"),
            )
        else:
            tooltips = (
                (self.snr, "Minimum local prominence divided by robust noise sigma estimated from adjacent differences."),
                (self.min_prominence, "Minimum apex height above the surrounding valleys; the stricter of this and S/N is used."),
                (self.smoothing, "Smoothing used only for detection. Raw data and integrated values are unchanged."),
                (self.min_width, "Reject candidates with a narrower full width at half height."),
                (self.max_width, "Upper limit for the search radius and full width at half height."),
                (self.min_distance, "When candidates are closer than this, the more prominent one is preferred."),
                (self.boundary_percent, "Integration boundaries are placed where the signal falls to this percentage above the local baseline."),
                (self.max_count, "Safety cap that prevents an unbounded candidate list after a poor detection setting."),
            )
        for widget, tooltip in tooltips:
            widget.setToolTip(tooltip)
        detection_note = QtWidgets.QLabel(
            "検出結果は確定値ではなく、編集可能な積分候補として追加されます。"
            if language == "ja"
            else "Detected peaks are added as editable integration candidates, not final results."
        )
        detection_note.setWordWrap(True)
        form.addRow(detection_note)
        root.addWidget(detection_group, 1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.import_browse_button.clicked.connect(self._browse_import)
        self.save_browse_button.clicked.connect(self._browse_save)
        self.database_browse_button.clicked.connect(self._browse_database)
        self.import_directory_value = import_directory
        self.save_directory_value = save_directory
        self.database_path_value = database_path
        self.render_quality_value = selected_quality
        self.automatic_update_check_value = bool(automatic_update_check)
        self.detection_values = {}

    def _browse_import(self):
        current = self.import_directory.text().strip()
        selected = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "読み込み開始フォルダ" if self.language == "ja" else "Default import folder",
            current,
        )
        if selected:
            self.import_directory.setText(selected)

    def _browse_save(self):
        current = self.save_directory.text().strip()
        selected = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "データ保存先" if self.language == "ja" else "Default save folder",
            current,
        )
        if selected:
            self.save_directory.setText(selected)

    def _browse_database(self):
        current = self.database_path.text().strip()
        if not current:
            current = str(Path(self.save_directory.text().strip() or ".") / "HPLC_Lab_Database.sqlite3")
        selected, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "研究室共通データベース"
            if self.language == "ja"
            else "Shared lab database",
            current,
            "SQLite database (*.sqlite3 *.sqlite *.db)",
        )
        if selected:
            if not Path(selected).suffix:
                selected += ".sqlite3"
            self.database_path.setText(selected)

    def _accept(self):
        directory = self.import_directory.text().strip()
        save_directory = self.save_directory.text().strip()
        database_path = self.database_path.text().strip()
        invalid_directory = next(
            (
                candidate
                for candidate in (directory, save_directory)
                if candidate and not Path(candidate).is_dir()
            ),
            "",
        )
        if invalid_directory:
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid folder",
                "指定したフォルダが見つかりません。"
                if self.language == "ja"
                else "The selected folder does not exist.",
            )
            return
        if database_path:
            database_parent = Path(database_path).expanduser().parent
            if not database_parent.is_dir():
                QtWidgets.QMessageBox.warning(
                    self,
                    "Invalid database path",
                    "データベースの保存先フォルダが見つかりません。"
                    if self.language == "ja"
                    else "The database folder does not exist.",
                )
                return
            if not Path(database_path).suffix:
                database_path += ".sqlite3"
        if self.max_width.value() < self.min_width.value():
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid value",
                "最大ピーク幅は最小ピーク幅以上にしてください。"
                if self.language == "ja"
                else "Maximum peak width must not be smaller than minimum peak width.",
            )
            return
        self.import_directory_value = directory
        self.save_directory_value = save_directory
        self.database_path_value = database_path
        self.render_quality_value = (
            LIGHTWEIGHT if self.lightweight_radio.isChecked() else HIGH_QUALITY
        )
        self.automatic_update_check_value = self.automatic_update_checkbox.isChecked()
        self.detection_values = {
            "auto_peak_snr_threshold": self.snr.value(),
            "auto_peak_min_prominence_uv": self.min_prominence.value(),
            "auto_peak_smoothing_min": self.smoothing.value(),
            "auto_peak_min_width_min": self.min_width.value(),
            "auto_peak_max_width_min": self.max_width.value(),
            "auto_peak_min_distance_min": self.min_distance.value(),
            "auto_peak_boundary_percent": self.boundary_percent.value(),
            "auto_peak_max_count": self.max_count.value(),
        }
        self.accept()


class ProjectNamingDialog(QtWidgets.QDialog):
    """Collect the five standardized filename components before Save As."""

    def __init__(self, parts: Dict[str, str], language: str = "ja", parent=None):
        super().__init__(parent)
        self.language = language
        self.setWindowTitle(
            "プロジェクト保存名" if language == "ja" else "Project filename"
        )
        self.resize(720, 360)
        root = QtWidgets.QVBoxLayout(self)
        explanation = QtWidgets.QLabel(
            "命名形式: YYYYMMDD_Title_Column_Condition_Author"
            if language == "ja"
            else "Naming pattern: YYYYMMDD_Title_Column_Condition_Author"
        )
        explanation.setWordWrap(True)
        root.addWidget(explanation)

        form = QtWidgets.QFormLayout()
        self.date_edit = QtWidgets.QLineEdit(parts.get("date", ""))
        self.date_edit.setMaxLength(8)
        self.title_edit = QtWidgets.QLineEdit(parts.get("title", ""))
        self.column_edit = QtWidgets.QLineEdit(parts.get("column", ""))
        self.condition_edit = QtWidgets.QLineEdit(parts.get("condition", ""))
        self.author_edit = QtWidgets.QLineEdit(parts.get("author", ""))
        form.addRow("日付 (YYYYMMDD)" if language == "ja" else "Date (YYYYMMDD)", self.date_edit)
        form.addRow("タイトル" if language == "ja" else "Title", self.title_edit)
        form.addRow("カラム" if language == "ja" else "Column", self.column_edit)
        form.addRow("測定条件" if language == "ja" else "Condition", self.condition_edit)
        form.addRow("測定者" if language == "ja" else "Author", self.author_edit)
        root.addLayout(form)

        preview_label = QtWidgets.QLabel(
            "提案ファイル名" if language == "ja" else "Suggested filename"
        )
        self.preview = QtWidgets.QLineEdit()
        self.preview.setReadOnly(True)
        root.addWidget(preview_label)
        root.addWidget(self.preview)
        for editor in (
            self.date_edit,
            self.title_edit,
            self.column_edit,
            self.condition_edit,
            self.author_edit,
        ):
            editor.textChanged.connect(self._update_preview)
        self._update_preview()

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def name_parts(self) -> Dict[str, str]:
        return {
            "date": self.date_edit.text().strip(),
            "title": self.title_edit.text().strip(),
            "column": self.column_edit.text().strip(),
            "condition": self.condition_edit.text().strip(),
            "author": self.author_edit.text().strip(),
        }

    def _update_preview(self, *_args):
        self.preview.setText(build_project_filename(self.name_parts()))

    def _accept(self):
        if not normalize_analysis_date(self.date_edit.text()):
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid date",
                "日付はYYYYMMDD形式で入力してください。"
                if self.language == "ja"
                else "Enter the date as YYYYMMDD.",
            )
            return
        self.accept()


class LabDatabaseDialog(QtWidgets.QDialog):
    """Read-only administrator view of the shared SQLite database."""

    def __init__(self, database_path: str, language: str = "ja", parent=None):
        super().__init__(parent)
        self.database_path = database_path
        self.language = language
        self.tables = {}
        self.setWindowTitle(
            "研究室HPLCデータベース" if language == "ja" else "Lab HPLC database"
        )
        self.resize(1250, 720)
        root = QtWidgets.QVBoxLayout(self)
        path_label = QtWidgets.QLabel(database_path)
        path_label.setTextInteractionFlags(path_label.textInteractionFlags())
        path_label.setWordWrap(True)
        root.addWidget(path_label)

        filter_row = QtWidgets.QHBoxLayout()
        filter_row.addWidget(QtWidgets.QLabel("検索" if language == "ja" else "Filter"))
        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_edit.setPlaceholderText(
            "全列を絞り込み" if language == "ja" else "Filter across all columns"
        )
        filter_row.addWidget(self.filter_edit, 1)
        root.addLayout(filter_row)

        self.tabs = QtWidgets.QTabWidget()
        titles = database_section_titles()
        localized = {
            "projects": "プロジェクト",
            "datasets": "クロマトグラム・条件",
            "gradients": "グラジエント",
        }
        for key in ("projects", "datasets", "gradients"):
            table = QtWidgets.QTableWidget()
            table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            table.setAlternatingRowColors(True)
            table.setSortingEnabled(True)
            table.verticalHeader().setVisible(False)
            self.tables[key] = table
            self.tabs.addTab(table, localized[key] if language == "ja" else titles[key])
        root.addWidget(self.tabs, 1)

        buttons = QtWidgets.QHBoxLayout()
        self.refresh_button = QtWidgets.QPushButton(
            "再読み込み" if language == "ja" else "Refresh"
        )
        self.export_button = QtWidgets.QPushButton(
            "一覧CSVを書き出す…" if language == "ja" else "Export tables to CSV…"
        )
        self.close_button = QtWidgets.QPushButton("閉じる" if language == "ja" else "Close")
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.export_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        root.addLayout(buttons)
        self.refresh_button.clicked.connect(self.reload)
        self.export_button.clicked.connect(self.export_csvs)
        self.close_button.clicked.connect(self.accept)
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.reload()

    def reload(self):
        try:
            sections = database_sections(self.database_path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Database error", str(exc))
            return
        for key, (columns, rows) in sections.items():
            table = self.tables[key]
            table.setSortingEnabled(False)
            table.clear()
            table.setColumnCount(len(columns))
            table.setHorizontalHeaderLabels(
                [self._header_text(column) for column in columns]
            )
            table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                for column_index, value in enumerate(row):
                    item = QtWidgets.QTableWidgetItem("" if value is None else str(value))
                    item.setFlags(item.flags() & ~ITEM_IS_EDITABLE)
                    table.setItem(row_index, column_index, item)
            table.resizeColumnsToContents()
            if key == "gradients":
                table.resizeRowsToContents()
                if "program" in columns:
                    table.setColumnWidth(columns.index("program"), 430)
            table.horizontalHeader().setStretchLastSection(True)
            table.setSortingEnabled(True)
        self._apply_filter()

    def _header_text(self, column: str) -> str:
        if self.language != "ja":
            return column
        labels = {
            "analysis_date": "測定日",
            "title": "タイトル",
            "project_title": "プロジェクト",
            "column_name": "カラム",
            "condition_name": "測定条件",
            "author": "測定者",
            "dataset_count": "クロマトグラム数",
            "total_peak_count": "総ピーク数",
            "result_summary": "結果概要",
            "project_path": "プロジェクトファイル",
            "project_modified_at": "更新日時",
            "synced_at": "DB同期日時",
            "chromatogram_no": "クロマトグラム#",
            "label": "ラベル",
            "wavelength_nm": "波長 (nm)",
            "sample_name": "サンプル名",
            "sample_id": "サンプルID",
            "analyte_name": "分析対象物",
            "sample_group": "グループ",
            "replicate": "反復",
            "method_name": "メソッド名",
            "instrument_name": "装置",
            "acquisition_datetime": "測定日時",
            "flow_rate_ml_min": "流量 (mL/min)",
            "column_temperature_c": "カラム温度 (°C)",
            "injection_volume_ul": "注入量 (µL)",
            "gradient_preset_name": "グラジエント名",
            "peak_count": "ピーク数",
            "major_peak_retention_min": "主ピークRT (min)",
            "major_peak_area_uv_min": "主ピーク面積 (µV·min)",
            "total_area_uv_min": "総面積 (µV·min)",
            "total_amount_nmol": "総量 (nmol)",
            "total_amount_ug": "総量 (µg)",
            "comments": "コメント・結果",
            "original_path": "元ファイル",
            "point_no": "点#",
            "time_min": "時間 (min)",
            "a_pct": "%A",
            "b_pct": "%B",
            "c_pct": "%C",
            "d_pct": "%D",
            "peak_no": "ピーク#",
            "retention_time_min": "保持時間 (min)",
            "start_min": "開始 (min)",
            "end_min": "終了 (min)",
            "raw_height_uv": "高さ (µV)",
            "raw_area_uv_min": "面積 (µV·min)",
            "area_mau_min": "面積 (mAU·min)",
            "raw_area_uv_sec": "面積 (µV·sec)",
            "area_mau_sec": "面積 (mAU·sec)",
            "area_percent": "%Area",
            "fwhm_min": "FWHM (min)",
            "gradient_b_pct": "ピーク時%B",
            "amount_nmol": "量 (nmol)",
            "amount_ug": "量 (µg)",
            "integration_source": "積分方法",
            "project_id": "プロジェクトID",
            "dataset_id": "データID",
            "latest_analysis_date": "最終使用日",
            "gradient_names": "グラジエント名",
            "program": "グラジエントプログラム",
            "solvent_a": "A液",
            "solvent_b": "B液",
            "solvent_c": "C液",
            "solvent_d": "D液",
            "columns": "使用カラム",
            "projects": "使用プロジェクト",
            "authors": "測定者",
            "usage_count": "使用数",
        }
        return labels.get(column, column)

    def _apply_filter(self, *_args):
        query = self.filter_edit.text().strip().casefold()
        for table in self.tables.values():
            for row in range(table.rowCount()):
                visible = not query or any(
                    query in (table.item(row, column).text().casefold() if table.item(row, column) else "")
                    for column in range(table.columnCount())
                )
                table.setRowHidden(row, not visible)

    def export_csvs(self):
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "CSV出力先" if self.language == "ja" else "CSV export folder",
            str(Path(self.database_path).parent),
        )
        if not directory:
            return
        try:
            paths = export_database_csvs(self.database_path, directory)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Database error", str(exc))
            return
        QtWidgets.QMessageBox.information(
            self,
            "HPLC Analyzer",
            ("%d個のCSVを保存しました。" % len(paths))
            if self.language == "ja"
            else ("Exported %d CSV files." % len(paths)),
        )


class QuantitationHelpDialog(QtWidgets.QDialog):
    def __init__(self, language: str = "ja", parent=None):
        super().__init__(parent)
        self.setWindowTitle("定量方法・計算式" if language == "ja" else "Quantitation method and equations")
        self.resize(760, 640)
        root = QtWidgets.QVBoxLayout(self)
        browser = QtWidgets.QTextBrowser()
        if language == "ja":
            html = """
            <h2>定量方法と計算式</h2>
            <h3>1. µVから吸光度への換算</h3>
            <p>ASCIIのRaw Intensityを <i>I</i> (µV)、検出器設定を <i>R</i> (AU/V) とすると、</p>
            <p><b>A (mAU) = I (µV) × R (AU/V) × 10<sup>−3</sup></b></p>
            <p><b>A (AU) = I (µV) × R (AU/V) × 10<sup>−6</sup></b></p>

            <h3>2. ピーク面積</h3>
            <p>指定したベースラインを差し引いた吸光度を、保持時間について台形積分します。</p>
            <p><b>S = ∫ A(t) dt</b>　[単位: mAU·sec]</p>
            <p>時間 <i>t</i> は秒として積分します。面積が負になる区間も符号付きで積分します。%Areaは、各ピークの正のµV·sec面積を合計して算出します。</p>

            <h3>3. 物質量</h3>
            <p>Beer–Lambertの法則 A = εlc と流量を用います。流量が一定の場合：</p>
            <p><b>n (nmol) = S (mAU·sec) × F (mL/min) × 10<sup>3</sup> / [60 × ε (M<sup>−1</sup>cm<sup>−1</sup>) × l (cm)]</b></p>
            <p>グラジエント表に時刻ごとの流量がすべて入力されている場合は、</p>
            <p><b>n (nmol) = 10<sup>3</sup> × ∫[A(t) × F(t) / 60]dt / (εl)</b></p>
            <p>として、秒単位の時間 <i>t</i> とmL/min単位の流量変化を反映します。</p>

            <h3>4. 質量</h3>
            <p><b>m (µg) = n (nmol) × 分子量 (g/mol) / 1000</b></p>

            <h3>使用条件</h3>
            <ul>
              <li>測定波長が214 ± 0.5 nmならε214、280 ± 0.5 nmならε280を使用します。</li>
              <li>AU/V、流量、セル光路長、対応するεが正の値で入力されている場合のみ物質量を表示します。</li>
              <li>セル光路長の初期値は1 cmです。</li>
              <li>算出量は、指定した分析対象物がピーク吸光をすべて占めると仮定した値です。共溶出、不純物、散乱、ベースライン設定の影響を受けます。</li>
            </ul>
            """
        else:
            html = """
            <h2>Quantitation method and equations</h2>
            <h3>1. Converting µV to absorbance</h3>
            <p>For raw intensity <i>I</i> (µV) and detector setting <i>R</i> (AU/V):</p>
            <p><b>A (mAU) = I (µV) × R (AU/V) × 10<sup>−3</sup></b></p>
            <p><b>A (AU) = I (µV) × R (AU/V) × 10<sup>−6</sup></b></p>
            <h3>2. Peak area</h3>
            <p><b>S = ∫ A(t) dt</b> [mAU·sec], after subtracting the selected baseline and integrating time in seconds.</p>
            <h3>3. Amount</h3>
            <p>Using Beer–Lambert law A = εlc and constant flow:</p>
            <p><b>n (nmol) = S × F × 10<sup>3</sup> / (60 εl)</b>, where F is in mL/min.</p>
            <p>If every gradient row contains a flow value, the program instead uses <b>n = 10<sup>3</sup> × ∫[A(t)F(t)/60]dt/(εl)</b>, with time in seconds.</p>
            <h3>4. Mass</h3>
            <p><b>m (µg) = n (nmol) × molecular weight (g/mol) / 1000</b></p>
            <p>ε214 is used at 214 ± 0.5 nm and ε280 at 280 ± 0.5 nm. AU/V, flow, path length and the matching ε must be positive. The default path length is 1 cm. Results assume that the specified analyte accounts for all peak absorbance.</p>
            """
        browser.setHtml(html)
        root.addWidget(browser, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)


class GradientDialog(QtWidgets.QDialog):
    def __init__(
        self,
        dataset: Dataset,
        language: str = "ja",
        parent=None,
        presets=None,
        preset_metadata=None,
    ):
        super().__init__(parent)
        self.dataset = dataset
        self.language = language
        self.presets = deepcopy(presets or {})
        self.preset_metadata = deepcopy(preset_metadata or {})
        normalized = normalize_preset_metadata(
            {}, self.presets, self.preset_metadata
        )
        self.preset_metadata.setdefault("conditions", {})
        self.preset_metadata["gradients"] = normalized["gradients"]
        self.applied_preset_name = dataset.effective_gradient_preset_name()
        self.last_loaded_preset_name = dataset.effective_gradient_preset_name()
        self._loading_preset = False
        self.setWindowTitle("グラジエントプログラム" if language == "ja" else "Gradient program")
        self.resize(800, 560)
        root = QtWidgets.QVBoxLayout(self)
        preset_row = QtWidgets.QHBoxLayout()
        preset_row.addWidget(QtWidgets.QLabel("プリセット" if language == "ja" else "Preset"))
        self.preset_combo = QtWidgets.QComboBox()
        self.preset_filter = QtWidgets.QLineEdit()
        self.preset_filter.setPlaceholderText(
            "名前で絞り込み" if language == "ja" else "Filter by name"
        )
        self.preset_filter.setMaximumWidth(150)
        self.preset_sort = QtWidgets.QComboBox()
        _populate_preset_sort_combo(self.preset_sort, language)
        self.save_preset_button = QtWidgets.QPushButton(
            "現在の条件を名前を付けて保存" if language == "ja" else "Save current program"
        )
        self.preview_preset_button = QtWidgets.QPushButton(
            "内容・差分…" if language == "ja" else "Preview / diff…"
        )
        self.apply_preset_button = QtWidgets.QPushButton("読み込む" if language == "ja" else "Load")
        self.delete_preset_button = QtWidgets.QPushButton("削除" if language == "ja" else "Delete")
        preset_row.addWidget(self.preset_combo, 1)
        preset_row.addWidget(self.preset_filter)
        preset_row.addWidget(self.preset_sort)
        preset_row.addWidget(self.preview_preset_button)
        preset_row.addWidget(self.save_preset_button)
        preset_row.addWidget(self.apply_preset_button)
        preset_row.addWidget(self.delete_preset_button)
        root.addLayout(preset_row)
        solvent_box = QtWidgets.QGroupBox("溶媒組成" if language == "ja" else "Solvent definitions")
        solvent_grid = QtWidgets.QGridLayout(solvent_box)
        solvent_grid.addWidget(QtWidgets.QLabel("Line"), 0, 0)
        solvent_grid.addWidget(QtWidgets.QLabel("Name"), 0, 1)
        solvent_grid.addWidget(QtWidgets.QLabel("Composition"), 0, 2)
        self.solvent_fields = {}
        for row, line in enumerate("ABCD", start=1):
            solvent_grid.addWidget(QtWidgets.QLabel(line), row, 0)
            name = QtWidgets.QLineEdit(dataset.measurement.solvents.get(line, Solvent()).name)
            composition = QtWidgets.QLineEdit(dataset.measurement.solvents.get(line, Solvent()).composition)
            solvent_grid.addWidget(name, row, 1)
            solvent_grid.addWidget(composition, row, 2)
            self.solvent_fields[line] = (name, composition)
            name.textChanged.connect(self._mark_program_changed)
            composition.textChanged.connect(self._mark_program_changed)
        root.addWidget(solvent_box)

        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(("Time (min)", "A (%)", "B (%)", "C (%)", "D (%)", "Flow (mL/min)"))
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table)
        points = dataset.measurement.gradient or [GradientPoint()]
        for point in points:
            self._append_point(point)
        self._updating_a = False
        self.table.itemChanged.connect(self._gradient_item_changed)
        self.table.itemChanged.connect(self._mark_program_changed)
        row_buttons = QtWidgets.QHBoxLayout()
        add_button = QtWidgets.QPushButton("行を追加" if language == "ja" else "Add row")
        delete_button = QtWidgets.QPushButton("選択行を削除" if language == "ja" else "Delete selected row")
        add_button.clicked.connect(lambda: self._append_point(GradientPoint()))
        delete_button.clicked.connect(self._delete_rows)
        row_buttons.addWidget(add_button)
        row_buttons.addWidget(delete_button)
        row_buttons.addStretch(1)
        root.addLayout(row_buttons)

        self.save_preset_button.clicked.connect(self._save_preset)
        self.preview_preset_button.clicked.connect(self._preview_preset)
        self.apply_preset_button.clicked.connect(self._apply_preset)
        self.delete_preset_button.clicked.connect(self._delete_preset)
        self.preset_filter.textChanged.connect(
            lambda _value=None: self._refresh_presets(
                self.preset_combo.currentText()
            )
        )
        self.preset_sort.currentIndexChanged.connect(
            lambda _value=None: self._refresh_presets(
                self.preset_combo.currentText()
            )
        )
        self._refresh_presets(self.applied_preset_name)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _append_point(self, point: GradientPoint):
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = (point.time_min, point.a_pct, point.b_pct, point.c_pct, point.d_pct, point.flow_ml_min)
        for column, value in enumerate(values):
            self.table.setItem(row, column, QtWidgets.QTableWidgetItem("" if value is None else "%g" % value))

    def _gradient_item_changed(self, item: QtWidgets.QTableWidgetItem):
        if self._updating_a or item.column() not in (2, 3, 4):
            return
        row = item.row()
        try:
            bcd = []
            for column in (2, 3, 4):
                cell = self.table.item(row, column)
                text = cell.text().strip() if cell else ""
                bcd.append(float(text or "0"))
            a_value = 100.0 - sum(bcd)
        except ValueError:
            return
        self._updating_a = True
        try:
            a_item = self.table.item(row, 1)
            if a_item is None:
                a_item = QtWidgets.QTableWidgetItem()
                self.table.setItem(row, 1, a_item)
            a_item.setText("%g" % a_value)
        finally:
            self._updating_a = False

    def _mark_program_changed(self, *_args):
        if not self._loading_preset and not self._updating_a:
            self.applied_preset_name = ""

    def _refresh_presets(self, selected_name: str = ""):
        selected_name = selected_name or self.preset_combo.currentText()
        sort_by = self.preset_sort.currentData() or "created"
        names = stable_preset_names(
            self.presets,
            self.preset_metadata,
            "gradients",
            sort_by,
        )
        names = filter_preset_names(names, self.preset_filter.text())
        self.preset_combo.clear()
        self.preset_combo.addItems(names)
        if selected_name:
            index = self.preset_combo.findText(selected_name)
            if index >= 0:
                self.preset_combo.setCurrentIndex(index)

    def _preview_preset(self):
        name = self.preset_combo.currentText()
        preset = self.presets.get(name)
        if preset is None:
            return
        current_points = []
        for row in range(self.table.rowCount()):
            values = []
            for column in range(6):
                item = self.table.item(row, column)
                values.append(item.text().strip() if item is not None else "")
            current_points.append(
                {
                    "time_min": values[0],
                    "a_pct": values[1],
                    "b_pct": values[2],
                    "c_pct": values[3],
                    "d_pct": values[4],
                    "flow_ml_min": values[5],
                }
            )
        current = {
            "gradient": current_points,
            "solvents": {
                line: {
                    "name": name_edit.text().strip(),
                    "composition": composition_edit.text().strip(),
                }
                for line, (name_edit, composition_edit) in self.solvent_fields.items()
            },
        }
        title = (
            "グラジエントプリセットの内容・差分: {0}".format(name)
            if self.language == "ja"
            else "Gradient preset preview / diff: {0}".format(name)
        )
        dialog_exec(
            PresetPreviewDialog(
                title,
                gradient_preset_preview_rows(current, preset, self.language),
                self.language,
                self,
            )
        )

    def _read_program(self):
        points = []
        for row in range(self.table.rowCount()):
            values = [
                self.table.item(row, column).text().strip() if self.table.item(row, column) else ""
                for column in range(6)
            ]
            if not any(values):
                continue
            points.append(
                GradientPoint(
                    time_min=float(values[0]),
                    a_pct=float(values[1]),
                    b_pct=float(values[2]),
                    c_pct=float(values[3]),
                    d_pct=float(values[4]),
                    flow_ml_min=optional_float(values[5]),
                )
            )
        valid, message = validate_gradient(points)
        if not valid:
            raise ValueError(message)
        solvents = {
            line: Solvent(name.text().strip(), composition.text().strip())
            for line, (name, composition) in self.solvent_fields.items()
        }
        return points, solvents

    def _save_preset(self):
        try:
            points, solvents = self._read_program()
        except (ValueError, IndexError) as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid gradient", str(exc))
            return
        initial_name = self.last_loaded_preset_name or self.preset_combo.currentText()
        name, accepted = QtWidgets.QInputDialog.getText(
            self,
            "グラジエントプリセット" if self.language == "ja" else "Gradient preset",
            "プリセット名" if self.language == "ja" else "Preset name",
            QtWidgets.QLineEdit.Normal,
            initial_name,
        )
        name = name.strip()
        if not accepted or not name:
            return
        if initial_name and initial_name != name:
            self.presets.pop(initial_name, None)
        self.presets[name] = {
            "gradient": [asdict(point) for point in points],
            "solvents": {line: asdict(solvent) for line, solvent in solvents.items()},
        }
        record_preset_saved(
            self.preset_metadata, "gradients", initial_name, name
        )
        self.applied_preset_name = name
        self.last_loaded_preset_name = name
        self._refresh_presets(name)

    def _apply_preset(self):
        name = self.preset_combo.currentText()
        payload = self.presets.get(name)
        if not payload:
            return
        self._loading_preset = True
        try:
            self.table.setRowCount(0)
            for point in payload.get("gradient", []):
                self._append_point(GradientPoint(**deepcopy(point)))
            if self.table.rowCount() == 0:
                self._append_point(GradientPoint())
            solvents = payload.get("solvents", {}) or {}
            for line, (name_edit, composition_edit) in self.solvent_fields.items():
                solvent = solvents.get(line, {}) or {}
                name_edit.setText(solvent.get("name", ""))
                composition_edit.setText(solvent.get("composition", ""))
            self.applied_preset_name = name
            self.last_loaded_preset_name = name
            record_preset_used(self.preset_metadata, "gradients", name)
            self._refresh_presets(name)
        finally:
            self._loading_preset = False

    def _delete_preset(self):
        name = self.preset_combo.currentText()
        if name in self.presets:
            del self.presets[name]
            record_preset_deleted(self.preset_metadata, "gradients", name)
            if self.applied_preset_name == name:
                self.applied_preset_name = ""
            if self.last_loaded_preset_name == name:
                self.last_loaded_preset_name = ""
            self._refresh_presets()

    def _delete_rows(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def _accept(self):
        try:
            points, solvents = self._read_program()
        except (ValueError, IndexError) as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid gradient", str(exc))
            return
        self.dataset.measurement.gradient = points
        self.dataset.measurement.solvents = solvents
        self.dataset.gradient_preset_name = self.applied_preset_name
        self.accept()


class LegendComposerDialog(QtWidgets.QDialog):
    """Choose ordered fields used to derive on-screen legend labels."""

    FIELD_LABELS = {
        "run_id": ("Run ID", "Run ID"),
        "label": ("ラベル", "Label"),
        "timestamp": ("タイムスタンプ", "Timestamp"),
        "wavelength": ("波長", "Wavelength"),
        "column": ("カラム", "Column"),
        "sample_name": ("サンプル名", "Sample name"),
        "analyte_name": ("分析対象物", "Analyte"),
        "group": ("グループ", "Group"),
    }

    def __init__(self, method: AnalysisMethod, language="ja", parent=None):
        super().__init__(parent)
        self.language = language
        selected = (
            list(method.legend_components)
            if isinstance(method.legend_components, list)
            else ["label", "wavelength"]
        )
        selected = [item for item in selected if item in LEGEND_COMPONENTS]
        order = selected + [item for item in LEGEND_COMPONENTS if item not in selected]
        self.setWindowTitle("凡例設定" if language == "ja" else "Legend composer")
        self.resize(480, 500)
        root = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel(
            "表示する項目を選び、上から順に連結します。空の値は省略されます。"
            if language == "ja"
            else "Selected fields are joined from top to bottom. Empty values are skipped."
        )
        note.setWordWrap(True)
        root.addWidget(note)
        self.list_widget = QtWidgets.QListWidget()
        for field_name in order:
            labels = self.FIELD_LABELS[field_name]
            item = QtWidgets.QListWidgetItem(
                labels[0] if language == "ja" else labels[1]
            )
            item.setData(USER_ROLE, field_name)
            item.setCheckState(CHECKED if field_name in selected else UNCHECKED)
            self.list_widget.addItem(item)
        root.addWidget(self.list_widget, 1)
        move_row = QtWidgets.QHBoxLayout()
        self.up_button = QtWidgets.QPushButton("↑ 上へ" if language == "ja" else "↑ Up")
        self.down_button = QtWidgets.QPushButton("↓ 下へ" if language == "ja" else "↓ Down")
        move_row.addWidget(self.up_button)
        move_row.addWidget(self.down_button)
        move_row.addStretch(1)
        root.addLayout(move_row)
        form = QtWidgets.QFormLayout()
        self.separator_edit = QtWidgets.QLineEdit(method.legend_separator)
        self.separator_edit.setMaxLength(16)
        form.addRow("区切り文字" if language == "ja" else "Separator", self.separator_edit)
        root.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))

    def _move(self, offset):
        row = self.list_widget.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= self.list_widget.count():
            return
        item = self.list_widget.takeItem(row)
        self.list_widget.insertItem(target, item)
        self.list_widget.setCurrentRow(target)

    def selected_components(self):
        return [
            self.list_widget.item(row).data(USER_ROLE)
            for row in range(self.list_widget.count())
            if self.list_widget.item(row).checkState() == CHECKED
        ]

    def _accept(self):
        if not self.selected_components():
            QtWidgets.QMessageBox.warning(
                self,
                "凡例設定" if self.language == "ja" else "Legend composer",
                "1項目以上を選択してください。"
                if self.language == "ja"
                else "Select at least one field.",
            )
            return
        self.accept()

    def apply_to_method(self, method):
        method.legend_components = self.selected_components()
        method.legend_separator = self.separator_edit.text()


class AxisLabelsDialog(QtWidgets.QDialog):
    """Axis text, tick spacing and label styles kept outside the main window."""

    def __init__(self, method: AnalysisMethod, language: str = "ja", parent=None):
        super().__init__(parent)
        self.language = language
        self.setWindowTitle(
            "軸・ラベル設定" if language == "ja" else "Axes and label styles"
        )
        self.resize(720, 660)
        root = QtWidgets.QVBoxLayout(self)

        labels_group = QtWidgets.QGroupBox(
            "ラベル文字列" if language == "ja" else "Label text"
        )
        labels_form = QtWidgets.QFormLayout(labels_group)
        self.x_label = QtWidgets.QLineEdit(method.x_axis_label)
        self.y1_label = QtWidgets.QLineEdit(method.y_axis_1_label)
        self.y2_label = QtWidgets.QLineEdit(method.y_axis_2_label)
        self.gradient_label = QtWidgets.QLineEdit(method.gradient_axis_label)
        self.x_label.setPlaceholderText("Retention time (min)")
        self.y1_label.setPlaceholderText("Automatic")
        self.y2_label.setPlaceholderText("Automatic")
        self.gradient_label.setPlaceholderText("Mobile phase B (%)")
        labels_form.addRow("X軸" if language == "ja" else "X axis", self.x_label)
        labels_form.addRow("縦軸1" if language == "ja" else "Y axis 1", self.y1_label)
        labels_form.addRow("縦軸2" if language == "ja" else "Y axis 2", self.y2_label)
        labels_form.addRow(
            "グラジエント軸" if language == "ja" else "Gradient axis",
            self.gradient_label,
        )
        note = QtWidgets.QLabel(
            "空欄は表示単位に応じた自動ラベルになります。"
            if language == "ja"
            else "Blank fields use automatic labels based on the display unit."
        )
        note.setWordWrap(True)
        labels_form.addRow(note)
        root.addWidget(labels_group)

        ticks_group = QtWidgets.QGroupBox(
            "X軸目盛" if language == "ja" else "X-axis ticks"
        )
        ticks_form = QtWidgets.QFormLayout(ticks_group)
        self.tick_mode_combo = QtWidgets.QComboBox()
        self.tick_mode_combo.addItem("表示範囲に合わせて自動" if language == "ja" else "Automatic for current view", "auto")
        self.tick_mode_combo.addItem("間隔を数値指定" if language == "ja" else "Manual spacing", "manual")
        self.tick_mode_combo.setCurrentIndex(
            max(0, self.tick_mode_combo.findData(method.x_tick_mode))
        )
        self.x_major_tick = QtWidgets.QDoubleSpinBox()
        self.x_minor_tick = QtWidgets.QDoubleSpinBox()
        for widget in (self.x_major_tick, self.x_minor_tick):
            widget.setRange(0.000001, 1000000.0)
            widget.setDecimals(6)
        self.x_major_tick.setValue(max(float(method.x_major_tick_min), 0.000001))
        self.x_minor_tick.setValue(max(float(method.x_minor_tick_min), 0.000001))
        ticks_form.addRow("モード" if language == "ja" else "Mode", self.tick_mode_combo)
        ticks_form.addRow("主目盛間隔 (min)" if language == "ja" else "Major spacing (min)", self.x_major_tick)
        ticks_form.addRow("副目盛間隔 (min)" if language == "ja" else "Minor spacing (min)", self.x_minor_tick)
        self.tick_mode_combo.currentIndexChanged.connect(self._sync_tick_mode)
        self._sync_tick_mode()
        root.addWidget(ticks_group)

        styles_group = QtWidgets.QGroupBox(
            "フォント・サイズ・色" if language == "ja" else "Font, size and color"
        )
        styles = QtWidgets.QGridLayout(styles_group)
        styles.addWidget(QtWidgets.QLabel("対象" if language == "ja" else "Target"), 0, 0)
        styles.addWidget(QtWidgets.QLabel("フォント" if language == "ja" else "Font"), 0, 1)
        styles.addWidget(QtWidgets.QLabel("サイズ" if language == "ja" else "Size"), 0, 2)
        styles.addWidget(QtWidgets.QLabel("色" if language == "ja" else "Color"), 0, 3)

        def font_combo(family: str):
            combo = QtWidgets.QFontComboBox()
            if family:
                combo.setCurrentFont(QtGui.QFont(family))
            return combo

        def size_spin(value: float):
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(4.0, 72.0)
            spin.setDecimals(1)
            spin.setSingleStep(0.5)
            spin.setValue(float(value))
            return spin

        self.axis_font_combo = font_combo(method.axis_label_font_family)
        self.tick_font_combo = font_combo(method.tick_label_font_family)
        self.legend_font_combo = font_combo(method.legend_font_family)
        self.retention_font_combo = font_combo(method.retention_label_font_family)
        self.axis_font_size = size_spin(method.axis_label_font_size)
        self.tick_font_size = size_spin(method.tick_label_font_size)
        self.legend_font_size = size_spin(method.legend_font_size)
        self.retention_font_size = size_spin(method.retention_label_font_size)
        self.axis_color_button = self._color_button(method.axis_label_color)
        self.tick_color_button = self._color_button(method.tick_label_color)
        self.legend_color_button = self._color_button(method.legend_font_color)
        self.retention_color_button = self._color_button(
            method.retention_label_color or "#000000"
        )
        style_rows = (
            ("軸タイトル" if language == "ja" else "Axis titles", self.axis_font_combo, self.axis_font_size, self.axis_color_button),
            ("目盛ラベル" if language == "ja" else "Tick labels", self.tick_font_combo, self.tick_font_size, self.tick_color_button),
            ("凡例" if language == "ja" else "Legend", self.legend_font_combo, self.legend_font_size, self.legend_color_button),
            ("保持時間ラベル" if language == "ja" else "Retention labels", self.retention_font_combo, self.retention_font_size, self.retention_color_button),
        )
        for row, (label, font_widget, size_widget, color_widget) in enumerate(style_rows, 1):
            styles.addWidget(QtWidgets.QLabel(label), row, 0)
            styles.addWidget(font_widget, row, 1)
            styles.addWidget(size_widget, row, 2)
            styles.addWidget(color_widget, row, 3)
        styles.setColumnStretch(1, 1)
        root.addWidget(styles_group, 1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _color_button(self, color_name: str):
        color = QtGui.QColor(color_name)
        if not color.isValid():
            color = QtGui.QColor("#000000")
        button = QtWidgets.QPushButton()
        button.color_name = color.name()
        self._update_color_button(button)
        button.clicked.connect(lambda: self._pick_color(button))
        return button

    @staticmethod
    def _update_color_button(button):
        color = QtGui.QColor(button.color_name)
        foreground = "#ffffff" if color.lightness() < 128 else "#000000"
        button.setText(button.color_name)
        button.setStyleSheet(
            "QPushButton { background-color: %s; color: %s; }"
            % (button.color_name, foreground)
        )

    def _pick_color(self, button):
        color = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(button.color_name), self, button.text()
        )
        if color.isValid():
            button.color_name = color.name()
            self._update_color_button(button)

    def _sync_tick_mode(self, *_args):
        manual = self.tick_mode_combo.currentData() == "manual"
        self.x_major_tick.setEnabled(manual)
        self.x_minor_tick.setEnabled(manual)

    def _accept(self):
        if (
            self.tick_mode_combo.currentData() == "manual"
            and self.x_minor_tick.value() >= self.x_major_tick.value()
        ):
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid tick spacing",
                "副目盛間隔は主目盛間隔より小さくしてください。"
                if self.language == "ja"
                else "Minor spacing must be smaller than major spacing.",
            )
            return
        self.accept()

    def apply_to_method(self, method: AnalysisMethod):
        method.x_axis_label = self.x_label.text().strip()
        method.y_axis_1_label = self.y1_label.text().strip()
        method.y_axis_2_label = self.y2_label.text().strip()
        method.gradient_axis_label = self.gradient_label.text().strip()
        method.x_tick_mode = self.tick_mode_combo.currentData() or "auto"
        method.x_major_tick_min = self.x_major_tick.value()
        method.x_minor_tick_min = self.x_minor_tick.value()
        method.axis_label_font_family = self.axis_font_combo.currentFont().family()
        method.axis_label_font_size = self.axis_font_size.value()
        method.axis_label_color = self.axis_color_button.color_name
        method.tick_label_font_family = self.tick_font_combo.currentFont().family()
        method.tick_label_font_size = self.tick_font_size.value()
        method.tick_label_color = self.tick_color_button.color_name
        method.legend_font_family = self.legend_font_combo.currentFont().family()
        method.legend_font_size = self.legend_font_size.value()
        method.legend_font_color = self.legend_color_button.color_name
        method.retention_label_font_family = self.retention_font_combo.currentFont().family()
        method.retention_label_font_size = self.retention_font_size.value()
        method.retention_label_color = self.retention_color_button.color_name


class PeakRangeDialog(QtWidgets.QDialog):
    def __init__(self, peak: PeakRegion, minimum: float, maximum: float, language: str = "ja", parent=None):
        super().__init__(parent)
        self.setWindowTitle("積分範囲" if language == "ja" else "Integration range")
        form = QtWidgets.QFormLayout(self)
        self.start = QtWidgets.QDoubleSpinBox()
        self.end = QtWidgets.QDoubleSpinBox()
        for widget in (self.start, self.end):
            widget.setDecimals(5)
            widget.setRange(minimum, maximum)
        self.start.setValue(peak.start_min)
        self.end.setValue(peak.end_min)
        form.addRow("開始 (min)" if language == "ja" else "Start (min)", self.start)
        form.addRow("終了 (min)" if language == "ja" else "End (min)", self.end)
        self.baseline_mode = QtWidgets.QComboBox()
        baseline_choices = (
            ("範囲両端を結ぶ直線" if language == "ja" else "Linear through endpoints", "linear"),
            ("両端近傍の中央値を結ぶ直線" if language == "ja" else "Linear through edge medians", "edge_average"),
            ("開始側近傍の中央値（水平）" if language == "ja" else "Constant from start edge", "constant_start"),
            ("手動指定した2点を結ぶ直線" if language == "ja" else "Manual linear baseline", "manual"),
            ("ゼロ基準" if language == "ja" else "Zero baseline", "zero"),
        )
        for text, data in baseline_choices:
            self.baseline_mode.addItem(text, data)
        self.baseline_mode.setCurrentIndex(max(0, self.baseline_mode.findData(peak.baseline_mode)))
        form.addRow("ベースライン" if language == "ja" else "Baseline", self.baseline_mode)
        self.baseline_start = QtWidgets.QDoubleSpinBox()
        self.baseline_end = QtWidgets.QDoubleSpinBox()
        for widget in (self.baseline_start, self.baseline_end):
            widget.setDecimals(6)
            widget.setRange(-1.0e12, 1.0e12)
        self.baseline_start.setValue(
            peak.baseline_start_uv
            if peak.baseline_start_uv is not None
            else peak.calculated_baseline_start_uv or 0.0
        )
        self.baseline_end.setValue(
            peak.baseline_end_uv
            if peak.baseline_end_uv is not None
            else peak.calculated_baseline_end_uv or 0.0
        )
        form.addRow("開始側ベースライン (µV)" if language == "ja" else "Baseline at start (µV)", self.baseline_start)
        form.addRow("終了側ベースライン (µV)" if language == "ja" else "Baseline at end (µV)", self.baseline_end)
        self.baseline_mode.currentIndexChanged.connect(self._sync_baseline_fields)
        self._sync_baseline_fields()
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept(self):
        if self.end.value() <= self.start.value():
            QtWidgets.QMessageBox.warning(self, "Invalid range", "End must be greater than start.")
            return
        self.accept()

    def _sync_baseline_fields(self):
        manual = self.baseline_mode.currentData() == "manual"
        self.baseline_start.setEnabled(manual)
        self.baseline_end.setEnabled(manual)
