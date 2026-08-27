from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import math
from pathlib import Path
import tempfile
from typing import List, Optional

import numpy as np
from matplotlib.backends import backend_pdf as _backend_pdf  # bundled for frozen SVG/PDF export
from matplotlib.backends import backend_svg as _backend_svg
from matplotlib.backend_bases import MouseButton
from matplotlib.figure import Figure
from matplotlib import font_manager
from matplotlib.ticker import MultipleLocator
from matplotlib.widgets import SpanSelector

from . import APP_NAME, APP_VERSION
from .analysis import (
    baseline_trace,
    detect_peaks,
    display_values,
    integrate_peak,
    recalculate_dataset_peaks,
    reference_values_for_display,
    split_peak_region,
)
from .dialogs import (
    AxisLabelsDialog,
    BatchMetadataDialog,
    DirectoryImportDialog,
    GradientDialog,
    LegendComposerDialog,
    LabDatabaseDialog,
    MetadataDialog,
    PeakRangeDialog,
    PreferencesDialog,
    ProjectNamingDialog,
    QuantitationHelpDialog,
    TextAnnotationDialog,
    dialog_exec,
)
from .database import initialize_database, sync_project_to_database
from .exporters import (
    export_chromatogram_csv,
    export_chromatograms_csv,
    export_metadata_csv,
    export_peak_csv,
)
from .i18n import Translator
from .models import (
    Dataset,
    PeakRegion,
    Project,
    TextAnnotation,
    sanitize_condition_presets,
)
from .naming import (
    apply_project_name_parts,
    build_project_filename,
    suggest_project_name_parts,
)
from .parser import load_chromatogram_file
from .preset_store import (
    load_preset_store_with_metadata,
    merge_preset_sources,
    normalize_preset_metadata,
    record_preset_deleted,
    record_preset_saved,
    save_preset_store,
)
from .project_io import (
    load_project,
    save_project,
)
from .report import export_analysis_report_pdf, render_analysis_report_pages
from .rendering import (
    HIGH_QUALITY,
    LIGHTWEIGHT,
    default_render_quality,
    normalize_render_quality,
    screen_series,
)
from .settings_store import (
    ApplicationSettings,
    DATABASE_PATH,
    FIGURE_FORMAT,
    IMPORT_DIRECTORY,
    LAST_IMPORT_DIRECTORY,
    LAST_PROJECT_DIRECTORY,
    LAST_SAVE_DIRECTORY,
    LEGACY_CONDITION_PRESETS,
    LEGACY_GRADIENT_PRESETS,
    NAMING_AUTHOR,
    RENDERING_QUALITY,
    SAVE_DIRECTORY,
    UI_LANGUAGE,
)
from .qt_compat import (
    QAction,
    QActionGroup,
    CHECKED,
    ITEM_IS_EDITABLE,
    QT_API,
    STANDARD_SAVE_SHORTCUT,
    UNCHECKED,
    USER_ROLE,
    WINDOW_MODAL,
    QtCore,
    QtGui,
    QtPrintSupport,
    QtWidgets,
)

if QT_API == 6:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
else:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar


COLORS = (
    "#1f77b4",
    "#d62728",
    "#2ca02c",
    "#9467bd",
    "#ff7f0e",
    "#17becf",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
)

INTEGRATION_BOUNDARY_COLOR = "#9ca3af"
AVAILABLE_PLOT_FONTS = {font.name for font in font_manager.fontManager.ttflist}

DATASET_VISIBLE_COLUMN = 0
DATASET_RUN_ID_COLUMN = 1
DATASET_LABEL_COLUMN = 2
DATASET_WAVELENGTH_COLUMN = 3
DATASET_GROUP_COLUMN = 4
DATASET_Y_AXIS_COLUMN = 5
DATASET_AUV_COLUMN = 6
DATASET_X_SHIFT_COLUMN = 7
DATASET_OFFSET_COLUMN = 8
DATASET_COLOR_COLUMN = 9
DATASET_SOURCE_COLUMN = 10
DATASET_COLUMN_COUNT = 11


def _resolved_plot_font(family: str):
    requested = str(family or "").strip()
    if not requested:
        return None
    if requested.casefold() == "arial":
        candidates = (
            "Arial",
            "Yu Gothic",
            "Meiryo",
            "Noto Sans CJK JP",
            "IPAexGothic",
            "Liberation Sans",
            "DejaVu Sans",
        )
        resolved = [name for name in candidates if name in AVAILABLE_PLOT_FONTS]
        return resolved or None
    if requested in AVAILABLE_PLOT_FONTS:
        return requested
    # Linux QA containers may not ship the chosen Windows font, so render with
    # a metric-compatible fallback without changing the stored project value.
    for fallback in ("Liberation Sans", "DejaVu Sans"):
        if fallback in AVAILABLE_PLOT_FONTS:
            return fallback
    return None


def _read_only_item(text: str) -> QtWidgets.QTableWidgetItem:
    item = QtWidgets.QTableWidgetItem(text)
    item.setFlags(item.flags() & ~ITEM_IS_EDITABLE)
    return item


def _format(value: Optional[float], digits: int = 5) -> str:
    if value is None:
        return ""
    return ("%%.%dg" % digits) % value


class AxisAwareNavigationToolbar(NavigationToolbar):
    """Pan only the axis region where the drag starts."""

    def __init__(self, canvas, parent):
        self._axis_pan_owner = parent
        super().__init__(canvas, parent)
        self._axis_pan_state = None

    def press_pan(self, event):
        if getattr(event, "button", None) not in (1, MouseButton.LEFT):
            return super().press_pan(event)
        if getattr(event, "x", None) is None or getattr(event, "y", None) is None:
            return
        owner = self._axis_pan_owner
        if owner is None or not getattr(owner, "_view_initialized", False):
            return
        target = owner._pan_target(event)
        if target not in ("x", "y1", "y2", "plot"):
            return
        if self._nav_stack() is None:
            self.push_current()
        owner._push_view_history()
        owner._begin_navigation_interaction()
        self._axis_pan_state = {
            "target": target,
            "start_x": float(event.x),
            "start_y": float(event.y),
            "x": tuple(owner.axes.get_xlim()),
            "y1": tuple(owner.axes.get_ylim()),
            "y2": (
                tuple(owner.axes_right.get_ylim())
                if owner.axes_right is not None
                else None
            ),
        }
        self.canvas.mpl_disconnect(self._id_drag)
        self._id_drag = self.canvas.mpl_connect(
            "motion_notify_event", self.drag_pan
        )

    @staticmethod
    def _shifted_limits(limits, pixel_delta: float, pixel_span: float):
        if not pixel_span:
            return limits
        lower, upper = limits
        data_delta = float(pixel_delta) * (upper - lower) / float(pixel_span)
        return lower - data_delta, upper - data_delta

    def drag_pan(self, event):
        state = self._axis_pan_state
        if state is None:
            return super().drag_pan(event)
        if getattr(event, "x", None) is None or getattr(event, "y", None) is None:
            return
        owner = self._axis_pan_owner
        if owner is None:
            return
        target = state["target"]
        bbox = owner.axes.bbox
        if target in ("x", "plot"):
            owner.axes.set_xlim(
                *self._shifted_limits(
                    state["x"], float(event.x) - state["start_x"], bbox.width
                )
            )
        if target in ("y1", "plot"):
            owner.axes.set_ylim(
                *self._shifted_limits(
                    state["y1"], float(event.y) - state["start_y"], bbox.height
                )
            )
        if target in ("y2", "plot") and owner.axes_right is not None:
            y2_limits = state.get("y2")
            if y2_limits is not None:
                owner.axes_right.set_ylim(
                    *self._shifted_limits(
                        y2_limits,
                        float(event.y) - state["start_y"],
                        bbox.height,
                    )
                )
        owner._request_canvas_draw(throttled=True)

    def release_pan(self, event):
        if self._axis_pan_state is None:
            return super().release_pan(event)
        self.canvas.mpl_disconnect(self._id_drag)
        self._id_drag = self.canvas.mpl_connect(
            "motion_notify_event", self.mouse_move
        )
        self._axis_pan_state = None
        owner = self._axis_pan_owner
        if owner is not None:
            owner._end_navigation_interaction()
            owner._request_canvas_draw(force=True)
        else:
            self.canvas.draw_idle()
        self.push_current()


class LeftElideDelegate(QtWidgets.QStyledItemDelegate):
    """Keep the end of long paths visible when the source column is narrow."""

    def paint(self, painter, option, index):
        styled = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        mode = QtCore.Qt.TextElideMode.ElideLeft if QT_API == 6 else QtCore.Qt.ElideLeft
        styled.text = styled.fontMetrics.elidedText(styled.text, mode, styled.rect.width())
        if QT_API == 6:
            control = QtWidgets.QStyle.ControlElement.CE_ItemViewItem
        else:
            control = QtWidgets.QStyle.CE_ItemViewItem
        style = styled.widget.style() if styled.widget is not None else QtWidgets.QApplication.style()
        style.drawControl(control, styled, painter, styled.widget)


class DatasetTableWidget(QtWidgets.QTableWidget):
    """Request Project-backed row moves instead of moving table items directly."""

    rowMoveRequested = QtCore.Signal(int, int)

    def __init__(self, rows=0, columns=0, parent=None):
        super().__init__(rows, columns, parent)
        internal_move = (
            QtWidgets.QAbstractItemView.DragDropMode.InternalMove
            if QT_API == 6
            else QtWidgets.QAbstractItemView.InternalMove
        )
        move_action = (
            QtCore.Qt.DropAction.MoveAction if QT_API == 6 else QtCore.Qt.MoveAction
        )
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(internal_move)
        self.setDragDropOverwriteMode(False)
        self.setDefaultDropAction(move_action)

    @staticmethod
    def _event_position(event):
        if QT_API == 6:
            position = event.position()
            return position.toPoint() if hasattr(position, "toPoint") else position
        return event.pos()

    def _forward_file_drop(self, method_name, event) -> bool:
        mime_data = event.mimeData()
        if mime_data is None or not mime_data.hasUrls():
            return False
        handler = getattr(self.window(), method_name, None)
        if callable(handler):
            handler(event)
        else:
            event.ignore()
        return True

    def dragEnterEvent(self, event):
        if self._forward_file_drop("dragEnterEvent", event):
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event):
        if self._forward_file_drop("dropEvent", event):
            return
        selected_rows = sorted(
            {index.row() for index in self.selectionModel().selectedRows()}
        )
        if len(selected_rows) != 1:
            event.ignore()
            return
        source_row = selected_rows[0]
        position = self._event_position(event)
        index = self.indexAt(position)
        if index.isValid():
            insertion_row = index.row()
            if position.y() >= self.visualRect(index).center().y():
                insertion_row += 1
        else:
            insertion_row = self.rowCount()
        target_row = insertion_row - 1 if insertion_row > source_row else insertion_row
        if 0 <= target_row < self.rowCount() and target_row != source_row:
            self.rowMoveRequested.emit(source_row, target_row)
        event.acceptProposedAction()


