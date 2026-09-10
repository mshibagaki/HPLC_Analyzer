from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import math
from pathlib import Path
import tempfile
from typing import List, Optional

import numpy as np
from matplotlib.backends import backend_pdf as _backend_pdf  # bundled for frozen SVG/PDF export
from matplotlib.backends import backend_svg as _backend_svg
from matplotlib.backend_bases import MouseButton
from matplotlib import font_manager
from matplotlib.patches import Rectangle
from matplotlib.ticker import MultipleLocator
from matplotlib.widgets import SpanSelector

from . import APP_NAME, APP_VERSION
from .analysis import (
    detect_peaks,
    display_values,
    integrate_peak,
    recalculate_dataset_peaks,
    split_peak_region,
)
from .auto_peak_settings import apply_auto_peak_thresholds
from .dialogs import (
    AutoPeakDetectionDialog,
    AxisLabelsDialog,
    BatchMetadataDialog,
    DirectoryImportDialog,
    DisplaySettingsDialog,
    FractionRangeDialog,
    GradientDialog,
    IntegrationListDialog,
    LeftElideDelegate,
    LegendComposerDialog,
    LabDatabaseDialog,
    MetadataDialog,
    PeakRangeDialog,
    PreferencesDialog,
    PresetManagerDialog,
    ProjectNamingDialog,
    QuantitationHelpDialog,
    ReportOptionsDialog,
    ReportScopeDialog,
    TextAnnotationDialog,
    ThreeDChromatogramDialog,
    WorkDirectoriesDialog,
    dialog_exec,
    SaturatedRangeDialog,
)
from .database import initialize_database, sync_project_to_database
from .exporters import (
    export_chromatogram_csv,
    export_chromatograms_csv,
    export_metadata_csv,
    export_peak_csv,
)
from .i18n import Translator
from .import_batch import (
    discover_chromatogram_files,
    discover_reload_candidates,
    normalized_source_path,
)
from .models import (
    Dataset,
    FractionRegion,
    PeakRegion,
    Project,
    TextAnnotation,
    VerticalMarker,
    WorkDirectory,
    sanitize_condition_presets,
)
from .naming import (
    apply_project_name_parts,
    build_project_filename,
    sanitize_filename_component,
    suggest_project_name_parts,
    timestamped_filename,
)
from .parser import load_chromatogram_file
from .peak_fitting import (
    LIMITED_FLANK_NOTE,
    apply_fit_result,
    clear_legacy_fit,
    fit_peak,
    fitted_peak_from_result,
    mirror_fitted_peak_for_legacy,
    fit_saturated_peak,
    is_saturation_corrected,
)
from .plot3d import ThreeDPlotOptions, suggest_z_tick_interval
from .preset_store import (
    load_complete_preset_store,
    merge_preset_sources,
    normalize_preset_metadata,
    record_preset_deleted,
    record_preset_saved,
    sanitize_analyte_presets,
    save_preset_store,
)
from .project_io import (
    load_project,
    save_project,
)
from .report import (
    ReportOptions,
    export_analysis_report_pdf,
    render_analysis_report_pages,
)
from .rendering import (
    HIGH_QUALITY,
    LIGHTWEIGHT,
    default_render_quality,
    default_trace_color,
    matplotlib_line_style,
    normalize_render_quality,
    safe_manual_x_tick_spacing,
    screen_series,
)
from .settings_store import (
    ApplicationSettings,
    AUTO_PEAK_SENSITIVITY_PRESETS,
    AUTOMATIC_UPDATE_CHECK,
    DATASET_COLUMN_ORDER,
    DATABASE_PATH,
    DEFAULT_DATASET_COLUMN_ORDER,
    FIGURE_FORMAT,
    IMPORT_DIRECTORY,
    LAST_IMPORT_DIRECTORY,
    LAST_PROJECT_DIRECTORY,
    LAST_SAVE_DIRECTORY,
    LEGACY_CONDITION_PRESETS,
    LEGACY_GRADIENT_PRESETS,
    NAMING_AUTHOR,
    RENDERING_QUALITY,
    SCREEN_RENDERER,
    SAVE_DIRECTORY,
    UI_LANGUAGE,
)
from .update_check import check_for_updates
from .screen_renderer import create_screen_render_surface
from .pyqtgraph_scene import pyqtgraph_scene_available
from .screen_scene import compose_base_screen_scene
from .screen_events import (
    ScreenPointerEvent,
    integration_peak_hit_target,
    normalize_pointer_event,
)
from .screen_navigation import (
    ScreenOverviewState,
    ScreenViewHistory,
    ScreenViewState,
    axis_pan_view,
    begin_axis_pan,
    compose_overview_state,
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
    from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
else:
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


class UpdateCheckWorker(QtCore.QObject):
    """Run the network-only update check outside the GUI thread."""

    finished = QtCore.Signal(dict)

    def run(self):
        self.finished.emit(check_for_updates(APP_VERSION))

INTEGRATION_BOUNDARY_COLOR = "#9ca3af"
AVAILABLE_PLOT_FONTS = {font.name for font in font_manager.fontManager.ttflist}


def dataset_display_color(dataset: Dataset, ordinal: int) -> str:
    """Resolve an explicit color first, then a channel-aware display default."""

    return (
        dataset.color
        or default_trace_color(dataset.y_axis, ordinal)
        or COLORS[ordinal % len(COLORS)]
    )


DATASET_VISIBLE_COLUMN = 0
DATASET_RUN_ID_COLUMN = 1
DATASET_LABEL_COLUMN = 2
DATASET_TIMESTAMP_COLUMN = 3
DATASET_WAVELENGTH_COLUMN = 4
DATASET_GROUP_COLUMN = 5
DATASET_Y_AXIS_COLUMN = 6
DATASET_AUV_COLUMN = 7
DATASET_X_SHIFT_COLUMN = 8
DATASET_OFFSET_COLUMN = 9
DATASET_COLOR_COLUMN = 10
DATASET_COLUMN_NAME_COLUMN = 11
DATASET_SOURCE_COLUMN = 12
DATASET_SELECTED_COLUMN = 13
DATASET_SOLO_COLUMN = 14
DATASET_COLUMN_COUNT = 15

DATASET_COLUMN_IDS = {
    DATASET_VISIBLE_COLUMN: "visible",
    DATASET_RUN_ID_COLUMN: "run_id",
    DATASET_LABEL_COLUMN: "label",
    DATASET_TIMESTAMP_COLUMN: "timestamp",
    DATASET_WAVELENGTH_COLUMN: "wavelength",
    DATASET_GROUP_COLUMN: "group",
    DATASET_Y_AXIS_COLUMN: "y_axis",
    DATASET_AUV_COLUMN: "auv",
    DATASET_X_SHIFT_COLUMN: "x_shift",
    DATASET_OFFSET_COLUMN: "offset",
    DATASET_COLOR_COLUMN: "color",
    DATASET_COLUMN_NAME_COLUMN: "column",
    DATASET_SOURCE_COLUMN: "source",
    DATASET_SELECTED_COLUMN: "selected",
    DATASET_SOLO_COLUMN: "solo",
}
DATASET_COLUMNS_BY_ID = {
    column_id: logical_column
    for logical_column, column_id in DATASET_COLUMN_IDS.items()
}
DATASET_HIDDEN_COLUMN_IDS = ("group", "source")

# Columns whose fitted-row values are derived rather than measured.
ESTIMATED_PEAK_COLUMNS = frozenset((3, 4, 5, 6, 7, 9))
SATURATION_ESTIMATED_PEAK_COLUMNS = frozenset((8, 14, 15))
ESTIMATED_VALUE_BACKGROUND = "#fdf2ff"

MOUSE_MODE_IDS = (
    "normal",
    "zoom",
    "pointer",
    "select",
    "integrate",
    "edit_peak",
    "split_peak",
    "move_trace",
    "annotation",
)

PEAK_NOTES_COLUMN = 18
PEAK_TYPE_COLUMN = 19
PEAK_PARENT_COLUMN = 20
PEAK_FIT_MODEL_COLUMN = 21
PEAK_FIT_R2_COLUMN = 22
PEAK_FIT_RMSE_COLUMN = 23
PEAK_FIT_AIC_COLUMN = 24
PEAK_COLUMN_COUNT = 25


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
        self._axis_pan_active = False

    def press_pan(self, event):
        if getattr(event, "button", None) not in (1, MouseButton.LEFT):
            return super().press_pan(event)
        owner = self._axis_pan_owner
        if owner is None or not getattr(owner, "_view_initialized", False):
            return
        target = owner._pan_target(event)
        normalized = owner._normalized_pointer_event(
            event, hit_region=target or ""
        )
        if not owner._begin_axis_pan(normalized):
            return
        if self._nav_stack() is None:
            self.push_current()
        self._axis_pan_active = True
        self.canvas.mpl_disconnect(self._id_drag)
        self._id_drag = self.canvas.mpl_connect(
            "motion_notify_event", self.drag_pan
        )

    def press_zoom(self, event):
        owner = self._axis_pan_owner
        if owner is not None and getattr(owner, "_view_initialized", False):
            if getattr(event, "inaxes", None) is owner.axes_overview:
                return
            owner._push_view_history()
        return super().press_zoom(event)

    def home(self, *args):
        owner = self._axis_pan_owner
        if owner is not None:
            owner._reset_view()

    def configure_subplots(self):
        # Issue #236 (workflow doc 9.16): open the application's own axis
        # dialog regardless of which renderer is active, rather than only
        # when PyQtGraph is on screen. The Matplotlib-only "Customize" action
        # (edit_parameters below) duplicated this dialog under PyQtGraph and
        # is removed from the toolbar; this is now the one path to it, on
        # both renderers.
        owner = self._axis_pan_owner
        if owner is not None:
            owner.edit_axis_labels()
            return
        return super().configure_subplots()

    def edit_parameters(self):
        # Kept only so a stray reference to the retained-but-hidden Customize
        # QAction (see MainWindow._install_mode_toolbar_actions) still opens
        # something sensible instead of failing.
        owner = self._axis_pan_owner
        if owner is not None:
            owner.edit_axis_labels()
            return
        return super().edit_parameters()

    def back(self, *args):
        owner = self._axis_pan_owner
        if owner is not None:
            owner._navigate_view_history("back")

    def forward(self, *args):
        owner = self._axis_pan_owner
        if owner is not None:
            owner._navigate_view_history("forward")

    def set_history_buttons(self):
        owner = getattr(self, "_axis_pan_owner", None)
        actions = getattr(self, "_actions", {}) or {}
        if owner is None or not hasattr(owner, "_view_history"):
            return super().set_history_buttons()
        capabilities = owner._view_history_capabilities()
        for command in ("back", "forward"):
            action = actions.get(command)
            if action is not None:
                action.setEnabled(capabilities[command])

    def drag_pan(self, event):
        if not self._axis_pan_active:
            return super().drag_pan(event)
        owner = self._axis_pan_owner
        if owner is None:
            return
        owner._update_axis_pan(owner._normalized_pointer_event(event))

    def release_pan(self, event):
        if not self._axis_pan_active:
            return super().release_pan(event)
        self.canvas.mpl_disconnect(self._id_drag)
        self._id_drag = self.canvas.mpl_connect(
            "motion_notify_event", self.mouse_move
        )
        self._axis_pan_active = False
        owner = self._axis_pan_owner
        if owner is not None:
            owner._end_axis_pan()
        else:
            self.canvas.draw_idle()
        self.push_current()


class ColorCellDelegate(QtWidgets.QStyledItemDelegate):
    """Keep the trace color legible even while its dataset row is selected."""

    def display_option(self, option, index):
        styled = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        if QT_API == 6:
            selected = QtWidgets.QStyle.StateFlag.State_Selected
            focus = QtWidgets.QStyle.StateFlag.State_HasFocus
        else:
            selected = QtWidgets.QStyle.State_Selected
            focus = QtWidgets.QStyle.State_HasFocus
        styled.state &= ~selected
        styled.state &= ~focus
        return styled

    def paint(self, painter, option, index):
        styled = self.display_option(option, index)
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

    def _drops_onto_row(self, event) -> bool:
        """Reject a drop landing on a row; only gaps between rows reorder."""
        if event.mimeData() is not None and event.mimeData().hasUrls():
            return False
        positions = (
            QtWidgets.QAbstractItemView.DropIndicatorPosition
            if QT_API == 6
            else QtWidgets.QAbstractItemView
        )
        return self.dropIndicatorPosition() == positions.OnItem

    def dragEnterEvent(self, event):
        if self._forward_file_drop("dragEnterEvent", event):
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData() is not None and event.mimeData().hasUrls():
            super().dragMoveEvent(event)
            return
        # Let the base class place the indicator first, then refuse the position
        # that would overwrite a chromatogram instead of reordering the list.
        super().dragMoveEvent(event)
        if self._drops_onto_row(event):
            event.ignore()

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
        # QAbstractItemView.startDrag removes the dragged row itself when an
        # internal move succeeds. The Project-backed reorder has already rebuilt
        # the table, so that removal would delete whichever chromatogram now sits
        # at the old position. Report the drop as ignored to keep it from running.
        ignore_action = (
            QtCore.Qt.DropAction.IgnoreAction if QT_API == 6 else QtCore.Qt.IgnoreAction
        )
        event.setDropAction(ignore_action)
        event.accept()


class MainWindow(QtWidgets.QMainWindow):
    _open_windows = set()
    timeRangeSelected = QtCore.Signal(float, float)

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
            stored_analytes,
            stored_metadata,
        ) = load_complete_preset_store()
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
        self._global_analyte_presets = sanitize_analyte_presets(
            stored_analytes
        )
        self._global_preset_metadata = normalize_preset_metadata(
            self._global_condition_presets,
            self._global_gradient_presets,
            stored_metadata,
            self._global_analyte_presets,
        )
        self._application_language = self._settings.get(UI_LANGUAGE)
        if (
            self._global_condition_presets
            or self._global_gradient_presets
            or self._global_analyte_presets
        ):
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
        self._peak_selection_sync_guard = False
        self._integration_list_dialog = None
        self._dataset_column_order = self._settings.get(DATASET_COLUMN_ORDER)
        self._solo_dataset_id = ""
        self._dataset_header_update_guard = False
        self._dataset_selection_sync_guard = False
        self._dataset_checkbox_press = False
        self._dataset_checkbox_target_rows = []
        self._span_selector = None
        self._span_selector_mode = None
        self._view_state = None
        self._view_state_update_guard = False
        self._view_history = ScreenViewHistory(max_entries=50)
        self._view_initialized = False
        self._dataset_lines = {}
        self._move_drag = None
        self._interaction_cursor = None
        self._annotation_artists = {}
        self._annotation_drag = None
        self._vertical_marker_artists = {}
        self._vertical_marker_label_artists = {}
        self._vertical_marker_drag = None
        self._selected_vertical_marker_id = ""
        self._selected_vertical_marker_ids = set()
        self._mouse_mode = "normal"
        self._selected_time_range = None
        self._edit_range_peak_id = None
        self._peak_edit_return_mouse_mode = None
        self._overview_view_patch = None
        self._overview_zoom_drag = None
        self._overview_zoom_patch = None
        self._overview_split_ratio = 0.25
        self._overview_split_drag = None
        self._overview_full_x = None
        self._overview_full_y = None
        self._overview_secondary_view_patch = None
        self._overview_window_state = ScreenOverviewState(
            enabled=False,
            full_x=(0.0, 1.0),
            detail_x=(0.0, 1.0),
        )
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
        self._screen_renderer_preference = self._settings.get(SCREEN_RENDERER)
        self._automatic_update_check = self._settings.get(AUTOMATIC_UPDATE_CHECK)
        self._auto_peak_sensitivity_presets = self._settings.get(
            AUTO_PEAK_SENSITIVITY_PRESETS
        )
        self._update_check_thread = None
        self._update_check_worker = None
        self._automatic_update_timer = QtCore.QTimer(self)
        self._automatic_update_timer.setSingleShot(True)
        self._automatic_update_timer.timeout.connect(
            lambda: self.check_for_updates(False)
        )
        self._automatic_update_scheduled = False
        self.axes_right = None
        self.axes_gradient = None
        self.axes_gradient_secondary = None
        self._preview_gradient_limits = (0.0, 100.0)
        self._overview_dataset_lines = {}
        self._plot_source_cache = {}
        self._peak_overlay_artists = {}
        self._navigation_interaction_active = False
        self._screen_pan_session = None
        self._screen_preview = None
        self._screen_preview_notice = ""
        self._force_matplotlib_screen_plot = False
        self._matplotlib_screen_complete = True
        self._pending_series_refresh = False
        self._draw_timer = None
        self._build_ui()
        self._build_menus()
        self._retranslate()
        self._refresh_all()
        self.resize(1500, 900)
        self._activate_preferred_screen_renderer()

    def _save_global_preset_file(self):
        try:
            save_preset_store(
                self._global_condition_presets,
                self._global_gradient_presets,
                metadata=self._global_preset_metadata,
                analyte_presets=self._global_analyte_presets,
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
            ("analytes", self._global_analyte_presets),
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
            self._global_analyte_presets,
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
        filename = timestamped_filename(filename)
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
            if (
                cached is None or line is None
                or not self._dataset_is_screen_visible(dataset)
            ):
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

    def _update_screen_preview_notice(self):
        self.screen_preview_checkbox.setText(self._history_label(
            "PyQtGraph画面描画", "PyQtGraph screen renderer"
        ))
        messages = {
            "active": (
                "PyQtGraphを画面描画に使用中です。図出力・印刷はMatplotlibを使用します。",
                "PyQtGraph is rendering the screen. Figure output and printing use Matplotlib.",
            ),
            "unsupported": (
                "この操作は従来描画に戻して続行します。",
                "This operation continues with the Matplotlib renderer.",
            ),
            "missing": (
                "PyQtGraphがインストールされていないか読み込めないため、Matplotlibで表示しています。",
                "PyQtGraph is not installed or could not be loaded; the screen is using Matplotlib.",
            ),
            "failed": (
                "PyQtGraphの初期化または描画に失敗したため、Matplotlibへ戻しました。",
                "PyQtGraph initialization or rendering failed; restored Matplotlib.",
            ),
        }
        message = messages.get(self._screen_preview_notice, ("", ""))
        self.screen_preview_label.setText(self._history_label(*message))
        self.screen_preview_checkbox.setToolTip(self._history_label(
            "Windows 11では既定、Windows 7では任意選択です。選択はアプリ設定へ保存されます。",
            "Default on Windows 11 and opt-in on Windows 7. The selection is saved in application settings.",
        ))

    def _activate_preferred_screen_renderer(self):
        if self._screen_renderer_preference != "pyqtgraph":
            return
        self.screen_preview_checkbox.setChecked(True)

    def _toggle_screen_preview(self, enabled):
        self._screen_renderer_preference = (
            "pyqtgraph" if enabled else "matplotlib"
        )
        self._settings.set(
            SCREEN_RENDERER, self._screen_renderer_preference, sync=True
        )
        if not enabled:
            self._stop_screen_preview()
            return
        controls = ()
        if any(control.isChecked() for control in controls):
            self._stop_screen_preview(unsupported=True)
            return
        if not pyqtgraph_scene_available():
            self._stop_screen_preview(missing=True)
            return
        try:
            if self._screen_preview is None:
                from .screen_preview import ExperimentalScreenPreview
                self._screen_preview = ExperimentalScreenPreview(self)
            if self._span_selector_mode in ("integrate", "edit", "fraction"):
                self._clear_span_selector()
            self.plot_stack.setCurrentWidget(self._screen_preview.consumer.widget)
            self._screen_preview_notice = "active"
            self._update_screen_preview_notice()
            # The constructor initially mirrors the existing complete figure.
            # Once the native widget is live, retain only Matplotlib axes/view
            # state until a full-quality export or fallback needs its artists.
            self._plot()
        except ImportError:
            self._stop_screen_preview(missing=True)
        except Exception:
            self._stop_screen_preview(failed=True)

    def _stop_screen_preview(
        self, *, failed=False, unsupported=False, missing=False
    ):
        if self._screen_preview is None and not self.screen_preview_checkbox.isChecked():
            return
        keep_preference = failed or unsupported or missing
        preview, self._screen_preview = self._screen_preview, None
        self.screen_preview_checkbox.blockSignals(True)
        self.screen_preview_checkbox.setChecked(False)
        self.screen_preview_checkbox.blockSignals(False)
        if keep_preference:
            # A runtime fallback changes only the active renderer. Preserve the
            # explicit application-level choice so restart can retry after an
            # installation or transient initialization problem. PySide2 may
            # otherwise expose the programmatic uncheck as the stored choice.
            self._screen_renderer_preference = "pyqtgraph"
            self._settings.set(SCREEN_RENDERER, "pyqtgraph", sync=True)
        self.plot_stack.setCurrentWidget(self.canvas)
        if preview is not None:
            preview.close()
        if not self._matplotlib_screen_complete:
            self._plot()
        if ((self.integrate_button.isChecked() or self.edit_peak_button.isChecked()
             or self._mouse_mode == "select")
                and self._span_selector is None
                and self._selected_dataset() is not None):
            self._install_span_selector("integrate" if self.integrate_button.isChecked() else
                                        "edit" if self.edit_peak_button.isChecked() else "select")
        self._screen_preview_notice = (
            "missing" if missing else
            "failed" if failed else "unsupported" if unsupported else ""
        )
        self._update_screen_preview_notice()
        self.canvas.draw_idle()
        self.toolbar.set_history_buttons()

    def _refresh_screen_preview(self):
        if self._screen_preview is None:
            return False
        try:
            split = self.project.method.view_mode == "split_y_axes"
            if self._screen_preview.consumer.split_y_axes != split:
                previous = self._screen_preview
                from .screen_preview import ExperimentalScreenPreview
                replacement = ExperimentalScreenPreview(self)
                self._screen_preview = replacement
                self.plot_stack.setCurrentWidget(replacement.consumer.widget)
                previous.close()
            self._screen_preview.refresh()
            return True
        except Exception:
            self._stop_screen_preview(failed=True)
            return False

    def _flush_canvas_draw(self):
        if self._refresh_screen_preview():
            return
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
        if self._refresh_screen_preview():
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

    def _matplotlib_view_state(self):
        return ScreenViewState(
            x=tuple(self.axes.get_xlim()),
            y1=tuple(self.axes.get_ylim()),
            y2=(
                tuple(self.axes_right.get_ylim())
                if self.axes_right is not None
                else None
            ),
            gradient=(
                tuple(self.axes_gradient.get_ylim())
                if self.axes_gradient is not None
                else None
            ),
        )

    def _screen_view_state(self):
        """Return the backend-neutral authoritative interactive view state."""

        if self._view_state is not None:
            return self._view_state
        return self._matplotlib_view_state()

    def _sync_view_state_from_axes(self):
        if (
            not self._view_state_update_guard
            and hasattr(self, "axes")
            and self._view_initialized
        ):
            self._view_state = self._matplotlib_view_state()

    def _begin_axis_pan(self, event):
        if self._screen_pan_session is not None:
            return False
        session = begin_axis_pan(event, self._screen_view_state())
        if session is None:
            return False
        self._push_view_history()
        self._begin_navigation_interaction()
        self._screen_pan_session = session
        return True

    def _update_axis_pan(self, event):
        if self._screen_pan_session is None:
            return False
        bbox = self.axes.bbox
        state = axis_pan_view(
            self._screen_pan_session,
            event,
            canvas_width=bbox.width,
            canvas_height=bbox.height,
        )
        self._view_state = state
        self._view_state_update_guard = True
        try:
            self.axes.set_xlim(*state.x)
            self.axes.set_ylim(*state.y1)
            if self.axes_right is not None and state.y2 is not None:
                self.axes_right.set_ylim(*state.y2)
        finally:
            self._view_state_update_guard = False
        self._request_canvas_draw(throttled=True)
        return True

    def _end_axis_pan(self):
        if self._screen_pan_session is None:
            return False
        self._screen_pan_session = None
        self._end_navigation_interaction()
        self._request_canvas_draw(force=True)
        return True

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
        original_force = self._force_matplotlib_screen_plot
        needs_rebuild = (
            original_quality == LIGHTWEIGHT
            or not self._matplotlib_screen_complete
        )
        if not needs_rebuild:
            yield
            return
        try:
            self._force_matplotlib_screen_plot = True
            self._render_quality = HIGH_QUALITY
            self._set_figure_layout_quality()
            self._plot()
            yield
        finally:
            self._force_matplotlib_screen_plot = original_force
            self._render_quality = original_quality
            self._set_figure_layout_quality()
            self._plot()

    @staticmethod
    def _mode_glyph_icon(kind: str):
        """Compact 24x24 icon for a mouse mode that has no toolbar icon yet.

        Drawn with QPainter, matching _vertical_pointer_icon's approach, so no
        external asset is added (kept offline-build-safe for Windows 7).
        """
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
        ink = QtGui.QColor("#111827")
        accent = QtGui.QColor("#2563eb")
        dash_pen = QtGui.QPen(ink, 1.4)
        dash_style = (
            QtCore.Qt.PenStyle.DashLine if QT_API == 6 else QtCore.Qt.DashLine
        )
        baseline = QtGui.QPainterPath()
        baseline.moveTo(3, 19)
        baseline.lineTo(21, 19)
        peak = QtGui.QPainterPath()
        peak.moveTo(6, 19)
        peak.lineTo(12, 5)
        peak.lineTo(18, 19)
        if kind == "select":
            painter.setPen(ink)
            painter.drawPath(baseline)
            dash_pen.setStyle(dash_style)
            painter.setPen(dash_pen)
            painter.setBrush(QtGui.QBrush(QtGui.QColor(37, 99, 235, 40)))
            painter.drawRect(4, 5, 16, 11)
        elif kind == "integrate":
            painter.setPen(ink)
            painter.drawPath(baseline)
            painter.setPen(QtGui.QPen(accent, 1.4))
            painter.setBrush(QtGui.QBrush(QtGui.QColor(37, 99, 235, 60)))
            painter.drawPath(peak)
        elif kind == "edit_peak":
            painter.setPen(ink)
            painter.drawPath(baseline)
            painter.setPen(QtGui.QPen(accent, 1.6))
            painter.drawPath(peak)
            painter.setPen(QtGui.QPen(ink, 1.4))
            painter.drawLine(17, 4, 21, 8)
            painter.drawLine(20, 5, 21, 8)
            painter.drawLine(20, 5, 17, 7)
        elif kind == "split_peak":
            painter.setPen(ink)
            painter.drawPath(baseline)
            painter.setPen(QtGui.QPen(accent, 1.4))
            painter.drawPath(peak)
            dash_pen.setStyle(dash_style)
            painter.setPen(dash_pen)
            painter.drawLine(12, 4, 12, 19)
        elif kind == "move_trace":
            painter.setPen(QtGui.QPen(accent, 1.6))
            painter.drawLine(3, 15, 21, 9)
            arrow = QtGui.QPainterPath()
            arrow.moveTo(17, 4)
            arrow.lineTo(21, 4)
            arrow.lineTo(21, 8)
            painter.setBrush(QtCore.Qt.NoBrush if QT_API != 6 else QtCore.Qt.BrushStyle.NoBrush)
            painter.drawLine(21, 4, 17, 4)
            painter.drawLine(21, 4, 21, 8)
            painter.drawLine(21, 4, 15, 10)
            painter.setPen(QtGui.QPen(ink, 1.2))
            painter.drawLine(3, 20, 21, 20)
        elif kind == "annotation":
            # Draw the serif T as geometry rather than text so the icon does
            # not depend on Times New Roman (or any other installed font).
            painter.setPen(QtGui.QPen(ink, 2.0))
            glyph = QtGui.QPainterPath()
            glyph.moveTo(4, 5)
            glyph.lineTo(20, 5)
            glyph.moveTo(6, 5)
            glyph.lineTo(6, 8)
            glyph.moveTo(18, 5)
            glyph.lineTo(18, 8)
            glyph.moveTo(12, 5)
            glyph.lineTo(12, 19)
            glyph.moveTo(8, 19)
            glyph.lineTo(16, 19)
            painter.drawPath(glyph)
        painter.end()
        return QtGui.QIcon(pixmap)

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
        self.screen_render_surface = create_screen_render_surface(
            figsize=(8, 5),
            constrained_layout=not self._is_lightweight_rendering(),
        )
        self.figure = self.screen_render_surface.figure
        self.axes = self.figure.add_subplot(111)
        self.canvas = self.screen_render_surface.widget
        self._draw_timer = QtCore.QTimer(self)
        self._draw_timer.setSingleShot(True)
        self._draw_timer.timeout.connect(self._flush_canvas_draw)
        self.toolbar = AxisAwareNavigationToolbar(self.canvas, self)
        self.pointer_action = self._action(checkable=True)
        self.pointer_action.setIcon(self._vertical_pointer_icon())
        self.pointer_action.toggled.connect(self._toggle_pointer_mode)
        self.pointer_toolbar_button = QtWidgets.QToolButton()
        self.pointer_toolbar_button.setDefaultAction(self.pointer_action)
        icon_only = (
            QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly
            if QT_API == 6
            else QtCore.Qt.ToolButtonIconOnly
        )
        self.pointer_toolbar_button.setToolButtonStyle(icon_only)
        self.pointer_toolbar_button.setIconSize(QtCore.QSize(18, 18))
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
        dataset_header = self.dataset_table.horizontalHeader()
        dataset_header.setSectionsMovable(True)
        dataset_header.sectionMoved.connect(self._dataset_header_section_moved)
        self.dataset_table.itemChanged.connect(self._dataset_item_changed)
        self.dataset_table.itemSelectionChanged.connect(self._dataset_selection_changed)
        self.dataset_table.rowMoveRequested.connect(self.move_dataset_to)
        self.dataset_table.setItemDelegateForColumn(
            DATASET_SOURCE_COLUMN, LeftElideDelegate(self.dataset_table)
        )
        self.dataset_table.setItemDelegateForColumn(
            DATASET_COLOR_COLUMN, ColorCellDelegate(self.dataset_table)
        )
        self.dataset_table.viewport().installEventFilter(self)
        for column_id in DATASET_HIDDEN_COLUMN_IDS:
            self.dataset_table.setColumnHidden(DATASET_COLUMNS_BY_ID[column_id], True)
        self._apply_dataset_column_order(self._dataset_column_order)
        left_layout.addWidget(self.dataset_table, 1)
        self.import_button = QtWidgets.QPushButton()
        self.remove_button = QtWidgets.QPushButton()
        self.batch_metadata_button = QtWidgets.QPushButton()
        self.color_button = QtWidgets.QPushButton()
        self.show_all_button = QtWidgets.QPushButton()
        self.hide_all_button = QtWidgets.QPushButton()
        self.move_dataset_up_button = QtWidgets.QPushButton()
        self.move_dataset_down_button = QtWidgets.QPushButton()
        self.group_run_button = QtWidgets.QPushButton()
        self.ungroup_run_button = QtWidgets.QPushButton()

        self.dataset_data_group = QtWidgets.QGroupBox()
        self.dataset_data_layout = QtWidgets.QGridLayout(self.dataset_data_group)
        data_buttons = (
            self.import_button,
            self.remove_button,
        )
        for index, button in enumerate(data_buttons):
            self.dataset_data_layout.addWidget(button, index // 2, index % 2)
        left_layout.addWidget(self.dataset_data_group)

        self.dataset_analysis_group = QtWidgets.QGroupBox()
        self.dataset_analysis_layout = QtWidgets.QGridLayout(
            self.dataset_analysis_group
        )
        analysis_buttons = (
            self.batch_metadata_button,
            self.color_button,
            self.group_run_button,
            self.ungroup_run_button,
        )
        for index, button in enumerate(analysis_buttons):
            self.dataset_analysis_layout.addWidget(button, index // 2, index % 2)
        left_layout.addWidget(self.dataset_analysis_group)

        self.spectrum_display_group = QtWidgets.QGroupBox()
        spectrum_display_buttons = QtWidgets.QGridLayout(
            self.spectrum_display_group
        )
        spectrum_display_buttons.addWidget(self.show_all_button, 0, 0)
        spectrum_display_buttons.addWidget(self.hide_all_button, 0, 1)
        spectrum_display_buttons.addWidget(self.move_dataset_up_button, 1, 0)
        spectrum_display_buttons.addWidget(self.move_dataset_down_button, 1, 1)
        left_layout.addWidget(self.spectrum_display_group)
        self.import_button.clicked.connect(self.import_ascii)
        self.remove_button.clicked.connect(self.remove_dataset)
        self.batch_metadata_button.clicked.connect(self.edit_batch_metadata)
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
        preview_controls = QtWidgets.QHBoxLayout()
        self.screen_preview_checkbox = QtWidgets.QCheckBox()
        self.screen_preview_checkbox.setEnabled(
            QT_API == 6 or pyqtgraph_scene_available()
        )
        self.screen_preview_checkbox.toggled.connect(self._toggle_screen_preview)
        self.screen_preview_label = QtWidgets.QLabel()
        self.screen_preview_label.setWordWrap(True)
        preview_controls.addWidget(self.screen_preview_checkbox)
        preview_controls.addWidget(self.screen_preview_label, 1)
        plot_layout.addLayout(preview_controls)
        self.plot_stack = QtWidgets.QStackedWidget()
        self.plot_stack.addWidget(self.canvas)
        plot_layout.addWidget(self.plot_stack, 1)
        plot_panel.setMinimumHeight(220)
        self.right_splitter.addWidget(plot_panel)

        analysis_panel = QtWidgets.QWidget()
        analysis_layout = QtWidgets.QVBoxLayout(analysis_panel)
        analysis_layout.setContentsMargins(0, 0, 0, 0)
        self.analysis_splitter = QtWidgets.QSplitter(vertical)
        self.analysis_splitter.setChildrenCollapsible(False)
        analysis_layout.addWidget(self.analysis_splitter, 1)
        analysis_controls_panel = QtWidgets.QWidget()
        controls = QtWidgets.QHBoxLayout(analysis_controls_panel)
        controls.setContentsMargins(0, 0, 0, 0)

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
        self.show_grid_checkbox = QtWidgets.QCheckBox()
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
        self.legend_axis_button_row = QtWidgets.QHBoxLayout()
        self.legend_axis_button_row.setContentsMargins(0, 0, 0, 0)
        self.legend_axis_button_row.setSpacing(4)
        self.legend_axis_button_row.addWidget(self.legend_settings_button)
        self.legend_axis_button_row.addWidget(self.axis_labels_button)
        display_controls.addLayout(self.legend_axis_button_row, 2, 0, 1, 2)
        display_controls.addWidget(self.show_integration_checkbox, 3, 0, 1, 2)
        display_controls.addWidget(self.show_retention_checkbox, 4, 0, 1, 2)
        display_controls.addWidget(self.show_gradient_checkbox, 5, 0, 1, 2)
        display_controls.addWidget(self.show_grid_checkbox, 6, 0, 1, 2)
        display_controls.setRowStretch(7, 1)

        self.navigation_group = QtWidgets.QGroupBox()
        navigation_controls = QtWidgets.QGridLayout(self.navigation_group)
        self.mouse_mode_label = QtWidgets.QLabel()
        self.mouse_mode_combo = QtWidgets.QComboBox()
        for mode_id in MOUSE_MODE_IDS:
            self.mouse_mode_combo.addItem(mode_id, mode_id)
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
        self.view_mode_combo.addItem("Split Y1 / Y2", "split_y_axes")
        self.move_trace_button = QtWidgets.QPushButton()
        self.move_trace_button.setCheckable(True)
        self.reset_trace_position_button = QtWidgets.QPushButton()
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
        # Row order (Issue #236 / workflow doc 9.18): move_controls (trace
        # move) moves below the view-reset row. Widget creation and signal
        # connections are unchanged; only the row numbers below move.
        navigation_controls.addWidget(self.mouse_mode_label, 0, 0)
        navigation_controls.addWidget(self.mouse_mode_combo, 0, 1)
        move_controls = QtWidgets.QHBoxLayout()
        move_controls.setContentsMargins(0, 0, 0, 0)
        move_controls.setSpacing(4)
        move_controls.addWidget(self.move_trace_button)
        move_controls.addWidget(self.move_axis_combo)
        move_controls.addWidget(self.reset_trace_position_button)
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
        navigation_controls.addLayout(move_controls, 5, 0, 1, 2)
        navigation_controls.setRowStretch(6, 1)

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
        self.clear_fractions_button = QtWidgets.QPushButton()
        self.fraction_numeric_button = QtWidgets.QPushButton()
        self.auto_detect_button = QtWidgets.QPushButton()
        self.fit_peak_button = QtWidgets.QPushButton()
        self.saturation_correction_button = QtWidgets.QPushButton()
        self.select_all_peaks_button = QtWidgets.QPushButton()
        self.delete_peak_button = QtWidgets.QPushButton()
        self.integration_list_button = QtWidgets.QPushButton()
        integration_controls.addWidget(self.baseline_label, 0, 0)
        integration_controls.addWidget(self.baseline_combo, 0, 1, 1, 2)
        integration_controls.addWidget(self.integrate_button, 1, 0)
        integration_controls.addWidget(self.edit_peak_button, 1, 1)
        integration_controls.addWidget(self.split_peak_button, 1, 2)
        integration_controls.addWidget(self.auto_detect_button, 2, 0, 1, 2)
        integration_controls.addWidget(self.fit_peak_button, 2, 2)
        integration_controls.addWidget(
            self.saturation_correction_button, 3, 0, 1, 3
        )
        integration_controls.addWidget(self.select_all_peaks_button, 4, 0, 1, 2)
        integration_controls.addWidget(self.delete_peak_button, 4, 2)
        integration_controls.addWidget(
            self.fraction_numeric_button, 5, 0, 1, 2
        )
        integration_controls.addWidget(self.clear_fractions_button, 5, 2)
        integration_controls.addWidget(self.integration_list_button, 6, 0, 1, 3)
        integration_controls.setRowStretch(7, 1)

        # Issue #236 / 9.15: installed here, once every group-panel control
        # it binds to (move_trace_button, integrate_button, edit_peak_button,
        # split_peak_button, annotation_action) actually exists.
        self._install_mode_toolbar_actions()

        controls.addWidget(self.display_group, 4)
        controls.addWidget(self.navigation_group, 2)
        controls.addWidget(self.integration_group, 4)
        analysis_controls_panel.setMinimumHeight(150)
        self.analysis_splitter.addWidget(analysis_controls_panel)
        peak_panel = QtWidgets.QWidget()
        peak_layout = QtWidgets.QVBoxLayout(peak_panel)
        peak_layout.setContentsMargins(0, 0, 0, 0)
        self.peak_title = QtWidgets.QLabel()
        self.peak_title.setFont(font)
        peak_layout.addWidget(self.peak_title)
        self.peak_table = QtWidgets.QTableWidget(0, PEAK_COLUMN_COUNT)
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
        peak_layout.addWidget(self.peak_table, 1)
        peak_panel.setMinimumHeight(80)
        self.analysis_splitter.addWidget(peak_panel)
        self.analysis_splitter.setStretchFactor(0, 3)
        self.analysis_splitter.setStretchFactor(1, 2)
        self.analysis_splitter.setSizes((180, 120))
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
        self.show_grid_checkbox.toggled.connect(self._method_controls_changed)
        self.legend_combo.currentIndexChanged.connect(self._method_controls_changed)
        self.zoom_axis_combo.currentIndexChanged.connect(self._zoom_axis_changed)
        self.view_mode_combo.currentIndexChanged.connect(self._view_mode_changed)
        self.mouse_mode_combo.currentIndexChanged.connect(self._mouse_mode_changed)
        self.integrate_button.toggled.connect(self._toggle_integration)
        self.split_peak_button.toggled.connect(self._toggle_split_mode)
        self.clear_fractions_button.clicked.connect(self.clear_fraction_regions)
        self.fraction_numeric_button.clicked.connect(self.edit_fraction_range_numeric)
        self.edit_peak_button.toggled.connect(self._toggle_edit_range_mode)
        self.delete_peak_button.clicked.connect(self.delete_peak)
        self.auto_detect_button.clicked.connect(self.auto_detect_peaks)
        self.fit_peak_button.clicked.connect(self.fit_selected_peak)
        self.saturation_correction_button.clicked.connect(
            self.correct_saturated_peak
        )
        self.select_all_peaks_button.clicked.connect(self.peak_table.selectAll)
        self.integration_list_button.clicked.connect(self.open_integration_list)
        self.move_trace_button.toggled.connect(self._toggle_move_mode)
        self.reset_trace_position_button.clicked.connect(
            self.reset_selected_trace_position
        )
        self.axis_labels_button.clicked.connect(self.edit_axis_labels)
        self.legend_settings_button.clicked.connect(self.edit_legend_composer)
        self.reset_view_button.clicked.connect(self._reset_view)
        self.reset_x_view_button.clicked.connect(self._reset_x_view)
        self.reset_y_view_button.clicked.connect(self._reset_y_view)
        self._install_view_shortcuts()
        self.peak_table.itemDoubleClicked.connect(self._peak_item_double_clicked)
        self.screen_render_surface.connect_event("scroll_event", self._on_scroll)
        self.screen_render_surface.connect_event(
            "button_press_event", self._on_canvas_press
        )
        self.screen_render_surface.connect_event(
            "motion_notify_event", self._on_canvas_motion
        )
        self.screen_render_surface.connect_event(
            "button_release_event", self._on_canvas_release
        )

    def _wire_toolbar_navigation_actions(self):
        toolbar_actions = getattr(self.toolbar, "_actions", {}) or {}
        for key in ("pan", "zoom"):
            action = toolbar_actions.get(key)
            if action is not None:
                action.triggered.connect(self._toolbar_navigation_triggered)

    def _bind_mode_control_pair(self, action, control):
        """Keep a new toolbar QAction and an existing mode control in lockstep.

        Both QAction and (checkable) QPushButton expose a toggled(bool)
        signal, so one small guarded pair of connections keeps whichever one
        the user actually clicks in sync with the other, without going
        through the mouse-mode combo (which stays in sync separately, through
        each control's own existing toggle handler).
        """
        guard = {"active": False}

        def from_action(checked):
            if guard["active"] or control.isChecked() == checked:
                return
            guard["active"] = True
            try:
                control.setChecked(checked)
            finally:
                guard["active"] = False

        def from_control(checked):
            if guard["active"] or action.isChecked() == checked:
                return
            guard["active"] = True
            try:
                action.setChecked(checked)
            finally:
                guard["active"] = False

        action.toggled.connect(from_action)
        control.toggled.connect(from_control)

    def _install_mode_toolbar_actions(self):
        """Add one toolbar icon per mouse mode (Issue #236 / workflow doc 9.15).

        Pointer already has one. "normal" and "zoom" are represented by the
        toolbar's own Pan and Zoom icons. The
        remaining six each get a new, small icon inserted next to pointer;
        five of them mirror an existing checkable control two-way, so the
        mode combo, this icon and the existing group-panel button (or, for
        annotation, the existing action shared with the edit menu) always
        agree, matching whichever the user actually clicked.
        """
        t = self.translator
        toolbar_actions = self.toolbar.actions()
        insert_before = toolbar_actions[0] if toolbar_actions else None

        def add_icon(action, tooltip_key):
            action.setToolTip(t(tooltip_key))
            if insert_before is not None:
                self.toolbar.insertAction(insert_before, action)
            else:
                self.toolbar.addAction(action)
            return action

        self.select_toolbar_action = self._action(checkable=True)
        self.select_toolbar_action.setIcon(self._mode_glyph_icon("select"))
        self.select_toolbar_action.toggled.connect(self._select_toolbar_toggled)
        add_icon(self.select_toolbar_action, "mouse_mode_select")

        self.integrate_toolbar_action = self._action(checkable=True)
        self.integrate_toolbar_action.setIcon(self._mode_glyph_icon("integrate"))
        self._bind_mode_control_pair(
            self.integrate_toolbar_action, self.integrate_button
        )
        add_icon(self.integrate_toolbar_action, "integrate")

        self.edit_peak_toolbar_action = self._action(checkable=True)
        self.edit_peak_toolbar_action.setIcon(self._mode_glyph_icon("edit_peak"))
        self._bind_mode_control_pair(
            self.edit_peak_toolbar_action, self.edit_peak_button
        )
        add_icon(self.edit_peak_toolbar_action, "edit_peak")

        self.split_peak_toolbar_action = self._action(checkable=True)
        self.split_peak_toolbar_action.setIcon(self._mode_glyph_icon("split_peak"))
        self._bind_mode_control_pair(
            self.split_peak_toolbar_action, self.split_peak_button
        )
        add_icon(self.split_peak_toolbar_action, "split_peak")

        self.move_trace_toolbar_action = self._action(checkable=True)
        self.move_trace_toolbar_action.setIcon(self._mode_glyph_icon("move_trace"))
        self._bind_mode_control_pair(
            self.move_trace_toolbar_action, self.move_trace_button
        )
        add_icon(self.move_trace_toolbar_action, "move_trace")

        # annotation_action is a QAction already (shared with the edit menu
        # and the "表示" panel button), so it is placed in the toolbar
        # directly rather than bound to a duplicate.
        self.annotation_action.setIcon(self._mode_glyph_icon("annotation"))
        if insert_before is not None:
            self.toolbar.insertAction(insert_before, self.annotation_action)
        else:
            self.toolbar.addAction(self.annotation_action)

        if insert_before is not None:
            self.toolbar.insertSeparator(insert_before)
        else:
            self.toolbar.addSeparator()

        self._configure_toolbar_actions()

    def _configure_toolbar_actions(self):
        """Keep independent Pan/Zoom icons and remove only Customize.

        Issue #249 reverses #236's temporary Pan/Zoom icon merge: the native
        Zoom QAction remains visible and represents the dedicated zoom mouse
        mode. Customize (the arrow icon, `edit_parameters`) remains removed
        (Issue #236 / 9.16): Subplots (`configure_subplots`) now always opens
        the application's own axis dialog on both renderers, so Customize's
        PyQtGraph behaviour was already a duplicate, and its Matplotlib-only
        axis-range/curve-color options are covered by that same dialog
        together with the existing "表示設定" trace-color/line-style dialog.
        """
        actions = getattr(self.toolbar, "_actions", {}) or {}
        customize_action = actions.get("edit_parameters")
        mode_actions = {
            "normal": actions.get("pan"),
            "zoom": actions.get("zoom"),
            "pointer": self.pointer_toolbar_widget_action,
            "select": self.select_toolbar_action,
            "integrate": self.integrate_toolbar_action,
            "edit_peak": self.edit_peak_toolbar_action,
            "split_peak": self.split_peak_toolbar_action,
            "move_trace": self.move_trace_toolbar_action,
            "annotation": self.annotation_action,
        }
        current_actions = list(self.toolbar.actions())
        mouse_actions = {action for action in mode_actions.values() if action is not None}
        utility_actions = [
            action
            for action in current_actions
            if not action.isSeparator()
            and action not in mouse_actions
            and action is not customize_action
        ]

        # Rebuild from the shared mode order instead of individually inserting
        # before a fixed action. Any future mode added to MOUSE_MODE_IDS has one
        # obvious mapping point and cannot silently drift from the dropdown.
        for action in current_actions:
            self.toolbar.removeAction(action)
        for mode_id in MOUSE_MODE_IDS:
            action = mode_actions.get(mode_id)
            if action is not None:
                self.toolbar.addAction(action)
        if utility_actions:
            self.toolbar.addSeparator()
            for action in utility_actions:
                self.toolbar.addAction(action)

    def _select_toolbar_toggled(self, checked: bool):
        """Route the select toolbar icon through the combo.

        "select" has no existing group-panel control (workflow doc 9.15), and
        entering/leaving it runs extra logic in _mouse_mode_changed (a
        dataset check, the span selector) that a simple two-way bind would
        bypass, so this goes through the combo -- the same path the dropdown
        itself uses -- instead of duplicating that logic here.
        """
        if getattr(self, "_select_toolbar_sync", False):
            return
        self._select_toolbar_sync = True
        try:
            if checked:
                index = self.mouse_mode_combo.findData("select")
                if index >= 0:
                    self.mouse_mode_combo.setCurrentIndex(index)
                if self.mouse_mode_combo.currentData() != "select":
                    # The dataset check in _mouse_mode_changed rejected it.
                    self.select_toolbar_action.setChecked(False)
            elif self._mouse_mode == "select":
                self._set_mouse_mode_display("normal")
                self._clear_span_selector()
                self._hide_interaction_cursor()
                self.statusBar().clearMessage()
                self._ensure_normal_mode_navigation()
        finally:
            self._select_toolbar_sync = False

    def _toolbar_navigation_triggered(self, *_args):
        pan_active = self.toolbar._actions["pan"].isChecked()
        zoom_active = self.toolbar._actions["zoom"].isChecked()
        if not zoom_active and self._screen_preview is not None:
            self._screen_preview.cancel_zoom_drag()
        target_mode = "zoom" if zoom_active else "normal"
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
        target_index = self.mouse_mode_combo.findData(target_mode)
        self.mouse_mode_combo.setCurrentIndex(max(0, target_index))
        if not (pan_active or zoom_active):
            self._ensure_normal_mode_navigation()

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
            mouse_press_type = (
                QtCore.QEvent.Type.MouseButtonPress
                if QT_API == 6
                else QtCore.QEvent.MouseButtonPress
            )
            shift_modifier = (
                QtCore.Qt.KeyboardModifier.ShiftModifier
                if QT_API == 6
                else QtCore.Qt.ShiftModifier
            )
            if event.type() == mouse_press_type:
                position = event.position() if QT_API == 6 else event.pos()
                if hasattr(position, "toPoint"):
                    position = position.toPoint()
                index = self.dataset_table.indexAt(position)
                checkbox_columns = (
                    DATASET_SELECTED_COLUMN,
                    DATASET_VISIBLE_COLUMN,
                )
                selected_rows = self._selected_dataset_rows()
                self._dataset_checkbox_target_rows = (
                    selected_rows
                    if (
                        index.isValid()
                        and index.column() in checkbox_columns
                        and index.row() in selected_rows
                        and len(selected_rows) > 1
                    )
                    else []
                )
                self._dataset_checkbox_press = bool(
                    index.isValid() and index.column() == DATASET_SELECTED_COLUMN
                )
                if index.isValid() and index.column() in checkbox_columns:
                    QtCore.QTimer.singleShot(0, self._finish_dataset_checkbox_press)
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
            "line_style",
            "peaks",
            "fitted_peaks",
        )
        return {
            "method": deepcopy(self.project.method),
            "runs": deepcopy(self.project.runs),
            "annotations": deepcopy(self.project.annotations),
            "work_directories": deepcopy(self.project.work_directories),
            "vertical_markers": deepcopy(self.project.vertical_markers),
            "fraction_regions": deepcopy(self.project.fraction_regions),
            "condition_presets": deepcopy(self.project.condition_presets),
            "gradient_presets": deepcopy(self.project.gradient_presets),
            "dataset_order": [dataset.id for dataset in self.project.datasets],
            # References, not copies: raw arrays are never mutated in place, so
            # holding the objects lets Undo restore a removed Dataset without
            # duplicating its time/intensity data in every history entry.
            "dataset_objects": {
                dataset.id: dataset for dataset in self.project.datasets
            },
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
        # next_run_number is a high-water mark: Undo restores Run IDs, not
        # permission to reuse numbers already allocated by a split/import.
        self.project.annotations = deepcopy(state.get("annotations", []))
        self.project.work_directories = deepcopy(state.get("work_directories", []))
        self.project.vertical_markers = deepcopy(
            state.get("vertical_markers", [])
        )
        self.project.fraction_regions = deepcopy(
            state.get("fraction_regions", [])
        )
        self.project.condition_presets = deepcopy(state["condition_presets"])
        self.project.gradient_presets = deepcopy(state["gradient_presets"])
        order = state.get("dataset_order", [])
        if order:
            by_id = {dataset.id: dataset for dataset in self.project.datasets}
            # A Dataset missing from the Project was removed after the snapshot;
            # restore the recorded object itself so Undo brings it back in place.
            by_id.update({
                item_id: dataset
                for item_id, dataset in state.get("dataset_objects", {}).items()
                if item_id not in by_id
            })
            reordered = [by_id[item_id] for item_id in order if item_id in by_id]
            if "dataset_objects" not in state:
                # Legacy in-memory state without the recorded objects: keep any
                # Dataset it does not know about rather than dropping it.
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
        had_solo = bool(self._solo_dataset_id)
        self._solo_dataset_id = ""
        if not self.project.datasets:
            return
        if all(dataset.visible == visible for dataset in self.project.datasets):
            if had_solo:
                self._refresh_dataset_table(self.dataset_table.currentRow())
                self._plot()
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

    # Single letters, so they must never be swallowed while text is being typed.
    VIEW_SHORTCUT_KEYS = (
        ("x", "_reset_x_view"), ("y", "_reset_y_view"),
        ("w", "_reset_view"), ("i", "_shortcut_integrate"),
        ("s", "_shortcut_split_peak"), ("z", "_shortcut_zoom"),
        ("p", "_shortcut_pan"), ("a", "_shortcut_select"),
    )

    def _set_mouse_mode_from_shortcut(self, mode):
        index = self.mouse_mode_combo.findData(mode)
        if index >= 0:
            self.mouse_mode_combo.setCurrentIndex(index)

    def _shortcut_integrate(self):
        self._set_mouse_mode_from_shortcut("integrate")

    def _shortcut_split_peak(self):
        self._set_mouse_mode_from_shortcut("split_peak")

    def _shortcut_zoom(self):
        self._set_mouse_mode_from_shortcut("zoom")

    def _shortcut_pan(self):
        self._set_mouse_mode_from_shortcut("normal")

    def _shortcut_select(self):
        self._set_mouse_mode_from_shortcut("select")

    def _install_view_shortcuts(self):
        """Bind x / y / w to the existing full-view buttons, both renderers.

        The shortcuts live on the window rather than on one renderer widget, so
        they behave the same for the Matplotlib canvas and the native preview.
        A shortcut consumes its key before the focused widget sees it, so they
        are disabled outright while an editor has focus instead of being
        filtered when they fire.
        """
        shortcut_class = getattr(QtGui, "QShortcut", None) or QtWidgets.QShortcut
        self._view_shortcuts = []
        for key, slot_name in self.VIEW_SHORTCUT_KEYS:
            shortcut = shortcut_class(QtGui.QKeySequence(key), self)
            shortcut.activated.connect(getattr(self, slot_name))
            self._view_shortcuts.append(shortcut)
        application = QtWidgets.QApplication.instance()
        if application is not None:
            application.focusChanged.connect(self._update_view_shortcuts)
        self._update_view_shortcuts()

    def _update_view_shortcuts(self, _old=None, new=None):
        widget = new if new is not None else QtWidgets.QApplication.focusWidget()
        editing = isinstance(widget, (
            QtWidgets.QLineEdit,
            QtWidgets.QAbstractSpinBox,
            QtWidgets.QTextEdit,
            QtWidgets.QPlainTextEdit,
            QtWidgets.QComboBox,
        ))
        for shortcut in getattr(self, "_view_shortcuts", ()):
            shortcut.setEnabled(not editing)

    def _update_dataset_order_buttons(self):
        row = self.dataset_table.currentRow()
        count = len(self.project.datasets)
        self.move_dataset_up_button.setEnabled(0 < row < count)
        self.move_dataset_down_button.setEnabled(0 <= row < count - 1)

    def _apply_dataset_column_order(self, order):
        normalized = [str(column_id) for column_id in order]
        if (
            len(normalized) == len(DEFAULT_DATASET_COLUMN_ORDER) - 1
            and set(normalized) == set(DEFAULT_DATASET_COLUMN_ORDER) - {"solo"}
        ):
            normalized.append("solo")
        elif (
            len(normalized) != len(DEFAULT_DATASET_COLUMN_ORDER)
            or set(normalized) != set(DEFAULT_DATASET_COLUMN_ORDER)
        ):
            normalized = list(DEFAULT_DATASET_COLUMN_ORDER)
        header = self.dataset_table.horizontalHeader()
        full_order = list(DATASET_HIDDEN_COLUMN_IDS) + normalized
        self._dataset_header_update_guard = True
        try:
            for target_position, column_id in enumerate(full_order):
                logical_column = DATASET_COLUMNS_BY_ID[column_id]
                current_position = header.visualIndex(logical_column)
                if current_position != target_position:
                    header.moveSection(current_position, target_position)
        finally:
            self._dataset_header_update_guard = False
        self._dataset_column_order = normalized

    def _current_dataset_column_order(self):
        header = self.dataset_table.horizontalHeader()
        visible_ids = set(DEFAULT_DATASET_COLUMN_ORDER)
        return [
            DATASET_COLUMN_IDS[header.logicalIndex(position)]
            for position in range(header.count())
            if DATASET_COLUMN_IDS[header.logicalIndex(position)] in visible_ids
        ]

    def _dataset_header_section_moved(
        self, _logical_column: int, _old_position: int, _new_position: int
    ):
        if self._dataset_header_update_guard:
            return
        order = self._current_dataset_column_order()
        self._dataset_column_order = order
        self._settings.set(DATASET_COLUMN_ORDER, order, sync=True)

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
        self.work_directories_action = self._action(self.edit_work_directories)
        self.reload_work_directories_action = self._action(self.reload_work_directories)
        self.export_figure_action = self._action(self.export_figure)
        self.three_d_figure_action = self._action(self.open_3d_chromatogram)
        self.copy_view_action = self._action(self.copy_view_to_clipboard)
        self.print_view_action = self._action(self.print_current_view)
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
        self.file_menu.addAction(self.work_directories_action)
        self.file_menu.addAction(self.reload_work_directories_action)
        self.file_menu.addSeparator()
        for action in (
            self.export_figure_action,
            self.three_d_figure_action,
            self.copy_view_action,
            self.print_view_action,
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
        self.preset_manager_action = self._action(self.manage_presets)
        self.settings_menu.addAction(self.preferences_action)
        self.settings_menu.addAction(self.preset_manager_action)
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
        self.check_updates_action = self._action(
            lambda _checked=False: self.check_for_updates(True)
        )
        self.about_action = self._action(self.about)
        self.help_menu.addAction(self.quantitation_help_action)
        self.help_menu.addAction(self.check_updates_action)
        self.help_menu.addSeparator()
        self.help_menu.addAction(self.about_action)
        self._update_undo_actions()

    def _retranslate(self):
        t = self.translator
        self._update_screen_preview_notice()
        if self._screen_preview is not None:
            self._screen_preview.consumer.set_overview_tooltips(
                t("navigation_scrollbar_tooltip"),
                t("overview_zoom_in_tooltip"),
                t("overview_zoom_out_tooltip"),
                t("overview_home_tooltip"),
            )
        self.file_menu.setTitle(t("file"))
        action_texts = (
            (self.new_action, "new"),
            (self.new_window_action, "new_window"),
            (self.open_action, "open"),
            (self.save_action, "save"),
            (self.save_as_action, "save_as"),
            (self.import_action, "import"),
            (self.import_directory_action, "import_directory"),
            (self.work_directories_action, "work_directories"),
            (self.reload_work_directories_action, "reload_work_directories"),
            (self.export_figure_action, "export_figure"),
            (self.three_d_figure_action, "three_d_chromatogram"),
            (self.copy_view_action, "copy_view"),
            (self.print_view_action, "print_view"),
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
            (self.preset_manager_action, "preset_manager"),
            (self.database_open_action, "database_open"),
            (self.database_sync_action, "database_sync"),
            (self.japanese_action, "japanese"),
            (self.english_action, "english"),
            (self.quantitation_help_action, "quantitation_help"),
            (self.check_updates_action, "check_updates"),
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
                t("timestamp"),
                t("wavelength"),
                t("group"),
                t("y_axis"),
                t("auv"),
                t("x_shift"),
                t("offset"),
                t("color"),
                t("column"),
                t("source"),
                t("selected"),
                t("solo"),
            )
        )
        self.import_button.setText(t("add"))
        self.remove_button.setText(t("remove"))
        self.batch_metadata_button.setText(t("batch_input"))
        self.color_button.setText(t("spectrum"))
        self.show_all_button.setText(t("show_all"))
        self.hide_all_button.setText(t("hide_all"))
        self.move_dataset_up_button.setText(t("move_up"))
        self.move_dataset_down_button.setText(t("move_down"))
        self.group_run_button.setText(t("group_run"))
        self.ungroup_run_button.setText(t("ungroup_run"))
        self.dataset_data_group.setTitle(t("dataset_data_group"))
        self.dataset_analysis_group.setTitle(t("dataset_analysis_group"))
        self.spectrum_display_group.setTitle(t("spectrum_display_group"))
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
        self.clear_fractions_button.setText(t("clear_fractions"))
        self.fraction_numeric_button.setText(t("fraction_numeric"))
        self.delete_peak_button.setText(t("delete_peak"))
        self.show_integration_checkbox.setText(t("show_integration"))
        self.show_retention_checkbox.setText(t("show_retention_labels"))
        self.show_gradient_checkbox.setText(t("show_gradient_b"))
        self.show_gradient_checkbox.setToolTip(self._history_label(
            "上下2画面では、選択中のクロマトグラムのB%曲線を両方に表示／非表示にします。",
            "In split view, show or hide the selected chromatogram's B% curve on both panels.",
        ))
        self.show_grid_checkbox.setText(t("show_major_grid"))
        self.reset_view_button.setText(t("reset_view"))
        self.reset_x_view_button.setText(t("reset_x_view"))
        self.reset_y_view_button.setText(t("reset_y_view"))
        # The keys are the same in both languages, so the hint needs no wording.
        for button, key in (
            (self.reset_view_button, "W"),
            (self.reset_x_view_button, "X"),
            (self.reset_y_view_button, "Y"),
        ):
            button.setToolTip("%s (%s)" % (button.text(), key))
        self.zoom_axis_label.setText(t("zoom_axis"))
        self.zoom_axis_combo.setItemText(0, t("zoom_auto"))
        self.zoom_axis_combo.setItemText(1, t("zoom_both"))
        self.zoom_axis_combo.setItemText(2, t("zoom_x"))
        self.zoom_axis_combo.setItemText(3, t("zoom_y"))
        self.view_mode_label.setText(t("view_mode"))
        self.view_mode_combo.setItemText(0, t("view_single"))
        self.view_mode_combo.setItemText(1, t("view_overview_detail"))
        self.view_mode_combo.setItemText(2, t("view_split_y_axes"))
        self.mouse_mode_label.setText(t("mouse_mode"))
        for index, mode_id in enumerate(MOUSE_MODE_IDS):
            self.mouse_mode_combo.setItemText(
                index, t("mouse_mode_" + mode_id)
            )
        self.move_trace_button.setText(t("move_trace"))
        self.reset_trace_position_button.setText(t("reset_trace_position"))
        self.pointer_action.setText(t("pointer_line"))
        self.pointer_action.setToolTip(t("pointer_hint"))
        self.pointer_toolbar_button.setAccessibleName(t("pointer_line"))
        self.pointer_control_button.setAccessibleName(t("pointer_line"))
        self.axis_labels_button.setText(t("axis_labels"))
        self.annotation_action.setText(t("add_text_annotation"))
        self.annotation_action.setToolTip(t("text_annotation_hint"))
        toolbar_actions = getattr(self.toolbar, "_actions", {}) or {}
        for key, tooltip_key in (
            ("home", "toolbar_home_tooltip"),
            ("back", "toolbar_back_tooltip"),
            ("forward", "toolbar_forward_tooltip"),
            ("pan", "toolbar_pan_tooltip"),
            ("zoom", "toolbar_zoom_tooltip"),
            ("configure_subplots", "toolbar_subplots_tooltip"),
            ("save_figure", "toolbar_save_tooltip"),
        ):
            action = toolbar_actions.get(key)
            if action is not None:
                action.setToolTip(t(tooltip_key))
        for action, tooltip_key in (
            (getattr(self, "select_toolbar_action", None), "mouse_mode_select"),
            (getattr(self, "integrate_toolbar_action", None), "integrate"),
            (getattr(self, "edit_peak_toolbar_action", None), "edit_peak"),
            (getattr(self, "split_peak_toolbar_action", None), "split_peak"),
            (getattr(self, "move_trace_toolbar_action", None), "move_trace"),
        ):
            if action is not None:
                action.setToolTip(t(tooltip_key))
        self.auto_detect_button.setText(t("auto_detect"))
        self.fit_peak_button.setText(t("fit_peak"))
        self.saturation_correction_button.setText(t("saturation_correction"))
        self.saturation_correction_button.setToolTip(
            t("saturation_correction_hint")
        )
        self.select_all_peaks_button.setText(t("select_all_peaks"))
        self.integration_list_button.setText(t("integration_list"))
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
            "種別" if ja else "Type",
            "親ピーク" if ja else "Parent peak",
            "フィットモデル" if ja else "Fit model",
            "Fit R²",
            "Fit RMSE (µV)",
            "Fit AIC",
        )
        tables = [self.peak_table]
        if self._integration_list_dialog is not None:
            tables.append(self._integration_list_dialog.table)
        for table in tables:
            table.setHorizontalHeaderLabels(headers)
        self._retranslate_integration_list()

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
        if self._solo_dataset_id and not any(
            dataset.id == self._solo_dataset_id
            for dataset in self.project.datasets
        ):
            self._solo_dataset_id = ""
        if selected_row is None:
            selected_row = self.dataset_table.currentRow()
        self._updating_table = True
        self.dataset_table.setRowCount(len(self.project.datasets))
        for row, dataset in enumerate(self.project.datasets):
            selected = _read_only_item("")
            selected.setCheckState(UNCHECKED)
            selected.setData(USER_ROLE, dataset.id)
            self.dataset_table.setItem(row, DATASET_SELECTED_COLUMN, selected)
            show = _read_only_item("")
            show.setCheckState(CHECKED if dataset.visible else UNCHECKED)
            show.setData(USER_ROLE, dataset.id)
            self.dataset_table.setItem(row, DATASET_VISIBLE_COLUMN, show)
            solo = _read_only_item("")
            solo.setCheckState(
                CHECKED if dataset.id == self._solo_dataset_id else UNCHECKED
            )
            solo.setData(USER_ROLE, dataset.id)
            self.dataset_table.setItem(row, DATASET_SOLO_COLUMN, solo)
            run_id = QtWidgets.QTableWidgetItem(dataset.run_id)
            run_id.setData(USER_ROLE, dataset.run_id)
            run_id.setToolTip(dataset.run_id)
            self.dataset_table.setItem(row, DATASET_RUN_ID_COLUMN, run_id)
            label = QtWidgets.QTableWidgetItem(dataset.label)
            label.setData(USER_ROLE, dataset.id)
            self.dataset_table.setItem(row, DATASET_LABEL_COLUMN, label)
            self.dataset_table.setItem(
                row,
                DATASET_TIMESTAMP_COLUMN,
                QtWidgets.QTableWidgetItem(dataset.measurement.acquisition_datetime),
            )
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
            color_value = dataset_display_color(dataset, row)
            color_item = _read_only_item(color_value)
            color_item.setBackground(QtGui.QColor(color_value))
            color_item.setForeground(QtGui.QColor("#ffffff" if QtGui.QColor(color_value).lightness() < 128 else "#000000"))
            self.dataset_table.setItem(row, DATASET_COLOR_COLUMN, color_item)
            self.dataset_table.setItem(
                row,
                DATASET_COLUMN_NAME_COLUMN,
                QtWidgets.QTableWidgetItem(dataset.measurement.column_name),
            )
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
        self.dataset_table.setColumnWidth(DATASET_SELECTED_COLUMN, 55)
        self.dataset_table.setColumnWidth(DATASET_VISIBLE_COLUMN, 55)
        self.dataset_table.setColumnWidth(DATASET_SOLO_COLUMN, 55)
        self.dataset_table.setColumnWidth(DATASET_RUN_ID_COLUMN, 160)
        self.dataset_table.setColumnWidth(DATASET_TIMESTAMP_COLUMN, 150)
        self.dataset_table.setColumnWidth(DATASET_COLUMN_NAME_COLUMN, 180)
        self.dataset_table.setColumnWidth(DATASET_SOURCE_COLUMN, 360)
        self.dataset_table.horizontalHeader().setStretchLastSection(True)
        self._updating_table = False
        if self.project.datasets:
            row = min(max(selected_row, 0), len(self.project.datasets) - 1)
            self.dataset_table.selectRow(row)
        self._sync_dataset_selection_checkboxes()
        self._update_dataset_order_buttons()

    def _selected_dataset(self) -> Optional[Dataset]:
        row = self.dataset_table.currentRow()
        if 0 <= row < len(self.project.datasets):
            return self.project.datasets[row]
        return None

    def _dataset_is_screen_visible(self, dataset: Dataset) -> bool:
        if self._solo_dataset_id:
            return dataset.id == self._solo_dataset_id
        return bool(dataset.visible)

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
        return self._group_dataset_rows(
            self._selected_dataset_rows(), self.dataset_table.currentRow()
        )

    def _group_dataset_ids_into_run(self, dataset_ids, target_dataset_id):
        selected_ids = set(dataset_ids)
        rows = [
            row for row, dataset in enumerate(self.project.datasets)
            if dataset.id in selected_ids
        ]
        current = next(
            (
                row for row, dataset in enumerate(self.project.datasets)
                if dataset.id == target_dataset_id
            ),
            -1,
        )
        return self._group_dataset_rows(rows, current)

    def _group_dataset_rows(self, rows, current):
        rows = sorted(set(rows))
        if len(rows) < 2 or current not in rows:
            QtWidgets.QMessageBox.information(
                self, APP_NAME, self.translator("select_run_group")
            )
            return False
        datasets = [self.project.datasets[row] for row in rows]
        if len({dataset.run_id for dataset in datasets}) < 2:
            return False
        target = self.project.run_for(self.project.datasets[current])
        run_ids = list(dict.fromkeys(dataset.run_id for dataset in datasets))
        answer = QtWidgets.QMessageBox.question(
            self,
            APP_NAME,
            self.translator(
                "confirm_run_group",
                count=len(datasets),
                run_id=target.id,
                run_ids="\n".join("- " + run_id for run_id in run_ids),
                label=target.label,
            ),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return False
        before = self._capture_analysis_state()
        self.project.group_datasets_into_run(datasets, target)
        self._push_undo_snapshot(
            before,
            self._history_label(
                "統合先Runへ統合", "Group into target Run"
            ),
        )
        self.project.dirty = True
        self._refresh_all(current)
        return True

    def ungroup_selected_runs(self):
        return self._ungroup_dataset_rows(self._selected_dataset_rows())

    def _ungroup_dataset_ids(self, dataset_ids):
        selected_ids = set(dataset_ids)
        rows = [
            row for row, dataset in enumerate(self.project.datasets)
            if dataset.id in selected_ids
        ]
        return self._ungroup_dataset_rows(rows)

    def _ungroup_dataset_rows(self, rows):
        rows = sorted(set(rows))
        if not rows:
            return False
        run_counts = {}
        for dataset in self.project.datasets:
            run_counts[dataset.run_id] = run_counts.get(dataset.run_id, 0) + 1
        datasets = [
            self.project.datasets[row]
            for row in rows
            if run_counts.get(self.project.datasets[row].run_id, 0) > 1
        ]
        if not datasets:
            return False
        answer = QtWidgets.QMessageBox.question(
            self,
            APP_NAME,
            self.translator("confirm_run_ungroup", count=len(datasets)),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return False
        before = self._capture_analysis_state()
        self.project.ungroup_datasets(datasets)
        self._push_undo_snapshot(
            before, self._history_label("Runを分離", "Ungroup Runs")
        )
        self.project.dirty = True
        self._refresh_all(rows[0])
        return True

    def _dataset_item_changed(self, item: QtWidgets.QTableWidgetItem):
        if self._updating_table or self._dataset_selection_sync_guard:
            return
        row, column = item.row(), item.column()
        if row < 0 or row >= len(self.project.datasets):
            return
        selected_rows = self._selected_dataset_rows()
        target_rows = list(self._dataset_checkbox_target_rows)
        self._dataset_checkbox_target_rows = []
        if not target_rows and row in selected_rows and len(selected_rows) > 1:
            target_rows = selected_rows
        if column == DATASET_SELECTED_COLUMN:
            if target_rows:
                self._dataset_selection_sync_guard = True
                try:
                    for target_row in target_rows:
                        target = self.dataset_table.item(
                            target_row, DATASET_SELECTED_COLUMN
                        )
                        if target is not None:
                            target.setCheckState(item.checkState())
                finally:
                    self._dataset_selection_sync_guard = False
            self._dataset_checkbox_press = False
            self._apply_dataset_checkbox_selection(row)
            return
        dataset = self.project.datasets[row]
        if column == DATASET_SOLO_COLUMN:
            checked = item.checkState() == CHECKED
            if checked:
                self._solo_dataset_id = dataset.id
            elif self._solo_dataset_id == dataset.id:
                self._solo_dataset_id = ""
            self._refresh_dataset_table(row)
            self._plot()
            return
        before = self._capture_analysis_state()
        label_changed = False
        shared_run_changed = False
        try:
            if column == DATASET_VISIBLE_COLUMN:
                visible = item.checkState() == CHECKED
                visibility_rows = target_rows or [row]
                self._updating_table = True
                try:
                    for target_row in visibility_rows:
                        self.project.datasets[target_row].visible = visible
                        target = self.dataset_table.item(
                            target_row, DATASET_VISIBLE_COLUMN
                        )
                        if target is not None:
                            target.setCheckState(
                                CHECKED if visible else UNCHECKED
                            )
                finally:
                    self._updating_table = False
            elif column == DATASET_RUN_ID_COLUMN:
                changed = self.project.rename_run(
                    self.project.run_for(dataset), item.text()
                )
                if not changed:
                    self._refresh_dataset_table(row)
                    return
                shared_run_changed = True
            elif column == DATASET_LABEL_COLUMN:
                label_changed = True
                old_label = dataset.label
                dataset.label = item.text().strip() or dataset.original_filename
                if not dataset.short_label or dataset.short_label == old_label:
                    dataset.short_label = dataset.label
            elif column == DATASET_TIMESTAMP_COLUMN:
                dataset.measurement.acquisition_datetime = item.text().strip()
                shared_run_changed = True
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
            elif column == DATASET_COLUMN_NAME_COLUMN:
                dataset.measurement.column_name = item.text().strip()
                shared_run_changed = True
        except ValueError as exc:
            message = str(exc)
            if column == DATASET_RUN_ID_COLUMN:
                message = self.translator(message)
            QtWidgets.QMessageBox.warning(self, self.translator("warning"), message)
            self._refresh_dataset_table(row)
            return
        self._push_undo_snapshot(
            before, self._history_label("クロマトグラム設定", "Chromatogram settings")
        )
        self.project.dirty = True
        if label_changed or shared_run_changed:
            self._refresh_dataset_table(row)
        self._refresh_peak_table()
        self._plot()
        self._update_title()

    def _sync_dataset_selection_checkboxes(self):
        selected_rows = set(self._selected_dataset_rows())
        self._dataset_selection_sync_guard = True
        try:
            for row in range(self.dataset_table.rowCount()):
                item = self.dataset_table.item(row, DATASET_SELECTED_COLUMN)
                if item is None:
                    continue
                state = CHECKED if row in selected_rows else UNCHECKED
                if item.checkState() != state:
                    item.setCheckState(state)
        finally:
            self._dataset_selection_sync_guard = False

    def _apply_dataset_checkbox_selection(self, preferred_row: int):
        checked_rows = [
            row
            for row in range(self.dataset_table.rowCount())
            if self.dataset_table.item(row, DATASET_SELECTED_COLUMN) is not None
            and self.dataset_table.item(row, DATASET_SELECTED_COLUMN).checkState()
            == CHECKED
        ]
        selection = self.dataset_table.selectionModel()
        if selection is None:
            return
        select = (
            QtCore.QItemSelectionModel.SelectionFlag.Select
            if QT_API == 6
            else QtCore.QItemSelectionModel.Select
        )
        rows = (
            QtCore.QItemSelectionModel.SelectionFlag.Rows
            if QT_API == 6
            else QtCore.QItemSelectionModel.Rows
        )
        no_update = (
            QtCore.QItemSelectionModel.SelectionFlag.NoUpdate
            if QT_API == 6
            else QtCore.QItemSelectionModel.NoUpdate
        )
        model = self.dataset_table.model()
        self._dataset_selection_sync_guard = True
        try:
            selection.clearSelection()
            for row in checked_rows:
                selection.select(
                    model.index(row, DATASET_SELECTED_COLUMN), select | rows
                )
            if checked_rows:
                current_row = (
                    preferred_row if preferred_row in checked_rows else checked_rows[0]
                )
                selection.setCurrentIndex(
                    model.index(current_row, DATASET_SELECTED_COLUMN), no_update
                )
            else:
                self.dataset_table.setCurrentCell(-1, -1)
        finally:
            self._dataset_selection_sync_guard = False
        self._dataset_selection_changed()

    def _finish_dataset_checkbox_press(self):
        if not self._dataset_checkbox_press:
            self._dataset_checkbox_target_rows = []
            return
        self._dataset_checkbox_press = False
        self._dataset_checkbox_target_rows = []
        self._dataset_selection_changed()

    def _dataset_selection_changed(self):
        if (
            self._updating_table
            or self._dataset_selection_sync_guard
            or self._dataset_checkbox_press
        ):
            return
        self._sync_dataset_selection_checkboxes()
        self._update_dataset_order_buttons()
        self._refresh_peak_table()
        self._plot()

    def _display_peaks(self, dataset=None):
        dataset = self._selected_dataset() if dataset is None else dataset
        return dataset.display_peaks() if dataset is not None else []

    def _peak_at_table_row(self, row: int, dataset=None):
        peaks = self._display_peaks(dataset)
        return peaks[row] if 0 <= row < len(peaks) else None

    def _selected_peak_rows(self):
        selection = self.peak_table.selectionModel()
        if selection is None:
            return []
        return sorted({index.row() for index in selection.selectedRows()})

    def _selected_peak_ids(self):
        return self._selected_peak_ids_from_table(self.peak_table)

    @staticmethod
    def _selected_peak_ids_from_table(table):
        ids = []
        selection = table.selectionModel()
        rows = (
            sorted({index.row() for index in selection.selectedRows()})
            if selection is not None else []
        )
        for row in rows:
            item = table.item(row, 0)
            if item is not None and item.data(USER_ROLE):
                ids.append(item.data(USER_ROLE))
        return ids

    def _select_peak_ids(self, peak_ids):
        self._select_peak_ids_in_table(self.peak_table, peak_ids)

    @staticmethod
    def _select_peak_ids_in_table(table, peak_ids):
        wanted = set(peak_ids or [])
        table.clearSelection()
        if not wanted:
            return
        matching_rows = []
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is not None and item.data(USER_ROLE) in wanted:
                matching_rows.append(row)
        if not matching_rows:
            return
        table.setCurrentCell(matching_rows[0], 0)
        for row in matching_rows:
            selection = QtWidgets.QTableWidgetSelectionRange(
                row, 0, row, table.columnCount() - 1
            )
            table.setRangeSelected(selection, True)

    def _refresh_peak_table(self, selected_peak_ids=None):
        dataset = self._selected_dataset()
        if selected_peak_ids is None:
            selected_peak_ids = self._selected_peak_ids()
        self._populate_peak_table(
            self.peak_table, dataset, selected_peak_ids
        )
        dialog = self._integration_list_dialog
        if dialog is not None:
            dialog.dataset_label.setText(
                (dataset.label or dataset.original_filename)
                if dataset is not None
                else self.translator.text("no_dataset")
            )
            self._populate_peak_table(
                dialog.table, dataset, selected_peak_ids
            )

    def _populate_peak_table(self, table, dataset, selected_peak_ids):
        peaks = self._display_peaks(dataset)
        parent_numbers = {
            peak.id: index
            for index, peak in enumerate(dataset.peaks, 1)
        } if dataset is not None else {}
        fitted_number = 0
        table.blockSignals(True)
        table.setRowCount(len(peaks))
        for row, peak in enumerate(peaks):
            if peak.is_fitted:
                fitted_number += 1
                row_label = "F%d" % fitted_number
                peak_type = "フィット" if self._application_language == "ja" else "Fit"
                if is_saturation_corrected(peak):
                    peak_type = (
                        "フィット（飽和補正）"
                        if self._application_language == "ja"
                        else "Fit (saturation corrected)"
                    )
                parent_label = (
                    "#%d" % parent_numbers[peak.parent_peak_id]
                    if peak.parent_peak_id in parent_numbers
                    else "?"
                )
                baseline_text = ""
                method_text = peak.fit_model.upper()
            else:
                row_label = str(parent_numbers.get(peak.id, row + 1))
                peak_type = "積分" if self._application_language == "ja" else "Integration"
                parent_label = ""
                baseline_text = peak.baseline_mode
                method_text = "auto" if peak.integration_source == "auto" else "manual"
            values = (
                row_label,
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
                baseline_text,
                method_text,
                peak.notes,
                peak_type,
                parent_label,
                peak.fit_model.upper() if peak.is_fitted else "",
                _format(peak.fit_r_squared) if peak.is_fitted else "",
                _format(peak.fit_rmse_uv) if peak.is_fitted else "",
                _format(peak.fit_aic) if peak.is_fitted else "",
            )
            for column, value in enumerate(values):
                item = (
                    QtWidgets.QTableWidgetItem(value)
                    if column == PEAK_NOTES_COLUMN
                    else _read_only_item(value)
                )
                if column == 0:
                    item.setData(USER_ROLE, peak.id)
                # Since Issue #218 a fitted row carries derived height, area and
                # width read off the model curve. They share their columns with
                # measured values, so they are tinted and explained rather than
                # left to look like something the detector recorded.
                estimated_columns = ESTIMATED_PEAK_COLUMNS
                if is_saturation_corrected(peak):
                    estimated_columns = (
                        estimated_columns | SATURATION_ESTIMATED_PEAK_COLUMNS
                    )
                if peak.is_fitted and column in estimated_columns:
                    item.setBackground(QtGui.QColor(ESTIMATED_VALUE_BACKGROUND))
                    item.setToolTip(self.translator("estimated_from_fit"))
                table.setItem(row, column, item)
        table.resizeColumnsToContents()
        table.setColumnWidth(PEAK_NOTES_COLUMN, 240)
        self._select_peak_ids_in_table(table, selected_peak_ids)
        table.blockSignals(False)

    def _peak_selection_changed(self):
        if self._updating_table or self._peak_selection_sync_guard:
            return
        dialog = self._integration_list_dialog
        if dialog is not None:
            self._peak_selection_sync_guard = True
            try:
                dialog.table.blockSignals(True)
                self._select_peak_ids_in_table(
                    dialog.table, self._selected_peak_ids()
                )
                dialog.table.blockSignals(False)
            finally:
                self._peak_selection_sync_guard = False
        if self.edit_peak_button.isChecked():
            row = self.peak_table.currentRow()
            peak = self._peak_at_table_row(row)
            if peak is not None and not peak.is_fitted:
                self._edit_range_peak_id = peak.id
            else:
                self._edit_range_peak_id = None
                self.edit_peak_button.setChecked(False)
                return
        if self._screen_preview is not None:
            try:
                self._screen_preview.set_peak_selection(
                    self._selected_peak_ids()
                )
            except Exception:
                self._stop_screen_preview(failed=True)
                self._plot()
        elif (self._is_lightweight_rendering()
                and self._peak_overlay_artists):
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
            fit_line = overlay.get("fit_line")
            if fit_line is not None:
                fit_line.set_color("#f59e0b" if selected else "#c026d3")
                fit_line.set_linewidth(
                    max(
                        1.8 if selected else 1.2,
                        self.project.method.line_width,
                    )
                )

    def _peak_item_double_clicked(self, item):
        if item.column() == PEAK_NOTES_COLUMN:
            self.peak_table.editItem(item)
            return
        peak = self._peak_at_table_row(item.row())
        if peak is not None and peak.is_fitted:
            self.fit_selected_peak()
            return
        self.edit_peak_properties()

    def _peak_item_changed(self, item):
        self._peak_notes_item_changed(self.peak_table, item)

    def _peak_notes_item_changed(self, table, item):
        if self._updating_table or item.column() != PEAK_NOTES_COLUMN:
            return
        dataset = self._selected_dataset()
        peaks = self._display_peaks(dataset)
        peak = peaks[item.row()] if 0 <= item.row() < len(peaks) else None
        if peak is None:
            return
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
        self._refresh_peak_table(
            self._selected_peak_ids_from_table(table)
        )

    def open_integration_list(self):
        dialog = self._integration_list_dialog
        if dialog is None:
            dialog = IntegrationListDialog(
                PEAK_COLUMN_COUNT,
                language=self._application_language,
                parent=self,
            )
            self._integration_list_dialog = dialog
            dialog.table.itemSelectionChanged.connect(
                self._detached_peak_selection_changed
            )
            dialog.table.itemChanged.connect(
                lambda item: self._peak_notes_item_changed(dialog.table, item)
            )
            dialog.table.itemDoubleClicked.connect(
                self._detached_peak_item_double_clicked
            )
            dialog.edit_button.clicked.connect(
                lambda: self._run_detached_peak_action(
                    self.edit_peak_properties
                )
            )
            dialog.fit_button.clicked.connect(
                lambda: self._run_detached_peak_action(
                    self.fit_selected_peak
                )
            )
            dialog.delete_button.clicked.connect(
                lambda: self._run_detached_peak_action(self.delete_peak)
            )
            self._set_peak_headers()
        self._refresh_peak_table(self._selected_peak_ids())
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _retranslate_integration_list(self):
        dialog = self._integration_list_dialog
        if dialog is None:
            return
        ja = self._application_language == "ja"
        dialog.setWindowTitle("積分リスト" if ja else "Integration list")
        dialog.edit_button.setText("範囲編集…" if ja else "Edit range…")
        dialog.fit_button.setText(
            "フィット／再計算" if ja else "Fit / recalculate"
        )
        dialog.delete_button.setText("削除" if ja else "Delete")

    def _detached_peak_selection_changed(self):
        if self._updating_table or self._peak_selection_sync_guard:
            return
        dialog = self._integration_list_dialog
        if dialog is None:
            return
        ids = self._selected_peak_ids_from_table(dialog.table)
        self._peak_selection_sync_guard = True
        try:
            self.peak_table.blockSignals(True)
            self._select_peak_ids_in_table(self.peak_table, ids)
            self.peak_table.blockSignals(False)
        finally:
            self._peak_selection_sync_guard = False
        self._peak_selection_changed()

    def _run_detached_peak_action(self, action):
        self._detached_peak_selection_changed()
        action()

    def _detached_peak_item_double_clicked(self, item):
        dialog = self._integration_list_dialog
        if dialog is None:
            return
        if item.column() == PEAK_NOTES_COLUMN:
            dialog.table.editItem(item)
            return
        self._detached_peak_selection_changed()
        peak = self._peak_at_table_row(item.row())
        if peak is not None and peak.is_fitted:
            self.fit_selected_peak()
        else:
            self.edit_peak_properties()

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
        self.show_grid_checkbox.blockSignals(True)
        self.show_grid_checkbox.setChecked(self.project.method.show_major_grid)
        self.show_grid_checkbox.blockSignals(False)
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
        self.project.method.show_major_grid = self.show_grid_checkbox.isChecked()
        self.project.method.legend_location = self.legend_combo.currentData()
        self.project.dirty = True
        if self._screen_preview is not None and new_unit == old_unit:
            self._sync_preview_gradient_axes()
            self._refresh_screen_preview()
        else:
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
        for axis in (self.axes, self.axes_right, self.axes_gradient, self.axes_gradient_secondary):
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
        return self._screen_view_state()

    def _apply_view_state(self, state):
        if not state:
            return
        self._view_state = state
        self._view_state_update_guard = True
        try:
            self.axes.set_xlim(*state.x)
            self.axes.set_ylim(*state.y1)
            if self.axes_right is not None and state.y2 is not None:
                self.axes_right.set_ylim(*state.y2)
            if self.axes_gradient is not None and state.gradient is not None:
                self.axes_gradient.set_ylim(*state.gradient)
            self._set_dynamic_x_ticks()
            self._update_overview_window()
        finally:
            self._view_state_update_guard = False
        self._request_canvas_draw(force=True, refresh_series=True)

    def _push_view_history(self):
        state = self._capture_view_state()
        if state is None:
            return
        self._view_history.record_before_change(state)

    def _navigate_view_history(self, command):
        current = self._capture_view_state()
        if current is None:
            return False
        state = self._view_history.navigate(command, current)
        if state is None:
            return False
        self._apply_view_state(state)
        self.toolbar.set_history_buttons()
        return True

    def _view_history_capabilities(self):
        current = self._capture_view_state()
        if current is None:
            return {"back": False, "forward": False}
        return self._view_history.capabilities(current)

    def _back_to_previous_view(self):
        self._navigate_view_history("back")

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
            spacing = safe_manual_x_tick_spacing(
                span,
                self.project.method.x_major_tick_min,
                self.project.method.x_minor_tick_min,
            )
        else:
            spacing = None
        if spacing is None:
            major_tick = self._nice_tick_step(span / 18.0)
            minor_tick = major_tick / 5.0
        else:
            major_tick, minor_tick = spacing
        self.axes.xaxis.set_major_locator(MultipleLocator(major_tick))
        self.axes.xaxis.set_minor_locator(MultipleLocator(minor_tick))
        self.axes.tick_params(axis="x", which="minor", length=3, labelbottom=False)

    def _connect_axes_callbacks(self):
        self._xlim_callback_id = self.axes.callbacks.connect(
            "xlim_changed", self._on_xlim_changed
        )
        self.axes.callbacks.connect("ylim_changed", self._on_ylim_changed)
        if self.axes_right is not None:
            self.axes_right.callbacks.connect(
                "ylim_changed", self._on_ylim_changed
            )

    def _on_xlim_changed(self, _axis):
        if self._tick_update_guard or self._view_state_update_guard:
            return
        self._tick_update_guard = True
        try:
            self._set_dynamic_x_ticks()
            self._update_overview_window()
            self._sync_view_state_from_axes()
        finally:
            self._tick_update_guard = False
        self._request_canvas_draw(throttled=True, refresh_series=True)
        self.toolbar.set_history_buttons()

    def _on_ylim_changed(self, _axis):
        if self._view_state_update_guard:
            return
        self._update_overview_window()
        self._sync_view_state_from_axes()
        self._request_canvas_draw(throttled=True, refresh_series=True)
        self.toolbar.set_history_buttons()

    def _update_overview_window(self):
        self._overview_window_state = compose_overview_state(
            enabled=self.axes_overview is not None,
            full_x=self._current_overview_x(),
            detail_x=tuple(self.axes.get_xlim()),
        )
        self._apply_matplotlib_overview_window(self._overview_window_state)

    def _current_overview_x(self):
        """Return the session-only overview range clamped to loaded data."""

        data_left, data_right = self._full_x_bounds()
        if self._overview_full_x is None:
            return data_left, data_right
        left, right = sorted(float(value) for value in self._overview_full_x)
        span = min(right - left, data_right - data_left)
        if span <= 0.0:
            return data_left, data_right
        left = min(max(left, data_left), data_right - span)
        return left, left + span

    def _overview_y_bounds(self, role="y1"):
        """Return one overview axis' data bounds inside its current X window."""

        scene = getattr(self, "_screen_scene", None)
        limits = (
            self._scene_y_limits(scene, role, self._current_overview_x())
            if scene is not None else None
        )
        if limits is not None:
            return tuple(float(value) for value in limits)
        if role == "y2":
            return None
        return tuple(float(value) for value in self.axes.get_ylim())

    def _screen_scroll_bounds(self, name):
        if name in ("overview_x", "detail_x"):
            return self._full_x_bounds()
        if name == "overview_y":
            return self._overview_y_bounds("y1")
        if name == "detail_y":
            scene = getattr(self, "_screen_scene", None)
            limits = (
                self._scene_y_limits(scene, "y1", self._full_x_bounds())
                if scene is not None else None
            )
            return limits if limits is not None else tuple(self.axes.get_ylim())
        return None

    def _set_detail_y_from_primary(self, limits):
        """Pan detail Y1/Y2 to the same relative data position."""

        state = self._screen_view_state()
        primary_bounds = self._screen_scroll_bounds("detail_y")
        low, high = sorted(float(value) for value in limits)
        primary_span = min(high - low, primary_bounds[1] - primary_bounds[0])
        primary_movable = max(
            (primary_bounds[1] - primary_bounds[0]) - primary_span, 0.0
        )
        position = (
            0.0 if primary_movable <= 0.0
            else min(max((low - primary_bounds[0]) / primary_movable, 0.0), 1.0)
        )
        y1_low = primary_bounds[0] + position * primary_movable
        y2 = state.y2
        if y2 is not None:
            scene = getattr(self, "_screen_scene", None)
            secondary_bounds = (
                self._scene_y_limits(scene, "y2", self._full_x_bounds())
                if scene is not None else None
            )
            if secondary_bounds is not None:
                secondary_span = min(
                    abs(y2[1] - y2[0]),
                    secondary_bounds[1] - secondary_bounds[0],
                )
                secondary_movable = max(
                    (secondary_bounds[1] - secondary_bounds[0])
                    - secondary_span,
                    0.0,
                )
                y2_low = secondary_bounds[0] + position * secondary_movable
                y2 = (y2_low, y2_low + secondary_span)
        self._apply_view_state(replace(
            state,
            y1=(y1_low, y1_low + primary_span),
            y2=y2,
        ))

    def _overview_y_fraction(self):
        if self._overview_full_y is None:
            return 0.0, 1.0
        low, high = sorted(float(value) for value in self._overview_full_y)
        span = min(high - low, 1.0)
        if span <= 0.0:
            return 0.0, 1.0
        low = min(max(low, 0.0), 1.0 - span)
        return low, low + span

    def _current_overview_y(self, role="y1"):
        bounds = self._overview_y_bounds(role)
        if bounds is None:
            return None
        data_low, data_high = bounds
        low_fraction, high_fraction = self._overview_y_fraction()
        data_span = data_high - data_low
        return (
            data_low + data_span * low_fraction,
            data_low + data_span * high_fraction,
        )

    def _set_overview_x(self, limits):
        """Change only the session-only overview X window."""

        data_left, data_right = self._full_x_bounds()
        left, right = sorted(float(value) for value in limits)
        minimum_span = max((data_right - data_left) * 1e-6, 1e-9)
        span = min(max(right - left, minimum_span), data_right - data_left)
        left = min(max(left, data_left), data_right - span)
        self._overview_full_x = (left, left + span)
        overview = compose_overview_state(
            self.axes_overview is not None,
            self._overview_full_x,
            tuple(self.axes.get_xlim()),
        )
        self._overview_window_state = overview
        self._apply_matplotlib_overview_window(overview)
        if self._screen_preview is not None:
            self._screen_preview.consumer.apply_view_state(
                self._screen_view_state(), overview
            )
            self._apply_overview_y_ranges()
        self._request_canvas_draw(throttled=True, refresh_series=True)

    def _set_overview_y(self, limits, role="y1"):
        """Set the shared overview Y window from one axis' absolute limits."""

        bounds = self._overview_y_bounds(role)
        if bounds is None:
            return
        data_low, data_high = bounds
        low, high = sorted(float(value) for value in limits)
        data_span = data_high - data_low
        self._set_overview_y_fraction((
            (low - data_low) / data_span,
            (high - data_low) / data_span,
        ))

    def _set_overview_y_fraction(self, limits):
        """Change both overview axes by the same session-only range fraction."""

        low, high = sorted(float(value) for value in limits)
        minimum_span = 1e-6
        span = min(max(high - low, minimum_span), 1.0)
        low = min(max(low, 0.0), 1.0 - span)
        self._overview_full_y = (low, low + span)
        self._apply_overview_y_ranges()
        self._request_canvas_draw(throttled=True, refresh_series=True)

    def _apply_overview_y_ranges(self):
        primary = self._current_overview_y("y1")
        secondary = self._current_overview_y("y2")
        if self.axes_overview is not None:
            self.axes_overview.set_ylim(*primary)
        if self.axes_overview_right is not None and secondary is not None:
            self.axes_overview_right.set_ylim(*secondary)
        if self._screen_preview is not None:
            self._screen_preview.consumer.overview.setYRange(
                *primary, padding=0.0
            )
            if secondary is not None:
                self._screen_preview.consumer.overview_secondary.setYRange(
                    *secondary, padding=0.0
                )
            self._screen_preview.consumer.sync_navigation_scrollbars()

    def _zoom_overview(self, factor, center=None):
        limits = self._current_overview_x()
        if center is None:
            center = sum(limits) / 2.0
        self._set_overview_x(self._scaled_limits(limits, factor, center))

    def _zoom_overview_y(self, factor, center=None):
        limits = self._overview_y_fraction()
        data_low, data_high = self._overview_y_bounds("y1")
        data_span = data_high - data_low
        if center is None:
            # Default the +/- buttons to the y=0 anchor (small peaks near the
            # baseline are the common case). The wheel keeps the cursor as
            # its anchor via the explicit `center` argument below. When 0 is
            # outside the data range, round to the nearest edge.
            if data_span > 0.0:
                zero_fraction = (0.0 - data_low) / data_span
            else:
                zero_fraction = 0.5
            center = min(max(zero_fraction, 0.0), 1.0)
        else:
            center = (float(center) - data_low) / data_span
        self._set_overview_y_fraction(
            self._scaled_limits(limits, factor, center)
        )

    def _apply_matplotlib_overview_window(self, state):
        if not state.enabled or self.axes_overview is None:
            self._overview_view_patch = None
            self._overview_secondary_view_patch = None
            return
        self.axes_overview.set_xlim(*state.full_x)
        self.axes_overview.set_ylim(*self._current_overview_y("y1"))
        secondary_y = self._current_overview_y("y2")
        if self.axes_overview_right is not None and secondary_y is not None:
            self.axes_overview_right.set_ylim(*secondary_y)
        if self._overview_view_patch is not None:
            try:
                left, right = state.detail_x
                bottom, top = self.axes.get_ylim()
                self._overview_view_patch.set_bounds(
                    left, min(bottom, top), right - left, abs(top - bottom)
                )
            except (ValueError, AttributeError, RuntimeError):
                self._overview_view_patch = None
        if self._overview_view_patch is None:
            left, right = state.detail_x
            bottom, top = self.axes.get_ylim()
            self._overview_view_patch = Rectangle(
                (left, min(bottom, top)),
                right - left,
                abs(top - bottom),
                facecolor="#2563eb",
                edgecolor="#1d4ed8",
                linewidth=0.8,
                alpha=0.14,
                zorder=10,
            )
            self.axes_overview.add_patch(self._overview_view_patch)
        if self.axes_overview_right is None or self.axes_right is None:
            self._overview_secondary_view_patch = None
            return
        left, right = state.detail_x
        bottom, top = self.axes_right.get_ylim()
        if self._overview_secondary_view_patch is None:
            self._overview_secondary_view_patch = Rectangle(
                (left, min(bottom, top)), right - left, abs(top - bottom),
                facecolor="#eab308", edgecolor="#ca8a04",
                linewidth=0.8, alpha=0.12, zorder=11,
            )
            self.axes_overview_right.add_patch(
                self._overview_secondary_view_patch
            )
        else:
            self._overview_secondary_view_patch.set_bounds(
                left, min(bottom, top), right - left, abs(top - bottom)
            )

    def _annotation_axis(self, annotation: TextAnnotation):
        if annotation.y_axis == 2 and self.axes_right is not None:
            return self.axes_right
        return self.axes

    def _scene_axis(self, axis_id):
        if axis_id == "y2" and self.axes_right is not None:
            return self.axes_right
        return self.axes

    def _draw_text_annotations(self):
        self._annotation_artists = {}
        for annotation in self._screen_scene.text_annotations:
            axis = self._scene_axis(annotation.axis_id)
            artist = axis.text(
                annotation.x_value,
                annotation.y_value,
                annotation.text,
                ha="left",
                va="bottom",
                fontfamily=_resolved_plot_font(annotation.font_family),
                fontsize=annotation.font_size,
                color=annotation.color,
                bbox={
                    "boxstyle": "round,pad=0.28",
                    "facecolor": annotation.background_color,
                    "edgecolor": annotation.border_color,
                    "linewidth": 0.8,
                    "alpha": 0.9,
                },
                zorder=30,
                picker=True,
            )
            self._annotation_artists[annotation.annotation_id] = artist

    def _marker_axis(self, marker: VerticalMarker):
        if marker.y_axis == 2 and self.axes_right is not None:
            return self.axes_right
        return self.axes

    def _draw_vertical_markers(self):
        self._vertical_marker_artists = {}
        self._vertical_marker_label_artists = {}
        for marker in self._screen_scene.vertical_markers:
            axis = self._scene_axis(marker.axis_id)
            artist = axis.axvline(
                marker.x_value,
                color=marker.color,
                linewidth=marker.line_width,
                linestyle="-",
                alpha=marker.alpha,
                zorder=25,
            )
            self._vertical_marker_artists[marker.marker_id] = artist
            self._vertical_marker_label_artists[marker.marker_id] = axis.text(
                marker.x_value,
                0.98,
                marker.label_text,
                transform=axis.get_xaxis_transform(),
                ha="right",
                va="top",
                rotation=90,
                color=marker.color,
                fontsize=max(7.0, self.project.method.tick_label_font_size),
                zorder=26,
            )

    def _draw_fraction_regions(self):
        for region in self._screen_scene.fraction_regions:
            self.axes.axvspan(
                region.start_x,
                region.end_x,
                color=region.fill_color,
                alpha=region.fill_alpha,
                zorder=2,
            )
            for value in region.boundary_values:
                self.axes.axvline(
                    value,
                    color=region.line_color,
                    linewidth=region.line_width,
                    linestyle="--",
                    alpha=region.line_alpha,
                    zorder=3,
                )
            self.axes.axvline(
                region.end_x,
                color=region.line_color,
                linewidth=region.line_width,
                linestyle="--",
                alpha=region.line_alpha,
            )

    @staticmethod
    def _scene_y_limits(scene, axis_id: str, x_range=None):
        values = []
        x_bounds = None
        if x_range is not None:
            x_bounds = tuple(sorted((float(x_range[0]), float(x_range[1]))))
        for trace in scene.traces:
            if trace.axis_id != axis_id:
                continue
            y_values = np.asarray(trace.y_values, dtype=float)
            mask = np.isfinite(y_values)
            if x_bounds is not None:
                x_values = np.asarray(trace.x_values, dtype=float)
                mask &= np.isfinite(x_values)
                mask &= x_values >= x_bounds[0]
                mask &= x_values <= x_bounds[1]
            finite_y = y_values[mask]
            if finite_y.size:
                values.append(finite_y)
        if not values:
            return None if x_bounds is not None else (0.0, 1.0)
        minimum = min(float(np.min(item)) for item in values)
        maximum = max(float(np.max(item)) for item in values)
        span = maximum - minimum
        padding = span * 0.05 if span > 0.0 else max(abs(minimum) * 0.05, 1.0)
        return (minimum - padding, maximum + padding)

    def _sync_preview_gradient_axes(self):
        """Mirror native gradient visibility without rebuilding the figure."""

        scene = getattr(self, "_screen_scene", None)
        visible = bool(
            self.project.method.show_gradient_b
            and scene is not None
            and scene.gradient is not None
        )
        if not visible:
            if self.axes_gradient is not None:
                self._preview_gradient_limits = tuple(
                    self.axes_gradient.get_ylim()
                )
            for attribute in (
                "axes_gradient_secondary",
                "axes_gradient",
            ):
                axis = getattr(self, attribute, None)
                if axis is not None:
                    axis.remove()
                    setattr(self, attribute, None)
            return
        if self.axes_gradient is None:
            self.axes_gradient = self.axes.twinx()
            if self.axes_right is not None and not self._split_y_axes:
                self.axes_gradient.spines["right"].set_position(
                    ("outward", 62)
                )
        if self._split_y_axes and self.axes_gradient_secondary is None:
            self.axes_gradient_secondary = self.axes_right.twinx()
            self.axes_gradient_secondary.sharey(self.axes_gradient)
        for gradient_axis in (
            self.axes_gradient,
            self.axes_gradient_secondary,
        ):
            if gradient_axis is not None:
                gradient_axis.set_ylabel(scene.gradient.axis_label)
                gradient_axis.set_ylim(*self._preview_gradient_limits)

    def _prepare_matplotlib_screen_skeleton(self, scene, view_state):
        """Keep only axes/view state while the native screen owns rendering."""

        x_label, y_label = self._axis_labels()
        x_axis = self.axes_right if self._split_y_axes else self.axes
        x_axis.set_xlabel(self.project.method.x_axis_label.strip() or x_label)
        self.axes.set_ylabel(
            self.project.method.y_axis_1_label.strip()
            or "%s — Y axis 1" % y_label
        )
        if self.axes_right is not None:
            self.axes_right.set_ylabel(
                self.project.method.y_axis_2_label.strip()
                or "%s — Y axis 2" % y_label
            )
        if scene.gradient is not None:
            for gradient_axis in (
                self.axes_gradient,
                self.axes_gradient_secondary,
            ):
                if gradient_axis is not None:
                    gradient_axis.set_ylabel(scene.gradient.axis_label)
                    gradient_axis.set_ylim(0.0, 100.0)

        if self.project.method.show_major_grid:
            self.axes.grid(
                True,
                which="major",
                # Vertical lines only, matching the native screen renderer.
                axis="x",
                color="#d1d5db",
                linewidth=0.6,
                alpha=0.75,
            )
        else:
            self.axes.grid(False, which="major")
        if self.axes_overview is not None:
            if self.project.method.show_major_grid:
                self.axes_overview.grid(
                    True,
                    which="major",
                    axis="x",
                    color="#d1d5db",
                    linewidth=0.5,
                    alpha=0.6,
                )
            else:
                self.axes_overview.grid(False, which="major")

        has_times = bool(scene.time_candidates)
        if not has_times:
            has_times = any(dataset.time_min.size for dataset in self.project.datasets)
        if has_times:
            full_bounds = self._full_x_bounds()
            self.axes.set_xlim(*full_bounds)
            self.axes.margins(x=0)
            if self._split_y_axes and self.axes_right is not None:
                self.axes_right.margins(x=0)
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

        if view_state is None:
            self.axes.set_ylim(*self._scene_y_limits(scene, "y1"))
            if self.axes_right is not None:
                self.axes_right.set_ylim(*self._scene_y_limits(scene, "y2"))
        else:
            self.axes.set_xlim(*view_state.x)
            self.axes.set_ylim(*view_state.y1)
            if self.axes_right is not None and view_state.y2 is not None:
                self.axes_right.set_ylim(*view_state.y2)
            if self.axes_gradient is not None and view_state.gradient is not None:
                self.axes_gradient.set_ylim(*view_state.gradient)

        self._set_dynamic_x_ticks()
        self._apply_plot_text_styles()
        self._connect_axes_callbacks()
        self._overview_window_state = compose_overview_state(
            enabled=self.axes_overview is not None,
            full_x=self._current_overview_x(),
            detail_x=tuple(self.axes.get_xlim()),
        )
        self._view_initialized = has_times
        self._view_state = (
            self._matplotlib_view_state() if self._view_initialized else None
        )
        if self._view_initialized:
            self._view_history.ensure_home(self._screen_view_state())
        self._matplotlib_screen_complete = False
        self._request_canvas_draw()

    def _plot(self, preserve_view: bool = True):
        if not hasattr(self, "axes"):
            return
        if getattr(self, "_screen_preview", None) is not None:
            self._screen_preview.cancel_move_drag()
            self._screen_preview.cancel_annotation_drag()
            self._screen_preview.cancel_zoom_drag()
        view_state = self._capture_view_state() if preserve_view else None
        self._overview_split_drag = None
        self._cancel_overview_zoom_drag()
        self._clear_span_selector()
        self._interaction_cursor = None
        self._annotation_artists = {}
        self._annotation_drag = None
        self._vertical_marker_artists = {}
        self._vertical_marker_label_artists = {}
        self._vertical_marker_drag = None
        self._overview_view_patch = None
        self._overview_secondary_view_patch = None
        self.figure.clear()
        self._split_y_axes = self.project.method.view_mode == "split_y_axes"
        split_axis = None
        if self.project.method.view_mode == "overview_detail":
            grid = self.figure.add_gridspec(
                2,
                1,
                height_ratios=(
                    self._overview_split_ratio,
                    1.0 - self._overview_split_ratio,
                ),
                hspace=0.08,
            )
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
            for spine in self.axes_overview.spines.values():
                spine.set_visible(True)
                spine.set_color("#6b7280")
                spine.set_linewidth(0.8)
        elif self._split_y_axes:
            self.axes_overview = None
            grid = self.figure.add_gridspec(2, 1, hspace=0.08)
            self.axes = self.figure.add_subplot(grid[0, 0])
            split_axis = self.figure.add_subplot(
                grid[1, 0], sharex=self.axes
            )
            self.axes.tick_params(axis="x", labelbottom=False)
        else:
            self.axes_overview = None
            self.axes = self.figure.add_subplot(111)
        self.axes_right = split_axis
        self.axes_gradient = None
        self.axes_gradient_secondary = None
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
        selected_dataset_ids = {
            self.project.datasets[row].id
            for row in self._selected_dataset_rows()
        }
        if not selected_dataset_ids and selected is not None:
            selected_dataset_ids.add(selected.id)
        selected_peak_ids = set()
        if selected is not None:
            display_peaks = selected.display_peaks()
            for row in self._selected_peak_rows():
                if 0 <= row < len(display_peaks):
                    selected_peak_ids.add(display_peaks[row].id)
        marker_ids = {marker.id for marker in self.project.vertical_markers}
        self._selected_vertical_marker_ids.intersection_update(marker_ids)
        if self._selected_vertical_marker_id in marker_ids:
            self._selected_vertical_marker_ids.add(
                self._selected_vertical_marker_id
            )
        elif self._selected_vertical_marker_id:
            self._selected_vertical_marker_id = ""
        if (
            not self._selected_vertical_marker_id
            and self._selected_vertical_marker_ids
        ):
            self._selected_vertical_marker_id = next(
                marker.id
                for marker in self.project.vertical_markers
                if marker.id in self._selected_vertical_marker_ids
            )
        visible = [
            dataset for dataset in self.project.datasets
            if self._dataset_is_screen_visible(dataset)
        ]
        base_scene = compose_base_screen_scene(
            self.project,
            selected_dataset_id=selected.id if selected is not None else "",
            selected_dataset_ids=selected_dataset_ids,
            selected_peak_ids=selected_peak_ids,
            selected_vertical_marker_id=self._selected_vertical_marker_id,
            selected_vertical_marker_ids=self._selected_vertical_marker_ids,
            color_resolver=dataset_display_color,
            solo_dataset_id=self._solo_dataset_id,
            include_hidden_display_items=(
                self._screen_preview is not None
                and not self._force_matplotlib_screen_plot
            ),
        )
        self._screen_scene = base_scene
        trace_by_id = {trace.dataset_id: trace for trace in base_scene.traces}
        overlays_by_dataset = {}
        for overlay_spec in base_scene.peak_overlays:
            overlays_by_dataset.setdefault(overlay_spec.dataset_id, []).append(
                overlay_spec
            )
        if not self._split_y_axes and any(
            dataset.y_axis == 2 for dataset in visible
        ):
            self.axes_right = self.axes.twinx()
            if self.axes_overview is not None:
                self.axes_overview_right = self.axes_overview.twinx()
                self.axes_overview_right.set_navigate(False)
        if (
            base_scene.gradient is not None
            and base_scene.gradient.visible
            and selected is not None
        ):
            self.axes_gradient = self.axes.twinx()
            if self._split_y_axes:
                self.axes_gradient_secondary = self.axes_right.twinx()
                # Both panels show the same selected gradient and B% scale.
                self.axes_gradient_secondary.sharey(self.axes_gradient)
            if self.axes_right is not None and not self._split_y_axes:
                self.axes_gradient.spines["right"].set_position(("outward", 62))

        if (
            self._screen_preview is not None
            and not self._force_matplotlib_screen_plot
        ):
            self._prepare_matplotlib_screen_skeleton(base_scene, view_state)
            return

        plotted = 0
        for dataset in self.project.datasets:
            trace = trace_by_id.get(dataset.id)
            if trace is None:
                continue
            color = trace.color
            label = trace.label
            target_axes = self.axes_right if trace.axis_id == "y2" and self.axes_right is not None else self.axes
            full_x = trace.x_values
            full_y = trace.y_values
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
                linewidth=trace.line_width,
                linestyle=matplotlib_line_style(trace.line_style),
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
                    linestyle=matplotlib_line_style(trace.line_style),
                    alpha=0.9,
                    antialiased=not self._is_lightweight_rendering(),
                )[0]
                self._overview_dataset_lines[dataset.id] = overview_line
            plotted += 1
            for overlay_spec in overlays_by_dataset.get(dataset.id, []):
                is_selected_peak = overlay_spec.is_selected
                peak_color = overlay_spec.color
                overlay = {
                    "base_color": color,
                    "patch": None,
                    "boundary_lines": [],
                    "retention_line": None,
                    "baseline_line": None,
                    "fit_line": None,
                }
                if overlay_spec.show_integration_area:
                    overlay["patch"] = target_axes.axvspan(
                        overlay_spec.start_x,
                        overlay_spec.end_x,
                        color=peak_color,
                        alpha=0.24 if is_selected_peak else 0.08,
                    )
                    for boundary in (
                        overlay_spec.start_x,
                        overlay_spec.end_x,
                    ):
                        boundary_line = target_axes.axvline(
                            boundary,
                            color=INTEGRATION_BOUNDARY_COLOR,
                            alpha=0.9 if is_selected_peak else 0.55,
                            linewidth=1.15 if is_selected_peak else 0.8,
                            linestyle="--",
                        )
                        overlay["boundary_lines"].append(boundary_line)
                    if overlay_spec.retention_x is not None:
                        overlay["retention_line"] = target_axes.axvline(
                            overlay_spec.retention_x,
                            color=peak_color,
                            alpha=0.75 if is_selected_peak else 0.35,
                            linewidth=1.1 if is_selected_peak else 0.8,
                        )
                    if overlay_spec.baseline_x is not None:
                        overlay["baseline_line"] = target_axes.plot(
                            overlay_spec.baseline_x,
                            overlay_spec.baseline_y,
                            color=peak_color,
                            linestyle="--",
                            linewidth=1.4 if is_selected_peak else 0.9,
                            alpha=0.95 if is_selected_peak else 0.55,
                            antialiased=not self._is_lightweight_rendering(),
                        )[0]
                if overlay_spec.fit_x is not None:
                    overlay["fit_line"] = target_axes.plot(
                        overlay_spec.fit_x,
                        overlay_spec.fit_y,
                        color=(
                            "#f59e0b" if is_selected_peak else "#c026d3"
                        ),
                        linewidth=max(
                            1.8 if is_selected_peak else 1.2,
                            self.project.method.line_width,
                        ),
                        linestyle=":",
                        alpha=0.95,
                        zorder=18,
                        label=overlay_spec.fit_label or None,
                    )[0]
                if overlay_spec.label_x is not None:
                    target_axes.annotate(
                        overlay_spec.label_text,
                        xy=(overlay_spec.label_x, overlay_spec.label_y),
                        xytext=(0, 5),
                        textcoords="offset points",
                        ha="center",
                        va="bottom",
                        rotation=90,
                        fontfamily=(
                            _resolved_plot_font(overlay_spec.label_font_family)
                        ),
                        fontsize=overlay_spec.label_font_size,
                        color=overlay_spec.label_color,
                    )
                self._peak_overlay_artists[overlay_spec.peak_id] = overlay

        for gradient_axis in (self.axes_gradient, self.axes_gradient_secondary):
            if gradient_axis is None or base_scene.gradient is None:
                continue
            gradient_spec = base_scene.gradient
            gradient_screen_x, gradient_screen_y = self._screen_data(
                gradient_spec.x_values,
                gradient_spec.y_values,
                gradient_axis,
                overview=True,
            )
            gradient_axis.plot(
                gradient_screen_x,
                gradient_screen_y,
                color="#111827",
                linestyle=":",
                linewidth=1.3,
                label=gradient_spec.label,
                antialiased=not self._is_lightweight_rendering(),
            )
            gradient_axis.set_ylim(0.0, 100.0)
            gradient_axis.set_ylabel(
                gradient_spec.axis_label
            )

        self._draw_vertical_markers()
        self._draw_fraction_regions()
        self._draw_text_annotations()

        x_label, y_label = self._axis_labels()
        axis_1_label = "Y axis 1"
        axis_2_label = "Y axis 2"
        x_axis = self.axes_right if self._split_y_axes else self.axes
        x_axis.set_xlabel(self.project.method.x_axis_label.strip() or x_label)
        self.axes.set_ylabel(
            self.project.method.y_axis_1_label.strip() or "%s — %s" % (y_label, axis_1_label)
        )
        if self.axes_right is not None:
            self.axes_right.set_ylabel(
                self.project.method.y_axis_2_label.strip() or "%s — %s" % (y_label, axis_2_label)
            )
        if self.project.method.show_major_grid:
            self.axes.grid(
                True,
                which="major",
                # Vertical lines only, matching the native screen renderer.
                axis="x",
                color="#d1d5db",
                linewidth=0.6,
                alpha=0.75,
            )
        else:
            self.axes.grid(False, which="major")
        if self.axes_overview is not None:
            if self.project.method.show_major_grid:
                self.axes_overview.grid(
                    True,
                    which="major",
                    axis="x",
                    color="#d1d5db",
                    linewidth=0.5,
                    alpha=0.6,
                )
            else:
                self.axes_overview.grid(False, which="major")

        times = list(base_scene.time_candidates)
        if not times:
            times = [float(dataset.time_min[-1] + dataset.x_shift_min) for dataset in self.project.datasets if dataset.time_min.size]
        if times:
            full_bounds = self._full_x_bounds()
            self.axes.set_xlim(*full_bounds)
            self.axes.margins(x=0)
            if self._split_y_axes and self.axes_right is not None:
                self.axes_right.margins(x=0)
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
            self.axes.set_xlim(*view_state.x)
            self.axes.set_ylim(*view_state.y1)
            if self.axes_right is not None and view_state.y2 is not None:
                self.axes_right.set_ylim(*view_state.y2)
            if self.axes_gradient is not None and view_state.gradient is not None:
                self.axes_gradient.set_ylim(*view_state.gradient)
        self._set_dynamic_x_ticks()
        self._apply_plot_text_styles()
        self._connect_axes_callbacks()
        self._update_overview_window()

        if plotted:
            handles = [
                self._dataset_lines[dataset.id]
                for dataset in self.project.datasets
                if self._dataset_is_screen_visible(dataset)
                and dataset.id in self._dataset_lines
            ]
            labels = [
                self.project.legend_label_for(dataset)
                for dataset in self.project.datasets
                if self._dataset_is_screen_visible(dataset)
                and dataset.id in self._dataset_lines
            ]
            for overlay in base_scene.peak_overlays:
                artist = self._peak_overlay_artists.get(overlay.peak_id, {}).get(
                    "fit_line"
                )
                if artist is not None and overlay.fit_label:
                    handles.append(artist)
                    labels.append(overlay.fit_label)
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
        self._view_state = (
            self._matplotlib_view_state() if self._view_initialized else None
        )
        if self._view_initialized:
            self._view_history.ensure_home(self._screen_view_state())
        if self._is_lightweight_rendering():
            self._refresh_screen_series_for_view()
        self._request_canvas_draw()
        if self.integrate_button.isChecked():
            self._install_span_selector("integrate")
        elif self.edit_peak_button.isChecked():
            self._install_span_selector("edit")
        elif self._mouse_mode == "select":
            self._install_span_selector("select")
            if self._span_selector is not None and self._selected_time_range:
                self._span_selector.extents = self._selected_time_range
                self._span_selector.set_visible(True)
        if (
            self.integrate_button.isChecked()
            or self.edit_peak_button.isChecked()
            or self.split_peak_button.isChecked()
            or self.pointer_button.isChecked()
            or self._mouse_mode == "select"
        ):
            self._ensure_interaction_cursor()
        self._matplotlib_screen_complete = True

    def _clear_span_selector(self):
        if self._span_selector is not None:
            self._span_selector.set_active(False)
            self._span_selector.disconnect_events()
            self._span_selector.set_visible(False)
            self._span_selector = None
            self._span_selector_mode = None

    def _install_span_selector(self, mode: str = "integrate"):
        self._clear_span_selector()
        if mode in ("integrate", "edit", "select") and self._screen_preview is not None:
            return
        selected = self._selected_dataset()
        selector_axis = (
            self.axes_right
            if selected is not None
            and selected.y_axis == 2
            and self.axes_right is not None
            else self.axes
        )
        if mode == "edit":
            callback = self._on_edit_span_selected
            color = "#f59e0b"
        elif mode == "select":
            callback = self._on_selection_span_selected
            color = "#7c3aed"
        else:
            callback = self._on_span_selected
            color = "#2563eb"
        self._span_selector = SpanSelector(
            selector_axis,
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
        if self._screen_preview is not None:
            try:
                self._screen_preview.cancel_span_drag()
                self._screen_preview.consumer.set_pointer_cursor()
            except Exception:
                self._stop_screen_preview(failed=True)
        if self._interaction_cursor is not None:
            self._interaction_cursor.set_visible(False)
            self._request_canvas_draw(throttled=True)

    def _deactivate_toolbar_navigation(self):
        mode = str(getattr(self.toolbar, "mode", "")).lower()
        if "pan" in mode:
            self.toolbar.pan()
        elif "zoom" in mode:
            self.toolbar.zoom()

    def _ensure_normal_mode_navigation(self):
        """Make the independent normal mode activate Pan, never Zoom."""

        actions = getattr(self.toolbar, "_actions", {}) or {}
        pan_action = actions.get("pan")
        zoom_action = actions.get("zoom")
        pan_active = pan_action is not None and pan_action.isChecked()
        zoom_active = zoom_action is not None and zoom_action.isChecked()
        if zoom_active:
            self.toolbar.zoom()
            pan_active = False
        if not pan_active and pan_action is not None:
            self.toolbar.pan()

    @property
    def selected_time_range(self):
        return self._selected_time_range

    def _clear_selected_time_range(self):
        """Clear the range state and every backend's corresponding band."""

        changed = self._selected_time_range is not None
        self._selected_time_range = None
        if self._screen_preview is not None:
            self._screen_preview.consumer.set_span_selection()
        if self._span_selector is not None and self._span_selector_mode == "select":
            clear = getattr(self._span_selector, "clear", None)
            if clear is not None:
                clear()
        return changed

    def _set_mouse_mode_display(self, mode: str):
        normalized = mode if mode in MOUSE_MODE_IDS else "normal"
        if self._mouse_mode == "select" and normalized != "select":
            self._clear_selected_time_range()
        self._mouse_mode = normalized
        index = self.mouse_mode_combo.findData(normalized)
        self.mouse_mode_combo.blockSignals(True)
        self.mouse_mode_combo.setCurrentIndex(max(0, index))
        self.mouse_mode_combo.blockSignals(False)

    def _mouse_tool_toggled(self, mode: str, enabled: bool):
        if enabled:
            self._set_mouse_mode_display(mode)
        elif self._mouse_mode == mode:
            self._set_mouse_mode_display("normal")
            # _set_mouse_mode_display updates the combo with blocked signals
            # (it only reflects a mode decided elsewhere), so it does not run
            # _mouse_mode_changed's own "normal" handling. Ending up at
            # "normal" this way -- leaving edit_peak, for example -- must
            # still leave pan or zoom active, not neither.
            self._ensure_normal_mode_navigation()

    def _mouse_mode_changed(self, *_args):
        mode = str(self.mouse_mode_combo.currentData() or "normal")
        if mode not in MOUSE_MODE_IDS:
            mode = "normal"
        if self._mouse_mode == "select" and mode != "select":
            self._clear_selected_time_range()
        self._mouse_mode = mode
        if self.select_toolbar_action.isChecked() != (mode == "select"):
            self.select_toolbar_action.setChecked(mode == "select")
        controls = {
            "pointer": self.pointer_action,
            "integrate": self.integrate_button,
            "edit_peak": self.edit_peak_button,
            "split_peak": self.split_peak_button,
            "move_trace": self.move_trace_button,
            "annotation": self.annotation_action,
        }
        selected_control = controls.get(mode)
        if selected_control is not None:
            selected_control.setChecked(True)
            return
        for control in controls.values():
            if control.isChecked():
                control.setChecked(False)
        self._clear_span_selector()
        self._hide_interaction_cursor()
        if mode != "zoom":
            self._cancel_overview_zoom_drag()
        if mode == "normal":
            self.statusBar().clearMessage()
            self._ensure_normal_mode_navigation()
            return
        self._deactivate_toolbar_navigation()
        if mode == "zoom":
            zoom_action = self.toolbar._actions.get("zoom")
            if zoom_action is not None and not zoom_action.isChecked():
                self.toolbar.zoom()
            return
        if mode == "select":
            if self._selected_dataset() is None:
                if self.select_toolbar_action.isChecked():
                    self.select_toolbar_action.setChecked(False)
                QtWidgets.QMessageBox.information(
                    self, APP_NAME, self.translator("no_dataset")
                )
                self._set_mouse_mode_display("normal")
                return
            self.statusBar().showMessage(self.translator("selection_hint"))
            self._install_span_selector("select")
            self._ensure_interaction_cursor()

    def _toggle_integration(self, enabled: bool):
        if enabled:
            if self._selected_dataset() is None:
                QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("no_dataset"))
                self.integrate_button.setChecked(False)
                return
            self._mouse_tool_toggled("integrate", True)
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
            self._mouse_tool_toggled("integrate", False)
            self.statusBar().clearMessage()
            self._clear_span_selector()
            if not (
                self.edit_peak_button.isChecked()
                or self.split_peak_button.isChecked()
                or self.pointer_button.isChecked()
            ):
                self._hide_interaction_cursor()

    def _toggle_edit_range_mode(self, enabled: bool):
        if enabled:
            row = self.peak_table.currentRow()
            peak = self._peak_at_table_row(row)
            if peak is None or peak.is_fitted:
                QtWidgets.QMessageBox.information(
                    self, APP_NAME, self.translator("select_peak")
                )
                self.edit_peak_button.setChecked(False)
                return
            if self._peak_edit_return_mouse_mode is None:
                self._peak_edit_return_mouse_mode = (
                    self._mouse_mode
                    if self._mouse_mode != "edit_peak" else "normal"
                )
            self._mouse_tool_toggled("edit_peak", True)
            self._edit_range_peak_id = peak.id
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
            restore_mode = self._peak_edit_return_mouse_mode
            restore_previous = self._mouse_mode == "edit_peak"
            self._peak_edit_return_mouse_mode = None
            self._mouse_tool_toggled("edit_peak", False)
            self._edit_range_peak_id = None
            if self._span_selector is not None and self._span_selector_mode == "edit":
                self._clear_span_selector()
            if not (
                self.integrate_button.isChecked()
                or self.split_peak_button.isChecked()
                or self.pointer_button.isChecked()
            ):
                self.statusBar().clearMessage()
                self._hide_interaction_cursor()
            if restore_previous and restore_mode not in (None, "normal", "edit_peak"):
                index = self.mouse_mode_combo.findData(restore_mode)
                if index >= 0:
                    self.mouse_mode_combo.setCurrentIndex(index)

    def _toggle_split_mode(self, enabled: bool):
        if enabled:
            row = self.peak_table.currentRow()
            peak = self._peak_at_table_row(row)
            if peak is None or peak.is_fitted:
                QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("select_peak"))
                self.split_peak_button.setChecked(False)
                return
            self._mouse_tool_toggled("split_peak", True)
            self._deactivate_toolbar_navigation()
            self.integrate_button.setChecked(False)
            self.edit_peak_button.setChecked(False)
            self.move_trace_button.setChecked(False)
            self.pointer_button.setChecked(False)
            self.annotation_action.setChecked(False)
            self.statusBar().showMessage(self.translator("split_hint"))
            self._ensure_interaction_cursor()
        else:
            self._mouse_tool_toggled("split_peak", False)
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

    def clear_fraction_regions(self):
        if not self.project.fraction_regions:
            return
        before = self._capture_analysis_state()
        self.project.fraction_regions = []
        self._push_undo_snapshot(
            before, self._history_label("フラクション範囲をクリア", "Clear fraction ranges")
        )
        self.project.dirty = True
        self._plot()
        self._update_title()

    def edit_fraction_range_numeric(self):
        if not self.project.datasets:
            QtWidgets.QMessageBox.information(
                self, APP_NAME, self.translator("no_dataset")
            )
            return
        minimum, maximum = self._full_x_bounds()
        dialog = FractionRangeDialog(
            regions=self.project.fraction_regions,
            default_interval=1.0,
            minimum=minimum,
            maximum=maximum,
            language=self._application_language,
            parent=self,
        )
        if not dialog_exec(dialog):
            return
        start, end, interval = dialog.values()
        target_id = dialog.selected_region_id
        before = self._capture_analysis_state()
        if target_id:
            region = next(
                (
                    item
                    for item in self.project.fraction_regions
                    if item.id == target_id
                ),
                None,
            )
            if region is None:
                return
            values = (float(start), float(end), float(interval))
            if values == (region.start_min, region.end_min, region.interval_min):
                return
            region.start_min, region.end_min, region.interval_min = values
            history = self._history_label(
                "フラクション範囲を編集", "Edit fraction range"
            )
        else:
            self.project.fraction_regions.append(
                FractionRegion(
                    start_min=float(start),
                    end_min=float(end),
                    interval_min=float(interval),
                )
            )
            history = self._history_label(
                "フラクション範囲を追加", "Add fraction range"
            )
        self._push_undo_snapshot(before, history)
        self.project.dirty = True
        self._plot()
        self._update_title()

    def _toggle_move_mode(self, enabled: bool):
        if enabled:
            if self._selected_dataset() is None:
                QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("no_dataset"))
                self.move_trace_button.setChecked(False)
                return
            self._mouse_tool_toggled("move_trace", True)
            self._deactivate_toolbar_navigation()
            self.integrate_button.setChecked(False)
            self.edit_peak_button.setChecked(False)
            self.split_peak_button.setChecked(False)
            self.pointer_button.setChecked(False)
            self.annotation_action.setChecked(False)
            self.statusBar().showMessage(self.translator("move_hint"))
        else:
            self._mouse_tool_toggled("move_trace", False)
            if self._screen_preview is not None:
                self._screen_preview.cancel_move_drag()
            self._move_drag = None
            if not (
                self.integrate_button.isChecked()
                or self.edit_peak_button.isChecked()
                or self.split_peak_button.isChecked()
            ):
                self.statusBar().clearMessage()

    def reset_selected_trace_position(self):
        dataset = self._selected_dataset()
        if dataset is None:
            QtWidgets.QMessageBox.information(
                self, APP_NAME, self.translator("no_dataset")
            )
            return
        self.move_trace_button.setChecked(False)
        if dataset.x_shift_min == 0.0 and dataset.offset == 0.0:
            return
        before = self._capture_analysis_state()
        dataset.x_shift_min = 0.0
        dataset.offset = 0.0
        try:
            recalculate_dataset_peaks(dataset)
        except ValueError as exc:
            self._restore_analysis_state(before)
            QtWidgets.QMessageBox.warning(
                self, self.translator("warning"), str(exc)
            )
            self._plot()
            return
        self._push_undo_snapshot(
            before,
            self._history_label("移動を元に戻す", "Reset trace position"),
        )
        self.project.dirty = True
        row = next(
            (
                index
                for index, item in enumerate(self.project.datasets)
                if item.id == dataset.id
            ),
            -1,
        )
        if row >= 0:
            self._updating_table = True
            self.dataset_table.item(row, DATASET_X_SHIFT_COLUMN).setText("0")
            self.dataset_table.item(row, DATASET_OFFSET_COLUMN).setText("0")
            self._updating_table = False
        self._plot()
        self._update_title()

    def _toggle_pointer_mode(self, enabled: bool):
        if enabled:
            self._mouse_tool_toggled("pointer", True)
            self._deactivate_toolbar_navigation()
            self.integrate_button.setChecked(False)
            self.edit_peak_button.setChecked(False)
            self.split_peak_button.setChecked(False)
            self.move_trace_button.setChecked(False)
            self.annotation_action.setChecked(False)
            self.statusBar().showMessage(self.translator("pointer_hint"))
            self._ensure_interaction_cursor()
        else:
            self._mouse_tool_toggled("pointer", False)
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
            self._mouse_tool_toggled("annotation", True)
            self._deactivate_toolbar_navigation()
            self.integrate_button.setChecked(False)
            self.edit_peak_button.setChecked(False)
            self.split_peak_button.setChecked(False)
            self.move_trace_button.setChecked(False)
            self.pointer_button.setChecked(False)
            self.statusBar().showMessage(self.translator("text_annotation_hint"))
        else:
            self._mouse_tool_toggled("annotation", False)
            if self._screen_preview is not None:
                self._screen_preview.cancel_annotation_drag()
            self.statusBar().clearMessage()

    def _split_selected_peak_at(self, displayed_time: float):
        dataset = self._selected_dataset()
        row = self.peak_table.currentRow()
        peak = self._peak_at_table_row(row, dataset)
        if dataset is None or peak is None or peak.is_fitted:
            return
        peak_index = next(
            (index for index, item in enumerate(dataset.peaks) if item.id == peak.id),
            -1,
        )
        if peak_index < 0:
            return
        raw_time = float(displayed_time) - dataset.x_shift_min
        before = self._capture_analysis_state()
        try:
            left, right = split_peak_region(
                dataset,
                peak,
                raw_time,
            )
            clear_legacy_fit(left)
            clear_legacy_fit(right)
            dataset.peaks[peak_index : peak_index + 1] = [left, right]
            dataset.fitted_peaks = [
                child
                for child in dataset.fitted_peaks
                if child.parent_peak_id != peak.id
            ]
            recalculate_dataset_peaks(dataset)
        except ValueError as exc:
            self._restore_analysis_state(before)
            QtWidgets.QMessageBox.warning(self, self.translator("warning"), str(exc))
            return
        self._push_undo_snapshot(
            before, self._history_label("積分エリアを分割", "Split integration area")
        )
        self.project.dirty = True
        self._refresh_peak_table([right.id])
        self._plot()
        self._update_title()

    def _matplotlib_hit_target(self, event):
        integration_hit = (
            self._integration_peak_hit_target(event)
            if self._mouse_mode == "select"
            else ("", "")
        )
        if event.canvas_x is None or event.canvas_y is None:
            return integration_hit
        for marker in reversed(self.project.vertical_markers):
            artist = self._vertical_marker_artists.get(marker.id)
            if artist is None or not artist.get_visible():
                continue
            axis = self._marker_axis(marker)
            if not axis.bbox.contains(event.canvas_x, event.canvas_y):
                continue
            marker_x = axis.transData.transform((marker.x_min, 0.0))[0]
            if abs(event.canvas_x - float(marker_x)) <= 6.0:
                return "vertical_marker", marker.id
        try:
            renderer = self.canvas.get_renderer()
        except (AttributeError, RuntimeError):
            return integration_hit
        for annotation in reversed(self.project.annotations):
            artist = self._annotation_artists.get(annotation.id)
            if artist is None or not artist.get_visible():
                continue
            try:
                bounds = artist.get_window_extent(renderer=renderer).expanded(1.08, 1.25)
            except (AttributeError, RuntimeError, ValueError):
                continue
            if bounds.contains(event.canvas_x, event.canvas_y):
                return "annotation", annotation.id
        return integration_hit

    def _integration_peak_hit_target(self, event):
        """Resolve an integration-area click from the backend-neutral scene."""

        target_axis = event.axis_role if self._split_y_axes else "plot"
        if target_axis not in ("y1", "y2", "plot"):
            return "", ""
        coordinate_axis = event.axis_role if event.axis_role in ("y1", "y2") else "y1"
        x_value, _y_value = event.data_for(coordinate_axis)
        scene = getattr(self, "_screen_scene", None)
        targets = (
            (
                overlay.peak_id,
                overlay.axis_id,
                overlay.start_x,
                overlay.end_x,
            )
            for overlay in (() if scene is None else scene.peak_overlays)
            if (
                self.project.method.show_integration_areas
                and (
                    overlay.show_integration_area
                    or overlay.prepare_integration_area
                )
            )
        )
        return integration_peak_hit_target(targets, target_axis, x_value)

    def _select_integration_peak(self, peak_id: str):
        """Select the dataset and table row represented by a plotted area."""

        for row, dataset in enumerate(self.project.datasets):
            if not any(peak.id == peak_id for peak in dataset.peaks):
                continue
            self.dataset_table.selectRow(row)
            self._refresh_peak_table([peak_id])
            self._peak_selection_changed()
            return

    def _annotation_at_event(self, event):
        if event.hit_kind != "annotation":
            return None
        return next(
            (
                annotation
                for annotation in self.project.annotations
                if annotation.id == event.hit_id
            ),
            None,
        )

    def _vertical_marker_at_event(self, event):
        if event.hit_kind != "vertical_marker":
            return None
        return next(
            (
                marker
                for marker in self.project.vertical_markers
                if marker.id == event.hit_id
            ),
            None,
        )

    def _select_vertical_marker(self, marker, additive: bool = False):
        if marker is None:
            self._selected_vertical_marker_ids.clear()
            self._selected_vertical_marker_id = ""
        elif additive:
            self._selected_vertical_marker_ids.add(marker.id)
            self._selected_vertical_marker_id = marker.id
        else:
            self._selected_vertical_marker_ids = {marker.id}
            self._selected_vertical_marker_id = marker.id
        if self._screen_preview is not None:
            self._plot()
            return
        for marker_id, artist in self._vertical_marker_artists.items():
            selected = marker_id in self._selected_vertical_marker_ids
            model = next(
                (
                    item
                    for item in self.project.vertical_markers
                    if item.id == marker_id
                ),
                None,
            )
            artist.set_color(
                "#f59e0b" if selected else ((model.color if model else "") or "#7c3aed")
            )
            artist.set_linewidth(2.0 if selected else 1.15)
            artist.set_alpha(0.95 if selected else 0.8)
            label = self._vertical_marker_label_artists.get(marker_id)
            if label is not None:
                label.set_color(
                    "#f59e0b"
                    if selected
                    else ((model.color if model else "") or "#7c3aed")
                )
        self._request_canvas_draw(force=True)

    def _place_vertical_marker(self, event):
        selected = self._selected_dataset()
        if self._split_y_axes and event.axis_role in ("y1", "y2"):
            y_axis = 2 if event.axis_role == "y2" else 1
        else:
            y_axis = 2 if event.axis_role == "y2" else (
                selected.y_axis if selected is not None else 1
            )
        source_role = event.axis_role if event.axis_role != "outside" else "y1"
        x_value, _y_value = event.data_for(source_role)
        if x_value is None:
            return
        before = self._capture_analysis_state()
        marker = VerticalMarker(x_min=float(x_value), y_axis=y_axis)
        self.project.vertical_markers.append(marker)
        self._selected_vertical_marker_ids = {marker.id}
        self._selected_vertical_marker_id = marker.id
        self._push_undo_snapshot(
            before, self._history_label("縦線を追加", "Add vertical marker")
        )
        self.project.dirty = True
        self._plot()
        self._update_title()

    def delete_selected_vertical_marker(self):
        marker_ids = set(self._selected_vertical_marker_ids)
        if self._selected_vertical_marker_id:
            marker_ids.add(self._selected_vertical_marker_id)
        if not marker_ids:
            return False
        before = self._capture_analysis_state()
        retained = [
            marker
            for marker in self.project.vertical_markers
            if marker.id not in marker_ids
        ]
        if len(retained) == len(self.project.vertical_markers):
            self._selected_vertical_marker_ids.clear()
            self._selected_vertical_marker_id = ""
            return False
        removed_count = len(self.project.vertical_markers) - len(retained)
        self.project.vertical_markers = retained
        self._selected_vertical_marker_ids.clear()
        self._selected_vertical_marker_id = ""
        self._push_undo_snapshot(
            before,
            self._history_label(
                "縦線を%d本削除" % removed_count,
                "Delete %d vertical marker(s)" % removed_count,
            ),
        )
        self.project.dirty = True
        self._plot()
        self._update_title()
        return True

    def delete_selected_plot_items(self):
        """Delete one unified range selection as a single undo step."""

        if self._mouse_mode != "select":
            return self.delete_selected_vertical_marker()
        dataset = self._selected_dataset()
        peak_ids = set(self._selected_peak_ids()) if dataset is not None else set()
        marker_ids = set(self._selected_vertical_marker_ids)
        if self._selected_vertical_marker_id:
            marker_ids.add(self._selected_vertical_marker_id)
        existing_peak_ids = {
            peak.id for peak in dataset.peaks
        } if dataset is not None else set()
        existing_marker_ids = {
            marker.id for marker in self.project.vertical_markers
        }
        peak_ids.intersection_update(existing_peak_ids)
        marker_ids.intersection_update(existing_marker_ids)
        if not peak_ids and not marker_ids:
            return False
        before = self._capture_analysis_state()
        if peak_ids:
            self._remove_peak_ids(dataset, peak_ids)
        if marker_ids:
            self.project.vertical_markers = [
                marker
                for marker in self.project.vertical_markers
                if marker.id not in marker_ids
            ]
        self._selected_vertical_marker_ids.clear()
        self._selected_vertical_marker_id = ""
        self._push_undo_snapshot(
            before,
            self._history_label(
                "選択項目を削除", "Delete selected plot items"
            ),
        )
        self.project.dirty = True
        self._clear_selected_time_range()
        self._refresh_peak_table()
        self._plot()
        self._update_title()
        return True

    def keyPressEvent(self, event):
        delete_key = (
            QtCore.Qt.Key.Key_Delete if QT_API == 6 else QtCore.Qt.Key_Delete
        )
        if event.key() == delete_key and self.delete_selected_plot_items():
            event.accept()
            return
        escape_key = (
            QtCore.Qt.Key.Key_Escape if QT_API == 6 else QtCore.Qt.Key_Escape
        )
        if event.key() == escape_key and (
            self.edit_peak_button.isChecked()
            or self._selected_time_range is not None
        ):
            if self.edit_peak_button.isChecked():
                self.edit_peak_button.setChecked(False)
            self._clear_selected_time_range()
            event.accept()
            return
        super().keyPressEvent(event)

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
        y_axis = 2 if event.axis_role == "y2" else (
            selected.y_axis if selected is not None else 1
        )
        target_role = "y2" if y_axis == 2 and self.axes_right is not None else "y1"
        x_value, y_value = event.data_for(target_role)
        if x_value is None or y_value is None:
            return
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
        if not isinstance(event, ScreenPointerEvent):
            event = self._normalized_pointer_event(event)
            event = event.with_hit_target(*self._matplotlib_hit_target(event))
        if event.button != 1:
            return
        if (
            self.axes_overview is not None
            and event.canvas_y is not None
            and abs(float(event.canvas_y) - float(self.axes_overview.bbox.y0)) <= 6.0
        ):
            self._overview_split_drag = True
            return
        if (
            self._mouse_mode == "zoom"
            and event.axis_role == "overview_y1"
            and self.axes_overview is not None
            and not event.double_click
        ):
            x_value, _y_value = event.data_for("overview_y1")
            if (
                x_value is not None
                and math.isfinite(float(x_value))
                and event.canvas_x is not None
            ):
                left, right = self._full_x_bounds()
                x_value = min(max(float(x_value), left), right)
                self._overview_zoom_drag = {
                    "start": x_value,
                    "pixel": float(event.canvas_x),
                }
                bottom, top = self.axes_overview.get_ylim()
                self._overview_zoom_patch = Rectangle(
                    (x_value, min(bottom, top)), 0.0, abs(top - bottom),
                    facecolor="#2563eb", edgecolor="#1d4ed8",
                    linewidth=1.0, alpha=0.12, zorder=20,
                )
                self.axes_overview.add_patch(self._overview_zoom_patch)
                self._request_canvas_draw(throttled=True)
            return
        # Issue #236/9.1: "normal" mode now defaults to pan being active, so
        # toolbar.mode is no longer empty while idle in it (it used to be,
        # which is what this gate originally relied on to mean "no native
        # pan/zoom drag owns this click"). A click on an existing marker or
        # annotation must still be handled (select, drag, or double-click to
        # edit) regardless -- only bail out to let a real pan/zoom drag start
        # for a click that isn't on one of those items.
        if str(getattr(self.toolbar, "mode", "")) and event.hit_kind not in (
            "vertical_marker", "annotation"
        ):
            return
        if self._mouse_mode == "select":
            if event.hit_kind == "integration_peak":
                self._select_integration_peak(event.hit_id)
                return
        marker = self._vertical_marker_at_event(event)
        if marker is not None:
            self._select_vertical_marker(
                marker,
                additive=(
                    self.pointer_button.isChecked()
                    or self._mouse_mode == "select"
                ),
            )
            target_role = "y2" if marker.y_axis == 2 else "y1"
            start_x, _start_y = event.data_for(target_role)
            if (
                start_x is not None
                and self._mouse_mode in ("pointer", "select")
            ):
                self._vertical_marker_drag = {
                    "marker": marker,
                    "axis_role": target_role,
                    "start_x": float(start_x),
                    "initial_x": float(marker.x_min),
                    "undo_state": self._capture_analysis_state(),
                }
                if (
                    self._span_selector_mode == "select"
                    and self._span_selector is not None
                ):
                    self._span_selector.set_active(False)
            return
        annotation = self._annotation_at_event(event)
        if annotation is not None:
            if event.double_click:
                self._edit_text_annotation(annotation)
                return
            target_role = "y2" if annotation.y_axis == 2 else "y1"
            start_x, start_y = event.data_for(target_role)
            if start_x is None or start_y is None:
                return
            self._annotation_drag = {
                "annotation": annotation,
                "artist": self._annotation_artists.get(annotation.id),
                "axis_role": target_role,
                "start_x": float(start_x),
                "start_y": float(start_y),
                "initial_x": annotation.x_min,
                "initial_y": annotation.y_value,
                "undo_state": self._capture_analysis_state(),
            }
            return
        source_role = event.axis_role if event.axis_role != "outside" else "y1"
        x_value, _y_value = event.data_for(source_role)
        if x_value is None:
            return
        if self.annotation_action.isChecked():
            self._place_text_annotation(event)
            return
        if self.pointer_button.isChecked():
            self._place_vertical_marker(event)
            return
        if self._selected_vertical_marker_id:
            self._select_vertical_marker(None)
        if event.axis_role in ("overview_y1", "overview_y2"):
            self._center_detail_on(float(x_value))
            return
        if event.double_click and not (
            self.integrate_button.isChecked()
            or self.edit_peak_button.isChecked()
            or self.split_peak_button.isChecked()
            or self.move_trace_button.isChecked()
            or self._mouse_mode == "select"
        ):
            self._back_to_previous_view()
            return
        if self.split_peak_button.isChecked():
            self._split_selected_peak_at(x_value)
            return
        if not self.move_trace_button.isChecked():
            return
        dataset = self._selected_dataset()
        if dataset is None:
            return
        target_role = "y2" if dataset.y_axis == 2 and self.axes_right is not None else "y1"
        x_value, y_value = event.data_for(target_role)
        if x_value is None or y_value is None:
            return
        self._move_drag = {
            "dataset": dataset,
            "start_x": float(x_value),
            "start_y": float(y_value),
            "initial_x_shift": dataset.x_shift_min,
            "initial_offset": dataset.offset,
            "undo_state": self._capture_analysis_state(),
        }

    def _on_canvas_motion(self, event):
        if not isinstance(event, ScreenPointerEvent):
            event = self._normalized_pointer_event(event)
        self._update_pointer_coordinates(event)
        if self._overview_split_drag is not None:
            return
        if self._overview_zoom_drag is not None:
            x_value, _y_value = event.data_for("overview_y1")
            if x_value is not None and math.isfinite(float(x_value)):
                left, right = self._full_x_bounds()
                current = min(max(float(x_value), left), right)
                start = self._overview_zoom_drag["start"]
                if self._overview_zoom_patch is not None:
                    self._overview_zoom_patch.set_x(min(start, current))
                    self._overview_zoom_patch.set_width(abs(current - start))
                    self._request_canvas_draw(throttled=True)
            return
        if self._vertical_marker_drag is not None:
            drag = self._vertical_marker_drag
            x_value, _y_value = event.data_for(drag["axis_role"])
            if x_value is None:
                return
            marker = drag["marker"]
            marker.x_min = (
                drag["initial_x"] + float(x_value) - drag["start_x"]
            )
            artist = self._vertical_marker_artists.get(marker.id)
            if artist is not None:
                artist.set_xdata([marker.x_min, marker.x_min])
            label = self._vertical_marker_label_artists.get(marker.id)
            if label is not None:
                label.set_x(marker.x_min)
                label.set_text("%g min" % marker.x_min)
            if self._screen_preview is not None:
                self._screen_preview.consumer.set_vertical_marker_position(
                    marker.id, marker.x_min
                )
            self._request_canvas_draw(throttled=True)
            return
        if self._annotation_drag is not None:
            drag = self._annotation_drag
            x_value, y_value = event.data_for(drag["axis_role"])
            if x_value is None or y_value is None:
                return
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
            or self._mouse_mode == "select"
        ):
            if event.axis_role in ("y1", "y2", "gradient"):
                x_value, _y_value = event.data_for(event.axis_role)
            else:
                x_value = None
            if x_value is not None:
                self._ensure_interaction_cursor()
                self._interaction_cursor.set_xdata([x_value, x_value])
                self._interaction_cursor.set_visible(True)
                self._request_canvas_draw(throttled=True)
            else:
                self._hide_interaction_cursor()
        if self._move_drag is None:
            return
        dataset = self._move_drag["dataset"]
        target_axes = self.axes_right if dataset.y_axis == 2 and self.axes_right is not None else self.axes
        target_role = "y2" if target_axes is self.axes_right else "y1"
        x_value, y_value = event.data_for(target_role)
        if x_value is None or y_value is None:
            return
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

    def _update_pointer_coordinates(self, event):
        """Show backend-neutral pointer coordinates in the toolbar location."""

        if not isinstance(event, ScreenPointerEvent):
            event = self._normalized_pointer_event(event)
        # The native preview sends ordinary motion here directly, while drag
        # motion arrives through _on_canvas_motion.  Update the shortcut target
        # at this shared point so either path follows the pointer.
        self._update_overview_shortcut_target(event)
        axis = {
            "y1": self.axes,
            "y2": self.axes_right,
            "gradient": self.axes_gradient,
            "overview_y1": self.axes_overview,
            "overview_y2": self.axes_overview_right,
        }.get(event.axis_role)
        message = ""
        if axis is not None:
            x_value, y_value = event.data_for(event.axis_role)
            if x_value is not None and y_value is not None:
                try:
                    message = axis.format_coord(x_value, y_value).rstrip()
                except (TypeError, ValueError, OverflowError):
                    message = ""
        self.toolbar.set_message(message)

    def _on_canvas_release(self, event):
        if not isinstance(event, ScreenPointerEvent):
            event = self._normalized_pointer_event(event)
        if self._overview_split_drag is not None:
            self._overview_split_drag = None
            if event.canvas_y is not None and self.axes_overview is not None:
                top = float(self.axes_overview.bbox.y1)
                bottom = float(self.axes.bbox.y0)
                if top > bottom:
                    ratio = (top - float(event.canvas_y)) / (top - bottom)
                    self._overview_split_ratio = min(0.85, max(0.15, ratio))
                    self._plot()
            return
        if self._overview_zoom_drag is not None:
            drag = self._overview_zoom_drag
            x_value, _y_value = event.data_for("overview_y1")
            pixel = event.canvas_x
            self._cancel_overview_zoom_drag()
            if x_value is None or pixel is None or not math.isfinite(float(x_value)):
                return
            left, right = self._full_x_bounds()
            current = min(max(float(x_value), left), right)
            if abs(float(pixel) - drag["pixel"]) < 3.0 or current == drag["start"]:
                return
            self._push_view_history()
            state = self._screen_view_state()
            self._apply_view_state(replace(
                state, x=tuple(sorted((drag["start"], current)))
            ))
            self.toolbar.set_history_buttons()
            return
        if self._vertical_marker_drag is not None:
            drag = self._vertical_marker_drag
            marker = drag["marker"]
            changed = marker.x_min != drag["initial_x"]
            self._vertical_marker_drag = None
            if changed:
                self._push_undo_snapshot(
                    drag["undo_state"],
                    self._history_label(
                        "縦線を移動", "Move vertical marker"
                    ),
                )
                self.project.dirty = True
                self._plot()
                self._update_title()
            elif (
                self._span_selector_mode == "select"
                and self._span_selector is not None
            ):
                self._span_selector.set_active(True)
            return
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
        if not changed:
            return
        try:
            recalculate_dataset_peaks(dataset)
        except ValueError as exc:
            if undo_state is not None:
                self._restore_analysis_state(undo_state)
            QtWidgets.QMessageBox.warning(
                self, self.translator("warning"), str(exc)
            )
            self._plot()
            return
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

    def _cancel_overview_zoom_drag(self):
        self._overview_zoom_drag = None
        patch = self._overview_zoom_patch
        self._overview_zoom_patch = None
        if patch is not None:
            try:
                patch.remove()
            except (ValueError, AttributeError, RuntimeError):
                pass
            self._request_canvas_draw(throttled=True)

    def _full_x_bounds(self):
        datasets = [
            dataset for dataset in self.project.datasets
            if self._dataset_is_screen_visible(dataset)
        ]
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
        self.toolbar.set_history_buttons()

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

        if getattr(self, "_split_y_axes", False) and self.axes_right is not None:
            edge = 5.0
            for axis, y_target, plot_target in (
                (self.axes, "y1", "plot_y1"),
                (self.axes_right, "y2", "plot_y2"),
            ):
                bbox = axis.bbox
                if (
                    bbox.x0 - edge <= x_value <= bbox.x1 + edge
                    and bbox.y0 - edge <= y_value <= bbox.y0 + edge
                ):
                    return "x"
                if (
                    bbox.y0 <= y_value <= bbox.y1
                    and bbox.x0 - edge <= x_value <= bbox.x0 + edge
                ):
                    return y_target
                if self._display_point_in_bbox(x_value, y_value, bbox):
                    return plot_target

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

    def _normalized_pointer_event(self, event, hit_region=""):
        normalized = normalize_pointer_event(
            event,
            {
                "y1": self.axes,
                "y2": self.axes_right,
                "gradient": self.axes_gradient,
                "overview_y1": self.axes_overview,
                "overview_y2": self.axes_overview_right,
            },
            hit_region=hit_region,
        )
        self._update_overview_shortcut_target(normalized)
        if self._split_y_axes:
            # A twinx B% overlay receives the Matplotlib mouse event. Editing
            # still belongs to its intensity panel, not the selected trace.
            source = getattr(event, "inaxes", None)
            if source is not None and source is self.axes_gradient:
                return replace(normalized, axis_role="y1")
            if source is not None and source is self.axes_gradient_secondary:
                return replace(normalized, axis_role="y2")
        return normalized

    def _update_overview_shortcut_target(self, event):
        """Remember the last plot under the pointer for view shortcuts."""
        axis_role = getattr(event, "axis_role", "outside")
        if axis_role in ("overview_y1", "overview_y2"):
            self._overview_shortcut_active = True
        elif axis_role in ("y1", "y2", "gradient"):
            self._overview_shortcut_active = False

    def _on_scroll(self, event):
        if not isinstance(event, ScreenPointerEvent):
            event = self._normalized_pointer_event(
                event, hit_region=self._scroll_target(event) or ""
            )
        self._update_overview_shortcut_target(event)
        if event.button not in ("up", "down"):
            return
        factor = 0.8 if event.button == "up" else 1.25
        if event.axis_role == "overview_y1":
            _unused, center_y = event.data_for("overview_y1")
            self._zoom_overview_y(factor, center_y)
            return
        configured_mode = self.project.method.zoom_axis
        if configured_mode != "auto":
            source_axis = {
                "y1": self.axes,
                "y2": self.axes_right,
            }.get(event.axis_role)
            center_x, _primary_y = event.data_for("y1")
            center_role = event.axis_role if event.axis_role in ("y1", "y2") else "y1"
            _axis_x, center_y = event.data_for(center_role)
            self._zoom_view(
                factor, center_x, source_axis, center_y, zoom_mode=configured_mode
            )
            return

        target = event.hit_region or None
        if target is None:
            return
        center_x, _unused = event.data_for("y1")
        if target == "x":
            self._zoom_view(factor, center_x, zoom_mode="x", y_axes=[])
            return
        if target == "y1":
            _axis_x, center_y = event.data_for("y1")
            self._zoom_view(
                factor,
                center_x,
                zoom_mode="y",
                y_axes=[self.axes],
                y_centers={self.axes: center_y},
            )
            return
        if target == "y2" and self.axes_right is not None:
            _axis_x, center_y = event.data_for("y2")
            self._zoom_view(
                factor,
                center_x,
                zoom_mode="y",
                y_axes=[self.axes_right],
                y_centers={self.axes_right: center_y},
            )
            return

        if target in ("plot_y1", "plot_y2"):
            target_axis = self.axes if target == "plot_y1" else self.axes_right
            target_role = "y1" if target == "plot_y1" else "y2"
            _axis_x, center_y = event.data_for(target_role)
            self._zoom_view(
                factor,
                center_x,
                source_axis=target_axis,
                center_y=center_y,
                zoom_mode="both",
                y_axes=[target_axis],
                y_centers={target_axis: center_y},
            )
            return

        y_targets = [self.axes]
        if self.axes_right is not None:
            y_targets.append(self.axes_right)
        y_centers = {
            axis: event.data_for("y1" if axis is self.axes else "y2")[1]
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

    def _on_time_range_selected(self, minimum: float, maximum: float):
        if not all(math.isfinite(float(value)) for value in (minimum, maximum)):
            return False
        start, end = sorted((float(minimum), float(maximum)))
        if start == end:
            return False
        self._selected_time_range = (start, end)
        self.statusBar().showMessage(
            self.translator("time_range_selected", start=start, end=end)
        )
        self.timeRangeSelected.emit(start, end)
        return True

    def _on_selection_span_selected(self, minimum: float, maximum: float):
        """Select integrations and vertical markers inside one time range."""

        if not self._on_time_range_selected(minimum, maximum):
            return
        start, end = self._selected_time_range
        dataset = self._selected_dataset()
        selected_peak_ids = []
        if dataset is not None:
            shift = float(dataset.x_shift_min)
            selected_peak_ids = [
                peak.id
                for peak in dataset.peaks
                if start <= (
                    float(peak.retention_time_min)
                    if peak.retention_time_min is not None
                    else (float(peak.start_min) + float(peak.end_min)) / 2.0
                ) + shift <= end
            ]
        self._select_peak_ids(selected_peak_ids)
        self._selected_vertical_marker_ids = {
            marker.id
            for marker in self.project.vertical_markers
            if start <= float(marker.x_min) <= end
        }
        self._selected_vertical_marker_id = next(
            (
                marker.id
                for marker in self.project.vertical_markers
                if marker.id in self._selected_vertical_marker_ids
            ),
            "",
        )
        self._plot()

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
        peak = self._peak_at_table_row(row, dataset)
        if dataset is None or peak is None:
            QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("no_dataset"))
            return
        if peak.is_fitted:
            self.fit_selected_peak()
            return
        peak_id = peak.id
        before = self._capture_analysis_state()
        dialog = PeakRangeDialog(peak, float(dataset.time_min[0]), float(dataset.time_min[-1]), self._application_language, self)
        if dialog_exec(dialog):
            if dialog.mouse_selection_requested:
                self._select_peak_ids([peak_id])
                self.edit_peak_button.setChecked(True)
                return
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

    def _remove_peak_ids(self, dataset, peak_ids):
        peak_ids = set(peak_ids)
        removed_parent_ids = {
            peak.id for peak in dataset.peaks if peak.id in peak_ids
        }
        affected_parent_ids = {
            peak.parent_peak_id
            for peak in dataset.fitted_peaks
            if peak.id in peak_ids
        }
        dataset.peaks = [
            peak for peak in dataset.peaks if peak.id not in peak_ids
        ]
        dataset.fitted_peaks = [
            peak
            for peak in dataset.fitted_peaks
            if peak.id not in peak_ids
            and peak.parent_peak_id not in removed_parent_ids
        ]
        if removed_parent_ids or affected_parent_ids:
            recalculate_dataset_peaks(dataset)
        for parent_id in affected_parent_ids - removed_parent_ids:
            self._sync_legacy_fit_for_parent(dataset, parent_id)

    def delete_peak(self):
        dataset = self._selected_dataset()
        peak_ids = set(self._selected_peak_ids())
        current = self._peak_at_table_row(self.peak_table.currentRow(), dataset)
        if not peak_ids and current is not None:
            peak_ids.add(current.id)
        if dataset is None or not peak_ids:
            return
        before = self._capture_analysis_state()
        self._remove_peak_ids(dataset, peak_ids)
        self._push_undo_snapshot(
            before, self._history_label("ピークを削除", "Delete peaks")
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
        dialog = AutoPeakDetectionDialog(
            self._selected_time_range,
            self._application_language,
            self,
        )
        if not dialog_exec(dialog):
            return
        self.integrate_button.setChecked(False)
        self.split_peak_button.setChecked(False)
        self.move_trace_button.setChecked(False)
        before = self._capture_analysis_state()
        thresholds = self._auto_peak_sensitivity_presets[dialog.sensitivity_id]
        apply_auto_peak_thresholds(self.project.method, thresholds)
        raw_time_range = (
            tuple(value - float(dataset.x_shift_min) for value in dialog.time_range)
            if dialog.time_range is not None
            else None
        )
        try:
            candidates = detect_peaks(
                dataset,
                self.project.method,
                time_range=raw_time_range,
            )
        except Exception as exc:
            self.project.method = deepcopy(before["method"])
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
        method_changed = self.project.method != before["method"]
        if not added and not method_changed:
            QtWidgets.QMessageBox.information(
                self,
                APP_NAME,
                "条件に一致する新しいピーク候補はありません。"
                if self._application_language == "ja"
                else "No new peak candidates matched the current settings.",
            )
            return
        if added:
            dataset.peaks.extend(added)
            recalculate_dataset_peaks(dataset)
        self._push_undo_snapshot(
            before, self._history_label("自動ピーク検出", "Automatic peak detection")
        )
        self.project.dirty = True
        self._refresh_peak_table([peak.id for peak in added])
        self._plot()
        self._update_title()
        if added:
            self.statusBar().showMessage(
                self.translator("auto_detected", count=len(added)), 7000
            )
        else:
            QtWidgets.QMessageBox.information(
                self,
                APP_NAME,
                "条件に一致する新しいピーク候補はありません。感度設定はMethodに適用しました。"
                if self._application_language == "ja"
                else "No new peak candidates matched. The sensitivity settings were applied to the Method.",
            )

    def fit_selected_peak(self):
        dataset = self._selected_dataset()
        row = self.peak_table.currentRow()
        selected_peak = self._peak_at_table_row(row, dataset)
        if dataset is None or selected_peak is None:
            QtWidgets.QMessageBox.information(
                self, APP_NAME, self.translator("select_peak")
            )
            return
        fitted_peak = selected_peak if selected_peak.is_fitted else None
        parent_peak = (
            dataset.parent_peak_for(selected_peak)
            if fitted_peak is not None
            else selected_peak
        )
        if parent_peak is None:
            QtWidgets.QMessageBox.warning(
                self, self.translator("warning"), self.translator("missing_fit_parent")
            )
            return
        labels = (
            ("自動選択", "auto"),
            ("Gaussian", "gaussian"),
            ("EMG（テーリング）", "emg"),
        ) if self._application_language == "ja" else (
            ("Automatic", "auto"),
            ("Gaussian", "gaussian"),
            ("EMG (tailing)", "emg"),
        )
        display_items = [label for label, _value in labels]
        selected_label, accepted = QtWidgets.QInputDialog.getItem(
            self,
            self.translator("fit_peak"),
            "モデル" if self._application_language == "ja" else "Model",
            display_items,
            0,
            False,
        )
        if not accepted:
            return
        model = dict(labels).get(selected_label, "auto")
        before = self._capture_analysis_state()
        try:
            result = fit_peak(dataset, parent_peak, model)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(
                self, self.translator("warning"), str(exc)
            )
            return
        if fitted_peak is None:
            fitted_peak = fitted_peak_from_result(parent_peak, result, dataset)
            dataset.fitted_peaks.append(fitted_peak)
        else:
            apply_fit_result(fitted_peak, parent_peak, result, dataset)
        mirror_fitted_peak_for_legacy(parent_peak, fitted_peak)
        recalculate_dataset_peaks(dataset)
        self._push_undo_snapshot(
            before,
            self._history_label(
                "フィット由来ピークを再計算"
                if selected_peak.is_fitted
                else "フィット由来ピークを追加",
                "Recalculate fitted peak"
                if selected_peak.is_fitted
                else "Add fitted peak",
            ),
        )
        self.project.dirty = True
        self._refresh_peak_table([fitted_peak.id])
        self._plot()
        self._update_title()
        self.statusBar().showMessage(
            "%s: R²=%.5f, RMSE=%.4g µV"
            % (result.model.upper(), result.r_squared, result.rmse_uv),
            7000,
        )

    def _selected_fit_target(self):
        """Return (dataset, parent integration, existing fitted child)."""

        dataset = self._selected_dataset()
        selected_peak = self._peak_at_table_row(self.peak_table.currentRow(), dataset)
        if dataset is None or selected_peak is None:
            QtWidgets.QMessageBox.information(
                self, APP_NAME, self.translator("select_peak")
            )
            return None
        fitted_peak = selected_peak if selected_peak.is_fitted else None
        parent_peak = (
            dataset.parent_peak_for(selected_peak)
            if fitted_peak is not None
            else selected_peak
        )
        if parent_peak is None:
            QtWidgets.QMessageBox.warning(
                self, self.translator("warning"), self.translator("missing_fit_parent")
            )
            return None
        return dataset, parent_peak, fitted_peak

    def _ask_fit_model(self):
        labels = (
            ("自動選択", "auto"),
            ("Gaussian", "gaussian"),
            ("EMG（テーリング）", "emg"),
        ) if self._application_language == "ja" else (
            ("Automatic", "auto"),
            ("Gaussian", "gaussian"),
            ("EMG (tailing)", "emg"),
        )
        selected_label, accepted = QtWidgets.QInputDialog.getItem(
            self,
            self.translator("fit_peak"),
            "モデル" if self._application_language == "ja" else "Model",
            [label for label, _value in labels],
            0,
            False,
        )
        return dict(labels).get(selected_label, "auto") if accepted else None

    def correct_saturated_peak(self):
        """Rebuild a clipped peak from its unsaturated flanks as a fitted row."""

        target = self._selected_fit_target()
        if target is None:
            return
        dataset, parent_peak, fitted_peak = target
        if fitted_peak is None:
            fitted_peak = next(
                (
                    peak for peak in reversed(dataset.fitted_peaks)
                    if peak.parent_peak_id == parent_peak.id
                    and is_saturation_corrected(peak)
                ),
                None,
            )
        model = self._ask_fit_model()
        if model is None:
            return
        dialog = SaturatedRangeDialog(
            parent_peak.start_min,
            parent_peak.end_min,
            self._application_language,
            self,
            initial=(parent_peak.start_min, parent_peak.end_min),
        )
        if not dialog_exec(dialog):
            return
        try:
            result, span = fit_saturated_peak(
                dataset, parent_peak, model, dialog.saturated_range
            )
        except ValueError as exc:
            reason = str(exc)
            message = (
                self.translator(reason)
                if reason in (
                    "not_saturated",
                    "saturated_range_outside_peak",
                    "saturated_range_too_narrow",
                )
                else reason
            )
            QtWidgets.QMessageBox.warning(
                self, self.translator("warning"), message
            )
            return
        before = self._capture_analysis_state()
        if fitted_peak is None:
            fitted_peak = fitted_peak_from_result(
                parent_peak, result, dataset, span
            )
            dataset.fitted_peaks.append(fitted_peak)
        else:
            apply_fit_result(fitted_peak, parent_peak, result, dataset, span)
        mirror_fitted_peak_for_legacy(parent_peak, fitted_peak)
        recalculate_dataset_peaks(dataset)
        self._push_undo_snapshot(
            before,
            self._history_label("飽和ピーク補正", "Saturated peak correction"),
        )
        self.project.dirty = True
        self._refresh_peak_table([fitted_peak.id])
        self._plot()
        self._update_title()
        self.statusBar().showMessage(
            "%s: %s %.4f–%.4f min (%d pts), R²=%.5f"
            % (
                self.translator("saturation_corrected"),
                result.model.upper(),
                span.start_min,
                span.end_min,
                span.point_count,
                result.r_squared,
            ),
            9000,
        )
        if LIMITED_FLANK_NOTE in fitted_peak.notes.splitlines():
            QtWidgets.QMessageBox.warning(
                self,
                self.translator("warning"),
                self.translator("limited_saturation_flanks"),
            )

    def _sync_legacy_fit_for_parent(self, dataset: Dataset, parent_id: str):
        parent = next(
            (peak for peak in dataset.peaks if peak.id == parent_id),
            None,
        )
        if parent is None:
            return
        children = [
            peak
            for peak in dataset.fitted_peaks
            if peak.parent_peak_id == parent_id
        ]
        if children:
            mirror_fitted_peak_for_legacy(parent, children[-1])
        else:
            clear_legacy_fit(parent)

    def _reset_view(self):
        if getattr(self, "_overview_shortcut_active", False):
            self._overview_full_x = None
            self._overview_full_y = None
            self._set_overview_x(self._full_x_bounds())
            self._set_overview_y(self._overview_y_bounds())
            return
        self._push_view_history()
        self._view_initialized = False
        self._plot(preserve_view=False)

    def _reset_x_view(self):
        if getattr(self, "_overview_shortcut_active", False):
            self._overview_full_x = None
            self._set_overview_x(self._full_x_bounds())
            return
        if not self._view_initialized:
            return
        self._push_view_history()
        self.axes.set_xlim(*self._full_x_bounds())
        self._set_dynamic_x_ticks()
        self._update_overview_window()
        self._request_canvas_draw()

    def _reset_y_view(self):
        if getattr(self, "_overview_shortcut_active", False):
            self._overview_full_y = None
            self._set_overview_y(self._overview_y_bounds())
            return
        if not self._view_initialized:
            return
        view_state = self._capture_view_state()
        self._push_view_history()
        y1 = self._scene_y_limits(
            self._screen_scene, "y1", view_state.x
        )
        y2 = (
            self._scene_y_limits(self._screen_scene, "y2", view_state.x)
            if view_state.y2 is not None else None
        )
        self._apply_view_state(replace(
            view_state,
            y1=y1 if y1 is not None else view_state.y1,
            y2=(
                y2 if y2 is not None else view_state.y2
            ),
        ))

    def _import_chromatogram_paths(self, paths, show_progress=False) -> int:
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
                channel = dataset.y_axis
                same_channel_count = sum(
                    1
                    for existing in self.project.datasets
                    if existing.y_axis == channel
                )
                dataset.color = dataset.color or default_trace_color(
                    channel, same_channel_count
                ) or COLORS[len(self.project.datasets) % len(COLORS)]
                # The label is a free user field. It stays blank on import so the
                # acquisition time is read in the timestamp column instead of
                # looking like a label. Run IDs and legends keep their own
                # filename fallback, so nothing downstream loses its identity.
                dataset.label = ""
                dataset.short_label = ""
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
            show_progress=True,
        )
        if imported > 0 and getattr(dialog, "register_work_directory", True):
            self._register_work_directory(
                directory,
                dialog.group_label,
                getattr(dialog, "recursive", False),
            )
        skipped_txt_count = getattr(dialog, "duplicate_txt_skip_count", 0)
        QtWidgets.QMessageBox.information(
            self,
            APP_NAME,
            "\n".join(
                (
                    self.translator("imported", count=imported),
                    self.translator(
                        "gcd_txt_skipped", count=skipped_txt_count
                    ),
                )
            ),
        )
        self._settings.set(LAST_IMPORT_DIRECTORY, str(directory), sync=True)
        return imported

    def _register_work_directory(self, directory, label="", recursive=False):
        path = str(Path(directory))
        normalized = normalized_source_path(path)
        if any(
            normalized_source_path(entry.path) == normalized
            for entry in self.project.work_directories
        ):
            return False
        before_state = self._capture_analysis_state()
        self.project.work_directories.append(
            WorkDirectory(
                path=path,
                label=str(label or "").strip() or Path(path).name,
                recursive=bool(recursive),
            )
        )
        self._push_undo_snapshot(
            before_state,
            self._history_label("作業ディレクトリを登録", "Register work directory"),
        )
        self.project.dirty = True
        self._update_title()
        return True

    def edit_work_directories(self):
        before_state = self._capture_analysis_state()
        before = deepcopy(self.project.work_directories)
        dialog = WorkDirectoriesDialog(
            before, self._application_language, self
        )
        if not dialog_exec(dialog):
            return False
        if dialog.directories == before:
            return False
        self.project.work_directories = dialog.directories
        self._push_undo_snapshot(
            before_state,
            self._history_label("作業ディレクトリ", "Work directories"),
        )
        self.project.dirty = True
        self._update_title()
        return True

    def _work_directory_reload_candidates(self):
        return discover_reload_candidates(
            self.project.work_directories, self.project.datasets
        )

    def reload_work_directories(self):
        if not self.project.work_directories:
            QtWidgets.QMessageBox.information(
                self,
                APP_NAME,
                self.translator("no_work_directories"),
            )
            self.edit_work_directories()
            return 0
        (
            candidates,
            duplicate_count,
            changed,
            errors,
            skipped_txt_count,
        ) = self._work_directory_reload_candidates()
        lines = [
            self.translator(
                "work_directory_reload_summary",
                new=len(candidates),
                duplicate=duplicate_count,
                changed=len(changed),
                errors=len(errors),
            )
        ]
        if skipped_txt_count:
            lines.append(
                self.translator("gcd_txt_skipped", count=skipped_txt_count)
            )
        if changed:
            lines.append(self.translator("changed_files_held"))
            lines.extend("- " + path for path in changed[:10])
        if errors:
            lines.append(self.translator("reload_errors"))
            lines.extend("- " + value for value in errors[:10])
        if not candidates:
            QtWidgets.QMessageBox.information(self, APP_NAME, "\n".join(lines))
            return 0
        answer = QtWidgets.QMessageBox.question(
            self,
            APP_NAME,
            "\n".join(lines + [self.translator("import_new_files_question")]),
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return 0
        return self._import_chromatogram_paths(
            [path for path, _label in candidates],
            show_progress=True,
        )

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
        dialog = PreferencesDialog(
            self.project.method,
            self._import_directory,
            self._application_language,
            self,
            save_directory=self._save_directory,
            database_path=self._database_path,
            render_quality=self._render_quality,
            automatic_update_check=self._automatic_update_check,
            auto_peak_sensitivity_presets=self._auto_peak_sensitivity_presets,
        )
        if not dialog_exec(dialog):
            return
        self._import_directory = dialog.import_directory_value
        self._save_directory = dialog.save_directory_value
        self._database_path = dialog.database_path_value
        self._automatic_update_check = dialog.automatic_update_check_value
        self._settings.set_many(
            {
                IMPORT_DIRECTORY: self._import_directory,
                SAVE_DIRECTORY: self._save_directory,
                DATABASE_PATH: self._database_path,
                AUTOMATIC_UPDATE_CHECK: self._automatic_update_check,
                AUTO_PEAK_SENSITIVITY_PRESETS: (
                    dialog.auto_peak_sensitivity_presets_value
                ),
            }
        )
        self._auto_peak_sensitivity_presets = deepcopy(
            dialog.auto_peak_sensitivity_presets_value
        )
        if not self._automatic_update_check:
            self._automatic_update_timer.stop()
            self._automatic_update_scheduled = False
        if (
            self._automatic_update_check
            and self.isVisible()
            and not self._automatic_update_scheduled
        ):
            self._automatic_update_scheduled = True
            self._automatic_update_timer.start(1500)
        render_quality_changed = dialog.render_quality_value != self._render_quality
        self._set_render_quality(
            dialog.render_quality_value,
            persist=True,
            replot=False,
        )
        if render_quality_changed:
            self._plot()

    def manage_presets(self):
        dialog = PresetManagerDialog(
            self._global_condition_presets,
            self._global_gradient_presets,
            self._global_preset_metadata,
            self._application_language,
            self,
            analytes=self._global_analyte_presets,
        )
        if not dialog_exec(dialog):
            return False
        project_presets_changed = (
            dialog.conditions != self._global_condition_presets
            or dialog.gradients != self._global_gradient_presets
        )
        if (
            not project_presets_changed
            and dialog.analytes == self._global_analyte_presets
        ):
            return False
        self._global_preset_metadata = deepcopy(dialog.metadata)
        self._global_analyte_presets = deepcopy(dialog.analytes)
        self.project.condition_presets = deepcopy(dialog.conditions)
        self.project.gradient_presets = deepcopy(dialog.gradients)
        self._persist_global_presets()
        # Analyte presets live only in the application-wide store, so managing
        # them alone changes nothing inside the Project and must not report the
        # Project as unsaved.
        if project_presets_changed:
            self.project.dirty = True
            self._update_title()
        return True

    def check_for_updates(self, manual=True):
        """Start one asynchronous update check and return without blocking Qt."""

        if self._update_check_thread is not None:
            return False
        thread = QtCore.QThread(self)
        worker = UpdateCheckWorker()
        worker.moveToThread(thread)
        self._update_check_thread = thread
        self._update_check_worker = worker
        self.check_updates_action.setEnabled(False)
        thread.started.connect(worker.run)
        worker.finished.connect(
            lambda result: self._present_update_check_result(result, bool(manual))
        )
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda: self._update_check_complete(thread))
        thread.start()
        return True

    def _update_check_complete(self, thread):
        if self._update_check_thread is thread:
            self._update_check_thread = None
            self._update_check_worker = None
            self.check_updates_action.setEnabled(True)
        thread.deleteLater()

    def _present_update_check_result(self, result, manual):
        status = result.get("status")
        if status == "update_available":
            answer = QtWidgets.QMessageBox.question(
                self,
                APP_NAME,
                self.translator(
                    "update_available", version=result.get("latest_version", "")
                ),
            )
            if answer == QtWidgets.QMessageBox.Yes:
                QtGui.QDesktopServices.openUrl(
                    QtCore.QUrl(str(result.get("release_url", "")))
                )
        elif manual and status == "current":
            QtWidgets.QMessageBox.information(
                self, APP_NAME, self.translator("update_current")
            )
        elif manual and status == "no_release":
            QtWidgets.QMessageBox.information(
                self, APP_NAME, self.translator("update_no_release")
            )
        elif manual:
            QtWidgets.QMessageBox.warning(
                self,
                APP_NAME,
                self.translator(
                    "update_failed", reason=result.get("reason", "")
                ),
            )

    def showEvent(self, event):
        super().showEvent(event)
        if self._automatic_update_check and not self._automatic_update_scheduled:
            self._automatic_update_scheduled = True
            self._automatic_update_timer.start(1500)

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
        # The selection checkbox mirrors the row selection, so either way of
        # picking several chromatograms reaches the same rows.
        rows = self._selected_dataset_rows()
        if not rows:
            row = self.dataset_table.currentRow()
            rows = [row] if 0 <= row < len(self.project.datasets) else []
        if not rows:
            return
        if len(rows) == 1:
            message = self.translator(
                "confirm_remove_dataset",
                label=self.project.datasets[rows[0]].label
                or self.project.datasets[rows[0]].original_filename,
            )
        else:
            message = self.translator("confirm_remove_datasets", count=len(rows))
        answer = QtWidgets.QMessageBox.question(
            self,
            self.translator("warning"),
            message,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        before_state = self._capture_analysis_state()
        for row in sorted(rows, reverse=True):
            self.project.remove_dataset_at(row)
        self._push_undo_snapshot(
            before_state,
            self._history_label("クロマトグラム削除", "Remove chromatograms"),
        )
        self.project.dirty = True
        self._refresh_all(max(0, rows[0] - 1))

    def edit_metadata(self):
        dataset = self._selected_dataset()
        if dataset is None:
            QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("no_dataset"))
            return
        before = self._capture_analysis_state()
        dialog = MetadataDialog(
            dataset,
            self._application_language,
            self,
            analyte_presets=self._global_analyte_presets,
            preset_metadata=self._global_preset_metadata,
        )
        if dialog_exec(dialog):
            self._global_analyte_presets = dialog.analyte_presets
            self._global_preset_metadata = dialog.preset_metadata
            self._save_global_preset_file()
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
        selected_ids = {
            self.project.datasets[row].id
            for row in self._selected_dataset_rows()
        }
        selected_ids.add(dataset.id)
        selected_datasets = [
            item for item in self.project.datasets if item.id in selected_ids
        ]
        resolved_colors = {
            item.id: dataset_display_color(item, index)
            for index, item in enumerate(self.project.datasets)
        }
        dialog = DisplaySettingsDialog(
            dataset,
            selected_datasets,
            resolved_colors,
            self._application_language,
            self,
            available_datasets=self.project.datasets,
        )
        if not dialog_exec(dialog):
            return
        changes = dialog.changes()
        before = self._capture_analysis_state()
        changed = False
        for item in self.project.datasets:
            values = changes.get(item.id, {})
            for field_name in ("color", "line_style"):
                if field_name not in values:
                    continue
                value = values[field_name]
                if getattr(item, field_name) != value:
                    setattr(item, field_name, value)
                    changed = True
        if not changed:
            return
        self._push_undo_snapshot(
            before, self._history_label("表示設定", "Display settings")
        )
        self.project.dirty = True
        self._refresh_dataset_table(self.dataset_table.currentRow())
        self._plot()
        self._update_title()

    def edit_axis_labels(self):
        before = self._capture_analysis_state()
        x_limits = self._screen_view_state().x
        dialog = AxisLabelsDialog(
            self.project.method,
            self._application_language,
            self,
            x_span_min=abs(x_limits[1] - x_limits[0]),
        )
        if not dialog_exec(dialog):
            return
        dialog.apply_to_method(self.project.method)
        self._push_undo_snapshot(
            before,
            self._history_label(
                "軸・ラベル・書式設定", "Axes, labels and formatting"
            ),
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
        before = [self._capture_analysis_state()]

        def group_runs(dataset_ids, target_dataset_id):
            changed = self._group_dataset_ids_into_run(
                dataset_ids, target_dataset_id
            )
            if changed:
                before[0] = self._capture_analysis_state()
            return changed

        def ungroup_runs(dataset_ids):
            changed = self._ungroup_dataset_ids(dataset_ids)
            if changed:
                before[0] = self._capture_analysis_state()
            return changed

        dialog = BatchMetadataDialog(
            self.project,
            selected.id if selected is not None else "",
            self._application_language,
            self,
            preset_metadata=self._global_preset_metadata,
            group_runs_callback=group_runs,
            ungroup_runs_callback=ungroup_runs,
            analyte_presets=self._global_analyte_presets,
        )
        if dialog_exec(dialog):
            self._global_preset_metadata = dialog.preset_metadata
            self._global_analyte_presets = dialog.analyte_presets
            for dataset in self.project.datasets:
                try:
                    recalculate_dataset_peaks(dataset)
                except ValueError:
                    pass
            self._push_undo_snapshot(
                before[0],
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
        self._solo_dataset_id = ""
        self._selected_time_range = None
        self._view_initialized = False
        self._view_history.clear()
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
            self._solo_dataset_id = ""
            self._selected_time_range = None
            self._settings.set(
                LAST_PROJECT_DIRECTORY, str(Path(path).parent), sync=True
            )
            self._merge_global_presets_into_project()
            self._view_initialized = False
            self._view_history.clear()
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

    def _save_static_figure(self, figure, path: str, bbox_inches="tight"):
        old_size = figure.get_size_inches().copy()
        try:
            figure.set_size_inches(
                self.project.method.figure_width_mm / 25.4,
                self.project.method.figure_height_mm / 25.4,
            )
            figure.savefig(
                path, dpi=self.project.method.dpi, bbox_inches=bbox_inches
            )
        finally:
            figure.set_size_inches(old_size)

    def _save_figure_file(self, path: str):
        """Save PNG/SVG/PDF from full data regardless of screen quality."""

        with self._full_quality_export_figure():
            self._save_static_figure(self.figure, path)

    def _select_figure_output_path(self, title: str, basename: str):
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
            title,
            self._default_save_path("%s.%s" % (basename, selected_format)),
            filters,
            preferred_filter,
        )
        if not path:
            return ""
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
        return path

    def export_figure(self):
        if not self.project.datasets:
            QtWidgets.QMessageBox.information(self, APP_NAME, self.translator("no_dataset"))
            return
        path = self._select_figure_output_path(
            self.translator("export_figure"), "chromatogram"
        )
        if not path:
            return
        try:
            self._save_figure_file(path)
            self._remember_save_path(path)
            self.statusBar().showMessage(self.translator("saved", path=path), 5000)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self.translator("error"), str(exc))
        finally:
            self._request_canvas_draw(force=True)

    def open_3d_chromatogram(self):
        rows = self._selected_dataset_rows()
        if len(rows) < 2:
            QtWidgets.QMessageBox.information(
                self, APP_NAME, self.translator("select_multiple_3d")
            )
            return
        datasets = [self.project.datasets[row] for row in rows]
        options = getattr(self, "_three_d_plot_options", None)
        x_limits = self._screen_view_state().x
        if options is None:
            options = ThreeDPlotOptions(
                x_tick_interval=max(0.001, self.project.method.x_major_tick_min),
                z_tick_interval=suggest_z_tick_interval(
                    datasets, self.project.method, x_limits
                ),
            )
        dialog = ThreeDChromatogramDialog(
            datasets,
            self.project.method,
            x_limits,
            options,
            self._application_language,
            self,
        )
        dialog.export_button.clicked.connect(
            lambda _checked=False: self.export_3d_chromatogram(dialog)
        )
        dialog_exec(dialog)
        if dialog.export_button.isEnabled():
            self._three_d_plot_options = dialog.plot_options()

    def export_3d_chromatogram(self, dialog):
        path = self._select_figure_output_path(
            self.translator("export_3d_figure"), "chromatogram_3d"
        )
        if not path:
            return
        try:
            # The dialog already uses the export figure dimensions.  Avoid a
            # second tight-layout crop so the PNG/SVG/PDF keeps the preview's
            # relative font size and margins.  The shared 2D export retains
            # its established tight crop through the default argument.
            self._save_static_figure(dialog.figure, path, bbox_inches=None)
            self._remember_save_path(path)
            self.statusBar().showMessage(self.translator("saved", path=path), 5000)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self.translator("error"), str(exc))

    def _current_view_pixmap(self):
        if self._screen_preview is not None:
            try:
                return self._screen_preview.consumer.snapshot()
            except Exception:
                self._stop_screen_preview(failed=True)
        return self.screen_render_surface.snapshot()

    def copy_view_to_clipboard(self):
        pixmap = self._current_view_pixmap()
        QtWidgets.QApplication.clipboard().setPixmap(pixmap)
        self.statusBar().showMessage(
            "現在の表示画面をクリップボードへコピーしました。"
            if self._application_language == "ja"
            else "The current view was copied to the clipboard.",
            5000,
        )

    @staticmethod
    def _draw_view_pixmap_to_printer(printer, pixmap):
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
            target_size = pixmap.size()
            target_size.scale(
                int(page_rect.width()), int(page_rect.height()), keep_aspect
            )
            scaled = pixmap.scaled(target_size, keep_aspect, smooth)
            x = page_rect.x() + (page_rect.width() - scaled.width()) / 2.0
            y = page_rect.y() + (page_rect.height() - scaled.height()) / 2.0
            painter.drawPixmap(QtCore.QPointF(x, y), scaled)
        finally:
            painter.end()

    def print_current_view(self):
        mode = (
            QtPrintSupport.QPrinter.PrinterMode.HighResolution
            if QT_API == 6
            else QtPrintSupport.QPrinter.HighResolution
        )
        printer = QtPrintSupport.QPrinter(mode)
        dialog = QtPrintSupport.QPrintDialog(printer, self)
        dialog.setWindowTitle(self.translator("print_view"))
        if not dialog_exec(dialog):
            return
        try:
            self._draw_view_pixmap_to_printer(
                printer, self._current_view_pixmap()
            )
            self.statusBar().showMessage(
                "表示画面の印刷ジョブを送信しました。"
                if self._application_language == "ja"
                else "The current-view print job was sent.",
                7000,
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, self.translator("error"), str(exc)
            )

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

    def _choose_report_datasets(self):
        if not self.project.datasets:
            QtWidgets.QMessageBox.information(
                self, APP_NAME, self.translator("no_dataset")
            )
            return None
        visible = [dataset for dataset in self.project.datasets if dataset.visible]
        selected = [
            self.project.datasets[row] for row in self._selected_dataset_rows()
        ]
        dialog = ReportScopeDialog(
            len(self.project.datasets),
            len(visible),
            len(selected),
            self._application_language,
            self,
        )
        if not dialog_exec(dialog):
            return None
        if dialog.scope() == "selected":
            return selected
        if dialog.scope() == "visible":
            return visible
        return list(self.project.datasets)

    def _choose_report_options(self):
        method = self.project.method
        dialog = ReportOptionsDialog(
            self._application_language,
            self,
            initial_values={
                "integration_range": method.show_integration_areas,
                "baseline": method.show_integration_areas,
                "retention_time": method.show_retention_labels,
                "gradient_b": method.show_gradient_b,
                "gradient_conditions": True,
                "quantitation": True,
            },
        )
        if not dialog_exec(dialog):
            return None
        return ReportOptions(**dialog.option_values())

    def export_report(self):
        datasets = self._choose_report_datasets()
        if datasets is None:
            return
        options = self._choose_report_options()
        if options is None:
            return
        path, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            self.translator("export_report"),
            self._default_save_path(self._report_pdf_filename()),
            self.translator("report_filter"),
        )
        if not path:
            return
        try:
            actual = export_analysis_report_pdf(
                path,
                self.project,
                datasets,
                self._application_language,
                options,
            )
            self._remember_save_path(actual)
            self.statusBar().showMessage(self.translator("saved", path=actual), 7000)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self.translator("error"), str(exc))

    def _report_pdf_filename(self) -> str:
        """Name the exported report after the project it came from.

        A saved project contributes its file name, an unsaved one its title, and
        an empty title falls back to the same default the Project model uses.
        The shared sanitizer handles characters Windows rejects and the length
        limit, so no new rule is introduced here.
        """

        source = (
            Path(self.project.project_path).stem
            if self.project.project_path
            else self.project.title
        )
        return "analysis_report_%s.pdf" % sanitize_filename_component(
            source, "Untitled-project"
        )

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
        datasets = self._choose_report_datasets()
        if datasets is None:
            return
        options = self._choose_report_options()
        if options is None:
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
        try:
            with tempfile.TemporaryDirectory(prefix="hplc_report_") as directory:
                pages = render_analysis_report_pages(
                    directory,
                    self.project,
                    datasets,
                    self._application_language,
                    options,
                )
                dialog = QtPrintSupport.QPrintPreviewDialog(printer, self)
                dialog.setWindowTitle(self.translator("print_report"))
                # The preview and the printer share one painting routine, and
                # both work from the same rendered pages, so what is previewed
                # is exactly what is printed.
                dialog.paintRequested.connect(
                    lambda target: self._draw_report_pages_to_printer(
                        target, pages
                    )
                )
                accepted = dialog_exec(dialog)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, self.translator("error"), str(exc))
            return
        if not accepted:
            return
        self.statusBar().showMessage(
            "印刷ジョブを送信しました。"
            if self._application_language == "ja"
            else "The report was sent to the printer.",
            7000,
        )

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
            self._stop_screen_preview()
            self._automatic_update_timer.stop()
            self._open_windows.discard(self)
            event.accept()
        else:
            event.ignore()