class MainWindow(QtWidgets.QMainWindow):
    _open_windows = set()

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self._settings = ApplicationSettings()
        settings_conditions = sanitize_condition_presets(
            self._settings.get(LEGACY_CONDITION_PRESETS)
        )
        settings_gradients = self._settings.get(LEGACY_GRADIENT_PRESETS)
        (
            stored_conditions,
            stored_gradients,
            stored_metadata,
        ) = load_preset_store_with_metadata()
        merged_conditions, merged_gradients = merge_preset_sources(
            settings_conditions,
            settings_gradients,
            stored_conditions,
            stored_gradients,
        )
        self._global_condition_presets = sanitize_condition_presets(
            merged_conditions
        )
        self._global_gradient_presets = merged_gradients
        self._global_preset_metadata = normalize_preset_metadata(
            self._global_condition_presets,
            self._global_gradient_presets,
            stored_metadata,
        )
        self._application_language = self._settings.get(UI_LANGUAGE)
        if self._global_condition_presets or self._global_gradient_presets:
            # v1.1.4 and earlier used QSettings only. Mirror those values into
            # a version-independent JSON file on first v1.1.5 launch, and
            # restore QSettings from that file if a future build changes path.
            self._settings.set(
                LEGACY_CONDITION_PRESETS, self._global_condition_presets
            )
            self._settings.set(
                LEGACY_GRADIENT_PRESETS, self._global_gradient_presets
            )
            self._settings.sync()
            self._save_global_preset_file()
        self.project = Project(
            ui_language=self._application_language,
            condition_presets=deepcopy(self._global_condition_presets),
            gradient_presets=deepcopy(self._global_gradient_presets),
        )
        self.translator = Translator(self._application_language)
        self._updating_table = False
        self._span_selector = None
        self._span_selector_mode = None
        self._view_state = None
        self._view_history = []
        self._view_initialized = False
        self._dataset_lines = {}
        self._move_drag = None
        self._interaction_cursor = None
        self._annotation_artists = {}
        self._annotation_drag = None
        self._edit_range_peak_id = None
        self._overview_view_patch = None
        self.axes_overview = None
        self.axes_overview_right = None
        self._xlim_callback_id = None
        self._tick_update_guard = False
        self._undo_stack = []
        self._redo_stack = []
        self._import_directory = self._settings.get(IMPORT_DIRECTORY)
        self._save_directory = self._settings.get(SAVE_DIRECTORY)
        self._database_path = self._settings.get(DATABASE_PATH)
        self._figure_export_format = self._settings.get(FIGURE_FORMAT)
        self._render_quality = self._settings.get(RENDERING_QUALITY)
        self.axes_right = None
        self.axes_gradient = None
        self._overview_dataset_lines = {}
        self._plot_source_cache = {}
        self._peak_overlay_artists = {}
        self._navigation_interaction_active = False
        self._pending_series_refresh = False
        self._draw_timer = None
        self._build_ui()
        self._build_menus()
        self._retranslate()
        self._refresh_all()
        self.resize(1500, 900)

    def _save_global_preset_file(self):
        try:
            save_preset_store(
                self._global_condition_presets,
                self._global_gradient_presets,
                metadata=self._global_preset_metadata,
            )
        except (OSError, TypeError, ValueError):
            # QSettings remains the fallback if the roaming-profile directory
            # is temporarily unavailable or read-only.
            pass

    def _persist_global_presets(self):
        self.project.condition_presets = sanitize_condition_presets(
            self.project.condition_presets
        )
        self._global_condition_presets = deepcopy(self.project.condition_presets)
        self._global_gradient_presets = deepcopy(self.project.gradient_presets)
        self._reconcile_global_preset_metadata()
        self._settings.set(
            LEGACY_CONDITION_PRESETS, self._global_condition_presets
        )
        self._settings.set(LEGACY_GRADIENT_PRESETS, self._global_gradient_presets)
        self._settings.sync()
        self._save_global_preset_file()

    def _reconcile_global_preset_metadata(self):
        for kind, presets in (
            ("conditions", self._global_condition_presets),
            ("gradients", self._global_gradient_presets),
        ):
            records = self._global_preset_metadata.setdefault(kind, {})
            for name in presets:
                if name not in records:
                    record_preset_saved(
                        self._global_preset_metadata, kind, "", name
                    )
            for name in list(records):
                if name not in presets:
                    record_preset_deleted(
                        self._global_preset_metadata, kind, name
                    )
        self._global_preset_metadata = normalize_preset_metadata(
            self._global_condition_presets,
            self._global_gradient_presets,
            self._global_preset_metadata,
        )

    def _merge_global_presets_into_project(self):
        project_conditions = sanitize_condition_presets(self.project.condition_presets)
        self._global_condition_presets = sanitize_condition_presets(
            self._global_condition_presets
        )
        project_gradients = deepcopy(self.project.gradient_presets)
        imported = False
        for name, payload in project_conditions.items():
            if name not in self._global_condition_presets:
                self._global_condition_presets[name] = deepcopy(payload)
                record_preset_saved(
                    self._global_preset_metadata, "conditions", "", name
                )
                imported = True
        for name, payload in project_gradients.items():
            if name not in self._global_gradient_presets:
                self._global_gradient_presets[name] = deepcopy(payload)
                record_preset_saved(
                    self._global_preset_metadata, "gradients", "", name
                )
                imported = True
        merged_conditions = project_conditions
        merged_conditions.update(deepcopy(self._global_condition_presets))
        merged_gradients = project_gradients
        merged_gradients.update(deepcopy(self._global_gradient_presets))
        self.project.condition_presets = merged_conditions
        self.project.gradient_presets = merged_gradients
        if imported:
            self._settings.set(
                LEGACY_CONDITION_PRESETS, self._global_condition_presets
            )
            self._settings.set(
                LEGACY_GRADIENT_PRESETS, self._global_gradient_presets
            )
            self._settings.sync()
            self._save_global_preset_file()

    def _default_save_path(self, filename: str) -> str:
        last_directory = self._settings.get(LAST_SAVE_DIRECTORY)
        directory = self._save_directory or last_directory
        if directory and Path(directory).is_dir():
            return str(Path(directory) / filename)
        return filename

    def _remember_save_path(self, path: str):
        directory = Path(path).parent
        if directory.is_dir():
            self._settings.set(LAST_SAVE_DIRECTORY, str(directory), sync=True)

    def _is_lightweight_rendering(self) -> bool:
        return self._render_quality == LIGHTWEIGHT

    def _set_figure_layout_quality(self):
        """Avoid repeated constrained-layout work on the legacy screen path."""

        if not hasattr(self, "figure"):
            return
        constrained = not self._is_lightweight_rendering()
        try:
            if hasattr(self.figure, "set_layout_engine"):
                self.figure.set_layout_engine("constrained" if constrained else None)
            else:
                self.figure.set_constrained_layout(constrained)
        except (AttributeError, RuntimeError, ValueError):
            pass

    @staticmethod
    def _axis_pixel_width(axis) -> float:
        try:
            width = float(axis.bbox.width)
        except (AttributeError, TypeError, ValueError):
            width = 1000.0
        return width if width > 0 else 1000.0

    def _screen_data(
        self,
        x_values,
        y_values,
        axis,
        *,
        x_limits=None,
        overview: bool = False,
        interactive: bool = False,
    ):
        return screen_series(
            x_values,
            y_values,
            self._render_quality,
            self._axis_pixel_width(axis),
            x_limits=x_limits,
            overview=overview,
            interactive=interactive,
        )

    def _refresh_screen_series_for_view(
        self,
        *,
        interactive: bool = False,
        full_range: bool = False,
    ):
        """Refresh only Line2D data; analysis arrays remain untouched."""

        if not self._is_lightweight_rendering() or not hasattr(self, "axes"):
            return
        x_limits = None if full_range or interactive else tuple(self.axes.get_xlim())
        for dataset in self.project.datasets:
            cached = self._plot_source_cache.get(dataset.id)
            line = self._dataset_lines.get(dataset.id)
            if cached is None or line is None or not dataset.visible:
                continue
            full_x, full_y = cached
            target_axis = (
                self.axes_right
                if dataset.y_axis == 2 and self.axes_right is not None
                else self.axes
            )
            screen_x, screen_y = self._screen_data(
                full_x,
                full_y,
                target_axis,
                x_limits=x_limits,
                interactive=interactive,
            )
            line.set_data(screen_x, screen_y)
            overview_line = self._overview_dataset_lines.get(dataset.id)
            if overview_line is not None and self.axes_overview is not None:
                overview_axis = (
                    self.axes_overview_right
                    if dataset.y_axis == 2 and self.axes_overview_right is not None
                    else self.axes_overview
                )
                overview_x, overview_y = self._screen_data(
                    full_x,
                    full_y,
                    overview_axis,
                    overview=True,
                )
                overview_line.set_data(overview_x, overview_y)

    def _flush_canvas_draw(self):
        refresh_series = self._pending_series_refresh
        self._pending_series_refresh = False
        if refresh_series:
            self._refresh_screen_series_for_view()
        if hasattr(self, "canvas"):
            self.canvas.draw_idle()

    def _request_canvas_draw(
        self,
        *,
        throttled: bool = False,
        refresh_series: bool = False,
        force: bool = False,
    ):
        """Coalesce rapid legacy-PC events while preserving immediate high quality."""

        if not hasattr(self, "canvas"):
            return
        if force:
            if self._draw_timer is not None:
                self._draw_timer.stop()
            self._pending_series_refresh = False
            if refresh_series:
                self._refresh_screen_series_for_view()
            self.canvas.draw_idle()
            return
        if self._is_lightweight_rendering() and throttled and self._draw_timer is not None:
            self._pending_series_refresh = (
                self._pending_series_refresh or refresh_series
            )
            self._draw_timer.start(40)
            return
        if refresh_series:
            self._refresh_screen_series_for_view()
        self.canvas.draw_idle()

    def _begin_navigation_interaction(self):
        if self._is_lightweight_rendering() and not self._navigation_interaction_active:
            self._navigation_interaction_active = True
            self._refresh_screen_series_for_view(interactive=True, full_range=True)

    def _end_navigation_interaction(self):
        if not self._navigation_interaction_active:
            return
        self._navigation_interaction_active = False
        self._refresh_screen_series_for_view()

    def _set_render_quality(
        self,
        quality: str,
        *,
        persist: bool = True,
        replot: bool = True,
    ):
        normalized = normalize_render_quality(quality, default_render_quality())
        changed = normalized != self._render_quality
        self._render_quality = normalized
        if persist:
            self._settings.set(RENDERING_QUALITY, normalized, sync=True)
        if changed:
            self._set_figure_layout_quality()
            if replot and hasattr(self, "axes"):
                self._plot()

    @contextmanager
    def _full_quality_export_figure(self):
        """Temporarily rebuild screen artists from full arrays for figure export."""

        original_quality = self._render_quality
        if original_quality != LIGHTWEIGHT:
            yield
            return
        try:
            self._render_quality = HIGH_QUALITY
            self._set_figure_layout_quality()
            self._plot()
            yield
        finally:
            self._render_quality = original_quality
            self._set_figure_layout_quality()
            self._plot()

    @staticmethod
    def _vertical_pointer_icon():
        """Create a compact line-and-cursor icon without an external asset."""
        pixmap = QtGui.QPixmap(24, 24)
        transparent = (
            QtCore.Qt.GlobalColor.transparent if QT_API == 6 else QtCore.Qt.transparent
        )
        pixmap.fill(transparent)
        painter = QtGui.QPainter(pixmap)
        antialiasing = (
            QtGui.QPainter.RenderHint.Antialiasing
            if QT_API == 6
            else QtGui.QPainter.Antialiasing
        )
        painter.setRenderHint(antialiasing, True)
        painter.setPen(QtGui.QPen(QtGui.QColor("#2563eb"), 2.0))
        painter.drawLine(13, 2, 13, 22)
        path = QtGui.QPainterPath()
        path.moveTo(2.5, 3.0)
        path.lineTo(10.5, 7.0)
        path.lineTo(6.2, 9.0)
        path.lineTo(4.2, 13.0)
        path.closeSubpath()
        painter.setPen(QtGui.QPen(QtGui.QColor("#111827"), 1.0))
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#f9fafb")))
        painter.drawPath(path)
        painter.end()
        return QtGui.QIcon(pixmap)

    @staticmethod
    def _text_beside_icon_style():
        return (
            QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            if QT_API == 6
            else QtCore.Qt.ToolButtonTextBesideIcon
        )

    def _build_ui(self):
        self.figure = Figure(
            figsize=(8, 5),
            constrained_layout=not self._is_lightweight_rendering(),
        )
        self.axes = self.figure.add_subplot(111)
        self.canvas = FigureCanvas(self.figure)
        self._draw_timer = QtCore.QTimer(self)
        self._draw_timer.setSingleShot(True)
        self._draw_timer.timeout.connect(self._flush_canvas_draw)
        self.toolbar = AxisAwareNavigationToolbar(self.canvas, self)
        self.pointer_action = self._action(checkable=True)
        self.pointer_action.setIcon(self._vertical_pointer_icon())
        self.pointer_action.toggled.connect(self._toggle_pointer_mode)
        self.pointer_toolbar_button = QtWidgets.QToolButton()
        self.pointer_toolbar_button.setDefaultAction(self.pointer_action)
        self.pointer_toolbar_button.setToolButtonStyle(self._text_beside_icon_style())
        self.pointer_toolbar_button.setIconSize(QtCore.QSize(18, 18))
        self.pointer_toolbar_button.setMinimumWidth(145)
        toolbar_actions = self.toolbar.actions()
        if toolbar_actions:
            self.pointer_toolbar_widget_action = self.toolbar.insertWidget(
                toolbar_actions[0], self.pointer_toolbar_button
            )
            self.toolbar.insertSeparator(toolbar_actions[0])
        else:
            self.pointer_toolbar_widget_action = self.toolbar.addWidget(
                self.pointer_toolbar_button
            )
            self.toolbar.addSeparator()
        # Compatibility alias retained for project tests and third-party macros.
        self.pointer_button = self.pointer_action
        self._wire_toolbar_navigation_actions()

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        self.project_location_label = QtWidgets.QLabel()
        self.project_location_label.setMaximumWidth(520)
        self.statusBar().addPermanentWidget(self.project_location_label)
        root = QtWidgets.QVBoxLayout(central)
        splitter = QtWidgets.QSplitter()
        root.addWidget(splitter, 1)

        left = QtWidgets.QWidget()
        left.setMinimumWidth(410)
        left_layout = QtWidgets.QVBoxLayout(left)
        self.dataset_title = QtWidgets.QLabel()
        font = self.dataset_title.font()
        font.setBold(True)
        self.dataset_title.setFont(font)
        left_layout.addWidget(self.dataset_title)
        self.dataset_table = DatasetTableWidget(0, DATASET_COLUMN_COUNT)
        self.dataset_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        extended_selection = (
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
            if QT_API == 6
            else QtWidgets.QAbstractItemView.ExtendedSelection
        )
        self.dataset_table.setSelectionMode(extended_selection)
        self.dataset_table.setWordWrap(False)
        self.dataset_table.verticalHeader().setVisible(False)
        self.dataset_table.itemChanged.connect(self._dataset_item_changed)
        self.dataset_table.itemSelectionChanged.connect(self._dataset_selection_changed)
        self.dataset_table.rowMoveRequested.connect(self.move_dataset_to)
        self.dataset_table.setItemDelegateForColumn(
            DATASET_SOURCE_COLUMN, LeftElideDelegate(self.dataset_table)
        )
        self.dataset_table.viewport().installEventFilter(self)
        left_layout.addWidget(self.dataset_table, 1)
        button_grid = QtWidgets.QGridLayout()
        self.import_button = QtWidgets.QPushButton()
        self.remove_button = QtWidgets.QPushButton()
        self.metadata_button = QtWidgets.QPushButton()
        self.batch_metadata_button = QtWidgets.QPushButton()
        self.gradient_button = QtWidgets.QPushButton()
        self.color_button = QtWidgets.QPushButton()
        self.show_all_button = QtWidgets.QPushButton()
        self.hide_all_button = QtWidgets.QPushButton()
        self.move_dataset_up_button = QtWidgets.QPushButton()
        self.move_dataset_down_button = QtWidgets.QPushButton()
        self.group_run_button = QtWidgets.QPushButton()
        self.ungroup_run_button = QtWidgets.QPushButton()
        button_grid.addWidget(self.import_button, 0, 0)
        button_grid.addWidget(self.remove_button, 0, 1)
        button_grid.addWidget(self.metadata_button, 1, 0)
        button_grid.addWidget(self.gradient_button, 1, 1)
        button_grid.addWidget(self.color_button, 2, 0)
        button_grid.addWidget(self.batch_metadata_button, 2, 1)
        button_grid.addWidget(self.show_all_button, 3, 0)
        button_grid.addWidget(self.hide_all_button, 3, 1)
        button_grid.addWidget(self.move_dataset_up_button, 4, 0)
        button_grid.addWidget(self.move_dataset_down_button, 4, 1)
        button_grid.addWidget(self.group_run_button, 5, 0)
        button_grid.addWidget(self.ungroup_run_button, 5, 1)
        left_layout.addLayout(button_grid)
        self.import_button.clicked.connect(self.import_ascii)
        self.remove_button.clicked.connect(self.remove_dataset)
        self.metadata_button.clicked.connect(self.edit_metadata)
        self.batch_metadata_button.clicked.connect(self.edit_batch_metadata)
        self.gradient_button.clicked.connect(self.edit_gradient)
        self.color_button.clicked.connect(self.change_color)
        self.show_all_button.clicked.connect(lambda: self.set_all_datasets_visible(True))
        self.hide_all_button.clicked.connect(lambda: self.set_all_datasets_visible(False))
        self.move_dataset_up_button.clicked.connect(
            lambda: self.move_selected_dataset(-1)
        )
        self.move_dataset_down_button.clicked.connect(
            lambda: self.move_selected_dataset(1)
        )
        self.group_run_button.clicked.connect(self.group_selected_runs)
        self.ungroup_run_button.clicked.connect(self.ungroup_selected_runs)
        splitter.addWidget(left)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        vertical = (
            QtCore.Qt.Orientation.Vertical if QT_API == 6 else QtCore.Qt.Vertical
        )
        self.right_splitter = QtWidgets.QSplitter(vertical)
        self.right_splitter.setChildrenCollapsible(False)
        right_layout.addWidget(self.right_splitter, 1)

        plot_panel = QtWidgets.QWidget()
        plot_layout = QtWidgets.QVBoxLayout(plot_panel)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.addWidget(self.toolbar)
        plot_layout.addWidget(self.canvas, 1)
        plot_panel.setMinimumHeight(220)
        self.right_splitter.addWidget(plot_panel)

        analysis_panel = QtWidgets.QWidget()
        analysis_layout = QtWidgets.QVBoxLayout(analysis_panel)
        analysis_layout.setContentsMargins(0, 0, 0, 0)
        controls = QtWidgets.QHBoxLayout()

        self.display_group = QtWidgets.QGroupBox()
        display_controls = QtWidgets.QGridLayout(self.display_group)
        self.unit_label = QtWidgets.QLabel()
        self.unit_combo = QtWidgets.QComboBox()
        self.unit_combo.addItem("Raw µV", "uV")
        self.unit_combo.addItem("mAU", "mAU")
        self.unit_combo.addItem("AU", "AU")
        self.unit_combo.addItem("Normalized", "normalized")
        self.show_integration_checkbox = QtWidgets.QCheckBox()
        self.show_retention_checkbox = QtWidgets.QCheckBox()
        self.show_gradient_checkbox = QtWidgets.QCheckBox()
        self.legend_label = QtWidgets.QLabel()
        self.legend_combo = QtWidgets.QComboBox()
        for label, value in (
            ("Auto", "best"),
            ("Upper right", "upper right"),
            ("Upper left", "upper left"),
            ("Lower right", "lower right"),
            ("Lower left", "lower left"),
            ("Outside right", "outside right"),
        ):
            self.legend_combo.addItem(label, value)
        self.axis_labels_button = QtWidgets.QPushButton()
        self.legend_settings_button = QtWidgets.QPushButton()
        self.annotation_action = self._action(checkable=True)
        self.annotation_action.toggled.connect(self._toggle_annotation_mode)
        self.annotation_button = QtWidgets.QToolButton()
        self.annotation_button.setDefaultAction(self.annotation_action)
        display_controls.addWidget(self.unit_label, 0, 0)
        display_controls.addWidget(self.unit_combo, 0, 1)
        display_controls.addWidget(self.legend_label, 1, 0)
        display_controls.addWidget(self.legend_combo, 1, 1)
        display_controls.addWidget(self.legend_settings_button, 2, 0, 1, 2)
        display_controls.addWidget(self.show_integration_checkbox, 3, 0, 1, 2)
        display_controls.addWidget(self.show_retention_checkbox, 4, 0, 1, 2)
        display_controls.addWidget(self.show_gradient_checkbox, 5, 0, 1, 2)
        display_controls.addWidget(self.axis_labels_button, 6, 0, 1, 2)
        display_controls.addWidget(self.annotation_button, 7, 0, 1, 2)
        display_controls.setRowStretch(8, 1)

        self.navigation_group = QtWidgets.QGroupBox()
        navigation_controls = QtWidgets.QGridLayout(self.navigation_group)
        self.reset_view_button = QtWidgets.QPushButton()
        self.reset_x_view_button = QtWidgets.QPushButton()
        self.reset_y_view_button = QtWidgets.QPushButton()
        self.zoom_axis_label = QtWidgets.QLabel()
        self.zoom_axis_combo = QtWidgets.QComboBox()
        self.zoom_axis_combo.addItem("Automatic by cursor", "auto")
        self.zoom_axis_combo.addItem("X + Y", "both")
        self.zoom_axis_combo.addItem("X", "x")
        self.zoom_axis_combo.addItem("Y", "y")
        self.view_mode_label = QtWidgets.QLabel()
        self.view_mode_combo = QtWidgets.QComboBox()
        self.view_mode_combo.addItem("Single", "single")
        self.view_mode_combo.addItem("Overview + detail", "overview_detail")
        self.move_trace_button = QtWidgets.QPushButton()
        self.move_trace_button.setCheckable(True)
        self.move_axis_combo = QtWidgets.QComboBox()
        self.move_axis_combo.addItem("X + Y", "both")
        self.move_axis_combo.addItem("X", "x")
        self.move_axis_combo.addItem("Y", "y")
        self.pointer_control_button = QtWidgets.QToolButton()
        self.pointer_control_button.setDefaultAction(self.pointer_action)
        self.pointer_control_button.setToolButtonStyle(
            self._text_beside_icon_style()
        )
        self.pointer_control_button.setIconSize(QtCore.QSize(18, 18))
        navigation_controls.addWidget(self.move_trace_button, 0, 0)
        navigation_controls.addWidget(self.move_axis_combo, 0, 1)
        navigation_controls.addWidget(self.zoom_axis_label, 1, 0)
        navigation_controls.addWidget(self.zoom_axis_combo, 1, 1)
        navigation_controls.addWidget(self.view_mode_label, 2, 0)
        navigation_controls.addWidget(self.view_mode_combo, 2, 1)
        navigation_controls.addWidget(self.pointer_control_button, 3, 0, 1, 2)
        reset_buttons = QtWidgets.QHBoxLayout()
        reset_buttons.setContentsMargins(0, 0, 0, 0)
        reset_buttons.addWidget(self.reset_view_button)
        reset_buttons.addWidget(self.reset_x_view_button)
        reset_buttons.addWidget(self.reset_y_view_button)
        navigation_controls.addLayout(reset_buttons, 4, 0, 1, 2)
        navigation_controls.setRowStretch(5, 1)

        self.integration_group = QtWidgets.QGroupBox()
        integration_controls = QtWidgets.QGridLayout(self.integration_group)
        self.baseline_label = QtWidgets.QLabel()
        self.baseline_combo = QtWidgets.QComboBox()
        self.baseline_combo.addItem("Endpoints", "linear")
        self.baseline_combo.addItem("Edge medians", "edge_average")
        self.baseline_combo.addItem("Constant start", "constant_start")
        self.baseline_combo.addItem("Manual", "manual")
        self.baseline_combo.addItem("Zero", "zero")
        self.integrate_button = QtWidgets.QPushButton()
        self.integrate_button.setCheckable(True)
        self.edit_peak_button = QtWidgets.QPushButton()
        self.edit_peak_button.setCheckable(True)
        self.split_peak_button = QtWidgets.QPushButton()
        self.split_peak_button.setCheckable(True)
        self.auto_detect_button = QtWidgets.QPushButton()
        self.select_all_peaks_button = QtWidgets.QPushButton()
        self.delete_peak_button = QtWidgets.QPushButton()
        integration_controls.addWidget(self.baseline_label, 0, 0)
        integration_controls.addWidget(self.baseline_combo, 0, 1, 1, 2)
        integration_controls.addWidget(self.integrate_button, 1, 0)
        integration_controls.addWidget(self.edit_peak_button, 1, 1)
        integration_controls.addWidget(self.split_peak_button, 1, 2)
        integration_controls.addWidget(self.auto_detect_button, 2, 0, 1, 3)
        integration_controls.addWidget(self.select_all_peaks_button, 3, 0, 1, 2)
        integration_controls.addWidget(self.delete_peak_button, 3, 2)
        integration_controls.setRowStretch(4, 1)

        controls.addWidget(self.display_group, 4)
        controls.addWidget(self.navigation_group, 2)
        controls.addWidget(self.integration_group, 4)
        analysis_layout.addLayout(controls)
        self.peak_title = QtWidgets.QLabel()
        self.peak_title.setFont(font)
        analysis_layout.addWidget(self.peak_title)
        self.peak_table = QtWidgets.QTableWidget(0, 19)
        self.peak_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.peak_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        edit_triggers = (
            QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked
            | QtWidgets.QAbstractItemView.EditTrigger.EditKeyPressed
            if QT_API == 6
            else QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
        )
        self.peak_table.setEditTriggers(edit_triggers)
        self.peak_table.setWordWrap(False)
        self.peak_table.verticalHeader().setVisible(False)
        self.peak_table.itemSelectionChanged.connect(self._peak_selection_changed)
        self.peak_table.itemChanged.connect(self._peak_item_changed)
        self.peak_table.installEventFilter(self)
        analysis_layout.addWidget(self.peak_table, 1)
        analysis_panel.setMinimumHeight(250)
        self.right_splitter.addWidget(analysis_panel)
        self.right_splitter.setStretchFactor(0, 3)
        self.right_splitter.setStretchFactor(1, 2)
        self.right_splitter.setSizes((600, 270))
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes((430, 1070))

        self.unit_combo.currentIndexChanged.connect(self._method_controls_changed)
        self.baseline_combo.currentIndexChanged.connect(self._method_controls_changed)
        self.show_integration_checkbox.toggled.connect(self._method_controls_changed)
        self.show_retention_checkbox.toggled.connect(self._method_controls_changed)
        self.show_gradient_checkbox.toggled.connect(self._method_controls_changed)
        self.legend_combo.currentIndexChanged.connect(self._method_controls_changed)
        self.zoom_axis_combo.currentIndexChanged.connect(self._zoom_axis_changed)
        self.view_mode_combo.currentIndexChanged.connect(self._view_mode_changed)
        self.integrate_button.toggled.connect(self._toggle_integration)
        self.split_peak_button.toggled.connect(self._toggle_split_mode)
        self.edit_peak_button.toggled.connect(self._toggle_edit_range_mode)
        self.delete_peak_button.clicked.connect(self.delete_peak)
        self.auto_detect_button.clicked.connect(self.auto_detect_peaks)
        self.select_all_peaks_button.clicked.connect(self.peak_table.selectAll)
        self.move_trace_button.toggled.connect(self._toggle_move_mode)
        self.axis_labels_button.clicked.connect(self.edit_axis_labels)
        self.legend_settings_button.clicked.connect(self.edit_legend_composer)
        self.reset_view_button.clicked.connect(self._reset_view)
        self.reset_x_view_button.clicked.connect(self._reset_x_view)
        self.reset_y_view_button.clicked.connect(self._reset_y_view)
        self.peak_table.itemDoubleClicked.connect(self._peak_item_double_clicked)
        self.canvas.mpl_connect("scroll_event", self._on_scroll)
        self.canvas.mpl_connect("button_press_event", self._on_canvas_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_canvas_motion)
        self.canvas.mpl_connect("button_release_event", self._on_canvas_release)

    def _wire_toolbar_navigation_actions(self):
        toolbar_actions = getattr(self.toolbar, "_actions", {}) or {}
        for key in ("pan", "zoom"):
            action = toolbar_actions.get(key)
            if action is not None:
                action.triggered.connect(self._toolbar_navigation_triggered)

    def _toolbar_navigation_triggered(self, *_args):
        for control in (
            self.integrate_button,
            self.edit_peak_button,
            self.split_peak_button,
            self.move_trace_button,
            self.pointer_action,
            self.annotation_action,
        ):
            if control.isChecked():
                control.setChecked(False)

    def _action(self, slot=None, checkable=False):
        action = QAction(self)
        action.setCheckable(checkable)
        if slot is not None:
            action.triggered.connect(slot)
        return action

    def eventFilter(self, watched, event):
        if watched is getattr(self, "peak_table", None):
            key_press_type = (
                QtCore.QEvent.Type.KeyPress if QT_API == 6 else QtCore.QEvent.KeyPress
            )
            delete_key = (
                QtCore.Qt.Key.Key_Delete if QT_API == 6 else QtCore.Qt.Key_Delete
            )
            editing_state = (
                QtWidgets.QAbstractItemView.State.EditingState
                if QT_API == 6
                else QtWidgets.QAbstractItemView.EditingState
            )
            if (
                event.type() == key_press_type
                and event.key() == delete_key
                and self.peak_table.state() != editing_state
            ):
                self.delete_peak()
                event.accept()
                return True
        if watched is self.dataset_table.viewport():
            wheel_type = QtCore.QEvent.Type.Wheel if QT_API == 6 else QtCore.QEvent.Wheel
            shift_modifier = (
                QtCore.Qt.KeyboardModifier.ShiftModifier
                if QT_API == 6
                else QtCore.Qt.ShiftModifier
            )
            if event.type() == wheel_type and event.modifiers() & shift_modifier:
                delta = event.angleDelta().y() or event.angleDelta().x()
                bar = self.dataset_table.horizontalScrollBar()
                bar.setValue(bar.value() - int(delta))
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _capture_analysis_state(self):
        dataset_fields = (
            "run_id",
            "label",
            "short_label",
            "measurement",
            "gradient_preset_name",
            "y_axis",
            "x_shift_min",
            "offset",
            "visible",
            "color",
            "peaks",
        )
        return {
            "method": deepcopy(self.project.method),
            "runs": deepcopy(self.project.runs),
            "annotations": deepcopy(self.project.annotations),
            "condition_presets": deepcopy(self.project.condition_presets),
            "gradient_presets": deepcopy(self.project.gradient_presets),
            "dataset_order": [dataset.id for dataset in self.project.datasets],
            "datasets": {
                dataset.id: {
                    field: deepcopy(getattr(dataset, field)) for field in dataset_fields
                }
                for dataset in self.project.datasets
            },
        }

    def _restore_analysis_state(self, state):
        self.project.method = deepcopy(state["method"])
        self.project.runs = deepcopy(state.get("runs", self.project.runs))
        self.project.annotations = deepcopy(state.get("annotations", []))
        self.project.condition_presets = deepcopy(state["condition_presets"])
        self.project.gradient_presets = deepcopy(state["gradient_presets"])
        order = state.get("dataset_order", [])
        if order:
            by_id = {dataset.id: dataset for dataset in self.project.datasets}
            reordered = [by_id[item_id] for item_id in order if item_id in by_id]
            reordered.extend(
                dataset
                for dataset in self.project.datasets
                if dataset.id not in set(order)
            )
            self.project.datasets[:] = reordered
        datasets = state.get("datasets", {})
        for dataset in self.project.datasets:
            values = datasets.get(dataset.id)
            if values is None:
                continue
            for field, value in values.items():
                setattr(dataset, field, deepcopy(value))
        self.project.rebuild_run_index(create_missing=False)

    def _push_undo_snapshot(self, state, label: str):
        self._undo_stack.append((label, state))
        self._undo_stack = self._undo_stack[-50:]
        self._redo_stack = []
        self._update_undo_actions()

    def _history_label(self, japanese: str, english: str) -> str:
        return japanese if self._application_language == "ja" else english

    def _reset_undo_history(self):
        self._undo_stack = []
        self._redo_stack = []
        self._update_undo_actions()

    def _update_undo_actions(self):
        if not hasattr(self, "undo_action"):
            return
        self.undo_action.setEnabled(bool(self._undo_stack))
        self.redo_action.setEnabled(bool(self._redo_stack))
        undo_text = self.translator("undo")
        redo_text = self.translator("redo")
        if self._undo_stack:
            undo_text += " — " + self._undo_stack[-1][0]
        if self._redo_stack:
            redo_text += " — " + self._redo_stack[-1][0]
        self.undo_action.setText(undo_text)
        self.redo_action.setText(redo_text)

    def undo(self):
        if not self._undo_stack:
            return
        selected = self._selected_dataset()
        selected_id = selected.id if selected is not None else ""
        label, state = self._undo_stack.pop()
        self._redo_stack.append((label, self._capture_analysis_state()))
        self._restore_analysis_state(state)
        self.project.dirty = True
        selected_row = next(
            (
                index
                for index, dataset in enumerate(self.project.datasets)
                if dataset.id == selected_id
            ),
            self.dataset_table.currentRow(),
        )
        self._refresh_all(selected_row)
        self._update_undo_actions()

    def redo(self):
        if not self._redo_stack:
            return
        selected = self._selected_dataset()
        selected_id = selected.id if selected is not None else ""
        label, state = self._redo_stack.pop()
        self._undo_stack.append((label, self._capture_analysis_state()))
        self._restore_analysis_state(state)
        self.project.dirty = True
        selected_row = next(
            (
                index
                for index, dataset in enumerate(self.project.datasets)
                if dataset.id == selected_id
            ),
            self.dataset_table.currentRow(),
        )
        self._refresh_all(selected_row)
        self._update_undo_actions()

    def set_all_datasets_visible(self, visible: bool):
        if not self.project.datasets or all(dataset.visible == visible for dataset in self.project.datasets):
            return
        before = self._capture_analysis_state()
        for dataset in self.project.datasets:
            dataset.visible = visible
        self._push_undo_snapshot(
            before,
            self._history_label("すべて表示", "Show all")
            if visible
            else self._history_label("すべて非表示", "Hide all"),
        )
        self.project.dirty = True
        self._refresh_dataset_table(self.dataset_table.currentRow())
        self._plot()
        self._update_title()

    def move_selected_dataset(self, direction: int):
        row = self.dataset_table.currentRow()
        target = row + (-1 if direction < 0 else 1)
        self.move_dataset_to(row, target)

    def move_dataset_to(self, row: int, target: int) -> bool:
        if (
            not (0 <= row < len(self.project.datasets))
            or not (0 <= target < len(self.project.datasets))
            or row == target
        ):
            return False
        before = self._capture_analysis_state()
        dataset = self.project.datasets.pop(row)
        self.project.datasets.insert(target, dataset)
        self._push_undo_snapshot(
            before,
            self._history_label(
                "クロマトグラムの順番", "Reorder chromatograms"
            ),
        )
        self.project.dirty = True
        self._refresh_dataset_table(target)
        self._plot()
        self._update_title()
        return True

    def _update_dataset_order_buttons(self):
        row = self.dataset_table.currentRow()
        count = len(self.project.datasets)
        self.move_dataset_up_button.setEnabled(0 < row < count)
        self.move_dataset_down_button.setEnabled(0 <= row < count - 1)

    def _build_menus(self):
        bar = self.menuBar()
        self.file_menu = bar.addMenu("")
        self.new_action = self._action(self.new_project)
        self.new_window_action = self._action(self.new_project_in_new_window)
        self.new_action.setShortcut("Ctrl+N")
        self.new_window_action.setShortcut("Ctrl+Shift+N")
        self.open_action = self._action(self.open_project)
        self.save_action = self._action(self.save_project)
        self.save_action.setShortcut(STANDARD_SAVE_SHORTCUT)
        self.save_as_action = self._action(self.save_project_as)
        self.import_action = self._action(self.import_ascii)
        self.import_directory_action = self._action(self.import_directory)
        self.export_figure_action = self._action(self.export_figure)
        self.export_peaks_action = self._action(self.export_peaks)
        self.export_trace_action = self._action(self.export_trace)
        self.export_traces_action = self._action(self.export_visible_traces)
        self.export_metadata_action = self._action(self.export_metadata)
        self.export_report_action = self._action(self.export_report)
        self.print_report_action = self._action(self.print_report)
        self.exit_action = self._action(self.close)
        for action in (
            self.new_action,
            self.new_window_action,
            self.open_action,
            self.save_action,
            self.save_as_action,
        ):
            self.file_menu.addAction(action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.import_action)
        self.file_menu.addAction(self.import_directory_action)
        self.file_menu.addSeparator()
        for action in (
            self.export_figure_action,
            self.export_peaks_action,
            self.export_trace_action,
            self.export_traces_action,
            self.export_metadata_action,
            self.export_report_action,
            self.print_report_action,
        ):
            self.file_menu.addAction(action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.exit_action)

        self.edit_menu = bar.addMenu("")
        self.undo_action = self._action(self.undo)
        self.redo_action = self._action(self.redo)
        self.undo_action.setShortcut("Ctrl+Z")
        self.redo_action.setShortcut("Ctrl+Y")
        self.edit_menu.addAction(self.undo_action)
        self.edit_menu.addAction(self.redo_action)
        self.edit_menu.addSeparator()
        self.edit_menu.addAction(self.annotation_action)

        self.settings_menu = bar.addMenu("")
        self.preferences_action = self._action(self.edit_preferences)
        self.settings_menu.addAction(self.preferences_action)
        self.settings_menu.addSeparator()
        self.language_menu = self.settings_menu.addMenu("")
        self.japanese_action = self._action(lambda: self.set_language("ja"), checkable=True)
        self.english_action = self._action(lambda: self.set_language("en"), checkable=True)
        language_group = QActionGroup(self)
        language_group.setExclusive(True)
        language_group.addAction(self.japanese_action)
        language_group.addAction(self.english_action)
        self.language_menu.addAction(self.japanese_action)
        self.language_menu.addAction(self.english_action)

        self.database_menu = bar.addMenu("")
        self.database_open_action = self._action(self.open_lab_database)
        self.database_sync_action = self._action(
            self.sync_current_project_to_database
        )
        self.database_menu.addAction(self.database_open_action)
        self.database_menu.addAction(self.database_sync_action)

        self.help_menu = bar.addMenu("")
        self.quantitation_help_action = self._action(self.show_quantitation_help)
        self.about_action = self._action(self.about)
        self.help_menu.addAction(self.quantitation_help_action)
        self.help_menu.addSeparator()
        self.help_menu.addAction(self.about_action)
        self._update_undo_actions()

    def _retranslate(self):
        t = self.translator
        self.file_menu.setTitle(t("file"))
        action_texts = (
            (self.new_action, "new"),
            (self.new_window_action, "new_window"),
            (self.open_action, "open"),
            (self.save_action, "save"),
            (self.save_as_action, "save_as"),
            (self.import_action, "import"),
            (self.import_directory_action, "import_directory"),
            (self.export_figure_action, "export_figure"),
            (self.export_peaks_action, "export_peaks"),
            (self.export_trace_action, "export_trace"),
            (self.export_traces_action, "export_traces"),
            (self.export_metadata_action, "export_metadata"),
            (self.export_report_action, "export_report"),
            (self.print_report_action, "print_report"),
            (self.exit_action, "exit"),
            (self.undo_action, "undo"),
            (self.redo_action, "redo"),
            (self.preferences_action, "preferences"),
            (self.database_open_action, "database_open"),
            (self.database_sync_action, "database_sync"),
            (self.japanese_action, "japanese"),
            (self.english_action, "english"),
            (self.quantitation_help_action, "quantitation_help"),
            (self.about_action, "about"),
        )
        for action, key in action_texts:
            action.setText(t(key))
        self.edit_menu.setTitle(t("edit"))
        self.settings_menu.setTitle(t("settings"))
        self.database_menu.setTitle(t("database"))
        self.language_menu.setTitle(t("language"))
        self.help_menu.setTitle(t("help"))
        self.dataset_title.setText(t("datasets"))
        self.dataset_table.setHorizontalHeaderLabels(
            (
                t("visible"),
                t("run_id"),
                t("label"),
                t("wavelength"),
                t("group"),
                t("y_axis"),
                t("auv"),
                t("x_shift"),
                t("offset"),
                t("color"),
                t("source"),
            )
        )
        self.import_button.setText(t("add"))
        self.remove_button.setText(t("remove"))
        self.metadata_button.setText(t("metadata"))
        self.batch_metadata_button.setText(t("batch_metadata"))
        self.gradient_button.setText(t("gradient"))
        self.color_button.setText(t("change_color"))
        self.show_all_button.setText(t("show_all"))
        self.hide_all_button.setText(t("hide_all"))
        self.move_dataset_up_button.setText(t("move_up"))
        self.move_dataset_down_button.setText(t("move_down"))
        self.group_run_button.setText(t("group_run"))
        self.ungroup_run_button.setText(t("ungroup_run"))
        self.display_group.setTitle(t("display_group"))
        self.navigation_group.setTitle(t("navigation_group"))
        self.integration_group.setTitle(t("integration_group"))
        self.unit_label.setText(t("display_unit"))
        self.baseline_label.setText(t("baseline"))
        baseline_texts = (
            t("baseline_endpoints"),
            t("baseline_edges"),
            t("baseline_constant"),
            t("baseline_manual"),
            t("baseline_zero"),
        )
        for index, text in enumerate(baseline_texts):
            self.baseline_combo.setItemText(index, text)
        self.legend_label.setText(t("legend"))
        self.legend_settings_button.setText(t("legend_settings"))
        legend_texts = (
            t("legend_auto"),
            t("legend_upper_right"),
            t("legend_upper_left"),
            t("legend_lower_right"),
            t("legend_lower_left"),
            t("legend_outside"),
        )
        for index, text in enumerate(legend_texts):
            self.legend_combo.setItemText(index, text)
        self.integrate_button.setText(t("integrate"))
        self.edit_peak_button.setText(t("edit_peak"))
        self.split_peak_button.setText(t("split_peak"))
        self.delete_peak_button.setText(t("delete_peak"))
        self.show_integration_checkbox.setText(t("show_integration"))
        self.show_retention_checkbox.setText(t("show_retention_labels"))
        self.show_gradient_checkbox.setText(t("show_gradient_b"))
        self.reset_view_button.setText(t("reset_view"))
        self.reset_x_view_button.setText(t("reset_x_view"))
        self.reset_y_view_button.setText(t("reset_y_view"))
        self.zoom_axis_label.setText(t("zoom_axis"))
        self.zoom_axis_combo.setItemText(0, t("zoom_auto"))
        self.zoom_axis_combo.setItemText(1, t("zoom_both"))
        self.zoom_axis_combo.setItemText(2, t("zoom_x"))
        self.zoom_axis_combo.setItemText(3, t("zoom_y"))
        self.view_mode_label.setText(t("view_mode"))
        self.view_mode_combo.setItemText(0, t("view_single"))
        self.view_mode_combo.setItemText(1, t("view_overview_detail"))
        self.move_trace_button.setText(t("move_trace"))
        self.pointer_action.setText(t("pointer_line"))
        self.pointer_action.setToolTip(t("pointer_hint"))
        self.pointer_toolbar_button.setAccessibleName(t("pointer_line"))
        self.pointer_control_button.setAccessibleName(t("pointer_line"))
        self.axis_labels_button.setText(t("axis_labels"))
        self.annotation_action.setText(t("add_text_annotation"))
        self.annotation_action.setToolTip(t("text_annotation_hint"))
        self.auto_detect_button.setText(t("auto_detect"))
        self.select_all_peaks_button.setText(t("select_all_peaks"))
        self.peak_title.setText(t("peaks"))
        self._set_peak_headers()
        self.japanese_action.setChecked(self._application_language == "ja")
        self.english_action.setChecked(self._application_language == "en")
        self._update_undo_actions()
        self._update_title()
        self._plot()

    def _set_peak_headers(self):
        ja = self._application_language == "ja"
        headers = (
            "#",
            "開始 (min)" if ja else "Start (min)",
            "終了 (min)" if ja else "End (min)",
            "保持時間 (min)" if ja else "Retention (min)",
            "高さ (µV)" if ja else "Height (µV)",
            "面積 (µV·sec)" if ja else "Area (µV·sec)",
            "高さ (mAU)" if ja else "Height (mAU)",
            "面積 (mAU·sec)" if ja else "Area (mAU·sec)",
            "%Area",
            "FWHM (min)",
            "%A",
            "%B",
            "%C",
            "%D",
            "量 (nmol)" if ja else "Amount (nmol)",
            "量 (µg)" if ja else "Amount (µg)",
            "ベースライン" if ja else "Baseline",
            "方法" if ja else "Method",
            "備考" if ja else "Notes",
        )
        self.peak_table.setHorizontalHeaderLabels(headers)

    def set_language(self, language: str):
        self._application_language = language if language in ("ja", "en") else "ja"
        self._settings.set(
            UI_LANGUAGE, self._application_language, sync=True
        )
        self.translator.set_language(self._application_language)
        self._retranslate()

    def _update_title(self):
        marker = "*" if self.project.dirty else ""
        name = Path(self.project.project_path).name if self.project.project_path else self.project.title
        self.setWindowTitle("%s%s — %s %s" % (name, marker, APP_NAME, APP_VERSION))
        if self.project.project_path:
            absolute_path = str(Path(self.project.project_path).absolute())
            location = self.translator(
                "project_location_saved", path=absolute_path
            )
        else:
            location = self.translator("project_location_unsaved")
        elide_mode = (
            QtCore.Qt.TextElideMode.ElideMiddle
            if QT_API == 6
            else QtCore.Qt.ElideMiddle
        )
        self.project_location_label.setText(
            self.project_location_label.fontMetrics().elidedText(
                location, elide_mode, 500
            )
        )
        self.project_location_label.setToolTip(location)
        self.project_location_label.setAccessibleName(location)

    def _refresh_all(self, selected_row: Optional[int] = None):
        self._refresh_dataset_table(selected_row)
        self._refresh_peak_table()
        self._sync_method_controls()
        self._plot()
        self._update_title()

    def _refresh_dataset_table(self, selected_row: Optional[int] = None):
        if selected_row is None:
            selected_row = self.dataset_table.currentRow()
        self._updating_table = True
        self.dataset_table.setRowCount(len(self.project.datasets))
        for row, dataset in enumerate(self.project.datasets):
            show = _read_only_item("")
            show.setCheckState(CHECKED if dataset.visible else UNCHECKED)
            show.setData(USER_ROLE, dataset.id)
            self.dataset_table.setItem(row, DATASET_VISIBLE_COLUMN, show)
            run_id = _read_only_item(dataset.run_id)
            run_id.setData(USER_ROLE, dataset.run_id)
            run_id.setToolTip(dataset.run_id)
            self.dataset_table.setItem(row, DATASET_RUN_ID_COLUMN, run_id)
            label = QtWidgets.QTableWidgetItem(dataset.label)
            label.setData(USER_ROLE, dataset.id)
            self.dataset_table.setItem(row, DATASET_LABEL_COLUMN, label)
            self.dataset_table.setItem(
                row,
                DATASET_WAVELENGTH_COLUMN,
                QtWidgets.QTableWidgetItem(_format(dataset.measurement.wavelength_nm)),
            )
            self.dataset_table.setItem(
                row,
                DATASET_GROUP_COLUMN,
                QtWidgets.QTableWidgetItem(dataset.measurement.group),
            )
            self.dataset_table.setItem(
                row, DATASET_Y_AXIS_COLUMN, QtWidgets.QTableWidgetItem(str(dataset.y_axis))
            )
            self.dataset_table.setItem(
                row,
                DATASET_AUV_COLUMN,
                QtWidgets.QTableWidgetItem(_format(dataset.measurement.aux_range_au_per_v)),
            )
            self.dataset_table.setItem(
                row,
                DATASET_X_SHIFT_COLUMN,
                QtWidgets.QTableWidgetItem(_format(dataset.x_shift_min)),
            )
            self.dataset_table.setItem(
                row,
                DATASET_OFFSET_COLUMN,
                QtWidgets.QTableWidgetItem(_format(dataset.offset)),
            )
            color_value = dataset.color or COLORS[row % len(COLORS)]
            color_item = _read_only_item(color_value)
            color_item.setBackground(QtGui.QColor(color_value))
            color_item.setForeground(QtGui.QColor("#ffffff" if QtGui.QColor(color_value).lightness() < 128 else "#000000"))
            self.dataset_table.setItem(row, DATASET_COLOR_COLUMN, color_item)
            source_text = dataset.original_path or dataset.original_filename
            source = _read_only_item(source_text)
            source.setToolTip(dataset.original_path)
            if QT_API == 6:
                alignment = QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
            else:
                alignment = QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
            source.setTextAlignment(alignment)
            self.dataset_table.setItem(row, DATASET_SOURCE_COLUMN, source)
        self.dataset_table.resizeColumnsToContents()
        self.dataset_table.setColumnWidth(DATASET_RUN_ID_COLUMN, 160)
        self.dataset_table.setColumnWidth(DATASET_SOURCE_COLUMN, 360)
        self.dataset_table.horizontalHeader().setStretchLastSection(True)
        self._updating_table = False
        if self.project.datasets:
            row = min(max(selected_row, 0), len(self.project.datasets) - 1)
            self.dataset_table.selectRow(row)
        self._update_dataset_order_buttons()

    def _selected_dataset(self) -> Optional[Dataset]:
        row = self.dataset_table.currentRow()
        if 0 <= row < len(self.project.datasets):
            return self.project.datasets[row]
        return None

    def _selected_dataset_rows(self):
        selection = self.dataset_table.selectionModel()
        if selection is None:
            return []
        return sorted(
            {
                index.row()
                for index in selection.selectedRows()
                if 0 <= index.row() < len(self.project.datasets)
            }
        )

    def group_selected_runs(self):
        rows = self._selected_dataset_rows()
        current = self.dataset_table.currentRow()
        if len(rows) < 2 or current not in rows:
            QtWidgets.QMessageBox.information(
                self, APP_NAME, self.translator("select_run_group")
            )
            return
        datasets = [self.project.datasets[row] for row in rows]
        if len({dataset.run_id for dataset in datasets}) < 2:
            return
        target = self.project.run_for(self.project.datasets[current])
        answer = QtWidgets.QMessageBox.question(
            self,
            APP_NAME,
            self.translator(
                "confirm_run_group",
                count=len(datasets),
                run_id=target.id,
                label=target.label,
            ),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        before = self._capture_analysis_state()
        self.project.group_datasets_into_run(datasets, target)
        self._push_undo_snapshot(
            before, self._history_label("Runを統合", "Group Runs")
        )
        self.project.dirty = True
        self._refresh_all(current)

    def ungroup_selected_runs(self):
        rows = self._selected_dataset_rows()
        if not rows:
            return
        run_counts = {}
        for dataset in self.project.datasets:
            run_counts[dataset.run_id] = run_counts.get(dataset.run_id, 0) + 1
        datasets = [
            self.project.datasets[row]
            for row in rows
            if run_counts.get(self.project.datasets[row].run_id, 0) > 1
        ]
        if not datasets:
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            APP_NAME,
            self.translator("confirm_run_ungroup", count=len(datasets)),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        before = self._capture_analysis_state()
        self.project.ungroup_datasets(datasets)
        self._push_undo_snapshot(
            before, self._history_label("Runを分離", "Ungroup Runs")
        )
        self.project.dirty = True
        self._refresh_all(rows[0])

    def _dataset_item_changed(self, item: QtWidgets.QTableWidgetItem):
        if self._updating_table:
            return
        row, column = item.row(), item.column()
        if row < 0 or row >= len(self.project.datasets):
            return
        dataset = self.project.datasets[row]
        before = self._capture_analysis_state()
        label_changed = False
        try:
            if column == DATASET_VISIBLE_COLUMN:
                dataset.visible = item.checkState() == CHECKED
            elif column == DATASET_LABEL_COLUMN:
                label_changed = True
                old_label = dataset.label
                dataset.label = item.text().strip() or dataset.original_filename
                if not dataset.short_label or dataset.short_label == old_label:
                    dataset.short_label = dataset.label
            elif column == DATASET_WAVELENGTH_COLUMN:
                text = item.text().strip()
                wavelength = float(text) if text else None
                if wavelength is not None and wavelength <= 0:
                    raise ValueError("Wavelength must be positive")
                dataset.measurement.wavelength_nm = wavelength
                recalculate_dataset_peaks(dataset)
            elif column == DATASET_GROUP_COLUMN:
                dataset.measurement.group = item.text().strip()
            elif column == DATASET_Y_AXIS_COLUMN:
                axis = int(item.text().strip())
                if axis not in (1, 2):
                    raise ValueError("Y axis must be 1 or 2")
                dataset.y_axis = axis
            elif column == DATASET_AUV_COLUMN:
                text = item.text().strip()
                dataset.measurement.aux_range_au_per_v = float(text) if text else None
                if dataset.measurement.aux_range_au_per_v is not None and dataset.measurement.aux_range_au_per_v <= 0:
                    raise ValueError("AU/V must be positive")
                recalculate_dataset_peaks(dataset)
            elif column == DATASET_X_SHIFT_COLUMN:
                dataset.x_shift_min = float(item.text().strip() or "0")
                recalculate_dataset_peaks(dataset)
            elif column == DATASET_OFFSET_COLUMN:
                dataset.offset = float(item.text().strip() or "0")
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, self.translator("warning"), str(exc))
            self._refresh_dataset_table(row)
            return
        self._push_undo_snapshot(
            before, self._history_label("クロマトグラム設定", "Chromatogram settings")
        )
        self.project.dirty = True
        if label_changed:
            self._refresh_dataset_table(row)
        self._refresh_peak_table()
        self._plot()
        self._update_title()

    def _dataset_selection_changed(self):
        self._update_dataset_order_buttons()
        self._refresh_peak_table()
        self._plot()

    def _selected_peak_rows(self):
        selection = self.peak_table.selectionModel()
        if selection is None:
            return []
        return sorted({index.row() for index in selection.selectedRows()})

    def _selected_peak_ids(self):
        ids = []
        for row in self._selected_peak_rows():
            item = self.peak_table.item(row, 0)
            if item is not None and item.data(USER_ROLE):
                ids.append(item.data(USER_ROLE))
        return ids

    def _select_peak_ids(self, peak_ids):
        wanted = set(peak_ids or [])
        if not wanted:
            return
        matching_rows = []
        for row in range(self.peak_table.rowCount()):
            item = self.peak_table.item(row, 0)
            if item is not None and item.data(USER_ROLE) in wanted:
                matching_rows.append(row)
        if not matching_rows:
            return
        self.peak_table.setCurrentCell(matching_rows[0], 0)
        for row in matching_rows:
            selection = QtWidgets.QTableWidgetSelectionRange(
                row, 0, row, self.peak_table.columnCount() - 1
            )
            self.peak_table.setRangeSelected(selection, True)

    def _refresh_peak_table(self, selected_peak_ids=None):
        dataset = self._selected_dataset()
        peaks = dataset.peaks if dataset else []
        if selected_peak_ids is None:
            selected_peak_ids = self._selected_peak_ids()
        self.peak_table.blockSignals(True)
        self.peak_table.setRowCount(len(peaks))
        for row, peak in enumerate(peaks):
            values = (
                str(row + 1),
                _format(peak.start_min),
                _format(peak.end_min),
                _format(peak.retention_time_min),
                _format(peak.raw_height_uv),
                _format(peak.raw_area_uv_sec),
                _format(peak.height_mau),
                _format(peak.area_mau_sec),
                _format(peak.area_percent),
                _format(peak.fwhm_min),
                _format(peak.gradient_a_pct),
                _format(peak.gradient_b_pct),
                _format(peak.gradient_c_pct),
                _format(peak.gradient_d_pct),
                _format(peak.amount_nmol),
                _format(peak.amount_ug),
                peak.baseline_mode,
                "auto" if peak.integration_source == "auto" else "manual",
                peak.notes,
            )
            for column, value in enumerate(values):
                item = (
                    QtWidgets.QTableWidgetItem(value)
                    if column == 18
                    else _read_only_item(value)
                )
                if column == 0:
                    item.setData(USER_ROLE, peak.id)
                self.peak_table.setItem(row, column, item)
        self.peak_table.resizeColumnsToContents()
        self.peak_table.setColumnWidth(18, 240)
        self.peak_table.clearSelection()
        self._select_peak_ids(selected_peak_ids)
        self.peak_table.blockSignals(False)

    def _peak_selection_changed(self):
        if self.edit_peak_button.isChecked():
            dataset = self._selected_dataset()
            row = self.peak_table.currentRow()
            if dataset is not None and 0 <= row < len(dataset.peaks):
                self._edit_range_peak_id = dataset.peaks[row].id
        if self._is_lightweight_rendering() and self._peak_overlay_artists:
            self._apply_peak_selection_styles()
            self._request_canvas_draw(throttled=True)
        else:
            self._plot()

    def _apply_peak_selection_styles(self):
        """Restyle existing integration artists without rebuilding the figure."""

        selected_ids = set(self._selected_peak_ids())
        for peak_id, overlay in self._peak_overlay_artists.items():
            selected = peak_id in selected_ids
            color = "#f59e0b" if selected else overlay["base_color"]
            patch = overlay.get("patch")
            if patch is not None:
                patch.set_facecolor(color)
                patch.set_alpha(0.24 if selected else 0.08)
            for boundary_line in overlay.get("boundary_lines", []):
                boundary_line.set_alpha(0.9 if selected else 0.55)
                boundary_line.set_linewidth(1.15 if selected else 0.8)
            retention_line = overlay.get("retention_line")
            if retention_line is not None:
                retention_line.set_color(color)
                retention_line.set_alpha(0.75 if selected else 0.35)
                retention_line.set_linewidth(1.1 if selected else 0.8)
            baseline_line = overlay.get("baseline_line")
            if baseline_line is not None:
                baseline_line.set_color(color)
                baseline_line.set_alpha(0.95 if selected else 0.55)
                baseline_line.set_linewidth(1.4 if selected else 0.9)

    def _peak_item_double_clicked(self, item):
        if item.column() == 18:
            self.peak_table.editItem(item)
            return
        self.edit_peak_properties()

    def _peak_item_changed(self, item):
        if self._updating_table or item.column() != 18:
            return
        dataset = self._selected_dataset()
        row = item.row()
        if dataset is None or not (0 <= row < len(dataset.peaks)):
            return
        peak = dataset.peaks[row]
        notes = item.text().strip()
        if notes == peak.notes:
            return
        before = self._capture_analysis_state()
        peak.notes = notes
        self._push_undo_snapshot(
            before, self._history_label("ピーク備考", "Peak notes")
        )
        self.project.dirty = True
        self._update_title()

    def _sync_method_controls(self):
        index = self.unit_combo.findData(self.project.method.display_unit)
        self.unit_combo.blockSignals(True)
        self.unit_combo.setCurrentIndex(max(0, index))
        self.unit_combo.blockSignals(False)
        self.baseline_combo.blockSignals(True)
        self.baseline_combo.setCurrentIndex(max(0, self.baseline_combo.findData(self.project.method.baseline_mode)))
        self.baseline_combo.blockSignals(False)
        self.show_integration_checkbox.blockSignals(True)
        self.show_integration_checkbox.setChecked(self.project.method.show_integration_areas)
        self.show_integration_checkbox.blockSignals(False)
        self.show_retention_checkbox.blockSignals(True)
        self.show_retention_checkbox.setChecked(self.project.method.show_retention_labels)
        self.show_retention_checkbox.blockSignals(False)
        self.show_gradient_checkbox.blockSignals(True)
        self.show_gradient_checkbox.setChecked(self.project.method.show_gradient_b)
        self.show_gradient_checkbox.blockSignals(False)
        self.legend_combo.blockSignals(True)
        self.legend_combo.setCurrentIndex(max(0, self.legend_combo.findData(self.project.method.legend_location)))
        self.legend_combo.blockSignals(False)
        self.zoom_axis_combo.blockSignals(True)
        self.zoom_axis_combo.setCurrentIndex(max(0, self.zoom_axis_combo.findData(self.project.method.zoom_axis)))
        self.zoom_axis_combo.blockSignals(False)
        self.view_mode_combo.blockSignals(True)
        self.view_mode_combo.setCurrentIndex(
            max(0, self.view_mode_combo.findData(self.project.method.view_mode))
        )
        self.view_mode_combo.blockSignals(False)

    def _method_controls_changed(self, *_args):
        old_unit = self.project.method.display_unit
        new_unit = self.unit_combo.currentData()
        self.project.method.display_unit = new_unit
        self.project.method.baseline_mode = self.baseline_combo.currentData()
        self.project.method.show_integration_areas = self.show_integration_checkbox.isChecked()
        self.project.method.show_retention_labels = self.show_retention_checkbox.isChecked()
        self.project.method.show_gradient_b = self.show_gradient_checkbox.isChecked()
        self.project.method.legend_location = self.legend_combo.currentData()
        self.project.dirty = True
        self._plot()
        self._update_title()
        if new_unit != old_unit and new_unit in ("mAU", "AU"):
            if any(
                dataset.visible
                and (dataset.measurement.aux_range_au_per_v is None or dataset.measurement.aux_range_au_per_v <= 0)
                for dataset in self.project.datasets
            ):
                QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("missing_auv"))

    def _zoom_axis_changed(self, *_args):
        self.project.method.zoom_axis = self.zoom_axis_combo.currentData() or "auto"
        self.project.dirty = True
        self._update_title()

    def _view_mode_changed(self, *_args):
        mode = self.view_mode_combo.currentData() or "single"
        if mode == self.project.method.view_mode:
            return
        before = self._capture_analysis_state()
        self.project.method.view_mode = mode
        self._push_undo_snapshot(
            before, self._history_label("表示モード", "View mode")
        )
        self.project.dirty = True
        self._plot()
        self._update_title()

    def _axis_labels(self):
        x_label = "Retention time (min)"
        y_labels = {
            "uV": "Intensity (µV)",
            "mAU": "Absorbance (mAU)",
            "AU": "Absorbance (AU)",
            "normalized": "Normalized intensity",
        }
        return x_label, y_labels[self.project.method.display_unit]

    @staticmethod
    def _set_text_font(text, family: str, size: float, color: str):
        resolved = _resolved_plot_font(family)
        if resolved:
            text.set_fontfamily(resolved)
        text.set_fontsize(size)
        text.set_color(color)

    def _apply_plot_text_styles(self):
        method = self.project.method
        axis_color = method.axis_label_color or "#000000"
        tick_color = method.tick_label_color or "#000000"
        for axis in (self.axes, self.axes_right, self.axes_gradient):
            if axis is None:
                continue
            self._set_text_font(
                axis.xaxis.label,
                method.axis_label_font_family,
                method.axis_label_font_size,
                axis_color,
            )
            self._set_text_font(
                axis.yaxis.label,
                method.axis_label_font_family,
                method.axis_label_font_size,
                axis_color,
            )
            axis.tick_params(
                axis="both",
                which="major",
                labelsize=method.tick_label_font_size,
                colors=tick_color,
            )
            for tick_label in list(axis.get_xticklabels()) + list(axis.get_yticklabels()):
                resolved = _resolved_plot_font(method.tick_label_font_family)
                if resolved:
                    tick_label.set_fontfamily(resolved)

    def _capture_view_state(self):
        if not self._view_initialized or not hasattr(self, "axes"):
            return None
        state = {"x": self.axes.get_xlim(), "y1": self.axes.get_ylim()}
        if self.axes_right is not None:
            state["y2"] = self.axes_right.get_ylim()
        if self.axes_gradient is not None:
            state["gradient"] = self.axes_gradient.get_ylim()
        return state

    def _apply_view_state(self, state):
        if not state:
            return
        self.axes.set_xlim(*state["x"])
        self.axes.set_ylim(*state["y1"])
        if self.axes_right is not None and "y2" in state:
            self.axes_right.set_ylim(*state["y2"])
        if self.axes_gradient is not None and "gradient" in state:
            self.axes_gradient.set_ylim(*state["gradient"])
        self._set_dynamic_x_ticks()
        self._update_overview_window()
        self._request_canvas_draw(force=True, refresh_series=True)

    def _push_view_history(self):
        state = self._capture_view_state()
        if state is None:
            return
        if not self._view_history or self._view_history[-1] != state:
            self._view_history.append(state)
            self._view_history = self._view_history[-50:]

    def _back_to_previous_view(self):
        nav_stack = getattr(self.toolbar, "_nav_stack", None)
        if getattr(nav_stack, "_pos", -1) > 0:
            self.toolbar.back()
            return
        if self._view_history:
            self._apply_view_state(self._view_history.pop())

    @staticmethod
    def _nice_tick_step(target: float) -> float:
        if target <= 0 or not math.isfinite(target):
            return 1.0
        magnitude = 10.0 ** math.floor(math.log10(target))
        fraction = target / magnitude
        if fraction <= 1.0:
            nice = 1.0
        elif fraction <= 2.0:
            nice = 2.0
        elif fraction <= 5.0:
            nice = 5.0
        else:
            nice = 10.0
        return nice * magnitude

    def _set_dynamic_x_ticks(self):
        left, right = self.axes.get_xlim()
        span = max(abs(right - left), 1.0e-9)
        if self.project.method.x_tick_mode == "manual":
            major_tick = max(float(self.project.method.x_major_tick_min), 1.0e-9)
            minor_tick = max(float(self.project.method.x_minor_tick_min), 1.0e-9)
        else:
            major_tick = self._nice_tick_step(span / 18.0)
            minor_tick = major_tick / 5.0
        self.axes.xaxis.set_major_locator(MultipleLocator(major_tick))
        self.axes.xaxis.set_minor_locator(MultipleLocator(minor_tick))
        self.axes.tick_params(axis="x", which="minor", length=3, labelbottom=False)

    def _connect_axes_callbacks(self):
        self._xlim_callback_id = self.axes.callbacks.connect(
            "xlim_changed", self._on_xlim_changed
        )

    def _on_xlim_changed(self, _axis):
        if self._tick_update_guard:
            return
        self._tick_update_guard = True
        try:
            self._set_dynamic_x_ticks()
            self._update_overview_window()
        finally:
            self._tick_update_guard = False
        self._request_canvas_draw(throttled=True, refresh_series=True)

    def _update_overview_window(self):
        if self.axes_overview is None:
            return
        if self._overview_view_patch is not None:
            try:
                left, right = self.axes.get_xlim()
                if hasattr(self._overview_view_patch, "set_x"):
                    self._overview_view_patch.set_x(left)
                    self._overview_view_patch.set_width(right - left)
                else:
                    self._overview_view_patch.set_xy(
                        ((left, 0.0), (left, 1.0), (right, 1.0), (right, 0.0))
                    )
                return
            except (ValueError, AttributeError, RuntimeError):
                self._overview_view_patch = None
        left, right = self.axes.get_xlim()
        self._overview_view_patch = self.axes_overview.axvspan(
            left,
            right,
            facecolor="#2563eb",
            edgecolor="#1d4ed8",
            linewidth=0.8,
            alpha=0.14,
            zorder=10,
        )

    def _annotation_axis(self, annotation: TextAnnotation):
        if annotation.y_axis == 2 and self.axes_right is not None:
            return self.axes_right
        return self.axes

    def _draw_text_annotations(self):
        self._annotation_artists = {}
        datasets = {dataset.id: dataset for dataset in self.project.datasets}
        for annotation in self.project.annotations:
            if not annotation.text.strip():
                continue
            dataset = datasets.get(annotation.dataset_id)
            if dataset is not None and not dataset.visible:
                continue
            axis = self._annotation_axis(annotation)
            artist = axis.text(
                annotation.x_min,
                annotation.y_value,
                annotation.text,
                ha="left",
                va="bottom",
                fontfamily=_resolved_plot_font(annotation.font_family or "Arial"),
                fontsize=annotation.font_size,
                color=annotation.color or "#000000",
                bbox={
                    "boxstyle": "round,pad=0.28",
                    "facecolor": annotation.background_color or "#ffffff",
                    "edgecolor": annotation.border_color or "#6b7280",
                    "linewidth": 0.8,
                    "alpha": 0.9,
                },
                zorder=30,
                picker=True,
            )
            self._annotation_artists[annotation.id] = artist

    def _plot(self, preserve_view: bool = True):
        if not hasattr(self, "axes"):
            return
        view_state = self._capture_view_state() if preserve_view else None
        if self._span_selector is not None:
            self._span_selector.set_active(False)
            self._span_selector = None
            self._span_selector_mode = None
        self._interaction_cursor = None
        self._annotation_artists = {}
        self._annotation_drag = None
        self._overview_view_patch = None
        self.figure.clear()
        if self.project.method.view_mode == "overview_detail":
            grid = self.figure.add_gridspec(2, 1, height_ratios=(1.0, 3.0))
            self.axes_overview = self.figure.add_subplot(grid[0, 0])
            self.axes = self.figure.add_subplot(grid[1, 0])
            self.axes_overview.set_navigate(False)
            self.axes_overview.set_title(
                "Overview",
                loc="left",
                fontsize=8,
                color="#4b5563",
            )
            self.axes_overview.tick_params(axis="x", labelbottom=False)
        else:
            self.axes_overview = None
            self.axes = self.figure.add_subplot(111)
        self.axes_right = None
        self.axes_gradient = None
        self.axes_overview_right = None
        self._dataset_lines = {}
        self._overview_dataset_lines = {}
        self._plot_source_cache = {}
        self._peak_overlay_artists = {}
        # figure.clear() invalidates the axes stored in Matplotlib's own
        # navigation history. Resetting the toolbar prevents stale axes from
        # making pan/zoom appear unresponsive after a redraw.
        self.toolbar.update()
        unit = self.project.method.display_unit
        selected = self._selected_dataset()
        visible = [dataset for dataset in self.project.datasets if dataset.visible]
        if any(dataset.y_axis == 2 for dataset in visible):
            self.axes_right = self.axes.twinx()
            if self.axes_overview is not None:
                self.axes_overview_right = self.axes_overview.twinx()
                self.axes_overview_right.set_navigate(False)
        if (
            self.project.method.show_gradient_b
            and selected is not None
            and selected.visible
            and selected.measurement.gradient
        ):
            self.axes_gradient = self.axes.twinx()
            if self.axes_right is not None:
                self.axes_gradient.spines["right"].set_position(("outward", 62))

        plotted = 0
        for index, dataset in enumerate(self.project.datasets):
            if not dataset.visible:
                continue
            try:
                values = display_values(dataset, unit)
            except ValueError:
                continue
            color = dataset.color or COLORS[index % len(COLORS)]
            label = self.project.legend_label_for(dataset)
            target_axes = self.axes_right if dataset.y_axis == 2 and self.axes_right is not None else self.axes
            full_x = dataset.time_min + dataset.x_shift_min
            full_y = values + dataset.offset
            self._plot_source_cache[dataset.id] = (full_x, full_y)
            screen_x, screen_y = self._screen_data(
                full_x,
                full_y,
                target_axes,
            )
            line = target_axes.plot(
                screen_x,
                screen_y,
                label=label,
                color=color,
                linewidth=self.project.method.line_width,
                antialiased=not self._is_lightweight_rendering(),
            )[0]
            self._dataset_lines[dataset.id] = line
            if self.axes_overview is not None:
                overview_target = (
                    self.axes_overview_right
                    if dataset.y_axis == 2 and self.axes_overview_right is not None
                    else self.axes_overview
                )
                overview_x, overview_y = self._screen_data(
                    full_x,
                    full_y,
                    overview_target,
                    overview=True,
                )
                overview_line = overview_target.plot(
                    overview_x,
                    overview_y,
                    color=color,
                    linewidth=max(0.6, self.project.method.line_width * 0.75),
                    alpha=0.9,
                    antialiased=not self._is_lightweight_rendering(),
                )[0]
                self._overview_dataset_lines[dataset.id] = overview_line
            plotted += 1
            if dataset is selected and (
                self.project.method.show_integration_areas
                or self.project.method.show_retention_labels
            ):
                selected_peak_rows = set(self._selected_peak_rows())
                for peak_index, peak in enumerate(dataset.peaks):
                    is_selected_peak = peak_index in selected_peak_rows
                    peak_color = "#f59e0b" if is_selected_peak else color
                    overlay = {
                        "base_color": color,
                        "patch": None,
                        "boundary_lines": [],
                        "retention_line": None,
                        "baseline_line": None,
                    }
                    if self.project.method.show_integration_areas:
                        overlay["patch"] = target_axes.axvspan(
                            peak.start_min + dataset.x_shift_min,
                            peak.end_min + dataset.x_shift_min,
                            color=peak_color,
                            alpha=0.24 if is_selected_peak else 0.08,
                        )
                        for boundary in (peak.start_min, peak.end_min):
                            boundary_line = target_axes.axvline(
                                boundary + dataset.x_shift_min,
                                color=INTEGRATION_BOUNDARY_COLOR,
                                alpha=0.9 if is_selected_peak else 0.55,
                                linewidth=1.15 if is_selected_peak else 0.8,
                                linestyle="--",
                            )
                            overlay["boundary_lines"].append(boundary_line)
                        if peak.retention_time_min is not None:
                            overlay["retention_line"] = target_axes.axvline(
                                peak.retention_time_min + dataset.x_shift_min,
                                color=peak_color,
                                alpha=0.75 if is_selected_peak else 0.35,
                                linewidth=1.1 if is_selected_peak else 0.8,
                            )
                        baseline_time, baseline_uv = baseline_trace(dataset, peak)
                        if baseline_time.size:
                            baseline_values = reference_values_for_display(dataset, baseline_uv, unit)
                            overlay["baseline_line"] = target_axes.plot(
                                baseline_time + dataset.x_shift_min,
                                baseline_values + dataset.offset,
                                color=peak_color,
                                linestyle="--",
                                linewidth=1.4 if is_selected_peak else 0.9,
                                alpha=0.95 if is_selected_peak else 0.55,
                                antialiased=not self._is_lightweight_rendering(),
                            )[0]
                    if self.project.method.show_retention_labels and peak.retention_time_min is not None:
                        retention = float(peak.retention_time_min)
                        label_y = float(np.interp(retention, dataset.time_min, values)) + dataset.offset
                        target_axes.annotate(
                            "%.2f" % (retention + dataset.x_shift_min),
                            xy=(retention + dataset.x_shift_min, label_y),
                            xytext=(0, 5),
                            textcoords="offset points",
                            ha="center",
                            va="bottom",
                            rotation=90,
                            fontfamily=(
                                _resolved_plot_font(
                                    self.project.method.retention_label_font_family
                                )
                            ),
                            fontsize=self.project.method.retention_label_font_size,
                            color=(
                                self.project.method.retention_label_color
                                or "#000000"
                            ),
                        )
                    self._peak_overlay_artists[peak.id] = overlay

        if self.axes_gradient is not None and selected is not None:
            ordered_gradient = sorted(selected.measurement.gradient, key=lambda point: point.time_min)
            gradient_label = "%%B (%s)" % self.project.legend_label_for(selected)
            gradient_x = np.asarray(
                [point.time_min for point in ordered_gradient], dtype=float
            )
            gradient_y = np.asarray(
                [point.b_pct for point in ordered_gradient], dtype=float
            )
            gradient_screen_x, gradient_screen_y = self._screen_data(
                gradient_x,
                gradient_y,
                self.axes_gradient,
                overview=True,
            )
            self.axes_gradient.plot(
                gradient_screen_x,
                gradient_screen_y,
                color="#111827",
                linestyle=":",
                linewidth=1.3,
                label=gradient_label,
                antialiased=not self._is_lightweight_rendering(),
            )
            self.axes_gradient.set_ylim(0.0, 100.0)
            self.axes_gradient.set_ylabel(
                self.project.method.gradient_axis_label.strip() or "Mobile phase B (%)"
            )

        self._draw_text_annotations()

        x_label, y_label = self._axis_labels()
        axis_1_label = "Y axis 1"
        axis_2_label = "Y axis 2"
        self.axes.set_xlabel(self.project.method.x_axis_label.strip() or x_label)
        self.axes.set_ylabel(
            self.project.method.y_axis_1_label.strip() or "%s — %s" % (y_label, axis_1_label)
        )
        if self.axes_right is not None:
            self.axes_right.set_ylabel(
                self.project.method.y_axis_2_label.strip() or "%s — %s" % (y_label, axis_2_label)
            )
        self.axes.grid(False)

        times = [float(dataset.time_min[-1] + dataset.x_shift_min) for dataset in visible if dataset.time_min.size]
        times.extend(
            max(point.time_min for point in dataset.measurement.gradient)
            for dataset in visible
            if dataset.measurement.gradient
        )
        if not times:
            times = [float(dataset.time_min[-1] + dataset.x_shift_min) for dataset in self.project.datasets if dataset.time_min.size]
        if times:
            full_bounds = self._full_x_bounds()
            self.axes.set_xlim(*full_bounds)
            self.axes.margins(x=0)
            if self.axes_overview is not None:
                self.axes_overview.set_xlim(*full_bounds)
                self.axes_overview.margins(x=0)
                self.axes_overview.tick_params(
                    axis="both", which="both", labelleft=False, labelbottom=False
                )
                if self.axes_overview_right is not None:
                    self.axes_overview_right.tick_params(
                        axis="both", which="both", labelright=False, labelbottom=False
                    )
        if view_state is not None:
            self.axes.set_xlim(*view_state["x"])
            self.axes.set_ylim(*view_state["y1"])
            if self.axes_right is not None and "y2" in view_state:
                self.axes_right.set_ylim(*view_state["y2"])
            if self.axes_gradient is not None and "gradient" in view_state:
                self.axes_gradient.set_ylim(*view_state["gradient"])
        self._set_dynamic_x_ticks()
        self._apply_plot_text_styles()
        self._connect_axes_callbacks()
        self._update_overview_window()

        if plotted:
            handles = [
                self._dataset_lines[dataset.id]
                for dataset in self.project.datasets
                if dataset.visible and dataset.id in self._dataset_lines
            ]
            labels = [
                self.project.legend_label_for(dataset)
                for dataset in self.project.datasets
                if dataset.visible and dataset.id in self._dataset_lines
            ]
            if self.axes_gradient is not None:
                gradient_handles, gradient_labels = (
                    self.axes_gradient.get_legend_handles_labels()
                )
                handles.extend(gradient_handles)
                labels.extend(gradient_labels)
            location = self.project.method.legend_location
            if location == "outside right":
                if self.axes_gradient is not None:
                    outside_anchor = 1.34
                elif self.axes_right is not None:
                    outside_anchor = 1.14
                else:
                    outside_anchor = 1.02
                legend = self.axes.legend(
                    handles,
                    labels,
                    loc="upper left",
                    bbox_to_anchor=(outside_anchor, 1.0),
                    frameon=False,
                )
            else:
                legend = self.axes.legend(handles, labels, loc=location, frameon=False)
            for legend_text in legend.get_texts():
                if _resolved_plot_font(self.project.method.legend_font_family):
                    legend_text.set_fontfamily(
                        _resolved_plot_font(self.project.method.legend_font_family)
                    )
                legend_text.set_fontsize(self.project.method.legend_font_size)
                legend_text.set_color(
                    self.project.method.legend_font_color or "#000000"
                )
            legend.set_draggable(True)
        self._view_initialized = bool(times)
        if self._is_lightweight_rendering():
            self._refresh_screen_series_for_view()
        self._request_canvas_draw()
        if self.integrate_button.isChecked():
            self._install_span_selector("integrate")
        elif self.edit_peak_button.isChecked():
            self._install_span_selector("edit")
        if (
            self.integrate_button.isChecked()
            or self.edit_peak_button.isChecked()
            or self.split_peak_button.isChecked()
            or self.pointer_button.isChecked()
        ):
            self._ensure_interaction_cursor()

    def _install_span_selector(self, mode: str = "integrate"):
        callback = (
            self._on_edit_span_selected if mode == "edit" else self._on_span_selected
        )
        color = "#f59e0b" if mode == "edit" else "#2563eb"
        self._span_selector = SpanSelector(
            self.axes,
            callback,
            "horizontal",
            useblit=True,
            props=dict(alpha=0.25, facecolor=color),
            interactive=True,
        )
        self._span_selector_mode = mode

    def _ensure_interaction_cursor(self):
        if self._interaction_cursor is None:
            self._interaction_cursor = self.axes.axvline(
                0.0,
                color="#2563eb",
                linestyle="--",
                linewidth=1.0,
                alpha=0.8,
                visible=False,
                zorder=20,
            )

    def _hide_interaction_cursor(self):
        if self._interaction_cursor is not None:
            self._interaction_cursor.set_visible(False)
            self._request_canvas_draw(throttled=True)

    def _deactivate_toolbar_navigation(self):
        mode = str(getattr(self.toolbar, "mode", "")).lower()
        if "pan" in mode:
            self.toolbar.pan()
        elif "zoom" in mode:
            self.toolbar.zoom()

    def _toggle_integration(self, enabled: bool):
        if enabled:
            if self._selected_dataset() is None:
                QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("no_dataset"))
                self.integrate_button.setChecked(False)
                return
            self._deactivate_toolbar_navigation()
            self.edit_peak_button.setChecked(False)
            self.split_peak_button.setChecked(False)
            self.move_trace_button.setChecked(False)
            self.pointer_button.setChecked(False)
            self.annotation_action.setChecked(False)
            self.statusBar().showMessage(self.translator("integration_hint"))
            self._install_span_selector("integrate")
            self._ensure_interaction_cursor()
        else:
            self.statusBar().clearMessage()
            if self._span_selector is not None:
                self._span_selector.set_active(False)
                self._span_selector = None
                self._span_selector_mode = None
            if not (
                self.edit_peak_button.isChecked()
                or self.split_peak_button.isChecked()
                or self.pointer_button.isChecked()
            ):
                self._hide_interaction_cursor()

    def _toggle_edit_range_mode(self, enabled: bool):
        if enabled:
            dataset = self._selected_dataset()
            row = self.peak_table.currentRow()
            if dataset is None or not (0 <= row < len(dataset.peaks)):
                QtWidgets.QMessageBox.information(
                    self, APP_NAME, self.translator("select_peak")
                )
                self.edit_peak_button.setChecked(False)
                return
            self._edit_range_peak_id = dataset.peaks[row].id
            self._deactivate_toolbar_navigation()
            self.integrate_button.setChecked(False)
            self.split_peak_button.setChecked(False)
            self.move_trace_button.setChecked(False)
            self.pointer_button.setChecked(False)
            self.annotation_action.setChecked(False)
            self.statusBar().showMessage(self.translator("edit_range_hint"))
            self._install_span_selector("edit")
            self._ensure_interaction_cursor()
        else:
            self._edit_range_peak_id = None
            if self._span_selector is not None and self._span_selector_mode == "edit":
                self._span_selector.set_active(False)
                self._span_selector = None
                self._span_selector_mode = None
            if not (
                self.integrate_button.isChecked()
                or self.split_peak_button.isChecked()
                or self.pointer_button.isChecked()
            ):
                self.statusBar().clearMessage()
                self._hide_interaction_cursor()

    def _toggle_split_mode(self, enabled: bool):
        if enabled:
            dataset = self._selected_dataset()
            row = self.peak_table.currentRow()
            if dataset is None or not (0 <= row < len(dataset.peaks)):
                QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("select_peak"))
                self.split_peak_button.setChecked(False)
                return
            self._deactivate_toolbar_navigation()
            self.integrate_button.setChecked(False)
            self.edit_peak_button.setChecked(False)
            self.move_trace_button.setChecked(False)
            self.pointer_button.setChecked(False)
            self.annotation_action.setChecked(False)
            self.statusBar().showMessage(self.translator("split_hint"))
            self._ensure_interaction_cursor()
        else:
            if not (
                self.integrate_button.isChecked()
                or self.edit_peak_button.isChecked()
                or self.move_trace_button.isChecked()
            ):
                self.statusBar().clearMessage()
            if not (
                self.integrate_button.isChecked()
                or self.edit_peak_button.isChecked()
                or self.pointer_button.isChecked()
            ):
                self._hide_interaction_cursor()

    def _toggle_move_mode(self, enabled: bool):
        if enabled:
            if self._selected_dataset() is None:
                QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("no_dataset"))
                self.move_trace_button.setChecked(False)
                return
            self._deactivate_toolbar_navigation()
            self.integrate_button.setChecked(False)
            self.edit_peak_button.setChecked(False)
            self.split_peak_button.setChecked(False)
            self.pointer_button.setChecked(False)
            self.annotation_action.setChecked(False)
            self.statusBar().showMessage(self.translator("move_hint"))
        else:
            self._move_drag = None
            if not (
                self.integrate_button.isChecked()
                or self.edit_peak_button.isChecked()
                or self.split_peak_button.isChecked()
            ):
                self.statusBar().clearMessage()

    def _toggle_pointer_mode(self, enabled: bool):
        if enabled:
            self._deactivate_toolbar_navigation()
            self.integrate_button.setChecked(False)
            self.edit_peak_button.setChecked(False)
            self.split_peak_button.setChecked(False)
            self.move_trace_button.setChecked(False)
            self.annotation_action.setChecked(False)
            self.statusBar().showMessage(self.translator("pointer_hint"))
            self._ensure_interaction_cursor()
        else:
            if not (
                self.integrate_button.isChecked()
                or self.edit_peak_button.isChecked()
                or self.split_peak_button.isChecked()
            ):
                self.statusBar().clearMessage()
                self._hide_interaction_cursor()

    def _toggle_annotation_mode(self, enabled: bool):
        if enabled:
            if not self.project.datasets:
                QtWidgets.QMessageBox.information(
                    self, APP_NAME, self.translator("no_dataset")
                )
                self.annotation_action.setChecked(False)
                return
            self._deactivate_toolbar_navigation()
            self.integrate_button.setChecked(False)
            self.edit_peak_button.setChecked(False)
            self.split_peak_button.setChecked(False)
            self.move_trace_button.setChecked(False)
            self.pointer_button.setChecked(False)
            self.statusBar().showMessage(self.translator("text_annotation_hint"))
        else:
            self.statusBar().clearMessage()

    def _split_selected_peak_at(self, displayed_time: float):
        dataset = self._selected_dataset()
        row = self.peak_table.currentRow()
        if dataset is None or not (0 <= row < len(dataset.peaks)):
            return
        raw_time = float(displayed_time) - dataset.x_shift_min
        before = self._capture_analysis_state()
        try:
            left, right = split_peak_region(
                dataset,
                dataset.peaks[row],
                raw_time,
            )
            dataset.peaks[row : row + 1] = [left, right]
            recalculate_dataset_peaks(dataset)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, self.translator("warning"), str(exc))
            return
        self._push_undo_snapshot(
            before, self._history_label("積分エリアを分割", "Split integration area")
        )
        self.project.dirty = True
        self._refresh_peak_table([right.id])
        self._plot()
        self._update_title()

    def _annotation_at_event(self, event):
        if getattr(event, "x", None) is None or getattr(event, "y", None) is None:
            return None
        try:
            renderer = self.canvas.get_renderer()
        except (AttributeError, RuntimeError):
            return None
        for annotation in reversed(self.project.annotations):
            artist = self._annotation_artists.get(annotation.id)
            if artist is None or not artist.get_visible():
                continue
            try:
                bounds = artist.get_window_extent(renderer=renderer).expanded(1.08, 1.25)
            except (AttributeError, RuntimeError, ValueError):
                continue
            if bounds.contains(float(event.x), float(event.y)):
                return annotation
        return None

    def _edit_text_annotation(self, annotation: TextAnnotation):
        before = self._capture_analysis_state()
        dialog = TextAnnotationDialog(
            annotation,
            self.project.datasets,
            self._application_language,
            self,
            allow_delete=True,
        )
        if not dialog_exec(dialog):
            return
        if dialog.delete_requested:
            self.project.annotations = [
                item for item in self.project.annotations if item.id != annotation.id
            ]
            label = self._history_label("テキストラベル削除", "Delete text label")
        else:
            label = self._history_label("テキストラベル編集", "Edit text label")
        self._push_undo_snapshot(before, label)
        self.project.dirty = True
        self._plot()
        self._update_title()

    def _place_text_annotation(self, event):
        selected = self._selected_dataset()
        y_axis = 2 if getattr(event, "inaxes", None) is self.axes_right else (
            selected.y_axis if selected is not None else 1
        )
        target_axes = self.axes_right if y_axis == 2 and self.axes_right is not None else self.axes
        if getattr(event, "x", None) is not None and getattr(event, "y", None) is not None:
            x_value, y_value = target_axes.transData.inverted().transform(
                (event.x, event.y)
            )
        else:
            x_value = float(event.xdata)
            y_value = float(event.ydata)
        annotation = TextAnnotation(
            x_min=float(x_value),
            y_value=float(y_value),
            dataset_id=selected.id if selected is not None else "",
            y_axis=y_axis,
            font_family="Arial",
        )
        dialog = TextAnnotationDialog(
            annotation,
            self.project.datasets,
            self._application_language,
            self,
        )
        accepted = bool(dialog_exec(dialog))
        self.annotation_action.setChecked(False)
        if not accepted:
            return
        before = self._capture_analysis_state()
        self.project.annotations.append(annotation)
        self._push_undo_snapshot(
            before, self._history_label("テキストラベル追加", "Add text label")
        )
        self.project.dirty = True
        self._plot()
        self._update_title()

    def _on_canvas_press(self, event):
        if event.button != 1:
            return
        if str(getattr(self.toolbar, "mode", "")):
            return
        annotation = self._annotation_at_event(event)
        if annotation is not None:
            if getattr(event, "dblclick", False):
                self._edit_text_annotation(annotation)
                return
            target_axes = self._annotation_axis(annotation)
            start_x, start_y = target_axes.transData.inverted().transform(
                (event.x, event.y)
            )
            self._annotation_drag = {
                "annotation": annotation,
                "artist": self._annotation_artists.get(annotation.id),
                "axes": target_axes,
                "start_x": float(start_x),
                "start_y": float(start_y),
                "initial_x": annotation.x_min,
                "initial_y": annotation.y_value,
                "undo_state": self._capture_analysis_state(),
            }
            return
        if event.xdata is None:
            return
        if self.annotation_action.isChecked():
            self._place_text_annotation(event)
            return
        if self.axes_overview is not None and getattr(event, "inaxes", None) in (
            self.axes_overview,
            self.axes_overview_right,
        ):
            self._center_detail_on(float(event.xdata))
            return
        if getattr(event, "dblclick", False) and not (
            self.integrate_button.isChecked()
            or self.edit_peak_button.isChecked()
            or self.split_peak_button.isChecked()
            or self.move_trace_button.isChecked()
        ):
            self._back_to_previous_view()
            return
        if self.split_peak_button.isChecked():
            self._split_selected_peak_at(event.xdata)
            return
        if not self.move_trace_button.isChecked():
            return
        dataset = self._selected_dataset()
        if dataset is None:
            return
        target_axes = self.axes_right if dataset.y_axis == 2 and self.axes_right is not None else self.axes
        x_value, y_value = target_axes.transData.inverted().transform((event.x, event.y))
        self._move_drag = {
            "dataset": dataset,
            "start_x": float(x_value),
            "start_y": float(y_value),
            "initial_x_shift": dataset.x_shift_min,
            "initial_offset": dataset.offset,
            "undo_state": self._capture_analysis_state(),
        }

    def _on_canvas_motion(self, event):
        if (
            self._annotation_drag is not None
            and getattr(event, "x", None) is not None
            and getattr(event, "y", None) is not None
        ):
            drag = self._annotation_drag
            x_value, y_value = drag["axes"].transData.inverted().transform(
                (event.x, event.y)
            )
            annotation = drag["annotation"]
            annotation.x_min = drag["initial_x"] + float(x_value) - drag["start_x"]
            annotation.y_value = drag["initial_y"] + float(y_value) - drag["start_y"]
            if drag["artist"] is not None:
                drag["artist"].set_position((annotation.x_min, annotation.y_value))
                self._request_canvas_draw(throttled=True)
            return
        if (
            self.integrate_button.isChecked()
            or self.edit_peak_button.isChecked()
            or self.split_peak_button.isChecked()
            or self.pointer_button.isChecked()
        ):
            detail_axes = tuple(
                axis
                for axis in (self.axes, self.axes_right, self.axes_gradient)
                if axis is not None
            )
            if event.xdata is not None and event.inaxes in detail_axes:
                self._ensure_interaction_cursor()
                self._interaction_cursor.set_xdata([event.xdata, event.xdata])
                self._interaction_cursor.set_visible(True)
                self._request_canvas_draw(throttled=True)
            else:
                self._hide_interaction_cursor()
        if self._move_drag is None or event.x is None or event.y is None:
            return
        dataset = self._move_drag["dataset"]
        target_axes = self.axes_right if dataset.y_axis == 2 and self.axes_right is not None else self.axes
        x_value, y_value = target_axes.transData.inverted().transform((event.x, event.y))
        direction = self.move_axis_combo.currentData()
        if direction in ("x", "both"):
            dataset.x_shift_min = self._move_drag["initial_x_shift"] + float(x_value) - self._move_drag["start_x"]
        if direction in ("y", "both"):
            dataset.offset = self._move_drag["initial_offset"] + float(y_value) - self._move_drag["start_y"]
        line = self._dataset_lines.get(dataset.id)
        if line is not None:
            try:
                values = display_values(dataset, self.project.method.display_unit)
                full_x = dataset.time_min + dataset.x_shift_min
                full_y = values + dataset.offset
                self._plot_source_cache[dataset.id] = (full_x, full_y)
                screen_x, screen_y = self._screen_data(
                    full_x,
                    full_y,
                    target_axes,
                    x_limits=tuple(self.axes.get_xlim()),
                    interactive=True,
                )
                line.set_data(screen_x, screen_y)
                self._request_canvas_draw(throttled=True)
            except ValueError:
                pass

    def _on_canvas_release(self, _event):
        if self._annotation_drag is not None:
            drag = self._annotation_drag
            annotation = drag["annotation"]
            changed = (
                annotation.x_min != drag["initial_x"]
                or annotation.y_value != drag["initial_y"]
            )
            self._annotation_drag = None
            if changed:
                self._push_undo_snapshot(
                    drag["undo_state"],
                    self._history_label(
                        "テキストラベル移動", "Move text label"
                    ),
                )
                self.project.dirty = True
                self._plot()
                self._update_title()
            return
        if self._move_drag is None:
            return
        dataset = self._move_drag["dataset"]
        undo_state = self._move_drag.get("undo_state")
        changed = (
            dataset.x_shift_min != self._move_drag["initial_x_shift"]
            or dataset.offset != self._move_drag["initial_offset"]
        )
        self._move_drag = None
        if changed:
            recalculate_dataset_peaks(dataset)
            if undo_state is not None:
                self._push_undo_snapshot(
                    undo_state, self._history_label("スペクトル移動", "Move trace")
                )
        self.project.dirty = True
        row = next((i for i, item in enumerate(self.project.datasets) if item.id == dataset.id), -1)
        if row >= 0:
            self._updating_table = True
            self.dataset_table.item(row, DATASET_X_SHIFT_COLUMN).setText(
                _format(dataset.x_shift_min)
            )
            self.dataset_table.item(row, DATASET_OFFSET_COLUMN).setText(
                _format(dataset.offset)
            )
            self._updating_table = False
        self._plot()
        self._update_title()

    def _full_x_bounds(self):
        datasets = [dataset for dataset in self.project.datasets if dataset.visible]
        if not datasets:
            datasets = list(self.project.datasets)
        starts = [float(dataset.time_min[0] + dataset.x_shift_min) for dataset in datasets if dataset.time_min.size]
        ends = [float(dataset.time_min[-1] + dataset.x_shift_min) for dataset in datasets if dataset.time_min.size]
        ends.extend(
            max(point.time_min for point in dataset.measurement.gradient)
            for dataset in datasets
            if dataset.measurement.gradient
        )
        left = min([0.0] + starts) if starts else 0.0
        return (left, max(ends)) if ends else (0.0, 1.0)

    @staticmethod
    def _scaled_limits(limits, factor: float, center: Optional[float] = None):
        left, right = limits
        midpoint = (left + right) / 2.0 if center is None else float(center)
        return midpoint + (left - midpoint) * factor, midpoint + (right - midpoint) * factor

    def _center_detail_on(self, center_x: float):
        if not self._view_initialized:
            return
        self._push_view_history()
        left, right = self.axes.get_xlim()
        width = right - left
        full_left, full_right = self._full_x_bounds()
        if width >= full_right - full_left:
            new_left, new_right = full_left, full_right
        else:
            new_left = center_x - width / 2.0
            new_right = center_x + width / 2.0
            if new_left < full_left:
                new_right += full_left - new_left
                new_left = full_left
            if new_right > full_right:
                new_left -= new_right - full_right
                new_right = full_right
        self.axes.set_xlim(new_left, new_right)
        self._set_dynamic_x_ticks()
        self._update_overview_window()
        self._request_canvas_draw(throttled=True, refresh_series=True)

    def _zoom_view(
        self,
        factor: float,
        center_x: Optional[float] = None,
        source_axis=None,
        center_y=None,
        zoom_mode: Optional[str] = None,
        y_axes=None,
        y_centers=None,
    ):
        if not self._view_initialized:
            return
        self._push_view_history()
        mode = zoom_mode or self.project.method.zoom_axis
        if mode not in ("both", "x", "y"):
            mode = "both"
        if mode in ("both", "x"):
            x_left, x_right = self._scaled_limits(self.axes.get_xlim(), factor, center_x)
            full_left, full_right = self._full_x_bounds()
            full_span = full_right - full_left
            if x_right - x_left >= full_span:
                x_left, x_right = full_left, full_right
            else:
                if x_left < full_left:
                    x_right += full_left - x_left
                    x_left = full_left
                if x_right > full_right:
                    x_left -= x_right - full_right
                    x_right = full_right
            self.axes.set_xlim(x_left, x_right)
        if mode in ("both", "y"):
            zoomable_axes = tuple(
                axis for axis in (self.axes, self.axes_right) if axis is not None
            )
            targets = (
                [axis for axis in y_axes if axis in zoomable_axes]
                if y_axes is not None
                else list(zoomable_axes)
            )
            center_map = y_centers or {}
            for target_axis in targets:
                axis_center = center_map.get(target_axis)
                if axis_center is None and source_axis is target_axis:
                    axis_center = center_y
                target_axis.set_ylim(
                    *self._scaled_limits(
                        target_axis.get_ylim(), factor, axis_center
                    )
                )
        self._set_dynamic_x_ticks()
        self._request_canvas_draw(throttled=True, refresh_series=True)

    @staticmethod
    def _display_point_in_bbox(x_value, y_value, bbox, padding: float = 0.0):
        if bbox is None or x_value is None or y_value is None:
            return False
        return (
            bbox.x0 - padding <= x_value <= bbox.x1 + padding
            and bbox.y0 - padding <= y_value <= bbox.y1 + padding
        )

    def _scroll_target(self, event):
        """Return plot, x, y1, or y2 from the cursor's display position."""
        source_axis = getattr(event, "inaxes", None)
        overview_axes = tuple(
            axis
            for axis in (self.axes_overview, self.axes_overview_right)
            if axis is not None
        )
        x_value = getattr(event, "x", None)
        y_value = getattr(event, "y", None)
        if x_value is None or y_value is None:
            if source_axis in overview_axes:
                return "x"
            return "plot" if source_axis is not None else None

        for overview_axis in overview_axes:
            if self._display_point_in_bbox(
                x_value, y_value, overview_axis.bbox
            ):
                return "x"

        plot_bbox = self.axes.bbox
        edge = 5.0
        if (
            plot_bbox.x0 - edge <= x_value <= plot_bbox.x1 + edge
            and plot_bbox.y0 - edge <= y_value <= plot_bbox.y0 + edge
        ):
            return "x"
        if (
            plot_bbox.y0 <= y_value <= plot_bbox.y1
            and plot_bbox.x0 - edge <= x_value <= plot_bbox.x0 + edge
        ):
            return "y1"
        if self.axes_right is not None and (
            plot_bbox.y0 <= y_value <= plot_bbox.y1
            and plot_bbox.x1 - edge <= x_value <= plot_bbox.x1 + edge
        ):
            return "y2"
        if self._display_point_in_bbox(x_value, y_value, plot_bbox):
            return "plot"

        try:
            renderer = self.canvas.get_renderer()
            x_bbox = self.axes.xaxis.get_tightbbox(renderer)
            y1_bbox = self.axes.yaxis.get_tightbbox(renderer)
            y2_bbox = (
                self.axes_right.yaxis.get_tightbbox(renderer)
                if self.axes_right is not None
                else None
            )
        except (AttributeError, RuntimeError):
            return None
        if self._display_point_in_bbox(x_value, y_value, x_bbox, 3.0):
            return "x"
        if self._display_point_in_bbox(x_value, y_value, y1_bbox, 3.0):
            return "y1"
        if self._display_point_in_bbox(x_value, y_value, y2_bbox, 3.0):
            return "y2"
        return None

    def _pan_target(self, event):
        """Resolve the drag-start region for the toolbar pan operation."""
        if (
            self.axes_overview is not None
            and getattr(event, "inaxes", None)
            in (self.axes_overview, self.axes_overview_right)
        ):
            return None
        return self._scroll_target(event)

    @staticmethod
    def _event_center_for_axis(event, axis):
        x_value = getattr(event, "x", None)
        y_value = getattr(event, "y", None)
        if x_value is not None and y_value is not None:
            transformed = axis.transData.inverted().transform((x_value, y_value))
            return float(transformed[0]), float(transformed[1])
        return getattr(event, "xdata", None), getattr(event, "ydata", None)

    def _on_scroll(self, event):
        if event.button not in ("up", "down"):
            return
        factor = 0.8 if event.button == "up" else 1.25
        configured_mode = self.project.method.zoom_axis
        if configured_mode != "auto":
            source_axis = getattr(event, "inaxes", None)
            center_x, _primary_y = self._event_center_for_axis(event, self.axes)
            center_axis = (
                source_axis
                if source_axis in (self.axes, self.axes_right)
                else self.axes
            )
            _axis_x, center_y = self._event_center_for_axis(
                event, center_axis
            )
            self._zoom_view(
                factor, center_x, source_axis, center_y, zoom_mode=configured_mode
            )
            return

        target = self._scroll_target(event)
        if target is None:
            return
        center_x, _unused = self._event_center_for_axis(event, self.axes)
        if target == "x":
            self._zoom_view(factor, center_x, zoom_mode="x", y_axes=[])
            return
        if target == "y1":
            _axis_x, center_y = self._event_center_for_axis(event, self.axes)
            self._zoom_view(
                factor,
                center_x,
                zoom_mode="y",
                y_axes=[self.axes],
                y_centers={self.axes: center_y},
            )
            return
        if target == "y2" and self.axes_right is not None:
            _axis_x, center_y = self._event_center_for_axis(
                event, self.axes_right
            )
            self._zoom_view(
                factor,
                center_x,
                zoom_mode="y",
                y_axes=[self.axes_right],
                y_centers={self.axes_right: center_y},
            )
            return

        y_targets = [self.axes]
        if self.axes_right is not None:
            y_targets.append(self.axes_right)
        y_centers = {
            axis: self._event_center_for_axis(event, axis)[1]
            for axis in y_targets
        }
        self._zoom_view(
            factor,
            center_x,
            zoom_mode="both",
            y_axes=y_targets,
            y_centers=y_centers,
        )

    def _on_span_selected(self, minimum: float, maximum: float):
        dataset = self._selected_dataset()
        if dataset is None or abs(maximum - minimum) <= 0:
            return
        minimum -= dataset.x_shift_min
        maximum -= dataset.x_shift_min
        region = PeakRegion(
            start_min=min(minimum, maximum),
            end_min=max(minimum, maximum),
            baseline_mode=self.project.method.baseline_mode,
        )
        before = self._capture_analysis_state()
        try:
            integrated = integrate_peak(dataset, region)
            dataset.peaks.append(integrated)
            recalculate_dataset_peaks(dataset)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, self.translator("warning"), str(exc))
            return
        self._push_undo_snapshot(
            before, self._history_label("手動積分", "Manual integration")
        )
        self.project.dirty = True
        self._refresh_peak_table([integrated.id])
        self._plot()
        self._update_title()

    def _on_edit_span_selected(self, minimum: float, maximum: float):
        dataset = self._selected_dataset()
        if (
            dataset is None
            or not self._edit_range_peak_id
            or abs(maximum - minimum) <= 0
        ):
            return
        peak = next(
            (
                item
                for item in dataset.peaks
                if item.id == self._edit_range_peak_id
            ),
            None,
        )
        if peak is None:
            self.edit_peak_button.setChecked(False)
            return
        peak_id = peak.id
        raw_minimum = float(minimum) - dataset.x_shift_min
        raw_maximum = float(maximum) - dataset.x_shift_min
        before = self._capture_analysis_state()
        peak.start_min = min(raw_minimum, raw_maximum)
        peak.end_min = max(raw_minimum, raw_maximum)
        try:
            recalculate_dataset_peaks(dataset)
        except ValueError as exc:
            self._restore_analysis_state(before)
            QtWidgets.QMessageBox.warning(
                self, self.translator("warning"), str(exc)
            )
            return
        self._push_undo_snapshot(
            before,
            self._history_label(
                "積分範囲をマウス修正", "Edit integration range with mouse"
            ),
        )
        self.project.dirty = True
        self.edit_peak_button.setChecked(False)
        self._refresh_peak_table([peak_id])
        self._plot()
        self._update_title()

    def edit_peak_properties(self):
        dataset = self._selected_dataset()
        row = self.peak_table.currentRow()
        if dataset is None or not (0 <= row < len(dataset.peaks)):
            QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("no_dataset"))
            return
        peak = dataset.peaks[row]
        peak_id = peak.id
        before = self._capture_analysis_state()
        dialog = PeakRangeDialog(peak, float(dataset.time_min[0]), float(dataset.time_min[-1]), self._application_language, self)
        if dialog_exec(dialog):
            peak.start_min = dialog.start.value()
            peak.end_min = dialog.end.value()
            peak.baseline_mode = dialog.baseline_mode.currentData()
            if peak.baseline_mode == "manual":
                peak.baseline_start_uv = dialog.baseline_start.value()
                peak.baseline_end_uv = dialog.baseline_end.value()
            else:
                peak.baseline_start_uv = None
                peak.baseline_end_uv = None
            recalculate_dataset_peaks(dataset)
            self._push_undo_snapshot(
                before, self._history_label("積分範囲を修正", "Edit integration range")
            )
            self.project.dirty = True
            self._refresh_peak_table([peak_id])
            self._plot()
            self._update_title()

    def edit_peak_range(self):
        """Backward-compatible alias for numeric range/baseline editing."""
        self.edit_peak_properties()

    def delete_peak(self):
        dataset = self._selected_dataset()
        rows = self._selected_peak_rows()
        if not rows and self.peak_table.currentRow() >= 0:
            rows = [self.peak_table.currentRow()]
        rows = [row for row in rows if 0 <= row < len(dataset.peaks)] if dataset is not None else []
        if dataset is None or not rows:
            return
        before = self._capture_analysis_state()
        for row in sorted(rows, reverse=True):
            del dataset.peaks[row]
        recalculate_dataset_peaks(dataset)
        self._push_undo_snapshot(
            before, self._history_label("積分ピークを削除", "Delete integrated peaks")
        )
        self.project.dirty = True
        self._refresh_peak_table()
        self._plot()
        self._update_title()

    def auto_detect_peaks(self):
        dataset = self._selected_dataset()
        if dataset is None:
            QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("no_dataset"))
            return
        self.integrate_button.setChecked(False)
        self.split_peak_button.setChecked(False)
        self.move_trace_button.setChecked(False)
        before = self._capture_analysis_state()
        try:
            candidates = detect_peaks(dataset, self.project.method)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, self.translator("warning"), str(exc))
            return
        existing = [
            peak.retention_time_min for peak in dataset.peaks if peak.retention_time_min is not None
        ]
        minimum_distance = max(0.0, self.project.method.auto_peak_min_distance_min)
        added = [
            peak
            for peak in candidates
            if peak.retention_time_min is not None
            and all(abs(peak.retention_time_min - value) >= minimum_distance for value in existing)
        ]
        if not added:
            QtWidgets.QMessageBox.information(
                self,
                APP_NAME,
                "条件に一致する新しいピーク候補はありません。"
                if self._application_language == "ja"
                else "No new peak candidates matched the current settings.",
            )
            return
        dataset.peaks.extend(added)
        recalculate_dataset_peaks(dataset)
        self._push_undo_snapshot(
            before, self._history_label("自動ピーク検出", "Automatic peak detection")
        )
        self.project.dirty = True
        self._refresh_peak_table([peak.id for peak in added])
        self._plot()
        self._update_title()
        self.statusBar().showMessage(
            self.translator("auto_detected", count=len(added)), 7000
        )

    def _reset_view(self):
        self._push_view_history()
        self._view_initialized = False
        self._plot(preserve_view=False)

    def _reset_x_view(self):
        if not self._view_initialized:
            return
        self._push_view_history()
        self.axes.set_xlim(*self._full_x_bounds())
        self._set_dynamic_x_ticks()
        self._update_overview_window()
        self.canvas.draw_idle()

    def _reset_y_view(self):
        if not self._view_initialized:
            return
        view_state = self._capture_view_state()
        self._push_view_history()
        x_limits = tuple(view_state["x"])
        self._view_initialized = False
        self._plot(preserve_view=False)
        self.axes.set_xlim(*x_limits)
        self._set_dynamic_x_ticks()
        self._update_overview_window()
        self.canvas.draw_idle()

    def _import_chromatogram_paths(
        self, paths, group_label="", show_progress=False
    ) -> int:
        paths = [str(path) for path in paths]
        if not paths:
            return 0
        self._settings.set(
            LAST_IMPORT_DIRECTORY, str(Path(paths[0]).parent), sync=True
        )
        imported = 0
        processed = 0
        canceled = False
        errors: List[str] = []
        progress = None
        if show_progress:
            progress = QtWidgets.QProgressDialog(
                self.translator("directory_import_progress"),
                self.translator("cancel"),
                0,
                len(paths),
                self,
            )
            progress.setWindowModality(WINDOW_MODAL)
            progress.setMinimumDuration(0)
            progress.setValue(0)
        for index, path in enumerate(paths):
            if progress is not None:
                QtWidgets.QApplication.processEvents()
                if progress.wasCanceled():
                    canceled = True
                    break
            try:
                dataset = load_chromatogram_file(path)
                normalized_group = str(group_label).strip()
                if normalized_group:
                    dataset.measurement.group = normalized_group
                dataset.color = COLORS[len(self.project.datasets) % len(COLORS)]
                self.project.add_dataset(dataset)
                imported += 1
            except Exception as exc:
                errors.append("%s: %s" % (Path(path).name, exc))
            processed = index + 1
            if progress is not None:
                progress.setValue(processed)
                QtWidgets.QApplication.processEvents()
        if progress is not None:
            if not canceled:
                progress.setValue(len(paths))
            progress.close()
        if imported:
            self._reset_undo_history()
            self.project.dirty = True
            self._refresh_all(len(self.project.datasets) - 1)
            message_key = "directory_import_canceled" if canceled else "imported"
            self.statusBar().showMessage(
                self.translator(
                    message_key,
                    count=imported,
                    total=len(paths),
                    processed=processed,
                ),
                5000,
            )
        elif canceled:
            self.statusBar().showMessage(
                self.translator(
                    "directory_import_canceled",
                    count=0,
                    total=len(paths),
                    processed=processed,
                ),
                5000,
            )
        if errors:
            QtWidgets.QMessageBox.warning(self, self.translator("warning"), "\n".join(errors))
        return imported

    def import_ascii(self):
        last_directory = self._settings.get(LAST_IMPORT_DIRECTORY)
        start_directory = self._import_directory or last_directory
        if start_directory and not Path(start_directory).is_dir():
            start_directory = ""
        paths, _selected_filter = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            self.translator("import"),
            start_directory,
            self.translator("ascii_filter"),
        )
        self._import_chromatogram_paths(paths)

    def import_directory(self):
        last_directory = self._settings.get(LAST_IMPORT_DIRECTORY)
        start_directory = self._import_directory or last_directory
        if start_directory and not Path(start_directory).is_dir():
            start_directory = ""
        dialog = DirectoryImportDialog(
            start_directory,
            self._application_language,
            self,
        )
        if not dialog_exec(dialog):
            return 0
        directory = dialog.directory_path
        self._settings.set(LAST_IMPORT_DIRECTORY, str(directory), sync=True)
        imported = self._import_chromatogram_paths(
            dialog.files,
            group_label=dialog.group_label,
            show_progress=True,
        )
        self._settings.set(LAST_IMPORT_DIRECTORY, str(directory), sync=True)
        return imported

    @staticmethod
    def _classify_dropped_urls(urls):
        paths = []
        for url in urls:
            if not url.isLocalFile():
                return None, []
            path = Path(url.toLocalFile())
            if not path.is_file():
                return None, []
            paths.append(str(path))
        if not paths:
            return None, []
        suffixes = [Path(path).suffix.lower() for path in paths]
        if all(suffix in (".txt", ".gcd") for suffix in suffixes):
            return "chromatograms", paths
        if len(paths) == 1 and suffixes[0] == ".hplcproj":
            return "project", paths
        return None, []

    def dragEnterEvent(self, event):
        mime_data = event.mimeData()
        if mime_data is not None and mime_data.hasUrls():
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event):
        mime_data = event.mimeData()
        urls = mime_data.urls() if mime_data is not None and mime_data.hasUrls() else []
        drop_kind, paths = self._classify_dropped_urls(urls)
        if drop_kind is None:
            QtWidgets.QMessageBox.warning(
                self,
                self.translator("warning"),
                self.translator("invalid_file_drop"),
            )
            event.acceptProposedAction()
            return
        if drop_kind == "chromatograms":
            self._import_chromatogram_paths(paths)
        elif self._confirm_unsaved():
            self._open_project_path(paths[0])
        event.acceptProposedAction()

    def edit_preferences(self):
        before = self._capture_analysis_state()
        old_method = deepcopy(self.project.method)
        dialog = PreferencesDialog(
            self.project.method,
            self._import_directory,
            self._application_language,
            self,
            save_directory=self._save_directory,
            database_path=self._database_path,
            render_quality=self._render_quality,
        )
        if not dialog_exec(dialog):
            return
        self._import_directory = dialog.import_directory_value
        self._save_directory = dialog.save_directory_value
        self._database_path = dialog.database_path_value
        self._settings.set_many(
            {
                IMPORT_DIRECTORY: self._import_directory,
                SAVE_DIRECTORY: self._save_directory,
                DATABASE_PATH: self._database_path,
            }
        )
        render_quality_changed = dialog.render_quality_value != self._render_quality
        self._set_render_quality(
            dialog.render_quality_value,
            persist=True,
            replot=False,
        )
        for field, value in dialog.detection_values.items():
            setattr(self.project.method, field, value)
        if self.project.method != old_method:
            self._push_undo_snapshot(
                before,
                self._history_label(
                    "自動ピーク検出設定", "Automatic peak-detection settings"
                ),
            )
            self.project.dirty = True
            self._update_title()
        if render_quality_changed:
            self._plot()

    def _database_not_configured(self):
        QtWidgets.QMessageBox.information(
            self,
            APP_NAME,
            self.translator("database_not_configured"),
        )

    def _sync_database_after_save(self, show_success: bool = False) -> bool:
        if not self._database_path:
            return False
        try:
            initialize_database(self._database_path)
            sync_project_to_database(self._database_path, self.project)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self,
                self.translator("warning"),
                self.translator("database_sync_failed", error=str(exc)),
            )
            return False
        if show_success:
            self.statusBar().showMessage(
                self.translator("database_synced", path=self._database_path),
                7000,
            )
        return True

    def sync_current_project_to_database(self):
        if not self._database_path:
            self._database_not_configured()
            return
        if not self.project.project_path or self.project.dirty:
            self.save_project()
            return
        self._sync_database_after_save(show_success=True)

    def open_lab_database(self):
        if not self._database_path:
            self._database_not_configured()
            return
        try:
            initialize_database(self._database_path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, self.translator("error"), str(exc)
            )
            return
        dialog = LabDatabaseDialog(
            self._database_path, self._application_language, self
        )
        dialog_exec(dialog)

    def remove_dataset(self):
        row = self.dataset_table.currentRow()
        if not (0 <= row < len(self.project.datasets)):
            return
        dataset = self.project.datasets[row]
        answer = QtWidgets.QMessageBox.question(
            self,
            self.translator("warning"),
            self.translator("confirm_remove_dataset", label=dataset.label),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self.project.remove_dataset_at(row)
        self._reset_undo_history()
        self.project.dirty = True
        self._refresh_all(max(0, row - 1))

    def edit_metadata(self):
        dataset = self._selected_dataset()
        if dataset is None:
            QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("no_dataset"))
            return
        before = self._capture_analysis_state()
        dialog = MetadataDialog(dataset, self._application_language, self)
        if dialog_exec(dialog):
            try:
                recalculate_dataset_peaks(dataset)
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, self.translator("warning"), str(exc))
            self._push_undo_snapshot(
                before, self._history_label("詳細・定量条件", "Details and quantitation")
            )
            self.project.dirty = True
            self._refresh_all(self.dataset_table.currentRow())

    def change_color(self):
        dataset = self._selected_dataset()
        if dataset is None:
            QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("no_dataset"))
            return
        initial = QtGui.QColor(dataset.color or COLORS[self.dataset_table.currentRow() % len(COLORS)])
        color = QtWidgets.QColorDialog.getColor(initial, self, self.translator("change_color"))
        if not color.isValid():
            return
        before = self._capture_analysis_state()
        dataset.color = color.name()
        self._push_undo_snapshot(
            before, self._history_label("スペクトル色", "Trace color")
        )
        self.project.dirty = True
        self._refresh_dataset_table(self.dataset_table.currentRow())
        self._plot()
        self._update_title()

    def edit_axis_labels(self):
        before = self._capture_analysis_state()
        dialog = AxisLabelsDialog(self.project.method, self._application_language, self)
        if not dialog_exec(dialog):
            return
        dialog.apply_to_method(self.project.method)
        self._push_undo_snapshot(
            before,
            self._history_label("軸・ラベル設定", "Axes and label styles"),
        )
        self.project.dirty = True
        self._plot()
        self._update_title()

    def edit_legend_composer(self):
        before = self._capture_analysis_state()
        dialog = LegendComposerDialog(
            self.project.method, self._application_language, self
        )
        if not dialog_exec(dialog):
            return
        dialog.apply_to_method(self.project.method)
        self._push_undo_snapshot(
            before,
            self._history_label("凡例設定", "Legend composer"),
        )
        self.project.dirty = True
        self._plot()
        self._update_title()

    def edit_batch_metadata(self):
        selected = self._selected_dataset()
        before = self._capture_analysis_state()
        dialog = BatchMetadataDialog(
            self.project,
            selected.id if selected is not None else "",
            self._application_language,
            self,
            preset_metadata=self._global_preset_metadata,
        )
        if dialog_exec(dialog):
            self._global_preset_metadata = dialog.preset_metadata
            for dataset in self.project.datasets:
                try:
                    recalculate_dataset_peaks(dataset)
                except ValueError:
                    pass
            self._push_undo_snapshot(
                before,
                self._history_label(
                    "条件の一括編集", "Edit batch conditions"
                ),
            )
            self._persist_global_presets()
            self.project.dirty = True
            self._refresh_all(self.dataset_table.currentRow())

    def edit_gradient(self):
        dataset = self._selected_dataset()
        if dataset is None:
            QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("no_dataset"))
            return
        before = self._capture_analysis_state()
        dialog = GradientDialog(
            dataset,
            self._application_language,
            self,
            presets=self.project.gradient_presets,
            preset_metadata=self._global_preset_metadata,
        )
        if dialog_exec(dialog):
            self.project.gradient_presets = dialog.presets
            self._global_preset_metadata = dialog.preset_metadata
            self._persist_global_presets()
            try:
                recalculate_dataset_peaks(dataset)
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, self.translator("warning"), str(exc))
            self._push_undo_snapshot(
                before, self._history_label("グラジエント条件", "Gradient conditions")
            )
            self.project.dirty = True
            self._refresh_peak_table()
            self._plot()
            self._update_title()

    def _confirm_unsaved(self) -> bool:
        if not self.project.dirty:
            return True
        answer = QtWidgets.QMessageBox.question(
            self,
            APP_NAME,
            self.translator("unsaved"),
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
        )
        if answer == QtWidgets.QMessageBox.Cancel:
            return False
        if answer == QtWidgets.QMessageBox.Save:
            return self.save_project()
        return True

    def new_project(self):
        if not self._confirm_unsaved():
            return
        self.project = Project(
            ui_language=self._application_language,
            condition_presets=deepcopy(self._global_condition_presets),
            gradient_presets=deepcopy(self._global_gradient_presets),
        )
        self._view_initialized = False
        self._view_history = []
        self._reset_undo_history()
        self.translator.set_language(self._application_language)
        self._refresh_all()
        self._retranslate()

    def new_project_in_new_window(self):
        window = MainWindow()
        self._open_windows.add(window)
        icon = self.windowIcon()
        if not icon.isNull():
            window.setWindowIcon(icon)
        window.show()
        return window

    def open_project(self):
        if not self._confirm_unsaved():
            return
        path, _selected_filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            self.translator("open"),
            self._settings.get(LAST_PROJECT_DIRECTORY),
            self.translator("project_filter"),
        )
        if not path:
            return
        self._open_project_path(path)

    def _open_project_path(self, path: str) -> bool:
        try:
            self.project = load_project(path)
            self._settings.set(
                LAST_PROJECT_DIRECTORY, str(Path(path).parent), sync=True
            )
            self._merge_global_presets_into_project()
            self._view_initialized = False
            self._view_history = []
            self._reset_undo_history()
            for dataset in self.project.datasets:
                recalculate_dataset_peaks(dataset)
            self.translator.set_language(self._application_language)
            self._refresh_all()
            self._retranslate()
            return True
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self.translator("error"), str(exc))
            return False

    def save_project(self) -> bool:
        if not self.project.project_path:
            return self.save_project_as()
        try:
            save_project(self.project.project_path, self.project)
            self._remember_save_path(self.project.project_path)
            database_synced = self._sync_database_after_save()
            if database_synced:
                message_key = "saved_and_indexed"
            elif not self._database_path:
                message_key = "saved_no_database"
            else:
                message_key = "saved"
            self.statusBar().showMessage(
                self.translator(message_key, path=self.project.project_path),
                7000 if database_synced else 5000,
            )
            self._update_title()
            return True
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self.translator("error"), str(exc))
            return False

    def save_project_as(self) -> bool:
        parts = suggest_project_name_parts(
            self.project,
            default_author=self._settings.get(NAMING_AUTHOR),
        )
        naming_dialog = ProjectNamingDialog(
            parts, self._application_language, self
        )
        if not dialog_exec(naming_dialog):
            return False
        parts = naming_dialog.name_parts()
        suggested_filename = build_project_filename(parts)
        path, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            self.translator("save_as"),
            self._default_save_path(suggested_filename),
            self.translator("project_filter"),
        )
        if not path:
            return False
        if not path.lower().endswith(".hplcproj"):
            path += ".hplcproj"
        apply_project_name_parts(self.project, parts)
        self._settings.set(NAMING_AUTHOR, self.project.author, sync=True)
        self.project.project_path = path
        return self.save_project()

    def _save_figure_file(self, path: str):
        """Save PNG/SVG/PDF from full data regardless of screen quality."""

        with self._full_quality_export_figure():
            old_size = self.figure.get_size_inches().copy()
            try:
                self.figure.set_size_inches(
                    self.project.method.figure_width_mm / 25.4,
                    self.project.method.figure_height_mm / 25.4,
                )
                self.figure.savefig(
                    path,
                    dpi=self.project.method.dpi,
                    bbox_inches="tight",
                )
            finally:
                self.figure.set_size_inches(old_size)

    def export_figure(self):
        if not self.project.datasets:
            QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("no_dataset"))
            return
        selected_format = self._figure_export_format
        filters = self.translator("figure_filter")
        preferred_filter = next(
            (
                item
                for item in filters.split(";;")
                if "*.%s" % selected_format in item.lower()
            ),
            filters.split(";;")[0],
        )
        path, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            self.translator("export_figure"),
            self._default_save_path("chromatogram.%s" % selected_format),
            filters,
            preferred_filter,
        )
        if not path:
            return
        for candidate in ("png", "svg", "pdf"):
            if "*.%s" % candidate in (_selected_filter or "").lower():
                selected_format = candidate
                break
        suffix = Path(path).suffix.lower().lstrip(".")
        if suffix in ("png", "svg", "pdf"):
            selected_format = suffix
        else:
            path += ".%s" % selected_format
        self._figure_export_format = selected_format
        self._settings.set(FIGURE_FORMAT, selected_format, sync=True)
        try:
            self._save_figure_file(path)
            self._remember_save_path(path)
            self.statusBar().showMessage(self.translator("saved", path=path), 5000)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self.translator("error"), str(exc))
        finally:
            self._request_canvas_draw(force=True)

    def export_peaks(self):
        if not any(dataset.peaks for dataset in self.project.datasets):
            QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("peaks"))
            return
        path, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            self.translator("export_peaks"),
            self._default_save_path("peak_table.csv"),
            self.translator("csv_filter"),
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            export_peak_csv(path, self.project.datasets)
            self._remember_save_path(path)
            self.statusBar().showMessage(self.translator("saved", path=path), 5000)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self.translator("error"), str(exc))

    def export_trace(self):
        dataset = self._selected_dataset()
        if dataset is None:
            QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("no_dataset"))
            return
        path, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            self.translator("export_trace"),
            self._default_save_path("%s.csv" % dataset.short_label),
            self.translator("csv_filter"),
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            export_chromatogram_csv(
                path,
                dataset,
                self.project.method.display_unit,
            )
            self._remember_save_path(path)
            self.statusBar().showMessage(self.translator("saved", path=path), 5000)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self.translator("error"), str(exc))

    def export_visible_traces(self):
        datasets = [dataset for dataset in self.project.datasets if dataset.visible]
        if not datasets:
            QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("no_dataset"))
            return
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            self.translator("export_traces"),
            str(Path(self._default_save_path("placeholder")).parent),
        )
        if not directory:
            return
        try:
            paths = export_chromatograms_csv(
                directory,
                datasets,
                self.project.method.display_unit,
            )
            self._remember_save_path(str(Path(directory) / "placeholder"))
            self.statusBar().showMessage(
                self.translator("batch_csv_saved", count=len(paths), path=directory), 7000
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self.translator("error"), str(exc))

    def export_metadata(self):
        if not self.project.datasets:
            QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("no_dataset"))
            return
        path, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            self.translator("export_metadata"),
            self._default_save_path("sample_conditions.csv"),
            self.translator("csv_filter"),
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            export_metadata_csv(path, self.project.datasets, self._application_language)
            self._remember_save_path(path)
            self.statusBar().showMessage(self.translator("saved", path=path), 5000)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self.translator("error"), str(exc))

    def _report_datasets(self):
        visible = [dataset for dataset in self.project.datasets if dataset.visible]
        return visible or list(self.project.datasets)

    def export_report(self):
        datasets = self._report_datasets()
        if not datasets:
            QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("no_dataset"))
            return
        path, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            self.translator("export_report"),
            self._default_save_path("analysis_report.pdf"),
            self.translator("report_filter"),
        )
        if not path:
            return
        try:
            actual = export_analysis_report_pdf(
                path, self.project, datasets, self._application_language
            )
            self._remember_save_path(actual)
            self.statusBar().showMessage(self.translator("saved", path=actual), 7000)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self.translator("error"), str(exc))

    @staticmethod
    def _draw_report_pages_to_printer(printer, pages):
        painter = QtGui.QPainter()
        if not painter.begin(printer):
            raise RuntimeError("Could not initialize the selected printer")
        try:
            unit = (
                QtPrintSupport.QPrinter.Unit.DevicePixel
                if QT_API == 6
                else QtPrintSupport.QPrinter.DevicePixel
            )
            page_rect = printer.pageRect(unit)
            keep_aspect = (
                QtCore.Qt.AspectRatioMode.KeepAspectRatio
                if QT_API == 6
                else QtCore.Qt.KeepAspectRatio
            )
            smooth = (
                QtCore.Qt.TransformationMode.SmoothTransformation
                if QT_API == 6
                else QtCore.Qt.SmoothTransformation
            )
            for index, page in enumerate(pages):
                if index and not printer.newPage():
                    raise RuntimeError("Could not create a new printer page")
                image = QtGui.QImage(page)
                if image.isNull():
                    raise RuntimeError("Could not render an analysis report page")
                target_size = image.size()
                target_size.scale(
                    int(page_rect.width()), int(page_rect.height()), keep_aspect
                )
                scaled = image.scaled(target_size, keep_aspect, smooth)
                x = page_rect.x() + (page_rect.width() - scaled.width()) / 2.0
                y = page_rect.y() + (page_rect.height() - scaled.height()) / 2.0
                painter.drawImage(QtCore.QPointF(x, y), scaled)
        finally:
            painter.end()

    def print_report(self):
        datasets = self._report_datasets()
        if not datasets:
            QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("no_dataset"))
            return
        mode = (
            QtPrintSupport.QPrinter.PrinterMode.HighResolution
            if QT_API == 6
            else QtPrintSupport.QPrinter.HighResolution
        )
        printer = QtPrintSupport.QPrinter(mode)
        if QT_API == 6:
            printer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.PageSizeId.A4))
        else:
            printer.setPageSize(QtPrintSupport.QPrinter.A4)
        dialog = QtPrintSupport.QPrintDialog(printer, self)
        dialog.setWindowTitle(self.translator("print_report"))
        if not dialog_exec(dialog):
            return
        try:
            with tempfile.TemporaryDirectory(prefix="hplc_report_") as directory:
                pages = render_analysis_report_pages(
                    directory, self.project, datasets, self._application_language
                )
                self._draw_report_pages_to_printer(printer, pages)
            self.statusBar().showMessage(
                "印刷ジョブを送信しました。"
                if self._application_language == "ja"
                else "The report was sent to the printer.",
                7000,
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self.translator("error"), str(exc))

    def show_quantitation_help(self):
        dialog = QuantitationHelpDialog(self._application_language, self)
        dialog_exec(dialog)

    def about(self):
        if self._application_language == "ja":
            text = (
                "島津GCsolution / LCsolution / PACsolutionのASCIIクロマトグラムとPACsolution GCDを、"
                "元データを保持したまま管理・重ね描き・積分・自動ピーク検出・定量・作図し、"
                "研究室共通データベースへ集約する解析ソフトです。\n\n"
                "Raw Intensity: µV\n"
                "mAU = µV × AU/V × 10⁻³\n\n"
                "研究用途の解析補助ソフトです。規制対象の品質試験や診断用途としては検証されていません。"
            )
        else:
            text = (
                "Analysis software for managing, overlaying, integrating, detecting, quantifying and plotting "
                "Shimadzu GCsolution / LCsolution / PACsolution ASCII chromatograms and PACsolution GCD files while preserving raw data "
                "and indexing projects in a shared lab database.\n\n"
                "Raw Intensity: µV\n"
                "mAU = µV × AU/V × 10⁻³\n\n"
                "For research analysis support. Not validated for regulated quality control or diagnosis."
            )
        QtWidgets.QMessageBox.about(self, "%s %s" % (APP_NAME, APP_VERSION), text)

    def closeEvent(self, event):
        if self._confirm_unsaved():
            self._open_windows.discard(self)
            event.accept()
        else:
            event.ignore()
