from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import csv
import math
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, PropertyMock, call, patch

import numpy as np
from matplotlib.ticker import MultipleLocator

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from hplc_app.analysis import recalculate_dataset_peaks
from hplc_app.database import database_sections
from hplc_app.dialogs import (
    AnalytePresetRegistrationDialog,
    AutoPeakDetectionDialog,
    AxisLabelsDialog,
    BatchMetadataDialog,
    ConditionCopyDialog,
    DirectoryImportDialog,
    DisplaySettingsDialog,
    FractionRangeDialog,
    GradientDialog,
    LabDatabaseDialog,
    LegendComposerDialog,
    MetadataDialog,
    PeakRangeDialog,
    PresetPreviewDialog,
    PresetManagerDialog,
    PreferencesDialog,
    ProjectNamingDialog,
    QuantitationHelpDialog,
    ReportOptionsDialog,
    SaturatedRangeDialog,
    ReportScopeDialog,
    TextAnnotationDialog,
    ThreeDChromatogramDialog,
    WorkDirectoriesDialog,
)
from hplc_app.auto_peak_settings import default_auto_peak_sensitivity_presets
from hplc_app.gui import (
    DATASET_COLUMN_IDS,
    DATASET_COLOR_COLUMN,
    DATASET_GROUP_COLUMN,
    DATASET_LABEL_COLUMN,
    DATASET_OFFSET_COLUMN,
    DATASET_COLUMN_NAME_COLUMN,
    DATASET_RUN_ID_COLUMN,
    DATASET_SELECTED_COLUMN,
    DATASET_SOLO_COLUMN,
    DATASET_SOURCE_COLUMN,
    DATASET_TIMESTAMP_COLUMN,
    DATASET_VISIBLE_COLUMN,
    DATASET_WAVELENGTH_COLUMN,
    DATASET_X_SHIFT_COLUMN,
    PEAK_COLUMN_COUNT,
    PEAK_NOTES_COLUMN,
    PEAK_TYPE_COLUMN,
    MOUSE_MODE_IDS,
    MainWindow,
    dataset_display_color,
)
from hplc_app.i18n import Translator
from hplc_app.models import (
    Dataset,
    FractionRegion,
    GradientPoint,
    PeakRegion,
    Project,
    TextAnnotation,
    VerticalMarker,
    WorkDirectory,
)
from hplc_app.parser import load_ascii_file
from hplc_app.exporters import export_peak_csv
from hplc_app.report import ReportOptions, analysis_report_figures
from hplc_app.plot3d import ThreeDPlotOptions, gradient_colors
from hplc_app.peak_fitting import (
    PeakFitResult,
    SaturatedSpan,
    fitted_peak_from_result,
    mirror_fitted_peak_for_legacy,
    gaussian_profile,
    is_saturation_corrected,
)
from hplc_app.preset_store import (
    load_preset_store,
    load_complete_preset_store,
    load_preset_store_with_metadata,
    normalize_preset_metadata,
    preset_store_path,
)
from hplc_app.qt_compat import (
    CHECKED,
    ITEM_IS_EDITABLE,
    QT_API,
    STANDARD_SAVE_SHORTCUT,
    UNCHECKED,
    USER_ROLE,
    QtCore,
    QtGui,
    QtPrintSupport,
    QtWidgets,
)
if QT_API == 6:
    from PySide6 import QtTest
else:
    from PySide2 import QtTest
from hplc_app.report import render_analysis_report_pages
from hplc_app.screen_events import ScreenPointerEvent
from hplc_app.screen_navigation import (
    ScreenViewState,
    compose_overview_state,
)
from hplc_app.screen_scene import (
    MAX_FRACTION_BOUNDARY_LINES,
    compose_base_screen_scene,
)


def _trace_edit_state(datasets):
    return [
        (item.id, item.visible, item.y_axis, item.x_shift_min, item.offset,
         deepcopy(item.peaks))
        for item in datasets
    ]
from hplc_app.settings_store import (
    ApplicationSettings,
    AUTO_PEAK_SENSITIVITY_PRESETS,
    DATASET_COLUMN_ORDER,
    DEFAULT_DATASET_COLUMN_ORDER,
    SCREEN_RENDERER,
)
from hplc_app.rendering import HIGH_QUALITY, LIGHTWEIGHT
from hplc_app.update_ui import UpdateDownloadDialog, UpdateDownloadWorker
from hplc_app.pyqtgraph_scene import (
    PyQtGraphSceneConsumer,
    pyqtgraph_scene_available,
)
from hplc_app.pyqtgraph_navigation import PyQtGraphNavigationController
from tests.gcd_fixtures import synthetic_gcd_bytes


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "sample_data"


class RowDropEvent:
    def __init__(self, position):
        self._position = QtCore.QPoint(position)
        self._mime_data = QtCore.QMimeData()
        self.accepted = False
        self.ignored = False
        self.drop_action = None

    def mimeData(self):
        return self._mime_data

    def pos(self):
        return self._position

    def position(self):
        return QtCore.QPointF(self._position)

    def acceptProposedAction(self):
        self.accepted = True

    def setDropAction(self, action):
        self.drop_action = action

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.ignored = True


class FakeProgressDialog:
    cancel_after = None
    instances = []

    def __init__(self, label, cancel_text, minimum, maximum, parent):
        self.label = label
        self.cancel_text = cancel_text
        self.minimum = minimum
        self.maximum = maximum
        self.parent = parent
        self.values = []
        self.closed = False
        self._canceled = False
        self.__class__.instances.append(self)

    def setWindowModality(self, _modality):
        pass

    def setMinimumDuration(self, _duration):
        pass

    def setValue(self, value):
        self.values.append(value)
        if (
            self.cancel_after is not None
            and value >= self.cancel_after
            and value < self.maximum
        ):
            self._canceled = True

    def wasCanceled(self):
        return self._canceled

    def close(self):
        self.closed = True


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._settings_directory = tempfile.TemporaryDirectory()
        cls._original_settings_format = QtCore.QSettings.defaultFormat()
        ini_format = (
            QtCore.QSettings.Format.IniFormat
            if QT_API == 6
            else QtCore.QSettings.IniFormat
        )
        user_scope = (
            QtCore.QSettings.Scope.UserScope
            if QT_API == 6
            else QtCore.QSettings.UserScope
        )
        QtCore.QSettings.setDefaultFormat(ini_format)
        QtCore.QSettings.setPath(
            ini_format, user_scope, cls._settings_directory.name
        )
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    @classmethod
    def tearDownClass(cls):
        QtCore.QSettings.setDefaultFormat(cls._original_settings_format)
        cls._settings_directory.cleanup()

    def setUp(self):
        self._preset_directory = tempfile.TemporaryDirectory()
        self._original_config_directory = os.environ.get(
            "HPLC_ANALYZER_CONFIG_DIR"
        )
        os.environ["HPLC_ANALYZER_CONFIG_DIR"] = self._preset_directory.name
        self.settings = QtCore.QSettings("Research Tools", "HPLC Analyzer")
        self.settings.clear()
        # Most existing GUI tests target the Matplotlib fallback explicitly.
        # Dedicated renderer-default tests remove this override.
        self.settings.setValue(SCREEN_RENDERER, "matplotlib")
        self.settings.sync()

    def tearDown(self):
        self.settings.clear()
        self.settings.sync()
        del self.settings
        if self._original_config_directory is None:
            os.environ.pop("HPLC_ANALYZER_CONFIG_DIR", None)
        else:
            os.environ["HPLC_ANALYZER_CONFIG_DIR"] = (
                self._original_config_directory
            )
        self._preset_directory.cleanup()

    def make_window(self):
        first = load_ascii_file(str(SAMPLES / "210601.TXT"))
        second = load_ascii_file(str(SAMPLES / "225120.TXT"))
        first.label = first.short_label = "Ch1"
        second.label = second.short_label = "Ch2"
        first.measurement.wavelength_nm = 280.0
        second.measurement.wavelength_nm = 214.0
        first.y_axis = 1
        second.y_axis = 2
        first.measurement.gradient = [
            GradientPoint(0.0, 90.0, 10.0, 0.0, 0.0),
            GradientPoint(90.0, 10.0, 90.0, 0.0, 0.0),
        ]
        first.peaks = [PeakRegion(start_min=5.0, end_min=10.0)]
        recalculate_dataset_peaks(first)
        project = Project(datasets=[first, second])
        # Existing GUI scenarios were authored against the former default and
        # select overview/split explicitly when that layout is under test.
        project.method.view_mode = "single"
        project.method.show_gradient_b = True
        window = MainWindow()
        window.project = project
        window.translator.set_language(project.ui_language)
        window._refresh_all(0)
        return window

    def make_lightweight_window(self):
        self.settings.setValue("rendering/quality", LIGHTWEIGHT)
        self.settings.sync()
        return self.make_window()

    def test_new_main_window_starts_in_overview_detail_mode(self):
        window = MainWindow()
        try:
            self.assertEqual(window.project.method.view_mode, "overview_detail")
            self.assertEqual(window.view_mode_combo.currentData(), "overview_detail")
        finally:
            window.project.dirty = False
            window.close()

    @staticmethod
    def drop_event(urls):
        mime_data = QtCore.QMimeData()
        mime_data.setUrls(urls)
        event = Mock()
        event.mimeData.return_value = mime_data
        return event

    def test_mainwindow_experimental_preview_navigation_refresh_and_snapshot(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.show()
            self.app.processEvents()
            initial = window._screen_view_state()
            raw = [dataset.intensity_uv.copy() for dataset in window.project.datasets]
            dirty = window.project.dirty
            self.assertIsNone(window._screen_preview)
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            self.assertIsNotNone(preview)
            self.assertFalse(window._matplotlib_screen_complete)
            self.assertEqual(window._dataset_lines, {})
            self.assertEqual(window._peak_overlay_artists, {})
            self.assertEqual(window._annotation_artists, {})
            self.assertEqual(window._vertical_marker_artists, {})
            self.assertTrue(all(not axis.lines for axis in window.figure.axes))
            self.assertTrue(all(axis.get_legend() is None for axis in window.figure.axes))
            self.assertIs(window.plot_stack.currentWidget(), preview.consumer.widget)
            self.assertEqual(preview.consumer.capture_view_state(), initial)
            self.assertIs(preview.navigation.history, window._view_history)
            self.assertEqual(window.project.dirty, dirty)
            consumer = preview.consumer
            core, gui = consumer.qt_core, consumer.qt_gui
            viewport = consumer.widget.viewport()
            position = consumer.widget.mapFromScene(
                consumer.primary.getAxis("right").sceneBoundingRect().center()
            )
            wheel = gui.QWheelEvent(
                core.QPointF(position), core.QPointF(viewport.mapToGlobal(position)),
                core.QPoint(), core.QPoint(0, 120), core.Qt.MouseButton.NoButton,
                core.Qt.KeyboardModifier.NoModifier, core.Qt.ScrollPhase.NoScrollPhase, False,
            )
            consumer.application.sendEvent(viewport, wheel)
            zoomed = window._screen_view_state()
            self.assertEqual(zoomed, consumer.capture_view_state())
            self.assertEqual(zoomed.x, initial.x)
            self.assertEqual(zoomed.y1, initial.y1)
            self.assertEqual(zoomed.gradient, initial.gradient)
            self.assertAlmostEqual(zoomed.y2[1] - zoomed.y2[0],
                                   0.8 * (initial.y2[1] - initial.y2[0]))
            window.toolbar.back()
            self.assertEqual(consumer.capture_view_state(), initial)
            window.toolbar.forward()
            self.assertEqual(consumer.capture_view_state(), zoomed)
            window.toolbar.home()
            window.zoom_axis_combo.setCurrentIndex(window.zoom_axis_combo.findData("y"))
            consumer.application.sendEvent(viewport, wheel)
            y_zoom = window._screen_view_state()
            self.assertEqual(y_zoom.x, initial.x)
            self.assertAlmostEqual(y_zoom.y1[1] - y_zoom.y1[0],
                                   0.8 * (initial.y1[1] - initial.y1[0]))
            self.assertAlmostEqual(y_zoom.y2[1] - y_zoom.y2[0],
                                   0.8 * (initial.y2[1] - initial.y2[0]))
            self.assertEqual(y_zoom.gradient, initial.gradient)
            window.toolbar.home()
            window.toolbar._actions["pan"].trigger()
            position = consumer.widget.mapFromScene(consumer.primary.vb.sceneBoundingRect().center())
            moved = position + core.QPoint(20, 25)
            for kind, point, button, held in (
                (core.QEvent.Type.MouseButtonPress, position, core.Qt.MouseButton.LeftButton, core.Qt.MouseButton.LeftButton),
                (core.QEvent.Type.MouseMove, moved, core.Qt.MouseButton.NoButton, core.Qt.MouseButton.LeftButton),
                (core.QEvent.Type.MouseButtonRelease, moved, core.Qt.MouseButton.LeftButton, core.Qt.MouseButton.NoButton),
            ):
                mouse = gui.QMouseEvent(kind, core.QPointF(point),
                    core.QPointF(viewport.mapToGlobal(point)), button, held,
                    core.Qt.KeyboardModifier.NoModifier)
                consumer.application.sendEvent(viewport, mouse)
            panned = window._screen_view_state()
            self.assertNotEqual(panned.x, initial.x)
            self.assertEqual(panned, consumer.capture_view_state())
            self.assertEqual(panned.gradient, initial.gradient)
            window.toolbar.back()
            self.assertEqual(consumer.capture_view_state(), initial)
            window.toolbar._actions["pan"].trigger()
            window._reset_y_view()
            self.assertEqual(consumer.capture_view_state(), window._screen_view_state())
            window._reset_x_view()
            self.assertEqual(consumer.capture_view_state().x, window._full_x_bounds())
            count = len(consumer.items)
            window._plot()
            window._plot()
            self.assertEqual(len(consumer.items), count)
            window.project.method.view_mode = "overview_detail"
            window._plot()
            self.assertTrue(consumer.overview_secondary.isVisible())
            self.assertEqual(len(consumer.overview_secondary.addedItems), 1)
            window.project.datasets[1].visible = False
            window._plot()
            self.assertEqual(consumer.last_evidence["counts"]["traces"], 1)
            self.assertIsNone(consumer.capture_view_state().y2)
            self.assertEqual(len(consumer.overview_items), 1)
            self.assertFalse(consumer.overview_secondary.isVisible())
            window.project.method.view_mode = "overview_detail"
            window._plot()
            self.assertTrue(consumer.overview.isVisible())
            pixmap = window._current_view_pixmap()
            self.assertFalse(pixmap.isNull())
            self.assertEqual(pixmap.size(), consumer.widget.size())
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "preview-export.png"
                savefig = window.figure.savefig
                export_evidence = {}

                def inspect_export(*args, **kwargs):
                    export_evidence["complete"] = window._matplotlib_screen_complete
                    export_evidence["dataset_lines"] = len(window._dataset_lines)
                    return savefig(*args, **kwargs)

                with patch.object(window.figure, "savefig", side_effect=inspect_export):
                    window._save_figure_file(str(path))
                self.assertGreater(path.stat().st_size, 0)
                self.assertTrue(export_evidence["complete"])
                self.assertEqual(
                    export_evidence["dataset_lines"],
                    sum(dataset.visible for dataset in window.project.datasets),
                )
                self.assertIs(window._screen_preview, preview)
                self.assertFalse(window._matplotlib_screen_complete)
                self.assertEqual(window._dataset_lines, {})
            for dataset, values in zip(window.project.datasets, raw):
                np.testing.assert_array_equal(dataset.intensity_uv, values)
            current = window._screen_view_state()
            window.screen_preview_checkbox.setChecked(False)
            self.assertIsNone(window._screen_preview)
            self.assertTrue(consumer._closed)
            self.assertTrue(window._matplotlib_screen_complete)
            self.assertEqual(
                len(window._dataset_lines),
                sum(dataset.visible for dataset in window.project.datasets),
            )
            self.assertEqual(window._screen_view_state(), current)
            self.assertIs(window.plot_stack.currentWidget(), window.canvas)
            window.screen_preview_checkbox.setChecked(True)
            window.project.dirty = False
            window.new_project()
            self.assertIsNotNone(window._screen_preview)
            self.assertEqual(window._screen_preview.consumer.items, [])
            self.assertEqual(window._view_history.count, 0)
        finally:
            window.project.dirty = False
            window.close()

    def test_qt6_pyqtgraph_default_persists_and_owns_neutral_view_state(self):
        if QT_API != 6 or not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        self.settings.remove(SCREEN_RENDERER)
        self.settings.sync()
        windows = []
        try:
            window = self.make_window()
            windows.append(window)
            self.assertTrue(window.screen_preview_checkbox.isChecked())
            self.assertIsNotNone(window._screen_preview)
            self.assertEqual(window._settings.get(SCREEN_RENDERER), "pyqtgraph")
            self.assertEqual(window._view_state, window._screen_view_state())
            preview = window._screen_preview
            state = preview.consumer.capture_view_state()
            preview.handle_event(
                "scroll_event",
                ScreenPointerEvent(
                    button="up", axis_role="y1", hit_region="x",
                    data_coordinates=((
                        "y1", sum(state.x) / 2.0, sum(state.y1) / 2.0,
                    ),),
                ),
            )
            native_state = preview.consumer.capture_view_state()
            self.assertEqual(window._view_state, native_state)
            self.assertEqual(window._matplotlib_view_state(), native_state)

            window.screen_preview_checkbox.setChecked(False)
            self.assertEqual(window._settings.get(SCREEN_RENDERER), "matplotlib")
            window.project.dirty = False
            window.close()
            windows.remove(window)

            restored = self.make_window()
            windows.append(restored)
            self.assertIsNone(restored._screen_preview)
            self.assertFalse(restored.screen_preview_checkbox.isChecked())
            restored.screen_preview_checkbox.setChecked(True)
            self.assertEqual(
                restored._settings.get(SCREEN_RENDERER), "pyqtgraph"
            )
            restored.project.dirty = False
            restored.close()
            windows.remove(restored)

            restarted = self.make_window()
            windows.append(restarted)
            self.assertIsNotNone(restarted._screen_preview)
            self.assertTrue(restarted.screen_preview_checkbox.isChecked())
        finally:
            for current in windows:
                current.project.dirty = False
                current.close()

    def test_renderer_fallback_explains_missing_and_qt5_can_activate(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        self.settings.setValue(SCREEN_RENDERER, "pyqtgraph")
        self.settings.sync()
        with patch(
            "hplc_app.screen_preview.PyQtGraphSceneConsumer",
            side_effect=ImportError("missing"),
        ):
            window = self.make_window()
        try:
            self.assertIsNone(window._screen_preview)
            self.assertIs(window.plot_stack.currentWidget(), window.canvas)
            self.assertEqual(window._screen_preview_notice, "missing")
            self.assertIn("インストール", window.screen_preview_label.text())
            self.assertEqual(window._settings.get(SCREEN_RENDERER), "pyqtgraph")

            window._screen_preview_notice = ""
            window._screen_renderer_preference = "pyqtgraph"
            window._activate_preferred_screen_renderer()
            self.assertIsNotNone(window._screen_preview)
            self.assertTrue(window.screen_preview_checkbox.isEnabled())
            self.assertEqual(window._screen_preview_notice, "active")
        finally:
            window.project.dirty = False
            window.close()

    def test_mainwindow_preview_falls_back_for_tools_split_and_failures(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            window.toolbar._actions["zoom"].trigger()
            self.assertIs(window._screen_preview, preview)
            self.assertTrue(window.toolbar._actions["zoom"].isChecked())
            window.toolbar._actions["zoom"].trigger()
            window.screen_preview_checkbox.setChecked(True)
            window.view_mode_combo.setCurrentIndex(window.view_mode_combo.findData("split_y_axes"))
            self.assertTrue(window._screen_preview.consumer.split_y_axes)
            window.view_mode_combo.setCurrentIndex(window.view_mode_combo.findData("single"))
            window.screen_preview_checkbox.setChecked(False)
            with patch("hplc_app.screen_preview.PyQtGraphSceneConsumer", side_effect=ImportError("missing")):
                window.screen_preview_checkbox.setChecked(True)
            self.assertIsNone(window._screen_preview)
            self.assertEqual(window._screen_preview_notice, "missing")
            with patch("hplc_app.screen_preview.ExperimentalScreenPreview._update_legend",
                       side_effect=RuntimeError("initialization failure")):
                window.screen_preview_checkbox.setChecked(True)
            self.assertIsNone(window._screen_preview)
            self.assertEqual(window.plot_stack.count(), 1)
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            with patch.object(preview, "refresh", side_effect=RuntimeError("render failure")):
                window._request_canvas_draw()
            self.assertIsNone(window._screen_preview)
            self.assertTrue(preview.consumer._closed)
            self.assertTrue(window._matplotlib_screen_complete)
            self.assertGreater(len(window._dataset_lines), 0)
            self.assertEqual(window.plot_stack.count(), 1)
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            with patch.object(preview.navigation, "handle_event", side_effect=RuntimeError("event failure")):
                self.assertTrue(preview.handle_event("button_press_event", ScreenPointerEvent(
                    button=1, axis_role="y1", hit_region="plot",
                )))
            self.assertIsNone(window._screen_preview)
            window.screen_preview_checkbox.setChecked(True)
            with patch.object(window._screen_preview.consumer, "snapshot", side_effect=RuntimeError("snapshot")):
                self.assertFalse(window._current_view_pixmap().isNull())
            self.assertIsNone(window._screen_preview)
            window.screen_preview_checkbox.setChecked(True)
            self.assertIsNotNone(window._screen_preview)
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_split_panels_native_navigation_and_markers(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.show()
            self.app.processEvents()
            raw = [dataset.intensity_uv.copy() for dataset in window.project.datasets]
            window.screen_preview_checkbox.setChecked(True)
            original = window._screen_preview.consumer
            window.view_mode_combo.setCurrentIndex(window.view_mode_combo.findData("split_y_axes"))
            preview = window._screen_preview
            self.assertIsNotNone(preview)
            consumer = preview.consumer
            self.assertTrue(original._closed)
            self.assertTrue(consumer.split_y_axes)
            self.assertEqual(window.plot_stack.count(), 2)
            self.assertIs(preview.navigation.history, window._view_history)
            self.app.processEvents()
            upper = consumer.primary.vb.sceneBoundingRect()
            lower = consumer.secondary.sceneBoundingRect()
            self.assertLess(upper.bottom(), lower.top())
            self.assertAlmostEqual(upper.left(), lower.left(), delta=1.0)
            self.assertAlmostEqual(upper.width(), lower.width(), delta=1.0)
            self.assertIs(consumer.gradient_layers[0][2], consumer.primary)
            self.assertEqual(consumer.capture_view_state(), window._screen_view_state())
            np.testing.assert_allclose(consumer.primary.viewRange()[0], consumer.secondary.viewRange()[0])

            core, gui = consumer.qt_core, consumer.qt_gui
            viewport = consumer.widget.viewport()

            def mouse(kind, point, button=core.Qt.MouseButton.NoButton,
                      held=core.Qt.MouseButton.NoButton):
                consumer.application.sendEvent(viewport, gui.QMouseEvent(
                    kind, core.QPointF(point), core.QPointF(viewport.mapToGlobal(point)),
                    button, held, core.Qt.KeyboardModifier.NoModifier,
                ))

            def click(point):
                mouse(core.QEvent.Type.MouseButtonPress, point,
                      core.Qt.MouseButton.LeftButton, core.Qt.MouseButton.LeftButton)
                mouse(core.QEvent.Type.MouseButtonRelease, point, core.Qt.MouseButton.LeftButton)

            initial = window._screen_view_state()
            position = consumer.widget.mapFromScene(
                consumer.secondary_plot.getAxis("left").sceneBoundingRect().center())
            self.assertEqual(consumer.pointer_event(consumer.widget.mapToScene(position)).hit_region, "y2")
            consumer.application.sendEvent(viewport, gui.QWheelEvent(
                core.QPointF(position), core.QPointF(viewport.mapToGlobal(position)),
                core.QPoint(), core.QPoint(0, 120), core.Qt.MouseButton.NoButton,
                core.Qt.KeyboardModifier.NoModifier, core.Qt.ScrollPhase.NoScrollPhase, False,
            ))
            zoomed = window._screen_view_state()
            self.assertEqual(zoomed, consumer.capture_view_state())
            self.assertEqual((zoomed.x, zoomed.y1, zoomed.gradient),
                             (initial.x, initial.y1, initial.gradient))
            self.assertAlmostEqual(zoomed.y2[1] - zoomed.y2[0],
                                   0.8 * (initial.y2[1] - initial.y2[0]))
            window.toolbar.back()
            self.assertEqual(consumer.capture_view_state(), initial)
            window.toolbar.forward()
            self.assertEqual(consumer.capture_view_state(), zoomed)
            window.toolbar.back()
            window.toolbar._actions["pan"].trigger()
            lower = consumer.secondary.sceneBoundingRect()
            position = consumer.widget.mapFromScene(lower.center())
            moved = position + core.QPoint(20, 25)
            self.assertEqual(consumer.pointer_event(consumer.widget.mapToScene(position)).hit_region, "plot_y2")
            mouse(core.QEvent.Type.MouseButtonPress, position,
                  core.Qt.MouseButton.LeftButton, core.Qt.MouseButton.LeftButton)
            mouse(core.QEvent.Type.MouseMove, moved, held=core.Qt.MouseButton.LeftButton)
            mouse(core.QEvent.Type.MouseButtonRelease, moved, core.Qt.MouseButton.LeftButton)
            panned = window._screen_view_state()
            self.assertEqual(panned, consumer.capture_view_state())
            self.assertEqual((panned.y1, panned.gradient), (initial.y1, initial.gradient))
            self.assertAlmostEqual(panned.x[0], initial.x[0] - 20 / lower.width() * (initial.x[1] - initial.x[0]))
            self.assertAlmostEqual(panned.y2[0], initial.y2[0] + 25 / lower.height() * (initial.y2[1] - initial.y2[0]))
            window.toolbar.back()
            self.assertEqual(consumer.capture_view_state(), initial)
            window.toolbar._actions["pan"].trigger()

            first, second = window.project.datasets
            second.measurement.gradient = deepcopy(first.measurement.gradient)
            window.dataset_table.selectRow(1)
            self.app.processEvents()
            self.assertEqual(len(consumer.gradient_layers), 2)
            self.assertAlmostEqual(consumer.gradient_layers[1][0].sceneBoundingRect().top(),
                                   consumer.secondary.sceneBoundingRect().top(), delta=1.0)
            self.assertAlmostEqual(consumer.primary.vb.sceneBoundingRect().width(),
                                   consumer.secondary.sceneBoundingRect().width(), delta=1.0)
            np.testing.assert_allclose(consumer.primary.viewRange()[0], consumer.secondary.viewRange()[0])
            np.testing.assert_allclose(consumer.primary.viewRange()[0], consumer.gradient.viewRange()[0])
            window.pointer_action.setChecked(True)
            for view, y_axis, time in ((consumer.primary.vb, 1, 12.0), (consumer.secondary, 2, 30.0)):
                point = consumer.widget.mapFromScene(view.mapViewToScene(
                    core.QPointF(time, sum(view.viewRange()[1]) / 2.0)))
                mouse(core.QEvent.Type.MouseMove, point)
                self.assertIs(consumer._cursor_view, view)
                self.assertTrue(consumer.pointer_cursor.isVisible())
                click(point)
                self.assertEqual(window.project.vertical_markers[-1].y_axis, y_axis)
            first_marker, second_marker = window.project.vertical_markers
            # Identical X in another panel must not select the other panel's marker.
            other_point = consumer.secondary.mapViewToScene(core.QPointF(first_marker.x_min,
                                                                         sum(consumer.secondary.viewRange()[1]) / 2))
            self.assertEqual(consumer.pointer_event(other_point).hit_id, "")
            self.assertIn(consumer.marker_items[first_marker.id], consumer.primary.vb.addedItems)
            self.assertIn(consumer.marker_items[second_marker.id], consumer.secondary.addedItems)
            window.undo()
            self.assertNotIn(second_marker.id, consumer.marker_items)
            window.redo()
            self.assertIn(second_marker.id, consumer.marker_items)
            self.assertFalse(window._current_view_pixmap().isNull())
            for dataset, values in zip(window.project.datasets, raw):
                np.testing.assert_array_equal(dataset.intensity_uv, values)
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_pointer_coordinates_and_motion_keep_plot_geometry(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.show()
            window.view_mode_combo.setCurrentIndex(
                window.view_mode_combo.findData("split_y_axes")
            )
            window.screen_preview_checkbox.setChecked(True)
            window.pointer_action.setChecked(True)
            preview = window._screen_preview
            consumer = preview.consumer
            core = consumer.qt_core

            def rectangle_values(rectangle):
                return (
                    rectangle.left(), rectangle.top(),
                    rectangle.width(), rectangle.height(),
                )

            before_geometry = (
                rectangle_values(consumer.primary.vb.sceneBoundingRect()),
                rectangle_values(consumer.secondary.sceneBoundingRect()),
            )
            with patch.object(preview, "refresh", wraps=preview.refresh) as refresh:
                for role, view, axis, x_value in (
                    ("y1", consumer.primary.vb, window.axes, 12.0),
                    ("y2", consumer.secondary, window.axes_right, 30.0),
                ):
                    for offset in range(5):
                        y_value = sum(view.viewRange()[1]) / 2.0 + offset
                        scene_position = view.mapViewToScene(
                            core.QPointF(x_value + offset * 0.1, y_value)
                        )
                        event = consumer.pointer_event(scene_position)
                        self.assertEqual(event.axis_role, role)
                        preview.handle_event("motion_notify_event", event)
                        coordinates = event.data_for(role)
                        self.assertEqual(
                            window.toolbar.locLabel.text(),
                            axis.format_coord(*coordinates).rstrip(),
                        )
                refresh.assert_not_called()
            self.app.processEvents()
            after_geometry = (
                rectangle_values(consumer.primary.vb.sceneBoundingRect()),
                rectangle_values(consumer.secondary.sceneBoundingRect()),
            )
            np.testing.assert_allclose(
                after_geometry, before_geometry, rtol=0.0, atol=0.0
            )

            window.view_mode_combo.setCurrentIndex(
                window.view_mode_combo.findData("overview_detail")
            )
            preview = window._screen_preview
            consumer = preview.consumer
            view = consumer.overview.vb
            scene_position = view.mapViewToScene(
                consumer.qt_core.QPointF(18.0, sum(view.viewRange()[1]) / 2.0)
            )
            event = consumer.pointer_event(scene_position)
            self.assertEqual(event.axis_role, "overview_y1")
            preview.handle_event("motion_notify_event", event)
            self.assertEqual(
                window.toolbar.locLabel.text(),
                window.axes_overview.format_coord(
                    *event.data_for("overview_y1")
                ).rstrip(),
            )

            window.screen_preview_checkbox.setChecked(False)
            legacy_event = ScreenPointerEvent(
                axis_role="y1",
                hit_region="plot",
                data_coordinates=(("y1", 7.25, 1234.5),),
            )
            window._on_canvas_motion(legacy_event)
            self.assertEqual(
                window.toolbar.locLabel.text(),
                window.axes.format_coord(7.25, 1234.5).rstrip(),
            )
            window._update_pointer_coordinates(ScreenPointerEvent())
            self.assertEqual(window.toolbar.locLabel.text(), "")
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_split_layout_switch_refresh_and_failure_cleanup(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.show()
            window.project.method.tick_label_font_size = 16
            window.view_mode_combo.setCurrentIndex(window.view_mode_combo.findData("split_y_axes"))
            window.screen_preview_checkbox.setChecked(True)
            self.assertFalse(window._current_view_pixmap().isNull())
            consumer = window._screen_preview.consumer
            self.assertAlmostEqual(consumer.primary.vb.sceneBoundingRect().width(),
                                   consumer.secondary.sceneBoundingRect().width(), delta=1.0)
            history = window._view_history
            for mode in ("split_y_axes", "single", "overview_detail", "split_y_axes"):
                previous = window._screen_preview.consumer
                state = window._screen_view_state()
                replacing = previous.split_y_axes != (mode == "split_y_axes")
                visible_when_closed = []
                original_close = previous.close

                def close_previous():
                    visible_when_closed.append(window.plot_stack.currentWidget())
                    original_close()

                with patch.object(previous, "close", side_effect=close_previous):
                    window.view_mode_combo.setCurrentIndex(
                        window.view_mode_combo.findData(mode)
                    )
                consumer = window._screen_preview.consumer
                self.assertIs(window._view_history, history)
                self.assertIs(window._screen_preview.navigation.history, history)
                self.assertEqual(consumer.capture_view_state(), state)
                self.assertEqual(consumer.split_y_axes, mode == "split_y_axes")
                self.assertEqual(consumer.overview.isVisible(), mode == "overview_detail")
                if previous is not consumer:
                    self.assertTrue(previous._closed)
                    self.assertTrue(replacing)
                    self.assertEqual(visible_when_closed, [consumer.widget])
                    self.assertIsNot(visible_when_closed[0], window.canvas)
                else:
                    self.assertFalse(replacing)
                    self.assertEqual(visible_when_closed, [])
                self.assertEqual(window.plot_stack.count(), 2)
            count = len(consumer.items)
            window.resize(1300, 950)
            window._plot()
            window._plot()
            self.assertEqual(len(consumer.items), count)
            window.project.method.tick_label_font_size = 16
            window._plot()
            self.assertFalse(window._current_view_pixmap().isNull())
            self.assertAlmostEqual(consumer.primary.vb.sceneBoundingRect().width(),
                                   consumer.secondary.sceneBoundingRect().width(), delta=1.0)
            window.project.datasets[1].visible = False
            window.project.method.show_gradient_b = False
            window._plot()
            self.assertEqual(consumer.last_evidence["counts"]["traces"], 1)
            self.assertFalse(consumer.gradient_axis.isVisible())
            self.assertAlmostEqual(consumer.primary.vb.sceneBoundingRect().width(),
                                   consumer.secondary.sceneBoundingRect().width(), delta=1.0)
            with patch("hplc_app.screen_preview.ExperimentalScreenPreview._update_legend",
                       side_effect=RuntimeError("layout replacement failure")):
                window.view_mode_combo.setCurrentIndex(window.view_mode_combo.findData("single"))
            self.assertTrue(consumer._closed)
            self.assertIsNone(window._screen_preview)
            self.assertEqual(window.plot_stack.count(), 1)
            self.assertEqual(window._screen_preview_notice, "failed")
        finally:
            window.project.dirty = False
            window.close()

    def test_split_gradient_visibility_shared_by_both_renderers(self):
        from hplc_app.project_io import load_project, save_project
        preview_modes = [False]
        if pyqtgraph_scene_available():
            preview_modes.append(True)
        for use_preview in preview_modes:
            with self.subTest(preview=use_preview):
                window = self.make_window()
                try:
                    first, second = window.project.datasets
                    second.measurement.gradient = [
                        GradientPoint(0.0, 80.0, 20.0, 0.0, 0.0),
                        GradientPoint(90.0, 30.0, 70.0, 0.0, 0.0),
                    ]
                    raw = [dataset.intensity_uv.copy() for dataset in window.project.datasets]
                    window.show()
                    window.view_mode_combo.setCurrentIndex(window.view_mode_combo.findData("split_y_axes"))
                    window.screen_preview_checkbox.setChecked(use_preview)

                    def assert_gradient(expected):
                        axes = (window.axes_gradient, window.axes_gradient_secondary)
                        self.assertEqual([axis is not None for axis in axes], [expected is not None] * 2)
                        if expected is not None:
                            if use_preview:
                                self.assertTrue(all(not axis.lines for axis in axes))
                                self.assertIsNone(window.axes.get_legend())
                            else:
                                for axis in axes:
                                    self.assertEqual(len(axis.lines), 1)
                                    np.testing.assert_array_equal(axis.lines[0].get_ydata(), expected)
                                    self.assertEqual(axis.get_xlim(), window.axes.get_xlim())
                                    self.assertEqual(axis.get_ylim(), window.axes_gradient.get_ylim())
                                labels = [text.get_text() for text in window.axes.get_legend().get_texts()]
                                self.assertEqual(sum(label.startswith("%B") for label in labels), 1)
                        if use_preview:
                            self.assertIsNotNone(window._screen_preview)
                            consumer = window._screen_preview.consumer
                            self.assertEqual(len(consumer.gradient_layers), 2)
                            self.assertEqual(consumer.last_evidence["counts"]["gradients"],
                                             0 if expected is None else 2)
                            for layer_index, (view, axis, host) in enumerate(
                                consumer.gradient_layers
                            ):
                                self.assertEqual(axis.isVisible(), expected is not None)
                                self.assertEqual(view.isVisible(), expected is not None)
                                self.assertEqual(
                                    sum(item.isVisible() for item in view.addedItems),
                                    0 if expected is None else 1,
                                )
                                if expected is not None:
                                    np.testing.assert_array_equal(view.addedItems[0].getData()[1], expected)
                                    np.testing.assert_allclose(
                                        view.viewRange()[0],
                                        consumer.primary.viewRange()[0],
                                        err_msg="gradient layer %d" % layer_index,
                                    )
                                    np.testing.assert_allclose(view.viewRange()[1], window.axes_gradient.get_ylim())
                                    self.assertAlmostEqual(view.sceneBoundingRect().top(),
                                                           host.vb.sceneBoundingRect().top(), delta=1.0)
                            self.assertAlmostEqual(consumer.primary.vb.sceneBoundingRect().width(),
                                                   consumer.secondary.sceneBoundingRect().width(), delta=1.0)
                            legend_labels = [label.text for _sample, label in consumer.primary.legend.items]
                            self.assertEqual(sum(label.startswith("%B") for label in legend_labels),
                                             0 if expected is None else 1)

                    assert_gradient([10.0, 90.0])
                    window.dataset_table.selectRow(1)
                    assert_gradient([20.0, 70.0])
                    window._plot()
                    assert_gradient([20.0, 70.0])
                    state = window._screen_view_state()
                    changed = ScreenViewState(x=(5.0, 40.0), y1=state.y1, y2=state.y2,
                                              gradient=(10.0, 80.0))
                    window._apply_view_state(changed)
                    assert_gradient([20.0, 70.0])
                    self.assertEqual(window.axes_gradient_secondary.get_ylim(), (10.0, 80.0))
                    window.show_gradient_checkbox.setChecked(False)
                    assert_gradient(None)
                    window.show_gradient_checkbox.setChecked(True)
                    assert_gradient([20.0, 70.0])
                    with tempfile.TemporaryDirectory() as directory:
                        for enabled in (False, True):
                            window.show_gradient_checkbox.setChecked(enabled)
                            project_path = str(Path(directory) / "both-panels.hplcproj")
                            save_project(project_path, window.project)
                            window.project = load_project(project_path)
                            window._refresh_all(1)
                            assert_gradient([20.0, 70.0] if enabled else None)
                        path = Path(directory) / "split.svg"
                        window._save_figure_file(str(path))
                        self.assertGreater(path.stat().st_size, 0)
                        assert_gradient([20.0, 70.0])
                        self.assertFalse(window._current_view_pixmap().isNull())
                    window.project.datasets[1].measurement.gradient = []
                    window._plot()
                    assert_gradient(None)
                    window.dataset_table.selectRow(0)
                    assert_gradient([10.0, 90.0])
                    window.project.datasets[0].visible = False
                    window._plot()
                    assert_gradient(None)
                    for dataset, values in zip(window.project.datasets, raw):
                        np.testing.assert_array_equal(dataset.intensity_uv, values)
                    window.view_mode_combo.setCurrentIndex(window.view_mode_combo.findData("single"))
                    self.assertIsNone(window.axes_gradient_secondary)
                    if use_preview:
                        self.assertEqual(len(window._screen_preview.consumer.gradient_layers), 1)
                finally:
                    window.project.dirty = False
                    window.close()

    def test_split_gradient_matplotlib_overlay_pointer_roles(self):
        window = self.make_window()
        try:
            window.view_mode_combo.setCurrentIndex(window.view_mode_combo.findData("split_y_axes"))
            window.pointer_action.setChecked(True)
            for role, selected in (("y1", 1), ("y2", 0)):
                # Selection can be on the opposite panel; the clicked panel wins.
                window.dataset_table.selectRow(selected)
                window.project.datasets[selected].measurement.gradient = deepcopy(
                    window.project.datasets[0].measurement.gradient)
                window._plot()
                panel = window.axes if role == "y1" else window.axes_right
                gradient = window.axes_gradient if role == "y1" else window.axes_gradient_secondary
                window.canvas.draw()
                x, y = panel.transData.transform((12.0, sum(panel.get_ylim()) / 2.0))
                event = SimpleNamespace(button=1, inaxes=gradient, x=float(x), y=float(y))
                normalized = window._normalized_pointer_event(event)
                self.assertEqual(normalized.axis_role, role)
                window._on_canvas_press(event)
                self.assertEqual(window.project.vertical_markers[-1].y_axis, 1 if role == "y1" else 2)
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_vertical_markers_use_native_events_and_shared_history(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        from hplc_app.project_io import load_project, save_project
        window = self.make_window()
        try:
            window.show()
            self.app.processEvents()
            raw = [dataset.intensity_uv.copy() for dataset in window.project.datasets]
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            consumer = preview.consumer
            core, gui = consumer.qt_core, consumer.qt_gui
            viewport = consumer.widget.viewport()
            window.pointer_action.setChecked(True)
            self.assertIs(window._screen_preview, preview)

            def point_at(time):
                limits = consumer.primary.viewRange()[1]
                return consumer.widget.mapFromScene(consumer.primary.vb.mapViewToScene(
                    core.QPointF(time, sum(limits) / 2.0)))

            def mouse(kind, point, button=core.Qt.MouseButton.NoButton,
                      held=core.Qt.MouseButton.NoButton):
                consumer.application.sendEvent(viewport, gui.QMouseEvent(
                    kind, core.QPointF(point), core.QPointF(viewport.mapToGlobal(point)),
                    button, held, core.Qt.KeyboardModifier.NoModifier,
                ))

            def click(point):
                mouse(core.QEvent.Type.MouseButtonPress, point,
                      core.Qt.MouseButton.LeftButton, core.Qt.MouseButton.LeftButton)
                mouse(core.QEvent.Type.MouseButtonRelease, point, core.Qt.MouseButton.LeftButton)

            point = point_at(12.0)
            expected_x = consumer.pointer_event(consumer.widget.mapToScene(point)).data_for("y1")[0]
            state = window._screen_view_state()
            with patch.object(window.canvas, "draw_idle") as mpl_draw:
                mouse(core.QEvent.Type.MouseMove, point)
                self.assertTrue(consumer.pointer_cursor.isVisible())
                self.assertAlmostEqual(consumer.pointer_cursor.value(), expected_x)
                mpl_draw.assert_not_called()
            self.assertEqual(window._screen_view_state(), state)
            click(point)
            self.assertEqual(len(window.project.vertical_markers), 1)
            first = window.project.vertical_markers[0]
            self.assertAlmostEqual(first.x_min, expected_x)
            self.assertEqual(first.y_axis, 1)
            self.assertEqual(window._selected_vertical_marker_id, first.id)
            self.assertEqual(consumer.marker_items[first.id].pen.color().name(), "#f59e0b")
            window.dataset_table.selectRow(1)
            click(point_at(30.0))
            self.assertEqual(len(window.project.vertical_markers), 2)
            second = window.project.vertical_markers[1]
            self.assertEqual(second.y_axis, 2)
            self.assertIn(consumer.marker_items[second.id], consumer.secondary.addedItems)
            window.project.dirty = False
            undo_count = len(window._undo_stack)
            # Pixel tolerance remains usable without creating another line.
            window._zoom_view(0.5, 20.0, zoom_mode="x")
            near = consumer.widget.mapToScene(point_at(first.x_min) + core.QPoint(5, 0))
            far = consumer.widget.mapToScene(point_at(first.x_min) + core.QPoint(9, 0))
            self.assertEqual(consumer.pointer_event(near).hit_id, first.id)
            self.assertEqual(consumer.pointer_event(far).hit_id, "")
            click(point_at(first.x_min) + core.QPoint(5, 0))
            self.assertEqual(len(window.project.vertical_markers), 2)
            self.assertEqual(window._selected_vertical_marker_id, first.id)
            self.assertEqual(len(window._undo_stack), undo_count)
            self.assertFalse(window.project.dirty)
            self.assertEqual(consumer.marker_items[second.id].pen.color().name(), "#f59e0b")
            consumer.application.sendEvent(consumer.widget, gui.QKeyEvent(
                core.QEvent.Type.KeyPress, core.Qt.Key.Key_Delete,
                core.Qt.KeyboardModifier.NoModifier,
            ))
            self.assertEqual(window.project.vertical_markers, [])
            self.assertNotIn(first.id, consumer.marker_items)
            self.assertNotIn(second.id, consumer.marker_items)
            self.assertIs(window._screen_preview, preview)
            window.undo()
            self.assertIn(first.id, consumer.marker_items)
            self.assertIn(second.id, consumer.marker_items)
            window.redo()
            self.assertNotIn(first.id, consumer.marker_items)
            self.assertNotIn(second.id, consumer.marker_items)
            window.undo()
            with tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / "native-markers.hplcproj")
                save_project(path, window.project)
                restored = load_project(path)
                self.assertEqual([(m.id, m.x_min, m.y_axis) for m in restored.vertical_markers],
                                 [(m.id, m.x_min, m.y_axis) for m in window.project.vertical_markers])
            for dataset, values in zip(window.project.datasets, raw):
                np.testing.assert_array_equal(dataset.intensity_uv, values)
            self.assertFalse(window._current_view_pixmap().isNull())
            window.pointer_action.setChecked(False)
            self.assertFalse(consumer.pointer_cursor.isVisible())
            # Existing markers stay selectable when placement mode is off.
            click(point_at(first.x_min))
            self.assertEqual(window._selected_vertical_marker_id, first.id)
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_vertical_marker_label_follows_drag_and_undo(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            marker = VerticalMarker(x_min=6.0)
            window.project.vertical_markers = [marker]
            window._plot()
            window.screen_preview_checkbox.setChecked(True)
            window.mouse_mode_combo.setCurrentIndex(
                window.mouse_mode_combo.findData("select")
            )
            preview = window._screen_preview
            item = preview.consumer.marker_items[marker.id]
            self.assertEqual(item.label.toPlainText(), "6 min")
            undo_count = len(window._undo_stack)
            window.project.dirty = False

            preview.handle_event(
                "button_press_event",
                ScreenPointerEvent(
                    button=1,
                    axis_role="y1",
                    hit_region="plot",
                    canvas_x=20.0,
                    data_coordinates=(("y1", 6.0, 0.0),),
                    hit_kind="vertical_marker",
                    hit_id=marker.id,
                ),
            )
            preview.handle_event(
                "motion_notify_event",
                ScreenPointerEvent(
                    button=1,
                    axis_role="y1",
                    hit_region="plot",
                    canvas_x=40.0,
                    data_coordinates=(("y1", 8.5, 0.0),),
                ),
            )
            self.assertEqual(marker.x_min, 8.5)
            item = preview.consumer.marker_items[marker.id]
            self.assertEqual(item.label.toPlainText(), "8.5 min")
            preview.handle_event(
                "button_release_event",
                ScreenPointerEvent(
                    button=1,
                    axis_role="y1",
                    hit_region="plot",
                    canvas_x=40.0,
                    data_coordinates=(("y1", 8.5, 0.0),),
                ),
            )
            self.assertEqual(len(window._undo_stack), undo_count + 1)
            self.assertTrue(window.project.dirty)
            window.undo()
            restored = window.project.vertical_markers[0]
            self.assertEqual(restored.x_min, 6.0)
            self.assertEqual(
                preview.consumer.marker_items[restored.id].label.toPlainText(),
                "6 min",
            )
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_pointer_boundaries_focus_and_failure_cleanup(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.pointer_action.setChecked(True)
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            self.assertIsNotNone(preview)
            consumer = preview.consumer
            # Axis margins and overview clicks are navigation, not placement.
            for role, region in (("outside", ""), ("y1", "x"), ("overview_y1", "x")):
                preview.handle_event("button_press_event", ScreenPointerEvent(
                    button=1, axis_role=role, hit_region=region,
                    data_coordinates=(("y1", 10.0, 0.0), ("overview_y1", 10.0, 0.0)),
                ))
            self.assertEqual(window.project.vertical_markers, [])
            consumer.set_pointer_cursor(10.0)
            self.app.sendEvent(consumer.widget.viewport(), QtCore.QEvent(QtCore.QEvent.Type.Leave))
            self.assertFalse(consumer.pointer_cursor.isVisible())
            window.project.vertical_markers = [VerticalMarker(x_min=10.0)]
            window._plot()
            window._select_vertical_marker(window.project.vertical_markers[0])
            key = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, QtCore.Qt.Key.Key_Delete,
                                 QtCore.Qt.KeyboardModifier.NoModifier)
            with patch.object(window, "delete_selected_plot_items", side_effect=RuntimeError("key failure")):
                self.app.sendEvent(consumer.widget, key)
            self.assertIsNone(window._screen_preview)
            self.assertTrue(consumer._closed)
            self.assertEqual(len(window.project.vertical_markers), 1)
            self.assertEqual(window.plot_stack.count(), 1)
            window.screen_preview_checkbox.setChecked(True)
            consumer = window._screen_preview.consumer
            with patch.object(consumer, "set_pointer_cursor", side_effect=RuntimeError("cursor failure")):
                window.pointer_action.setChecked(False)
            self.assertIsNone(window._screen_preview)
            self.assertTrue(consumer._closed)
        finally:
            window.project.dirty = False
            window.close()

    def test_lightweight_screen_decimates_visible_lines_only(self):
        window = self.make_lightweight_window()
        first, second = window.project.datasets
        first_line = window._dataset_lines[first.id]
        self.assertEqual(window._render_quality, LIGHTWEIGHT)
        self.assertLess(len(first_line.get_xdata()), first.time_min.size)
        self.assertLessEqual(len(first_line.get_xdata()), 5000)
        self.assertFalse(first_line.get_antialiased())
        self.assertIsNotNone(window.axes_right)
        self.assertIsNotNone(window.axes_gradient)
        self.assertIn(first.peaks[0].id, window._peak_overlay_artists)

        second.visible = False
        window._plot()
        self.assertNotIn(second.id, window._dataset_lines)
        self.assertNotIn(second.id, window._plot_source_cache)

        window.axes.set_xlim(4.0, 12.0)
        window._refresh_screen_series_for_view()
        visible_x = np.asarray(window._dataset_lines[first.id].get_xdata())
        self.assertLessEqual(float(visible_x[0]), 4.0)
        self.assertGreaterEqual(float(visible_x[-1]), 12.0)
        self.assertEqual(window.axes.get_xlim(), (4.0, 12.0))
        window.project.dirty = False
        window.close()

    def test_optional_pyqtgraph_consumer_renders_production_scene_snapshot(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional PyQtGraph dependency is not installed")
        window = self.make_window()
        window.project.datasets[0].line_style = "dashed"
        window.project.method.show_integration_areas = True
        window.project.method.show_retention_labels = True
        window.project.vertical_markers.append(
            VerticalMarker(x_min=12.0, y_axis=2)
        )
        window.project.fraction_regions.append(
            FractionRegion(start_min=15.0, end_min=18.0, interval_min=1.0)
        )
        window.project.annotations.append(
            TextAnnotation(
                text="Scene",
                x_min=20.0,
                y_value=0.02,
                dataset_id=window.project.datasets[1].id,
                y_axis=2,
            )
        )
        window._plot()
        source_before = [trace.x_values.copy() for trace in window._screen_scene.traces]
        consumer = PyQtGraphSceneConsumer(size=(800, 500))
        try:
            evidence = consumer.render(window._screen_scene)
            view_evidence = consumer.apply_view_state(
                ScreenViewState(
                    x=(4.0, 22.0),
                    y1=(-0.1, 0.2),
                    y2=(-0.3, 0.4),
                    gradient=(10.0, 80.0),
                ),
                compose_overview_state(
                    enabled=True,
                    full_x=(0.0, 60.0),
                    detail_x=(4.0, 22.0),
                ),
            )
            pixmap = consumer.snapshot()
            left_button = getattr(
                getattr(consumer.qt_core.Qt, "MouseButton", consumer.qt_core.Qt),
                "LeftButton",
            )
            plot_event = consumer.pointer_event(
                consumer.primary.vb.sceneBoundingRect().center(),
                button=left_button,
                double_click=True,
                key="ctrl",
            )
            self.assertEqual(evidence["counts"]["traces"], 2)
            dash_style = getattr(
                getattr(consumer.qt_core.Qt, "PenStyle", consumer.qt_core.Qt),
                "DashLine",
            )
            self.assertEqual(
                consumer.trace_items[window.project.datasets[0].id]
                .opts["pen"]
                .style(),
                dash_style,
            )
            self.assertEqual(evidence["counts"]["gradients"], 1)
            self.assertEqual(evidence["counts"]["peak_overlays"], 1)
            self.assertEqual(evidence["counts"]["vertical_markers"], 1)
            self.assertEqual(evidence["counts"]["fraction_regions"], 1)
            self.assertEqual(evidence["counts"]["text_annotations"], 1)
            self.assertTrue(evidence["shared_x"])
            self.assertAlmostEqual(evidence["gradient_range"][0], 0.0, places=2)
            self.assertAlmostEqual(evidence["gradient_range"][1], 100.0, places=2)
            self.assertEqual(view_evidence["x"], (4.0, 22.0))
            self.assertEqual(view_evidence["y1"], (-0.1, 0.2))
            self.assertEqual(view_evidence["y2"], (-0.3, 0.4))
            self.assertEqual(view_evidence["gradient"], (10.0, 80.0))
            self.assertTrue(view_evidence["overview_enabled"])
            self.assertEqual(view_evidence["overview_full_x"], (0.0, 60.0))
            self.assertEqual(view_evidence["overview_detail_x"], (4.0, 22.0))
            self.assertFalse(pixmap.isNull())
            self.assertGreater(pixmap.width(), 0)
            self.assertIsInstance(plot_event, ScreenPointerEvent)
            self.assertEqual(plot_event.button, 1)
            self.assertEqual(plot_event.axis_role, "y1")
            self.assertEqual(plot_event.hit_region, "plot")
            self.assertTrue(plot_event.double_click)
            self.assertEqual(plot_event.key, "ctrl")
            self.assertIsNotNone(plot_event.data_for("y1")[0])
            self.assertIsNotNone(plot_event.data_for("y2")[1])
            self.assertIsNotNone(plot_event.data_for("gradient")[1])
            overview_event = consumer.pointer_event(
                consumer.overview.vb.sceneBoundingRect().center()
            )
            self.assertEqual(overview_event.axis_role, "overview_y1")
            self.assertEqual(overview_event.hit_region, "x")
            for axis, role, region in (
                (consumer.primary.getAxis("bottom"), "y1", "x"),
                (consumer.primary.getAxis("left"), "y1", "y1"),
                (consumer.primary.getAxis("right"), "y2", "y2"),
                (consumer.gradient_axis, "gradient", "gradient"),
            ):
                axis_event = consumer.pointer_event(
                    axis.sceneBoundingRect().center()
                )
                self.assertEqual(axis_event.axis_role, role)
                self.assertEqual(axis_event.hit_region, region)
            hidden_overview = consumer.apply_view_state(
                ScreenViewState(x=(4.0, 22.0), y1=(-0.1, 0.2)),
                compose_overview_state(
                    enabled=False,
                    full_x=(0.0, 60.0),
                    detail_x=(4.0, 22.0),
                ),
            )
            self.assertFalse(hidden_overview["overview_enabled"])

            detail_traces = dict(consumer.trace_items)
            overview_traces = dict(consumer.overview_trace_items)
            old_marker = next(iter(consumer.marker_items.values()))
            old_annotation = next(iter(consumer.annotation_items.values()))
            old_items = tuple(consumer.items)
            item_count = len(consumer.items)
            window._plot()
            stable = consumer.render(window._screen_scene)
            self.assertTrue(stable["reused_traces"])
            self.assertTrue(stable["reused_static"])
            self.assertEqual(tuple(consumer.items), old_items)
            self.assertIs(
                next(iter(consumer.marker_items.values())), old_marker
            )
            self.assertIs(
                next(iter(consumer.annotation_items.values())), old_annotation
            )

            window.project.datasets[0].offset += 0.5
            window.project.vertical_markers[0].x_min = 13.0
            window.project.annotations[0].text = "Updated scene"
            window._plot()
            refreshed = consumer.render(window._screen_scene)
            self.assertTrue(refreshed["reused_traces"])
            self.assertFalse(refreshed["reused_static"])
            self.assertEqual(len(consumer.items), item_count)
            for dataset_id, item in detail_traces.items():
                self.assertIs(consumer.trace_items[dataset_id], item)
                self.assertIs(
                    consumer.overview_trace_items[dataset_id],
                    overview_traces[dataset_id],
                )
            self.assertIsNot(
                next(iter(consumer.marker_items.values())), old_marker
            )
            self.assertIsNot(
                next(iter(consumer.annotation_items.values())), old_annotation
            )

            window.project.method.show_gradient_b = False
            window.project.method.show_integration_areas = False
            window.project.method.show_retention_labels = False
            window.project.vertical_markers.clear()
            window.project.fraction_regions.clear()
            window.project.annotations.clear()
            window._plot()
            cleared = consumer.render(window._screen_scene)
            self.assertTrue(cleared["reused_traces"])
            self.assertEqual(cleared["counts"]["gradients"], 0)
            self.assertEqual(cleared["counts"]["peak_overlays"], 0)
            self.assertEqual(cleared["counts"]["vertical_markers"], 0)
            self.assertEqual(cleared["counts"]["fraction_regions"], 0)
            self.assertEqual(cleared["counts"]["text_annotations"], 0)
            self.assertEqual(
                sum(item.isVisible() for item in consumer.items),
                len(detail_traces),
            )
            self.assertEqual(consumer.marker_items, {})
            self.assertEqual(consumer.annotation_items, {})
            for item in detail_traces.values():
                self.assertIn(item, consumer.items)
            for trace, original in zip(window._screen_scene.traces, source_before):
                self.assertTrue(np.array_equal(trace.x_values, original))
        finally:
            consumer.close()
            window.project.dirty = False
            window.close()

    def test_pyqtgraph_reuses_high_density_trace_items_without_touching_raw_data(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional PyQtGraph dependency is not installed")
        window = self.make_window()
        window.project.method.show_gradient_b = False
        window.project.method.show_integration_areas = False
        window.project.method.show_retention_labels = False
        time_values = np.linspace(0.0, 30.0, 12000)
        for index, dataset in enumerate(window.project.datasets):
            dataset.time_min = time_values.copy()
            dataset.intensity_uv = np.sin(time_values + index).astype(float)
        raw = [dataset.intensity_uv.copy() for dataset in window.project.datasets]
        window._plot()
        consumer = PyQtGraphSceneConsumer(size=(800, 500))
        try:
            first = consumer.render(window._screen_scene)
            detail_items = dict(consumer.trace_items)
            overview_items = dict(consumer.overview_trace_items)
            self.assertFalse(first["reused_traces"])
            self.assertLess(
                first["rendered_trace_points"], first["source_trace_points"]
            )
            self.assertLessEqual(
                first["rendered_trace_points"], 5000 * len(raw)
            )

            window.project.datasets[0].offset = 2.5
            window.project.datasets[0].color = "#123456"
            window.project.datasets[0].label = "Updated benchmark"
            window.project.method.line_width = 2.25
            window._plot()
            second = consumer.render(window._screen_scene)
            self.assertTrue(second["reused_traces"])
            self.assertEqual(
                set(consumer.trace_items), set(detail_items)
            )
            for dataset_id in detail_items:
                self.assertIs(consumer.trace_items[dataset_id], detail_items[dataset_id])
                self.assertIs(
                    consumer.overview_trace_items[dataset_id],
                    overview_items[dataset_id],
                )
            self.assertAlmostEqual(
                float(np.max(consumer.trace_items[window.project.datasets[0].id].getData()[1])),
                float(np.max(window._screen_scene.traces[0].y_values)),
            )
            updated_item = consumer.trace_items[window.project.datasets[0].id]
            self.assertEqual(updated_item.name(), window._screen_scene.traces[0].label)
            self.assertEqual(updated_item.opts["pen"].color().name(), "#123456")
            self.assertAlmostEqual(updated_item.opts["pen"].widthF(), 2.25)

            window.project.datasets[1].visible = False
            window._plot()
            rebuilt = consumer.render(window._screen_scene)
            self.assertFalse(rebuilt["reused_traces"])
            self.assertEqual(len(consumer.trace_items), 1)
            self.assertEqual(len(consumer.overview_trace_items), 1)
            self.assertEqual(len(consumer.items), 1)
            self.assertEqual(len(consumer.overview_items), 1)
            for dataset, values in zip(window.project.datasets, raw):
                np.testing.assert_array_equal(dataset.intensity_uv, values)
        finally:
            consumer.close()
            window.project.dirty = False
            window.close()

    def test_integrated_benchmark_discovers_and_loads_real_inputs(self):
        from scripts.benchmark_integrated_screen import (
            _discover_inputs,
            _project_digest,
            _project_from_inputs,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "valid.TXT"
            invalid = root / "notes.txt"
            valid.write_bytes((SAMPLES / "210601.TXT").read_bytes())
            invalid.write_text("not a chromatogram", encoding="utf-8")
            discovered = _discover_inputs(
                [root, valid], recursive=False
            )
            self.assertEqual(
                [path.name for path in discovered],
                ["notes.txt", "valid.TXT"],
            )
            project, loaded, errors = _project_from_inputs(
                [root], decorated=True
            )
            self.assertEqual([path.name for path in loaded], ["valid.TXT"])
            self.assertEqual([item["file"] for item in errors], ["notes.txt"])
            self.assertEqual(len(project.datasets), 1)
            digest = _project_digest(project)
            self.assertEqual(digest, _project_digest(project))
            self.assertTrue(project.method.show_gradient_b)
            self.assertEqual(len(project.vertical_markers), 1)
            with self.assertRaisesRegex(ValueError, "No benchmark input could be loaded"):
                _project_from_inputs([invalid])

    def test_pyqtgraph_viewport_dispatches_and_disconnects_shared_events(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional PyQtGraph dependency is not installed")
        consumer = PyQtGraphSceneConsumer(size=(800, 500))
        try:
            consumer.snapshot()
            core, gui = consumer.qt_core, consumer.qt_gui
            types = getattr(core.QEvent, "Type", core.QEvent)
            buttons = getattr(core.Qt, "MouseButton", core.Qt)
            modifiers = getattr(core.Qt, "KeyboardModifier", core.Qt)
            phases = getattr(core.Qt, "ScrollPhase", core.Qt)
            viewport = consumer.widget.viewport()
            position = consumer.widget.mapFromScene(
                consumer.primary.vb.sceneBoundingRect().center()
            )
            local = core.QPointF(position)
            global_position = core.QPointF(viewport.mapToGlobal(position))
            received = {name: [] for name in consumer.POINTER_EVENTS}
            connections = {
                name: consumer.connect_event(name, events.append)
                for name, events in received.items()
            }

            def send_mouse(kind, button, held):
                event = gui.QMouseEvent(
                    kind, local, global_position, button, held,
                    modifiers.ControlModifier | modifiers.ShiftModifier,
                )
                consumer.application.sendEvent(viewport, event)

            send_mouse(types.MouseButtonPress, buttons.LeftButton, buttons.LeftButton)
            send_mouse(types.MouseMove, buttons.NoButton, buttons.LeftButton)
            send_mouse(types.MouseButtonRelease, buttons.LeftButton, buttons.NoButton)
            send_mouse(types.MouseButtonDblClick, buttons.MiddleButton, buttons.MiddleButton)
            presses = received["button_press_event"]
            self.assertEqual([event.button for event in presses], [1, 2])
            self.assertFalse(presses[0].double_click)
            self.assertTrue(presses[1].double_click)
            self.assertEqual(presses[0].key, "ctrl+shift")
            self.assertEqual(presses[0].hit_region, "plot")
            self.assertEqual(received["motion_notify_event"][0].button, 1)
            self.assertEqual(received["button_release_event"][0].button, 1)
            self.assertIsInstance(presses[0], ScreenPointerEvent)
            send_mouse(types.MouseButtonPress, buttons.RightButton, buttons.RightButton)
            self.assertEqual(presses[-1].button, 3)

            for angle, pixel in ((120, 0), (-120, 0), (0, 15), (0, 0)):
                event = gui.QWheelEvent(
                    local, global_position, core.QPoint(0, pixel),
                    core.QPoint(0, angle), buttons.NoButton,
                    modifiers.AltModifier, phases.NoScrollPhase, False,
                )
                consumer.application.sendEvent(viewport, event)
            self.assertEqual(
                [event.button for event in received["scroll_event"]],
                ["up", "down", "up"],
            )
            self.assertEqual(received["scroll_event"][0].key, "alt")
            # Filters observe events without consuming native Qt handling.
            self.assertFalse(consumer._pointer_filter.eventFilter(
                viewport, core.QEvent(types.User)
            ))
            consumer.disconnect_event(connections["button_press_event"])
            consumer.disconnect_event(connections["button_press_event"])
            send_mouse(types.MouseButtonPress, buttons.RightButton, buttons.RightButton)
            self.assertEqual(len(presses), 3)
            with self.assertRaises(ValueError):
                consumer.connect_event("unknown_event", presses.append)
            with self.assertRaises(TypeError):
                consumer.connect_event("scroll_event", None)
            consumer.close()
            self.assertEqual(consumer._connections, {})
            with self.assertRaises(RuntimeError):
                consumer.connect_event("scroll_event", presses.append)
        finally:
            consumer.close()

    def test_pyqtgraph_navigation_owns_zoom_pan_overview_and_history(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional PyQtGraph dependency is not installed")
        consumer = PyQtGraphSceneConsumer(size=(800, 500))
        navigation = None
        try:
            initial = ScreenViewState(
                x=(20.0, 80.0), y1=(0.0, 100.0),
                y2=(0.0, 1000.0), gradient=(10.0, 90.0),
            )
            consumer.apply_view_state(initial, compose_overview_state(
                False, (0.0, 100.0), initial.x
            ))
            consumer.snapshot()
            navigation = PyQtGraphNavigationController(consumer)
            core, gui = consumer.qt_core, consumer.qt_gui
            types = core.QEvent.Type
            buttons, modifiers = core.Qt.MouseButton, core.Qt.KeyboardModifier
            viewport = consumer.widget.viewport()

            def send_mouse(kind, position, button, held):
                event = gui.QMouseEvent(
                    kind, core.QPointF(position),
                    core.QPointF(viewport.mapToGlobal(position)),
                    button, held, modifiers.NoModifier,
                )
                consumer.application.sendEvent(viewport, event)

            observed = []
            consumer.connect_event("scroll_event", observed.append)
            position = consumer.widget.mapFromScene(
                consumer.primary.getAxis("right").sceneBoundingRect().center()
            )
            wheel = gui.QWheelEvent(
                core.QPointF(position), core.QPointF(viewport.mapToGlobal(position)),
                core.QPoint(), core.QPoint(0, 120), buttons.NoButton,
                modifiers.NoModifier, core.Qt.ScrollPhase.NoScrollPhase, False,
            )
            consumer.application.sendEvent(viewport, wheel)
            zoomed = consumer.capture_view_state()
            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0].hit_region, "y2")
            self.assertEqual(zoomed.x, initial.x)
            self.assertEqual(zoomed.y1, initial.y1)
            self.assertEqual(zoomed.gradient, initial.gradient)
            self.assertAlmostEqual(zoomed.y2[1] - zoomed.y2[0], 800.0)
            self.assertTrue(navigation.capabilities()["back"])
            navigation.navigate("back")
            self.assertEqual(consumer.capture_view_state(), initial)
            navigation.navigate("forward")
            self.assertEqual(consumer.capture_view_state(), zoomed)
            navigation.navigate("home")
            navigation.set_pan_enabled(True)
            rectangle = consumer.primary.vb.sceneBoundingRect()
            position = consumer.widget.mapFromScene(rectangle.center())
            moved = position + core.QPoint(20, 30)
            send_mouse(types.MouseButtonPress, position, buttons.LeftButton, buttons.LeftButton)
            consumer.application.sendEvent(viewport, wheel)
            self.assertEqual(consumer.capture_view_state(), initial)
            send_mouse(types.MouseMove, moved, buttons.NoButton, buttons.LeftButton)
            send_mouse(types.MouseButtonRelease, moved, buttons.LeftButton, buttons.NoButton)
            panned = consumer.capture_view_state()
            self.assertAlmostEqual(panned.x[0], 20.0 - 20.0 * 60.0 / rectangle.width())
            self.assertAlmostEqual(panned.y1[0], 30.0 * 100.0 / rectangle.height())
            self.assertAlmostEqual(panned.y2[0], 30.0 * 1000.0 / rectangle.height())
            self.assertEqual(panned.gradient, initial.gradient)
            navigation.navigate("back")
            self.assertEqual(consumer.capture_view_state(), initial)
            navigation.navigate("forward")
            self.assertEqual(consumer.capture_view_state(), panned)
            # Unimplemented native right-drag must not change our owned ranges.
            send_mouse(types.MouseButtonPress, position, buttons.RightButton, buttons.RightButton)
            send_mouse(types.MouseMove, moved, buttons.NoButton, buttons.RightButton)
            send_mouse(types.MouseButtonRelease, moved, buttons.RightButton, buttons.NoButton)
            self.assertEqual(consumer.capture_view_state(), panned)
            consumer.apply_view_state(initial, compose_overview_state(
                True, (0.0, 100.0), initial.x
            ))
            overview_rect = consumer.overview_region.rect()
            self.assertEqual(
                (overview_rect.top(), overview_rect.bottom()),
                (initial.y1[0], initial.y1[1]),
            )
            consumer.snapshot()
            navigation.reset_history()
            point = consumer.overview.vb.mapViewToScene(core.QPointF(95.0, 0.5))
            position = consumer.widget.mapFromScene(point)
            send_mouse(types.MouseButtonPress, position, buttons.LeftButton, buttons.LeftButton)
            send_mouse(types.MouseButtonRelease, position, buttons.LeftButton, buttons.NoButton)
            self.assertEqual(consumer.capture_view_state().x, initial.x)
            overview_rect = consumer.overview_region.rect()
            self.assertEqual(
                (overview_rect.left(), overview_rect.right()), initial.x
            )
            navigation.navigate("back")
            self.assertEqual(consumer.capture_view_state(), initial)
            # Zooming out beyond an overview's full extent is a no-op and must
            # retain the forward entry instead of truncating navigation history.
            full = ScreenViewState(
                x=(0.0, 100.0), y1=initial.y1,
                y2=initial.y2, gradient=initial.gradient,
            )
            consumer.apply_view_state(full, compose_overview_state(True, full.x, full.x))
            navigation.reset_history()
            navigation.handle_event("scroll_event", ScreenPointerEvent(
                button="up", axis_role="y1", hit_region="x",
            ))
            navigation.navigate("back")
            self.assertTrue(navigation.capabilities()["forward"])
            navigation.handle_event("scroll_event", ScreenPointerEvent(
                button="down", axis_role="y1", hit_region="x",
            ))
            self.assertEqual(consumer.capture_view_state().x, (-12.5, 112.5))
            self.assertFalse(navigation.capabilities()["forward"])
            navigation.navigate("back")
            self.assertEqual(consumer.capture_view_state(), full)
            self.assertTrue(navigation.capabilities()["forward"])
            with self.assertRaises(RuntimeError):
                PyQtGraphNavigationController(consumer)
            navigation.close()
            navigation.close()
            self.assertIsNone(consumer._pointer_handler)
            self.assertFalse(consumer._dispatch_viewport_event(wheel))
        finally:
            if navigation is not None:
                navigation.close()
            consumer.close()

    def test_pyqtgraph_pan_ends_on_any_release_and_apply_failure(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional PyQtGraph dependency is not installed")
        consumer = PyQtGraphSceneConsumer(size=(800, 500))
        navigation = None
        try:
            initial = ScreenViewState(
                x=(0.0, 100.0), y1=(0.0, 1000.0),
                y2=None, gradient=None,
            )
            consumer.apply_view_state(
                initial, compose_overview_state(False, initial.x, initial.x)
            )
            consumer.snapshot()
            navigation = PyQtGraphNavigationController(consumer)
            navigation.set_pan_enabled(True)
            press = ScreenPointerEvent(
                button=1, axis_role="y1", hit_region="plot",
                canvas_x=200.0, canvas_y=200.0,
            )
            motion = ScreenPointerEvent(
                button=1, axis_role="y1", hit_region="plot",
                canvas_x=225.0, canvas_y=230.0,
            )
            other_button_release = ScreenPointerEvent(
                button=3, axis_role="y1", hit_region="plot",
                canvas_x=225.0, canvas_y=230.0,
            )
            navigation.handle_event("button_press_event", press)
            navigation.handle_event("motion_notify_event", motion)
            navigation.handle_event(
                "button_release_event", other_button_release
            )
            released = consumer.capture_view_state()
            self.assertIsNone(navigation._pan)
            navigation.handle_event(
                "motion_notify_event",
                ScreenPointerEvent(
                    button=1, axis_role="y1", hit_region="plot",
                    canvas_x=300.0, canvas_y=300.0,
                ),
            )
            self.assertEqual(consumer.capture_view_state(), released)

            consumer.apply_view_state(
                initial, compose_overview_state(False, initial.x, initial.x)
            )
            navigation.reset_history()
            navigation.handle_event("button_press_event", press)
            with patch.object(
                consumer, "apply_view_state", side_effect=RuntimeError("apply")
            ):
                with self.assertRaises(RuntimeError):
                    navigation.handle_event("motion_notify_event", motion)
            self.assertIsNone(navigation._pan)
        finally:
            if navigation is not None:
                navigation.close()
            consumer.close()

    def test_pyqtgraph_pan_ends_on_buttonless_motion_and_outside_release(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional PyQtGraph dependency is not installed")
        consumer = PyQtGraphSceneConsumer(size=(800, 500))
        navigation = None
        try:
            initial = ScreenViewState(
                x=(0.0, 100.0), y1=(0.0, 1000.0),
                y2=None, gradient=None,
            )

            def restart():
                consumer.apply_view_state(
                    initial, compose_overview_state(False, initial.x, initial.x)
                )
                navigation.reset_history()

            consumer.apply_view_state(
                initial, compose_overview_state(False, initial.x, initial.x)
            )
            consumer.snapshot()
            navigation = PyQtGraphNavigationController(consumer)
            navigation.set_pan_enabled(True)
            press = ScreenPointerEvent(
                button=1, axis_role="y1", hit_region="plot",
                canvas_x=200.0, canvas_y=200.0,
            )
            # A fast drag leaves the plot before the button is released.
            far_motion = ScreenPointerEvent(
                button=1, axis_role="outside", hit_region="",
                canvas_x=760.0, canvas_y=470.0,
            )
            outside_release = ScreenPointerEvent(
                button=1, axis_role="outside", hit_region="",
                canvas_x=760.0, canvas_y=470.0,
            )
            later_motion = ScreenPointerEvent(
                button=1, axis_role="y1", hit_region="plot",
                canvas_x=300.0, canvas_y=300.0,
            )
            navigation.handle_event("button_press_event", press)
            navigation.handle_event("motion_notify_event", far_motion)
            self.assertIsNotNone(navigation._pan)
            navigation.handle_event("button_release_event", outside_release)
            self.assertIsNone(navigation._pan)
            released = consumer.capture_view_state()
            navigation.handle_event("motion_notify_event", later_motion)
            self.assertEqual(consumer.capture_view_state(), released)

            # A release delivered to another widget never reaches the handler, so
            # the next button-less motion has to end the gesture on its own.
            restart()
            navigation.handle_event("button_press_event", press)
            navigation.handle_event("motion_notify_event", far_motion)
            self.assertIsNotNone(navigation._pan)
            navigation.handle_event(
                "motion_notify_event",
                ScreenPointerEvent(
                    button=None, axis_role="y1", hit_region="plot",
                    canvas_x=400.0, canvas_y=250.0,
                ),
            )
            self.assertIsNone(navigation._pan)
            abandoned = consumer.capture_view_state()
            navigation.handle_event("motion_notify_event", later_motion)
            self.assertIsNone(navigation._pan)
            self.assertEqual(consumer.capture_view_state(), abandoned)

            # A button-less motion outside the plot must end it just as reliably.
            restart()
            navigation.handle_event("button_press_event", press)
            navigation.handle_event(
                "motion_notify_event",
                ScreenPointerEvent(
                    button=None, axis_role="outside", hit_region="",
                    canvas_x=790.0, canvas_y=490.0,
                ),
            )
            self.assertIsNone(navigation._pan)
        finally:
            if navigation is not None:
                navigation.close()
            consumer.close()

    def test_lightweight_overview_is_coarser_and_peak_selection_reuses_patch(self):
        window = self.make_lightweight_window()
        window.project.method.view_mode = "overview_detail"
        window._plot()
        dataset = window.project.datasets[0]
        main_count = len(window._dataset_lines[dataset.id].get_xdata())
        overview_count = len(window._overview_dataset_lines[dataset.id].get_xdata())
        self.assertLess(overview_count, main_count)
        self.assertLessEqual(overview_count, 2000)

        peak_id = dataset.peaks[0].id
        original_axes = window.axes
        original_patch = window._peak_overlay_artists[peak_id]["patch"]
        window.peak_table.selectRow(0)
        self.app.processEvents()
        self.assertIs(window.axes, original_axes)
        self.assertIs(window._peak_overlay_artists[peak_id]["patch"], original_patch)
        window.project.dirty = False
        window.close()

    def test_split_y_axis_mode_routes_traces_and_shares_x_navigation(self):
        window = self.make_window()
        window.project.method.view_mode = "split_y_axes"
        window._plot(preserve_view=False)
        first, second = window.project.datasets
        self.assertTrue(window._split_y_axes)
        self.assertIs(window._dataset_lines[first.id].axes, window.axes)
        self.assertIs(window._dataset_lines[second.id].axes, window.axes_right)
        self.assertTrue(
            window.axes.get_shared_x_axes().joined(window.axes, window.axes_right)
        )
        window.axes_right.set_xlim(4.0, 12.0)
        self.assertEqual(window.axes.get_xlim(), (4.0, 12.0))

        window.canvas.draw()
        bbox = window.axes_right.bbox
        event = SimpleNamespace(
            button="up",
            x=float((bbox.x0 + bbox.x1) / 2.0),
            y=float((bbox.y0 + bbox.y1) / 2.0),
            xdata=8.0,
            ydata=0.0,
            inaxes=window.axes_right,
        )
        self.assertEqual(window._scroll_target(event), "plot_y2")
        y1_before = window.axes.get_ylim()
        y2_before = window.axes_right.get_ylim()
        window._on_scroll(event)
        self.assertEqual(window.axes.get_ylim(), y1_before)
        self.assertLess(
            window.axes_right.get_ylim()[1] - window.axes_right.get_ylim()[0],
            y2_before[1] - y2_before[0],
        )
        window.project.dirty = False
        window.close()

    def test_render_quality_preference_is_app_only_and_user_switchable(self):
        window = self.make_lightweight_window()
        project_dirty = window.project.dirty
        dialog = PreferencesDialog(
            window.project.method,
            language="ja",
            render_quality=window._render_quality,
        )
        self.assertTrue(dialog.lightweight_radio.isChecked())
        self.assertIn(
            "元データ",
            " ".join(label.text() for label in dialog.findChildren(QtWidgets.QLabel)),
        )
        dialog.high_quality_radio.setChecked(True)
        dialog._accept()
        self.assertEqual(dialog.render_quality_value, HIGH_QUALITY)
        window._set_render_quality(dialog.render_quality_value)
        self.assertEqual(window._render_quality, HIGH_QUALITY)
        self.assertEqual(
            self.settings.value("rendering/quality"), HIGH_QUALITY
        )
        self.assertEqual(window.project.dirty, project_dirty)
        self.assertEqual(
            len(window._dataset_lines[window.project.datasets[0].id].get_xdata()),
            window.project.datasets[0].time_min.size,
        )
        self.assertEqual(
            window.screen_render_surface.capabilities.backend_id,
            "matplotlib_qt",
        )
        self.assertTrue(
            window.screen_render_surface.capabilities.supports_native_snapshot
        )
        self.assertIs(window.screen_render_surface.widget, window.canvas)
        window.project.dirty = False
        window.close()

    def test_update_preferences_and_result_presentation_are_non_mutating(self):
        window = self.make_window()
        dialog = PreferencesDialog(
            window.project.method,
            language="en",
            automatic_update_check=False,
        )
        self.assertFalse(dialog.automatic_update_checkbox.isChecked())
        dialog.automatic_update_checkbox.setChecked(True)
        dialog._accept()
        self.assertTrue(dialog.automatic_update_check_value)

        update = {
            "status": "update_available",
            "latest_version": "1.3.0",
            "release_url": "https://github.com/mshibagaki/HPLC_Analyzer/releases/tag/v1.3.0",
            "reason": "",
        }
        with patch.object(
            QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Yes
        ) as question, patch.object(QtGui.QDesktopServices, "openUrl") as open_url:
            window._present_update_check_result(update, manual=True)
        question.assert_called_once()
        open_url.assert_called_once()
        self.assertIn("github.com", open_url.call_args[0][0].toString())

        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
            window._present_update_check_result(
                {"status": "error", "reason": "offline"}, manual=False
            )
        warning.assert_not_called()
        window._update_check_thread = object()
        self.assertFalse(window.check_for_updates())
        window._update_check_thread = None
        current = {
            "status": "current",
            "latest_version": "1.2.4",
            "release_url": "https://github.com/mshibagaki/HPLC_Analyzer/releases/tag/v1.2.4",
            "reason": "",
        }
        with patch("hplc_app.gui.check_for_updates", return_value=current):
            self.assertTrue(window.check_for_updates(manual=False))
            elapsed = QtCore.QElapsedTimer()
            elapsed.start()
            while window._update_check_thread is not None and elapsed.elapsed() < 3000:
                QtWidgets.QApplication.processEvents()
        self.assertIsNone(window._update_check_thread)
        self.assertTrue(window.check_updates_action.isEnabled())
        window.project.dirty = False
        window.close()

    def test_update_download_worker_reports_progress_cancels_and_forbids_launch(self):
        progress = []
        finished = []

        def stage(**arguments):
            arguments["progress"]("manifest", 0, None)
            arguments["progress"]("installer", 5, 10)
            self.assertFalse(arguments["cancelled"]())
            return {"status": "verified", "reason": "", "launch_allowed": True}

        worker = UpdateDownloadWorker({"fixture": True}, stage=stage)
        worker.progress.connect(
            lambda asset, received, total: progress.append((asset, received, total))
        )
        worker.finished.connect(finished.append)
        worker.run()
        self.assertEqual(progress, [("manifest", 0, None), ("installer", 5, 10)])
        self.assertEqual(finished[0]["status"], "verified")
        self.assertFalse(finished[0]["launch_allowed"])

        canceled = []

        def canceled_stage(**arguments):
            canceled.append(arguments["cancelled"]())
            return {"status": "canceled", "reason": "Download canceled"}

        canceled_worker = UpdateDownloadWorker({}, stage=canceled_stage)
        canceled_worker.cancel()
        canceled_worker.finished.connect(finished.append)
        canceled_worker.run()
        self.assertEqual(canceled, [True])
        self.assertEqual(finished[-1]["status"], "canceled")
        self.assertFalse(finished[-1]["launch_allowed"])

    def test_update_download_dialog_presents_progress_cancel_and_safe_results(self):
        dialog = UpdateDownloadDialog(language="en")
        dialog.apply_progress("manifest", 12, None)
        self.assertEqual(dialog.progress_bar.minimum(), 0)
        self.assertEqual(dialog.progress_bar.maximum(), 0)
        self.assertIn("12", dialog.detail_label.text())
        dialog.apply_progress("installer", 50, 100)
        self.assertEqual(dialog.progress_bar.maximum(), 100)
        self.assertEqual(dialog.progress_bar.value(), 50)

        canceled = []
        dialog.cancel_requested.connect(lambda: canceled.append(True))
        worker = UpdateDownloadWorker({}, stage=lambda **_kwargs: {})
        dialog.bind_worker(worker)
        dialog.action_button.click()
        self.assertEqual(dialog.state, "canceling")
        self.assertEqual(canceled, [True])
        worker_cancel_state = []
        worker._stage = lambda **arguments: worker_cancel_state.append(
            arguments["cancelled"]()
        ) or {"status": "canceled"}
        worker.run()
        self.assertEqual(worker_cancel_state, [True])

        dialog.apply_result(
            {"status": "held", "reason": "Signer not approved", "launch_allowed": True}
        )
        self.assertEqual(dialog.state, "held")
        self.assertFalse(dialog.result["launch_allowed"])
        self.assertIn("disabled", dialog.phase_label.text())
        self.assertEqual(dialog.action_button.text(), "Close")
        dialog.close()

    def test_preset_manager_renames_duplicates_and_deletes_all_kinds(self):
        metadata = {
            "conditions": {
                "C18": {"id": "condition-id", "created_at": "", "updated_at": "", "last_used_at": ""}
            },
            "gradients": {
                "fast": {"id": "gradient-id", "created_at": "", "updated_at": "", "last_used_at": ""}
            },
            "analytes": {
                "LL-37": {"id": "analyte-id", "created_at": "", "updated_at": "", "last_used_at": ""}
            },
        }
        dialog = PresetManagerDialog(
            {"C18": {"column_name": "C18"}},
            {"fast": {"gradient": []}},
            metadata,
            "en",
            analytes={"LL-37": {"molecular_weight_g_mol": 4493.3}},
        )
        dialog.condition_list.setCurrentRow(0)
        with patch.object(
            QtWidgets.QInputDialog, "getText", return_value=("RP C18", True)
        ):
            dialog._rename_or_duplicate("rename")
        self.assertIn("RP C18", dialog.conditions)
        self.assertEqual(dialog.metadata["conditions"]["RP C18"]["id"], "condition-id")

        dialog.tabs.setCurrentIndex(1)
        dialog.gradient_list.setCurrentRow(0)
        with patch.object(
            QtWidgets.QInputDialog, "getText", return_value=("fast copy", True)
        ):
            dialog._rename_or_duplicate("duplicate")
        self.assertIn("fast copy", dialog.gradients)
        self.assertNotEqual(
            dialog.metadata["gradients"]["fast copy"]["id"], "gradient-id"
        )
        dialog.gradient_list.setCurrentRow(
            [dialog.gradient_list.item(row).text() for row in range(dialog.gradient_list.count())].index("fast copy")
        )
        with patch.object(
            QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Yes
        ):
            dialog._delete()
        self.assertNotIn("fast copy", dialog.gradients)

        dialog.tabs.setCurrentIndex(2)
        dialog.analyte_list.setCurrentRow(0)
        with patch.object(
            QtWidgets.QInputDialog, "getText", return_value=("LL-37 copy", True)
        ):
            dialog._rename_or_duplicate("duplicate")
        self.assertIn("LL-37 copy", dialog.analytes)
        self.assertNotEqual(
            dialog.metadata["analytes"]["LL-37 copy"]["id"], "analyte-id"
        )
        dialog.close()

    def test_preset_manager_exports_and_imports_with_explicit_conflict_choice(self):
        metadata = {
            "conditions": {"C18": {"id": "condition-id"}},
            "gradients": {},
        }
        dialog = PresetManagerDialog(
            {"C18": {"column_name": "old"}}, {}, metadata, "en"
        )
        dialog.condition_list.setCurrentRow(0)
        with tempfile.TemporaryDirectory() as directory:
            export_path = Path(directory) / "presets.json"
            with patch.object(
                QtWidgets.QInputDialog,
                "getItem",
                return_value=("Selected preset only", True),
            ), patch.object(
                QtWidgets.QFileDialog,
                "getSaveFileName",
                return_value=(str(export_path), "JSON (*.json)"),
            ):
                self.assertTrue(dialog._export_package())
            package = json.loads(export_path.read_text(encoding="utf-8"))
            self.assertEqual(set(package["presets"]["conditions"]), {"C18"})
            self.assertEqual(package["presets"]["gradients"], {})

            package["presets"]["conditions"]["C18"]["column_name"] = "new"
            export_path.write_text(json.dumps(package), encoding="utf-8")
            with patch.object(
                QtWidgets.QFileDialog,
                "getOpenFileName",
                return_value=(str(export_path), "JSON (*.json)"),
            ), patch.object(
                QtWidgets.QInputDialog,
                "getItem",
                return_value=("Replace", True),
            ):
                self.assertTrue(dialog._import_package())
        self.assertEqual(dialog.conditions["C18"]["column_name"], "new")
        dialog.close()

    def test_application_language_persists_without_dirtying_or_rewriting_project(self):
        class MemorySettings:
            def __init__(self):
                self.values = {}

            def value(self, key, default=None):
                return self.values.get(key, default)

            def setValue(self, key, value):
                self.values[key] = value

            def sync(self):
                return None

            def status(self):
                return 0

        backend = MemorySettings()
        settings_factory = lambda: ApplicationSettings(backend)
        with patch("hplc_app.gui.ApplicationSettings", side_effect=settings_factory):
            first = MainWindow()
            self.assertEqual(first._application_language, "ja")
            legacy_language = first.project.ui_language
            first.project.dirty = False
            first.set_language("en")
            self.assertEqual(first._application_language, "en")
            self.assertEqual(first.translator.language, "en")
            self.assertFalse(first.project.dirty)
            self.assertEqual(first.project.ui_language, legacy_language)
            first.close()

            second = MainWindow()
            self.assertEqual(second._application_language, "en")
            self.assertEqual(second.translator.language, "en")
            self.assertTrue(second.english_action.isChecked())
            second.project.dirty = False
            second.close()

    def test_opening_legacy_project_does_not_change_application_language(self):
        class MemorySettings:
            def __init__(self):
                self.values = {"ui/language": "en"}

            def value(self, key, default=None):
                return self.values.get(key, default)

            def setValue(self, key, value):
                self.values[key] = value

            def sync(self):
                return None

            def status(self):
                return 0

        with tempfile.TemporaryDirectory() as directory:
            dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
            legacy_project = Project(ui_language="ja", datasets=[dataset])
            path = str(Path(directory) / "legacy-language.hplcproj")
            from hplc_app.project_io import save_project

            save_project(path, legacy_project)
            backend = MemorySettings()
            settings_factory = lambda: ApplicationSettings(backend)
            with patch(
                "hplc_app.gui.ApplicationSettings", side_effect=settings_factory
            ), patch.object(
                QtWidgets.QFileDialog,
                "getOpenFileName",
                return_value=(path, ""),
            ):
                window = MainWindow()
                window.open_project()
                self.assertEqual(window.project.ui_language, "ja")
                self.assertEqual(window._application_language, "en")
                self.assertEqual(window.translator.language, "en")
                self.assertFalse(window.project.dirty)
                window.close()

    def test_lightweight_png_svg_pdf_export_temporarily_uses_full_data(self):
        window = self.make_lightweight_window()
        dataset = window.project.datasets[0]
        dataset.line_style = "dotted"
        window._plot()
        screen_count = len(window._dataset_lines[dataset.id].get_xdata())
        self.assertLess(screen_count, dataset.time_min.size)
        peak = dataset.peaks[0]
        metrics = (
            peak.raw_area_uv_sec,
            peak.area_mau_sec,
            peak.retention_time_min,
            peak.fwhm_min,
            peak.area_percent,
        )
        observed_counts = []
        observed_styles = []

        def observe_save(path, **_kwargs):
            observed_counts.append(
                len(window._dataset_lines[dataset.id].get_xdata())
            )
            observed_styles.append(
                window._dataset_lines[dataset.id].get_linestyle()
            )
            Path(path).write_bytes(b"verified full-data export")

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(window.figure, "savefig", side_effect=observe_save):
                for suffix in ("png", "svg", "pdf"):
                    window._save_figure_file(str(Path(directory) / ("figure." + suffix)))
        self.assertEqual(observed_counts, [dataset.time_min.size] * 3)
        self.assertEqual(observed_styles, [":", ":", ":"])
        self.assertEqual(window._render_quality, LIGHTWEIGHT)
        self.assertLess(
            len(window._dataset_lines[dataset.id].get_xdata()),
            dataset.time_min.size,
        )
        peak_after = dataset.peaks[0]
        self.assertEqual(
            (
                peak_after.raw_area_uv_sec,
                peak_after.area_mau_sec,
                peak_after.retention_time_min,
                peak_after.fwhm_min,
                peak_after.area_percent,
            ),
            metrics,
        )
        window.project.dirty = False
        window.close()

    def test_dual_axes_gradient_axis_full_path_and_exact_xlim(self):
        window = self.make_window()
        self.assertEqual(len(window.figure.axes), 3)
        self.assertEqual(
            window.dataset_table.item(0, DATASET_SOURCE_COLUMN).text(),
            window.project.datasets[0].original_path,
        )
        self.assertEqual(
            window.dataset_table.item(0, DATASET_WAVELENGTH_COLUMN).text(), "280"
        )
        self.assertAlmostEqual(window.axes.get_xlim()[0], 0.0, places=8)
        self.assertAlmostEqual(
            window.axes.get_xlim()[1],
            max(float(dataset.time_min[-1]) for dataset in window.project.datasets),
            places=8,
        )
        ticks = window.axes.xaxis.get_major_locator().tick_values(0.0, 15.0)
        self.assertIn(5.0, ticks)
        self.assertIn(10.0, ticks)
        window.project.dirty = False
        window.close()

    def test_run_id_column_keeps_dataset_rows_and_shows_shared_run(self):
        window = self.make_window()
        shared_run = window.project.run_for(window.project.datasets[0])
        window.project.datasets[1].bind_run(shared_run)
        window.project.runs = [shared_run]
        window.project.rebuild_run_index(create_missing=False)
        window._refresh_dataset_table(0)

        self.assertEqual(window.dataset_table.rowCount(), 2)
        first = window.dataset_table.item(0, DATASET_RUN_ID_COLUMN)
        second = window.dataset_table.item(1, DATASET_RUN_ID_COLUMN)
        self.assertEqual(first.text(), shared_run.id)
        self.assertEqual(second.text(), shared_run.id)
        self.assertEqual(first.toolTip(), shared_run.id)
        self.assertTrue(bool(first.flags() & ITEM_IS_EDITABLE))
        self.assertTrue(
            bool(
                window.dataset_table.item(0, DATASET_LABEL_COLUMN).flags()
                & ITEM_IS_EDITABLE
            )
        )

        window.project.dirty = False
        window.close()

    def test_run_id_inline_rename_shared_rows_history_save_and_legend(self):
        from hplc_app.project_io import load_project, save_project

        window = self.make_window()
        first, second = window.project.datasets
        run = window.project.run_for(first)
        window.project.group_datasets_into_run([first, second], run)
        window.project.method.legend_components = ["run_id", "wavelength"]
        window._refresh_all(0)
        old_id = run.id
        peaks = deepcopy(first.peaks)
        window.dataset_table.item(0, DATASET_RUN_ID_COLUMN).setText("手動 ID_A")
        self.assertEqual([d.run_id for d in window.project.datasets], ["手動 ID_A"] * 2)
        for row in range(2):
            item = window.dataset_table.item(row, DATASET_RUN_ID_COLUMN)
            self.assertEqual(item.text(), "手動 ID_A")
            self.assertEqual(item.toolTip(), "手動 ID_A")
            self.assertEqual(item.data(USER_ROLE), "手動 ID_A")
        self.assertEqual(len(window._undo_stack), 1)
        self.assertTrue(window.project.dirty)
        self.assertTrue(any("手動 ID_A" in t.get_text()
                            for t in window.axes.get_legend().get_texts()))
        window.undo()
        self.assertEqual([d.run_id for d in window.project.datasets], [old_id] * 2)
        window.redo()
        self.assertEqual([d.run_id for d in window.project.datasets], ["手動 ID_A"] * 2)
        window.dataset_table.item(0, DATASET_LABEL_COLUMN).setText("new label")
        window.dataset_table.item(0, DATASET_TIMESTAMP_COLUMN).setText("2020-01-01T00:00:00")
        window.move_dataset_to(0, 1)
        self.assertEqual([d.run_id for d in window.project.datasets], ["手動 ID_A"] * 2)
        self.assertEqual(first.peaks, peaks)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "renamed.hplcproj")
            save_project(path, window.project)
            restored = load_project(path)
            self.assertEqual([d.run_id for d in restored.datasets], ["手動 ID_A"] * 2)
            output = Path(directory) / "renamed.svg"
            window._save_figure_file(str(output))
            self.assertIn("手動 ID_A", output.read_text(encoding="utf-8"))
        window.project.dirty = False
        window.close()

    def test_run_id_collision_empty_and_noop_do_not_mutate_or_group(self):
        window = self.make_window()
        ids = [d.run_id for d in window.project.datasets]
        for language in ("ja", "en"):
            window.set_language(language)
            window.project.dirty = False
            window._reset_undo_history()
            for candidate, key in ((ids[1], "run_id_duplicate"), ("  ", "run_id_empty")):
                with patch.object(QtWidgets.QMessageBox, "warning") as warning:
                    window.dataset_table.item(0, DATASET_RUN_ID_COLUMN).setText(candidate)
                self.assertEqual(warning.call_args.args[2], window.translator(key))
                self.assertEqual([d.run_id for d in window.project.datasets], ids)
                self.assertEqual(window.dataset_table.item(0, DATASET_RUN_ID_COLUMN).text(), ids[0])
                self.assertEqual(len(window.project.runs), 2)
                self.assertFalse(window.project.dirty)
                self.assertEqual(window._undo_stack, [])
            with patch.object(QtWidgets.QMessageBox, "warning") as warning:
                window.dataset_table.item(0, DATASET_RUN_ID_COLUMN).setText(" " + ids[0] + " ")
            warning.assert_not_called()
            self.assertFalse(window.project.dirty)
            self.assertEqual(window._undo_stack, [])
        window.project.dirty = False
        window.close()

    def test_timestamp_and_column_columns_edit_shared_run_and_support_undo(self):
        window = self.make_window()
        shared_run = window.project.run_for(window.project.datasets[0])
        window.project.datasets[1].bind_run(shared_run)
        window.project.runs = [shared_run]
        window.project.rebuild_run_index(create_missing=False)
        shared_run.timestamp = "2026-08-27T10:00:00"
        shared_run.column_name = "C4"
        window._refresh_dataset_table(0)

        self.assertEqual(DATASET_COLUMN_NAME_COLUMN + 1, DATASET_SOURCE_COLUMN)
        self.assertEqual(
            window.dataset_table.item(1, DATASET_TIMESTAMP_COLUMN).text(),
            "2026-08-27T10:00:00",
        )
        window.dataset_table.item(0, DATASET_TIMESTAMP_COLUMN).setText(
            "2026-08-27T11:30:00"
        )
        self.assertEqual(shared_run.timestamp, "2026-08-27T11:30:00")
        self.assertEqual(
            window.dataset_table.item(1, DATASET_TIMESTAMP_COLUMN).text(),
            "2026-08-27T11:30:00",
        )
        window.dataset_table.item(0, DATASET_COLUMN_NAME_COLUMN).setText("C18")
        self.assertEqual(shared_run.column_name, "C18")
        self.assertEqual(
            window.dataset_table.item(1, DATASET_COLUMN_NAME_COLUMN).text(), "C18"
        )

        window.undo()
        self.assertEqual(window.project.runs[0].column_name, "C4")
        window.undo()
        self.assertEqual(window.project.runs[0].timestamp, "2026-08-27T10:00:00")
        window.redo()
        self.assertEqual(window.project.runs[0].timestamp, "2026-08-27T11:30:00")

        window.project.dirty = False
        window.close()

    def test_project_location_distinguishes_unsaved_and_saved_paths(self):
        window = self.make_window()
        self.assertIn("未保存", window.project_location_label.text())
        self.assertEqual(
            window.project_location_label.toolTip(),
            "プロジェクト: 未保存",
        )

        window.set_language("en")
        self.assertIn("Not saved yet", window.project_location_label.text())
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / ("日本語_" + "long-name-" * 20 + ".hplcproj")
            window.project.project_path = str(destination)
            window.project.dirty = True
            window._update_title()
            absolute_path = str(destination.absolute())
            self.assertEqual(
                window.project_location_label.toolTip(),
                "Project: " + absolute_path,
            )
            self.assertEqual(
                window.project_location_label.accessibleName(),
                "Project: " + absolute_path,
            )
            self.assertIn(destination.name + "*", window.windowTitle())
            self.assertLessEqual(window.project_location_label.width(), 520)
        window.project.dirty = False
        window.close()

    def test_project_location_updates_after_save_open_and_new(self):
        window = self.make_window()
        with tempfile.TemporaryDirectory() as directory:
            first_path = Path(directory) / "保存先.hplcproj"
            window.project.project_path = str(first_path)
            window.project.dirty = True
            self.assertTrue(window.save_project())
            self.assertEqual(
                window.project_location_label.toolTip(),
                "プロジェクト: " + str(first_path.absolute()),
            )

            second_path = Path(directory) / "opened.hplcproj"
            window.project.project_path = str(second_path)
            window.project.dirty = True
            self.assertTrue(window.save_project())
            window.new_project()
            self.assertIn("未保存", window.project_location_label.text())

            save_as_path = Path(directory) / "名前を付けて保存"
            with patch("hplc_app.gui.dialog_exec", return_value=True), patch.object(
                QtWidgets.QFileDialog,
                "getSaveFileName",
                return_value=(str(save_as_path), ""),
            ):
                self.assertTrue(window.save_project_as())
            saved_as_project = Path(str(save_as_path) + ".hplcproj")
            self.assertEqual(
                window.project_location_label.toolTip(),
                "プロジェクト: " + str(saved_as_project.absolute()),
            )
            window.new_project()

            with patch.object(
                QtWidgets.QFileDialog,
                "getOpenFileName",
                return_value=(str(second_path), ""),
            ):
                window.open_project()
            self.assertEqual(
                window.project_location_label.toolTip(),
                "プロジェクト: " + str(second_path.absolute()),
            )
            self.assertIn(second_path.name, window.windowTitle())

            window.new_project()
            self.assertEqual(window.project.project_path, "")
            self.assertEqual(
                window.project_location_label.toolTip(),
                "プロジェクト: 未保存",
            )
        window.project.dirty = False
        window.close()

    def test_zoom_ticks_and_peak_changes_preserve_current_view(self):
        window = self.make_window()
        window.project.method.x_tick_mode = "auto"
        window.axes.set_xlim(4.0, 12.0)
        window.axes.set_ylim(0.0, 100000.0)
        before = window.axes.get_xlim()
        window.peak_table.selectRow(0)
        self.app.processEvents()
        self.assertAlmostEqual(window.axes.get_xlim()[0], before[0], places=6)
        self.assertAlmostEqual(window.axes.get_xlim()[1], before[1], places=6)
        ticks = window.axes.xaxis.get_major_locator().tick_values(4.0, 12.0)
        self.assertIn(5.0, ticks)
        self.assertLessEqual(ticks[1] - ticks[0], 1.0)

        first_spacing = ticks[1] - ticks[0]
        window.axes.set_xlim(6.0, 8.0)
        updated = window.axes.xaxis.get_major_locator().tick_values(6.0, 8.0)
        self.assertLess(updated[1] - updated[0], first_spacing)

        window.project.method.x_tick_mode = "manual"
        window.project.method.x_major_tick_min = 0.4
        window.project.method.x_minor_tick_min = 0.1
        window._set_dynamic_x_ticks()
        manual_major = window.axes.xaxis.get_major_locator().tick_values(6.0, 8.0)
        manual_minor = window.axes.xaxis.get_minor_locator().tick_values(6.0, 8.0)
        self.assertAlmostEqual(manual_major[1] - manual_major[0], 0.4, places=8)
        self.assertAlmostEqual(manual_minor[1] - manual_minor[0], 0.1, places=8)
        window.project.dirty = False
        window.close()

    def test_zoom_axis_modes_and_double_click_back(self):
        window = self.make_window()
        original_x = window.axes.get_xlim()
        original_y = window.axes.get_ylim()
        original_y2 = window.axes_right.get_ylim()
        original_gradient = window.axes_gradient.get_ylim()
        window.project.method.zoom_axis = "x"
        window._zoom_view(0.8, center_x=8.0, source_axis=window.axes, center_y=original_y[0])
        self.assertLess(window.axes.get_xlim()[1] - window.axes.get_xlim()[0], original_x[1] - original_x[0])
        self.assertEqual(window.axes.get_ylim(), original_y)
        self.assertEqual(window.axes_right.get_ylim(), original_y2)
        self.assertEqual(window.axes_gradient.get_ylim(), original_gradient)
        event = SimpleNamespace(button=1, xdata=8.0, dblclick=True)
        window._on_canvas_press(event)
        self.assertAlmostEqual(window.axes.get_xlim()[0], original_x[0], places=6)
        self.assertAlmostEqual(window.axes.get_xlim()[1], original_x[1], places=6)

        before_x = window.axes.get_xlim()
        before_y = window.axes.get_ylim()
        before_y2 = window.axes_right.get_ylim()
        window.dataset_table.selectRow(1)
        window.project.method.zoom_axis = "y"
        window._zoom_view(
            0.8,
            center_x=8.0,
            source_axis=window.axes_right,
            center_y=sum(before_y2) / 2.0,
        )
        self.assertEqual(window.axes.get_xlim(), before_x)
        self.assertLess(window.axes.get_ylim()[1] - window.axes.get_ylim()[0], before_y[1] - before_y[0])
        self.assertLess(
            window.axes_right.get_ylim()[1] - window.axes_right.get_ylim()[0],
            before_y2[1] - before_y2[0],
        )

        window.project.datasets[1].visible = False
        window.dataset_table.selectRow(0)
        window._plot(preserve_view=False)
        self.assertIsNone(window.axes_right)
        y1_only = window.axes.get_ylim()
        window._zoom_view(0.8, zoom_mode="y")
        self.assertLess(
            window.axes.get_ylim()[1] - window.axes.get_ylim()[0],
            y1_only[1] - y1_only[0],
        )
        window.project.dirty = False
        window.close()

    def test_fixed_both_zoom_changes_x_y1_y2_but_not_gradient(self):
        window = self.make_window()
        before_x = window.axes.get_xlim()
        before_y1 = window.axes.get_ylim()
        before_y2 = window.axes_right.get_ylim()
        before_gradient = window.axes_gradient.get_ylim()

        window._zoom_view(
            0.8,
            center_x=sum(before_x) / 2.0,
            source_axis=window.axes_right,
            center_y=sum(before_y2) / 2.0,
            zoom_mode="both",
        )

        self.assertLess(
            window.axes.get_xlim()[1] - window.axes.get_xlim()[0],
            before_x[1] - before_x[0],
        )
        self.assertLess(
            window.axes.get_ylim()[1] - window.axes.get_ylim()[0],
            before_y1[1] - before_y1[0],
        )
        self.assertLess(
            window.axes_right.get_ylim()[1] - window.axes_right.get_ylim()[0],
            before_y2[1] - before_y2[0],
        )
        self.assertEqual(window.axes_gradient.get_ylim(), before_gradient)
        window.project.dirty = False
        window.close()

    def test_scroll_uses_selected_zoom_axis(self):
        window = self.make_window()
        before_x = window.axes.get_xlim()
        before_y = window.axes.get_ylim()
        window.project.method.zoom_axis = "x"
        window._on_scroll(SimpleNamespace(button="up", xdata=10.0, inaxes=window.axes, ydata=sum(before_y) / 2.0))
        self.assertNotEqual(window.axes.get_xlim(), before_x)
        self.assertEqual(window.axes.get_ylim(), before_y)
        window.project.dirty = False
        window.close()

    def test_scroll_accepts_backend_neutral_pointer_event(self):
        window = self.make_window()
        window.project.method.zoom_axis = "auto"
        before_x = window.axes.get_xlim()
        before_y1 = window.axes.get_ylim()
        before_y2 = window.axes_right.get_ylim()

        window._on_scroll(
            ScreenPointerEvent(
                button="up",
                axis_role="y1",
                hit_region="x",
                data_coordinates=(
                    ("y1", sum(before_x) / 2.0, sum(before_y1) / 2.0),
                    ("y2", sum(before_x) / 2.0, sum(before_y2) / 2.0),
                ),
            )
        )

        self.assertLess(
            window.axes.get_xlim()[1] - window.axes.get_xlim()[0],
            before_x[1] - before_x[0],
        )
        self.assertEqual(window.axes.get_ylim(), before_y1)
        self.assertEqual(window.axes_right.get_ylim(), before_y2)
        window.project.dirty = False
        window.close()

    def test_toolbar_back_and_forward_use_backend_neutral_view_history(self):
        window = self.make_window()
        window.project.method.zoom_axis = "x"
        before = window.axes.get_xlim()
        window._on_scroll(
            ScreenPointerEvent(
                button="up",
                axis_role="y1",
                hit_region="x",
                data_coordinates=(("y1", sum(before) / 2.0, 0.0),),
            )
        )
        zoomed = window.axes.get_xlim()
        self.assertNotEqual(zoomed, before)
        back_action = window.toolbar._actions["back"]
        forward_action = window.toolbar._actions["forward"]
        self.assertTrue(back_action.isEnabled())

        back_action.trigger()
        self.assertTrue(np.allclose(window.axes.get_xlim(), before))
        self.assertTrue(forward_action.isEnabled())
        forward_action.trigger()
        self.assertTrue(np.allclose(window.axes.get_xlim(), zoomed))
        window.toolbar._actions["home"].trigger()
        self.assertTrue(np.allclose(window.axes.get_xlim(), before))
        window.project.dirty = False
        window.close()

    def test_toolbar_pan_and_zoom_each_sync_to_their_own_mouse_mode(self):
        window = self.make_window()
        try:
            pan_action = window.toolbar._actions["pan"]
            zoom_action = window.toolbar._actions["zoom"]
            self.assertEqual(window.mouse_mode_combo.currentData(), "normal")

            # A real click on the native Pan tool (only .trigger(), not a
            # plain .pan() call, emits `triggered`) must not re-enter itself
            # and must clear any other active mode control -- 9.1's
            # loop-safety half.
            window.dataset_table.selectRow(0)
            window.integrate_button.setChecked(True)
            self.assertEqual(window.mouse_mode_combo.currentData(), "integrate")
            pan_action.trigger()
            self.assertEqual(window.mouse_mode_combo.currentData(), "normal")
            self.assertFalse(window.integrate_button.isChecked())

            # Leaving "normal" deactivates both pan and zoom; returning to it
            # with neither active falls back to pan, matching the toolbar's
            # own tool (never "neither").
            integrate_index = window.mouse_mode_combo.findData("integrate")
            window.mouse_mode_combo.setCurrentIndex(integrate_index)
            self.assertFalse(pan_action.isChecked())
            self.assertFalse(zoom_action.isChecked())
            normal_index = window.mouse_mode_combo.findData("normal")
            window.mouse_mode_combo.setCurrentIndex(normal_index)
            self.assertTrue(pan_action.isChecked())
            self.assertFalse(zoom_action.isChecked())

            zoom_action.trigger()
            self.assertEqual(window.mouse_mode_combo.currentData(), "zoom")
            self.assertFalse(pan_action.isChecked())
            self.assertTrue(zoom_action.isChecked())
            window.mouse_mode_combo.setCurrentIndex(normal_index)
            self.assertTrue(pan_action.isChecked())
            self.assertFalse(zoom_action.isChecked())
        finally:
            window.project.dirty = False
            window.close()

    def test_mode_toolbar_icons_three_way_sync_with_group_controls_and_combo(self):
        window = self.make_window()
        try:
            window.dataset_table.selectRow(0)
            window.peak_table.selectRow(0)
            toolbar_actions = window.toolbar.actions()
            self.assertEqual(len(MOUSE_MODE_IDS), 9)
            self.assertEqual(window.mouse_mode_combo.count(), 9)
            self.assertIn(window.toolbar._actions["pan"], toolbar_actions)
            self.assertIn(window.toolbar._actions["zoom"], toolbar_actions)
            for action in (
                window.select_toolbar_action,
                window.integrate_toolbar_action,
                window.edit_peak_toolbar_action,
                window.split_peak_toolbar_action,
                window.move_trace_toolbar_action,
                window.annotation_action,
            ):
                self.assertIn(action, toolbar_actions)

            pairs = (
                (window.integrate_toolbar_action, window.integrate_button, "integrate"),
                (window.edit_peak_toolbar_action, window.edit_peak_button, "edit_peak"),
                (window.split_peak_toolbar_action, window.split_peak_button, "split_peak"),
                (window.move_trace_toolbar_action, window.move_trace_button, "move_trace"),
            )
            for toolbar_action, control, mode in pairs:
                with self.subTest(mode=mode):
                    # Group-panel button -> toolbar icon.
                    control.setChecked(True)
                    self.assertTrue(toolbar_action.isChecked())
                    self.assertEqual(window.mouse_mode_combo.currentData(), mode)
                    control.setChecked(False)
                    self.assertFalse(toolbar_action.isChecked())

                    # Toolbar icon -> group-panel button.
                    toolbar_action.setChecked(True)
                    self.assertTrue(control.isChecked())
                    self.assertEqual(window.mouse_mode_combo.currentData(), mode)
                    toolbar_action.setChecked(False)
                    self.assertFalse(control.isChecked())

                    # Combo -> both.
                    index = window.mouse_mode_combo.findData(mode)
                    window.mouse_mode_combo.setCurrentIndex(index)
                    self.assertTrue(control.isChecked())
                    self.assertTrue(toolbar_action.isChecked())
                    normal_index = window.mouse_mode_combo.findData("normal")
                    window.mouse_mode_combo.setCurrentIndex(normal_index)
                    self.assertFalse(control.isChecked())
                    self.assertFalse(toolbar_action.isChecked())

            # "select" has no group-panel control, so it routes through the
            # combo instead of a duplicate two-way bind (9.15's special case).
            window.select_toolbar_action.setChecked(True)
            self.assertEqual(window.mouse_mode_combo.currentData(), "select")
            window.select_toolbar_action.setChecked(False)
            self.assertEqual(window.mouse_mode_combo.currentData(), "normal")

            # No two mode icons are ever checked at the same time.
            window.integrate_toolbar_action.setChecked(True)
            window.edit_peak_toolbar_action.setChecked(True)
            checked = [
                action.isChecked()
                for action in (
                    window.toolbar._actions["pan"],
                    window.toolbar._actions["zoom"],
                    window.pointer_action,
                    window.select_toolbar_action,
                    window.integrate_toolbar_action,
                    window.edit_peak_toolbar_action,
                    window.split_peak_toolbar_action,
                    window.move_trace_toolbar_action,
                    window.annotation_action,
                )
            ]
            self.assertEqual(sum(checked), 1)

            mode_actions = {
                "normal": window.toolbar._actions["pan"],
                "zoom": window.toolbar._actions["zoom"],
                "pointer": window.pointer_toolbar_widget_action,
                "select": window.select_toolbar_action,
                "integrate": window.integrate_toolbar_action,
                "edit_peak": window.edit_peak_toolbar_action,
                "split_peak": window.split_peak_toolbar_action,
                "move_trace": window.move_trace_toolbar_action,
                "annotation": window.annotation_action,
            }
            visible_actions = [
                action for action in window.toolbar.actions() if not action.isSeparator()
            ]
            self.assertEqual(
                visible_actions[: len(MOUSE_MODE_IDS)],
                [mode_actions[mode_id] for mode_id in MOUSE_MODE_IDS],
            )
            self.assertTrue(
                all(
                    action not in mode_actions.values()
                    for action in visible_actions[len(MOUSE_MODE_IDS) :]
                )
            )
            self.assertFalse(window.annotation_action.icon().isNull())
            annotation_image = window.annotation_action.icon().pixmap(24, 24).toImage()
            self.assertGreater(annotation_image.pixelColor(12, 12).alpha(), 0)

            select_index = window.mouse_mode_combo.findData("select")
            window.mouse_mode_combo.setCurrentIndex(select_index)
            self.assertTrue(window.select_toolbar_action.isChecked())
            for mode_id in MOUSE_MODE_IDS:
                if mode_id == "select":
                    continue
                with self.subTest(select_to=mode_id):
                    window.mouse_mode_combo.setCurrentIndex(select_index)
                    target_index = window.mouse_mode_combo.findData(mode_id)
                    window.mouse_mode_combo.setCurrentIndex(target_index)
                    self.assertFalse(window.select_toolbar_action.isChecked())
        finally:
            window.project.dirty = False
            window.close()

    def test_subplots_action_opens_axis_dialog_on_both_renderers_and_customize_is_removed(self):
        window = self.make_window()
        try:
            toolbar_actions = window.toolbar._actions
            self.assertNotIn(
                toolbar_actions.get("edit_parameters"), window.toolbar.actions()
            )
            self.assertIn(toolbar_actions.get("zoom"), window.toolbar.actions())
            with patch("hplc_app.gui.AxisLabelsDialog") as dialog_class, patch(
                "hplc_app.gui.dialog_exec", return_value=False
            ):
                toolbar_actions["configure_subplots"].trigger()
                self.assertEqual(dialog_class.call_count, 1)
                toolbar_actions["edit_parameters"].trigger()
                self.assertEqual(dialog_class.call_count, 2)

            if pyqtgraph_scene_available():
                window.screen_preview_checkbox.setChecked(True)
                self.app.processEvents()
                with patch("hplc_app.gui.AxisLabelsDialog") as dialog_class, patch(
                    "hplc_app.gui.dialog_exec", return_value=False
                ):
                    toolbar_actions["configure_subplots"].trigger()
                    self.assertEqual(dialog_class.call_count, 1)
        finally:
            window.project.dirty = False
            window.close()

    def test_navigation_group_row_order_keeps_widgets_connected(self):
        window = self.make_window()
        try:
            layout = window.navigation_group.layout()

            def row_of(widget):
                for i in range(layout.count()):
                    item = layout.itemAt(i)
                    child_layout = item.layout()
                    matches = item.widget() is widget or (
                        child_layout is not None
                        and child_layout.indexOf(widget) != -1
                    )
                    if matches:
                        row, _column, _row_span, _column_span = (
                            layout.getItemPosition(i)
                        )
                        return row
                return None

            # reset_buttons (row 4) now comes before move_controls (row 5),
            # which holds move_trace_button (Issue #236 / 9.18).
            expected_rows = {
                window.zoom_axis_label: 1,
                window.view_mode_label: 2,
                window.pointer_control_button: 3,
                window.reset_view_button: 4,
                window.move_trace_button: 5,
            }
            for widget, expected in expected_rows.items():
                self.assertEqual(row_of(widget), expected)

            # Reordering must not have disconnected any of the row's controls.
            window.zoom_axis_combo.setCurrentIndex(
                window.zoom_axis_combo.findData("x")
            )
            self.assertEqual(window.project.method.zoom_axis, "x")
            window.view_mode_combo.setCurrentIndex(
                window.view_mode_combo.findData("split_y_axes")
            )
            self.assertEqual(window.project.method.view_mode, "split_y_axes")
        finally:
            window.project.dirty = False
            window.close()

    def test_cursor_position_selects_x_y1_y2_or_both_for_wheel_zoom(self):
        window = self.make_window()
        window.project.method.zoom_axis = "auto"
        window.axes.set_ylim(0.0, 100.0)
        window.axes_right.set_ylim(-1000.0, 1000.0)
        window.canvas.draw()
        bbox = window.axes.bbox

        def event_at(x_value, y_value, inaxes=None):
            x_data, y_data = window.axes.transData.inverted().transform(
                (x_value, y_value)
            )
            return SimpleNamespace(
                button="up",
                x=float(x_value),
                y=float(y_value),
                xdata=float(x_data),
                ydata=float(y_data),
                inaxes=inaxes,
            )

        x_before = window.axes.get_xlim()
        y1_before = window.axes.get_ylim()
        y2_before = window.axes_right.get_ylim()
        x_event = event_at((bbox.x0 + bbox.x1) / 2.0, bbox.y0 - 2.0)
        self.assertEqual(window._scroll_target(x_event), "x")
        window._on_scroll(x_event)
        self.assertLess(
            window.axes.get_xlim()[1] - window.axes.get_xlim()[0],
            x_before[1] - x_before[0],
        )
        self.assertEqual(window.axes.get_ylim(), y1_before)
        self.assertEqual(window.axes_right.get_ylim(), y2_before)

        x_after = window.axes.get_xlim()
        y1_event = event_at(bbox.x0 - 2.0, (bbox.y0 + bbox.y1) / 2.0)
        self.assertEqual(window._scroll_target(y1_event), "y1")
        window._on_scroll(y1_event)
        self.assertEqual(window.axes.get_xlim(), x_after)
        self.assertLess(
            window.axes.get_ylim()[1] - window.axes.get_ylim()[0],
            y1_before[1] - y1_before[0],
        )
        self.assertEqual(window.axes_right.get_ylim(), y2_before)

        y1_after = window.axes.get_ylim()
        y2_event = event_at(bbox.x1 + 2.0, (bbox.y0 + bbox.y1) / 2.0)
        self.assertEqual(window._scroll_target(y2_event), "y2")
        window._on_scroll(y2_event)
        self.assertEqual(window.axes.get_xlim(), x_after)
        self.assertEqual(window.axes.get_ylim(), y1_after)
        self.assertLess(
            window.axes_right.get_ylim()[1] - window.axes_right.get_ylim()[0],
            y2_before[1] - y2_before[0],
        )

        plot_x_before = window.axes.get_xlim()
        plot_y1_before = window.axes.get_ylim()
        plot_y2_before = window.axes_right.get_ylim()
        plot_event = event_at(
            (bbox.x0 + bbox.x1) / 2.0,
            (bbox.y0 + bbox.y1) / 2.0,
            window.axes_gradient,
        )
        self.assertEqual(window._scroll_target(plot_event), "plot")
        window._on_scroll(plot_event)
        self.assertLess(
            window.axes.get_xlim()[1] - window.axes.get_xlim()[0],
            plot_x_before[1] - plot_x_before[0],
        )
        self.assertLess(
            window.axes.get_ylim()[1] - window.axes.get_ylim()[0],
            plot_y1_before[1] - plot_y1_before[0],
        )
        self.assertLess(
            window.axes_right.get_ylim()[1] - window.axes_right.get_ylim()[0],
            plot_y2_before[1] - plot_y2_before[0],
        )
        window.project.dirty = False
        window.close()

    def test_pan_drag_target_follows_x_y1_y2_or_plot_region(self):
        window = self.make_window()
        window.canvas.draw()
        bbox = window.axes.bbox
        window.toolbar.pan()

        def set_limits():
            window.axes.set_xlim(0.0, 100.0)
            window.axes.set_ylim(0.0, 1000.0)
            window.axes_right.set_ylim(-100.0, 100.0)
            window.axes_gradient.set_ylim(0.0, 100.0)

        def drag(start_x, start_y, delta_x=24.0, delta_y=30.0, inaxes=None):
            window.toolbar.press_pan(
                SimpleNamespace(
                    button=1,
                    x=float(start_x),
                    y=float(start_y),
                    inaxes=inaxes,
                )
            )
            window.toolbar.drag_pan(
                SimpleNamespace(
                    x=float(start_x + delta_x),
                    y=float(start_y + delta_y),
                )
            )
            window.toolbar.release_pan(SimpleNamespace())

        center_x = (bbox.x0 + bbox.x1) / 2.0
        center_y = (bbox.y0 + bbox.y1) / 2.0

        set_limits()
        before = (
            window.axes.get_xlim(),
            window.axes.get_ylim(),
            window.axes_right.get_ylim(),
        )
        drag(center_x, bbox.y0 - 2.0)
        self.assertNotEqual(window.axes.get_xlim(), before[0])
        self.assertEqual(window.axes.get_ylim(), before[1])
        self.assertEqual(window.axes_right.get_ylim(), before[2])

        set_limits()
        before = (
            window.axes.get_xlim(),
            window.axes.get_ylim(),
            window.axes_right.get_ylim(),
        )
        drag(bbox.x0 - 2.0, center_y)
        self.assertEqual(window.axes.get_xlim(), before[0])
        self.assertNotEqual(window.axes.get_ylim(), before[1])
        self.assertEqual(window.axes_right.get_ylim(), before[2])

        set_limits()
        before = (
            window.axes.get_xlim(),
            window.axes.get_ylim(),
            window.axes_right.get_ylim(),
        )
        drag(bbox.x1 + 2.0, center_y)
        self.assertEqual(window.axes.get_xlim(), before[0])
        self.assertEqual(window.axes.get_ylim(), before[1])
        self.assertNotEqual(window.axes_right.get_ylim(), before[2])

        set_limits()
        before_gradient = window.axes_gradient.get_ylim()
        drag(center_x, center_y, inaxes=window.axes_gradient)
        self.assertNotEqual(window.axes.get_xlim(), (0.0, 100.0))
        self.assertNotEqual(window.axes.get_ylim(), (0.0, 1000.0))
        self.assertNotEqual(window.axes_right.get_ylim(), (-100.0, 100.0))
        self.assertEqual(window.axes_gradient.get_ylim(), before_gradient)

        window.toolbar.pan()
        window.project.dirty = False
        window.close()

    def test_axis_pan_accepts_backend_neutral_navigation_events(self):
        window = self.make_window()
        window.canvas.draw()
        window.axes.set_xlim(0.0, 100.0)
        window.axes.set_ylim(0.0, 1000.0)
        window.axes_right.set_ylim(-100.0, 100.0)
        bbox = window.axes.bbox
        before_x = window.axes.get_xlim()
        before_y1 = window.axes.get_ylim()
        before_y2 = window.axes_right.get_ylim()

        self.assertTrue(
            window._begin_axis_pan(
                ScreenPointerEvent(
                    button=1,
                    hit_region="y1",
                    canvas_x=float(bbox.x0),
                    canvas_y=float(bbox.y0),
                )
            )
        )
        self.assertTrue(
            window._update_axis_pan(
                ScreenPointerEvent(
                    canvas_x=float(bbox.x0 + 25.0),
                    canvas_y=float(bbox.y0 + 30.0),
                )
            )
        )
        self.assertTrue(window._end_axis_pan())
        self.assertEqual(window.axes.get_xlim(), before_x)
        self.assertNotEqual(window.axes.get_ylim(), before_y1)
        self.assertEqual(window.axes_right.get_ylim(), before_y2)
        self.assertIsNone(window._screen_pan_session)
        window.project.dirty = False
        window.close()

    def test_split_mode_uses_clicked_time_without_resetting_view(self):
        window = self.make_window()
        dataset = window.project.datasets[0]
        dataset.peaks[0].start_min = 5.0
        dataset.peaks[0].end_min = 10.0
        fitted = fitted_peak_from_result(
            dataset.peaks[0],
            PeakFitResult(
                "gaussian",
                {"amplitude_uv": 1000.0, "center_min": 7.0, "sigma_min": 0.5},
                7.0,
                2.0,
                0.99,
                12.0,
                40,
            ),
        )
        dataset.fitted_peaks = [fitted]
        mirror_fitted_peak_for_legacy(dataset.peaks[0], fitted)
        window._refresh_peak_table()
        window.axes.set_xlim(4.0, 12.0)
        window.peak_table.selectRow(0)
        window.split_peak_button.setChecked(True)
        window._split_selected_peak_at(7.5)
        self.assertEqual(len(window.project.datasets[0].peaks), 2)
        self.assertEqual(window.project.datasets[0].fitted_peaks, [])
        self.assertTrue(
            all(not peak.fit_model for peak in window.project.datasets[0].peaks)
        )
        self.assertEqual(window._selected_peak_rows(), [1])
        self.assertEqual(window.peak_table.currentRow(), 1)
        self.assertTrue(window.split_peak_button.isChecked())
        self.assertGreater(
            window.project.datasets[0].peaks[window._selected_peak_rows()[0]].retention_time_min,
            window.project.datasets[0].peaks[0].retention_time_min,
        )
        self.assertAlmostEqual(window.axes.get_xlim()[0], 4.0, places=6)
        self.assertAlmostEqual(window.axes.get_xlim()[1], 12.0, places=6)
        window._split_selected_peak_at(8.5)
        self.assertEqual(len(window.project.datasets[0].peaks), 3)
        self.assertEqual(window._selected_peak_rows(), [2])
        self.assertEqual(window.peak_table.currentRow(), 2)
        self.assertTrue(window.split_peak_button.isChecked())
        self.assertAlmostEqual(
            window.project.datasets[0].peaks[2].start_min, 8.5, places=6
        )
        window.split_peak_button.setChecked(False)
        window.project.dirty = False
        window.close()

    def test_inline_label_change_updates_legend(self):
        window = self.make_window()
        window.dataset_table.item(0, DATASET_LABEL_COLUMN).setText("Updated label")
        self.app.processEvents()
        labels = window.axes.get_legend_handles_labels()[1]
        self.assertIn("Updated label_280 nm", labels)
        self.assertEqual(window.project.datasets[0].short_label, "Updated label")
        window.project.dirty = False
        window.close()

    def test_legend_composer_dialog_applies_order_separator_and_is_undoable(self):
        window = self.make_window()
        dialog = LegendComposerDialog(window.project.method, "en")
        for row in range(dialog.list_widget.count()):
            item = dialog.list_widget.item(row)
            item.setCheckState(
                CHECKED
                if item.data(USER_ROLE) in ("run_id", "label", "timestamp")
                else UNCHECKED
            )
            if item.data(USER_ROLE) == "run_id":
                dialog.list_widget.setCurrentRow(row)
        dialog._move(-1)
        dialog._move(-1)
        dialog.separator_edit.setText(" / ")
        dialog.apply_to_method(window.project.method)
        self.assertEqual(
            window.project.method.legend_components,
            ["run_id", "label", "timestamp"],
        )
        self.assertEqual(window.project.method.legend_separator, " / ")
        dialog.close()

        fake = LegendComposerDialog(window.project.method, "en")
        for row in range(fake.list_widget.count()):
            item = fake.list_widget.item(row)
            item.setCheckState(CHECKED if item.data(USER_ROLE) == "label" else UNCHECKED)
        fake.separator_edit.setText("-")
        fake.gradient_name_checkbox.setChecked(True)
        with patch("hplc_app.gui.LegendComposerDialog", return_value=fake), patch(
            "hplc_app.gui.dialog_exec", return_value=True
        ):
            window.edit_legend_composer()
        self.assertEqual(window.project.method.legend_components, ["label"])
        self.assertTrue(
            window.project.method.gradient_legend_include_dataset_name
        )
        self.assertTrue(window.project.dirty)
        window.undo()
        self.assertEqual(
            window.project.method.legend_components,
            ["run_id", "label", "timestamp"],
        )
        self.assertFalse(
            window.project.method.gradient_legend_include_dataset_name
        )
        window.project.dirty = False
        window.close()

    def test_inline_label_change_updates_every_dataset_in_the_same_run(self):
        window = self.make_window()
        shared_run = window.project.run_for(window.project.datasets[0])
        window.project.datasets[1].bind_run(shared_run)
        window.project.runs = [shared_run]
        window.project.rebuild_run_index(create_missing=False)
        window._refresh_dataset_table(0)

        label_column = next(
            column
            for column in range(window.dataset_table.columnCount())
            if window.dataset_table.horizontalHeaderItem(column).text()
            in ("ラベル", "Label")
        )
        window.dataset_table.item(0, label_column).setText("Shared run label")
        self.app.processEvents()

        self.assertEqual(
            [dataset.label for dataset in window.project.datasets],
            ["Shared run label", "Shared run label"],
        )
        self.assertEqual(
            window.dataset_table.item(1, label_column).text(), "Shared run label"
        )
        labels = window.axes.get_legend_handles_labels()[1]
        labels.extend(window.axes_right.get_legend_handles_labels()[1])
        self.assertIn("Shared run label_280 nm", labels)
        self.assertIn("Shared run label_214 nm", labels)

        window.project.dirty = False
        window.close()

    def test_wavelength_is_directly_editable_and_updates_legend(self):
        window = self.make_window()
        wavelength_item = window.dataset_table.item(0, DATASET_WAVELENGTH_COLUMN)
        self.assertTrue(bool(wavelength_item.flags() & ITEM_IS_EDITABLE))
        wavelength_item.setText("254.5")
        self.app.processEvents()
        self.assertAlmostEqual(
            window.project.datasets[0].measurement.wavelength_nm, 254.5, places=6
        )
        labels = window.axes.get_legend_handles_labels()[1]
        self.assertIn("Ch1_254.5 nm", labels)
        window.project.dirty = False
        window.close()

    def test_peak_notes_are_inline_editable_and_undoable(self):
        window = self.make_window()
        self.assertEqual(window.peak_table.columnCount(), PEAK_COLUMN_COUNT)
        note_item = window.peak_table.item(0, PEAK_NOTES_COLUMN)
        self.assertTrue(bool(note_item.flags() & ITEM_IS_EDITABLE))
        note_item.setText("LL-37 identified by MALDI-TOF MS")
        self.app.processEvents()
        self.assertEqual(
            window.project.datasets[0].peaks[0].notes,
            "LL-37 identified by MALDI-TOF MS",
        )
        window.undo()
        self.assertEqual(window.project.datasets[0].peaks[0].notes, "")
        window.project.dirty = False
        window.close()

    def test_analysis_state_restores_shared_run_metadata_and_bindings(self):
        window = self.make_window()
        first, second = window.project.datasets
        shared_run = window.project.run_for(first)
        second.bind_run(shared_run)
        window.project.runs[:] = [shared_run]
        window.project.rebuild_run_index(create_missing=False)
        original_name = first.measurement.sample_name
        original_preset = first.gradient_preset_name
        state = window._capture_analysis_state()

        first.measurement.sample_name = "changed after snapshot"
        second.gradient_preset_name = "changed gradient"
        window._restore_analysis_state(state)

        restored_first, restored_second = window.project.datasets
        self.assertEqual(restored_first.measurement.sample_name, original_name)
        self.assertEqual(restored_second.measurement.sample_name, original_name)
        self.assertEqual(restored_first.gradient_preset_name, original_preset)
        self.assertIs(restored_first.bound_run(), restored_second.bound_run())
        self.assertIs(
            window.project.run_for(restored_first), restored_first.bound_run()
        )
        self.assertEqual(len(window.project.runs), 1)
        window.project.dirty = False
        window.close()

    def test_text_annotation_dialog_and_drag_movement(self):
        window = self.make_window()
        dataset = window.project.datasets[0]
        annotation = TextAnnotation(
            text="LL-37",
            x_min=7.0,
            y_value=1000.0,
            dataset_id=dataset.id,
        )
        dialog = TextAnnotationDialog(
            annotation, window.project.datasets, "ja", allow_delete=True
        )
        dialog.text_edit.setPlainText("LL-37 peak")
        dialog.font_combo.setCurrentFont(QtGui.QFont("Arial"))
        dialog.font_size.setValue(13.5)
        dialog.x_spin.setValue(7.5)
        dialog.y_spin.setValue(1500.0)
        dialog._accept()
        self.assertEqual(annotation.text, "LL-37 peak")
        self.assertAlmostEqual(annotation.font_size, 13.5, places=6)
        self.assertAlmostEqual(annotation.x_min, 7.5, places=6)

        window.project.annotations = [annotation]
        window._plot()
        window.canvas.draw()
        artist = window._annotation_artists[annotation.id]
        self.assertEqual(artist.get_text(), "LL-37 peak")
        self.assertIsNotNone(artist.get_bbox_patch())
        extent = artist.get_window_extent(renderer=window.canvas.get_renderer())
        original_size = (extent.width, extent.height)
        window.axes.set_xlim(5.0, 10.0)
        window.axes.set_ylim(500.0, 2500.0)
        window.canvas.draw()
        zoomed_extent = artist.get_window_extent(
            renderer=window.canvas.get_renderer()
        )
        self.assertAlmostEqual(zoomed_extent.width, original_size[0], delta=0.5)
        self.assertAlmostEqual(zoomed_extent.height, original_size[1], delta=0.5)
        self.assertAlmostEqual(artist.get_fontsize(), 13.5, places=6)
        extent = zoomed_extent
        start_x = (extent.x0 + extent.x1) / 2.0
        start_y = (extent.y0 + extent.y1) / 2.0
        xdata, ydata = window.axes.transData.inverted().transform((start_x, start_y))
        window._on_canvas_press(
            SimpleNamespace(
                button=1,
                x=start_x,
                y=start_y,
                xdata=float(xdata),
                ydata=float(ydata),
                inaxes=window.axes,
                dblclick=False,
            )
        )
        window._on_canvas_motion(
            SimpleNamespace(x=start_x + 30.0, y=start_y + 20.0)
        )
        window._on_canvas_release(SimpleNamespace())
        self.assertNotAlmostEqual(annotation.x_min, 7.5, places=6)
        moved_x = window.project.annotations[0].x_min
        window.undo()
        self.assertNotAlmostEqual(window.project.annotations[0].x_min, moved_x, places=6)
        window.project.dirty = False
        window.close()

    def test_vertical_pointer_click_persists_selects_deletes_and_undoes(self):
        window = self.make_window()
        window.pointer_action.setChecked(True)
        window.canvas.draw()
        x_pixel, y_pixel = window.axes.transData.transform((7.25, 0.0))
        click = SimpleNamespace(
            button=1,
            x=float(x_pixel),
            y=float(y_pixel),
            xdata=7.25,
            ydata=0.0,
            inaxes=window.axes,
            dblclick=False,
        )
        window._on_canvas_press(click)
        self.assertEqual(len(window.project.vertical_markers), 1)
        marker = window.project.vertical_markers[0]
        self.assertAlmostEqual(marker.x_min, 7.25)
        self.assertEqual(window._selected_vertical_marker_id, marker.id)
        self.assertIn(marker.id, window._vertical_marker_artists)

        window.canvas.draw()
        x_pixel, y_pixel = window.axes.transData.transform((7.25, 0.0))
        click.x = float(x_pixel)
        click.y = float(y_pixel)
        window._on_canvas_press(click)
        self.assertEqual(len(window.project.vertical_markers), 1)
        delete_key = (
            QtCore.Qt.Key.Key_Delete if QT_API == 6 else QtCore.Qt.Key_Delete
        )
        key_press = (
            QtCore.QEvent.Type.KeyPress if QT_API == 6 else QtCore.QEvent.KeyPress
        )
        no_modifier = (
            QtCore.Qt.KeyboardModifier.NoModifier
            if QT_API == 6
            else QtCore.Qt.NoModifier
        )
        window.keyPressEvent(QtGui.QKeyEvent(key_press, delete_key, no_modifier))
        self.assertEqual(window.project.vertical_markers, [])
        window.undo()
        self.assertEqual(len(window.project.vertical_markers), 1)
        self.assertAlmostEqual(window.project.vertical_markers[0].x_min, 7.25)
        window.project.dirty = False
        window.close()

    def test_matplotlib_vertical_marker_label_follows_drag_and_undo(self):
        window = self.make_window()
        try:
            marker = VerticalMarker(x_min=6.0)
            window.project.vertical_markers = [marker]
            window._plot()
            window.mouse_mode_combo.setCurrentIndex(
                window.mouse_mode_combo.findData("select")
            )
            self.assertEqual(
                window._vertical_marker_label_artists[marker.id].get_text(),
                "6 min",
            )
            undo_count = len(window._undo_stack)
            window.project.dirty = False
            window._on_canvas_press(
                ScreenPointerEvent(
                    button=1,
                    axis_role="y1",
                    hit_region="plot",
                    data_coordinates=(("y1", 6.0, 0.0),),
                    hit_kind="vertical_marker",
                    hit_id=marker.id,
                )
            )
            self.assertFalse(window._span_selector.active)
            window._on_canvas_motion(
                ScreenPointerEvent(
                    axis_role="y1",
                    hit_region="plot",
                    data_coordinates=(("y1", 8.5, 0.0),),
                )
            )
            self.assertEqual(marker.x_min, 8.5)
            self.assertEqual(
                window._vertical_marker_label_artists[marker.id].get_text(),
                "8.5 min",
            )
            window._on_canvas_release(ScreenPointerEvent(button=1))
            self.assertTrue(window._span_selector.active)
            self.assertEqual(len(window._undo_stack), undo_count + 1)
            window.undo()
            restored = window.project.vertical_markers[0]
            self.assertEqual(restored.x_min, 6.0)
            self.assertEqual(
                window._vertical_marker_label_artists[restored.id].get_text(),
                "6 min",
            )
        finally:
            window.project.dirty = False
            window.close()

    def test_canvas_press_accepts_backend_neutral_hit_targets(self):
        window = self.make_window()
        dataset = window.project.datasets[0]
        marker = VerticalMarker(x_min=6.0)
        annotation = TextAnnotation(
            text="Target",
            x_min=7.0,
            y_value=1000.0,
            dataset_id=dataset.id,
        )
        window.project.vertical_markers = [marker]
        window.project.annotations = [annotation]
        window._plot()

        window._on_canvas_press(
            ScreenPointerEvent(
                button=1,
                axis_role="y1",
                data_coordinates=(("y1", 6.0, 0.0),),
                hit_kind="vertical_marker",
                hit_id=marker.id,
            )
        )
        self.assertEqual(window._selected_vertical_marker_id, marker.id)
        window._on_canvas_release(ScreenPointerEvent(button=1))

        window._on_canvas_press(
            ScreenPointerEvent(
                button=1,
                axis_role="y1",
                data_coordinates=(("y1", 7.0, 1000.0),),
                hit_kind="annotation",
                hit_id=annotation.id,
            )
        )
        self.assertIsNotNone(window._annotation_drag)
        window._on_canvas_motion(
            ScreenPointerEvent(
                axis_role="y1",
                data_coordinates=(("y1", 7.5, 1100.0),),
            )
        )
        window._on_canvas_release(ScreenPointerEvent(button=1))
        self.assertAlmostEqual(annotation.x_min, 7.5, places=6)
        self.assertAlmostEqual(annotation.y_value, 1100.0, places=6)
        self.assertIsNone(window._annotation_drag)
        window.project.dirty = False
        window.close()

    def test_canvas_press_motion_and_release_accept_backend_neutral_events(self):
        window = self.make_window()
        selected = window.project.datasets[0]
        window.pointer_action.setChecked(True)
        window._on_canvas_press(
            ScreenPointerEvent(
                button=1,
                axis_role="y1",
                data_coordinates=(("y1", 7.25, 0.0),),
            )
        )
        self.assertEqual(len(window.project.vertical_markers), 1)
        self.assertAlmostEqual(window.project.vertical_markers[0].x_min, 7.25)

        window.pointer_action.setChecked(False)
        window.integrate_button.setChecked(True)
        window._on_canvas_motion(
            ScreenPointerEvent(
                axis_role="y1",
                data_coordinates=(("y1", 6.5, 100.0),),
            )
        )
        self.assertTrue(window._interaction_cursor.get_visible())
        self.assertAlmostEqual(
            float(window._interaction_cursor.get_xdata()[0]), 6.5, places=6
        )

        window.integrate_button.setChecked(False)
        window.move_trace_button.setChecked(True)
        initial_shift = selected.x_shift_min
        initial_offset = selected.offset
        window._on_canvas_press(
            ScreenPointerEvent(
                button=1,
                axis_role="y1",
                data_coordinates=(("y1", 1.0, 100.0),),
            )
        )
        window._on_canvas_motion(
            ScreenPointerEvent(
                axis_role="y1",
                data_coordinates=(("y1", 1.5, 1100.0),),
            )
        )
        window._on_canvas_release(ScreenPointerEvent(button=1))
        self.assertAlmostEqual(selected.x_shift_min, initial_shift + 0.5, places=6)
        self.assertAlmostEqual(selected.offset, initial_offset + 1000.0, places=6)
        self.assertIsNone(window._move_drag)
        window.project.dirty = False
        window.close()

    def test_preview_rectangle_zoom_modes_history_and_cancellation(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.show()
            window.view_mode_combo.setCurrentIndex(window.view_mode_combo.findData("split_y_axes"))
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            consumer = preview.consumer
            initial = window._screen_view_state()
            for configured, role in (("x", "y1"), ("y", "y2"), ("both", "y1"),
                                     ("auto", "y2")):
                with self.subTest(configured=configured, role=role):
                    window._apply_view_state(initial)
                    consumer.apply_view_state(initial, consumer.overview_state)
                    window._view_history.clear()
                    window._view_history.ensure_home(initial)
                    window.toolbar.set_history_buttons()
                    window.zoom_axis_combo.setCurrentIndex(
                        window.zoom_axis_combo.findData(configured))
                    if not window.toolbar._actions["zoom"].isChecked():
                        window.toolbar._actions["zoom"].trigger()
                    def event(x, y, pixel_x, pixel_y):
                        return ScreenPointerEvent(
                            button=1, axis_role=role,
                            hit_region="plot_y2" if role == "y2" else "plot_y1",
                            canvas_x=pixel_x, canvas_y=pixel_y,
                            data_coordinates=((role, x, y),),
                        )
                    before_count = window._view_history.count
                    preview.handle_event("button_press_event", event(10.0, 100.0, 100.0, 100.0))
                    preview.handle_event("motion_notify_event", event(20.0, 500.0, 300.0, 300.0))
                    self.assertTrue(consumer.zoom_rectangle.isVisible())
                    preview.handle_event("button_release_event", event(20.0, 500.0, 300.0, 300.0))
                    state = window._screen_view_state()
                    mode = "both" if configured == "auto" else configured
                    self.assertEqual(state.x, (10.0, 20.0) if mode in ("x", "both") else initial.x)
                    if mode in ("y", "both"):
                        self.assertEqual(getattr(state, role), (100.0, 500.0))
                    self.assertEqual(window._view_history.count, before_count)
                    self.assertTrue(window.toolbar._actions["back"].isEnabled())
                    window.toolbar._actions["back"].trigger()
                    self.assertEqual(window._screen_view_state(), initial)
                    window.toolbar._actions["forward"].trigger()
                    self.assertEqual(window._screen_view_state(), state)
            # Click-only, wrong-button and Escape do not change the view/history.
            before = window._screen_view_state()
            count = window._view_history.count
            sample = ScreenPointerEvent(button=1, axis_role="y1", hit_region="plot_y1",
                                        canvas_x=10.0, canvas_y=10.0,
                                        data_coordinates=(("y1", 12.0, 200.0),))
            preview.handle_event("button_press_event", sample)
            preview.handle_event("button_release_event", sample)
            self.assertEqual(window._screen_view_state(), before)
            preview.handle_event("button_press_event", sample)
            escape = consumer.qt_gui.QKeyEvent(
                consumer.qt_core.QEvent.Type.KeyPress, consumer.qt_core.Qt.Key.Key_Escape,
                consumer.qt_core.Qt.KeyboardModifier.NoModifier)
            self.app.sendEvent(consumer.widget.viewport(), escape)
            self.assertIsNone(preview._zoom_drag)
            self.assertEqual(window._view_history.count, count)
        finally:
            if window.toolbar._actions["zoom"].isChecked():
                window.toolbar._actions["zoom"].trigger()
            window.project.dirty = False
            window.close()

    def test_preview_zoom_is_dedicated_and_keeps_drag_ownership_outside_plot(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.show()
            window.zoom_axis_combo.setCurrentIndex(
                window.zoom_axis_combo.findData("both")
            )
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            consumer = preview.consumer
            initial = window._screen_view_state()
            self.assertEqual(window._mouse_mode, "normal")

            def event(x, y, pixel_x, pixel_y, button):
                return ScreenPointerEvent(
                    button=button, axis_role="y1", hit_region="plot",
                    canvas_x=pixel_x, canvas_y=pixel_y,
                    data_coordinates=(("y1", x, y),),
                )

            window._ensure_normal_mode_navigation()
            preview.handle_event(
                "button_press_event", event(10.0, 100.0, 100.0, 100.0, 3)
            )
            self.assertIsNone(preview._zoom_drag)
            window.mouse_mode_combo.setCurrentIndex(
                window.mouse_mode_combo.findData("zoom")
            )
            before_count = window._view_history.count
            preview.handle_event(
                "button_press_event", event(10.0, 100.0, 100.0, 100.0, 1)
            )
            outside = ScreenPointerEvent(
                axis_role="outside", hit_region="", canvas_x=900.0,
                canvas_y=900.0,
                data_coordinates=(("y1", initial.x[1] + 50.0,
                                  initial.y1[1] + 500.0),),
            )
            preview.handle_event(
                "motion_notify_event", outside
            )
            self.assertTrue(consumer.zoom_rectangle.isVisible())
            preview.handle_event("button_release_event", outside)
            state = window._screen_view_state()
            self.assertEqual(state.x, (10.0, initial.x[1]))
            self.assertEqual(state.y1, (100.0, initial.y1[1]))
            self.assertEqual(window._view_history.count, before_count)
            self.assertTrue(window.toolbar._actions["back"].isEnabled())
            window.toolbar._actions["back"].trigger()
            self.assertEqual(window._screen_view_state(), initial)
            preview.handle_event(
                "button_press_event", event(10.0, 100.0, 100.0, 100.0, 1)
            )
            preview.handle_event(
                "button_release_event",
                ScreenPointerEvent(axis_role="outside", canvas_x=300.0,
                                   canvas_y=300.0),
            )
            self.assertIsNone(preview._zoom_drag)
            self.assertEqual(window._screen_view_state(), initial)
        finally:
            if window.toolbar._actions["zoom"].isChecked():
                window.toolbar._actions["zoom"].trigger()
            window.project.dirty = False
            window.close()

    def test_preview_zoom_double_click_back_and_axis_only_drags(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.show()
            window.view_mode_combo.setCurrentIndex(
                window.view_mode_combo.findData("split_y_axes")
            )
            window.screen_preview_checkbox.setChecked(True)
            window.mouse_mode_combo.setCurrentIndex(
                window.mouse_mode_combo.findData("zoom")
            )
            preview = window._screen_preview
            initial = window._screen_view_state()
            narrowed = replace(
                initial, x=(5.0, 25.0), y1=(50.0, 550.0),
                y2=(100.0, 900.0),
            )

            def event(region, role, x, y, pixel_x, pixel_y, **kwargs):
                return ScreenPointerEvent(
                    button=1, axis_role=role, hit_region=region,
                    canvas_x=pixel_x, canvas_y=pixel_y,
                    data_coordinates=((role, x, y),), **kwargs
                )

            window._view_history.clear()
            window._view_history.ensure_home(initial)
            preview.navigation._record_and_apply(narrowed)
            window._apply_view_state(preview.consumer.capture_view_state())
            preview.handle_event(
                "button_press_event",
                event("plot_y1", "y1", 10.0, 200.0, 100.0, 100.0,
                      double_click=True),
            )
            self.assertEqual(window._screen_view_state(), initial)

            # Normal mode continues to use the navigation controller's Back.
            window._view_history.clear()
            window._view_history.ensure_home(initial)
            preview.navigation._record_and_apply(narrowed)
            window._apply_view_state(preview.consumer.capture_view_state())
            window.mouse_mode_combo.setCurrentIndex(
                window.mouse_mode_combo.findData("normal")
            )
            preview.handle_event(
                "button_press_event",
                event("plot_y1", "y1", 10.0, 200.0, 100.0, 100.0,
                      double_click=True),
            )
            self.assertEqual(window._screen_view_state(), initial)
            window.mouse_mode_combo.setCurrentIndex(
                window.mouse_mode_combo.findData("zoom")
            )

            # With no older entry, double-click is a defined no-op.
            window._view_history.clear()
            window._view_history.ensure_home(initial)
            preview.handle_event(
                "button_press_event",
                event("plot_y1", "y1", 10.0, 200.0, 100.0, 100.0,
                      double_click=True),
            )
            self.assertEqual(window._screen_view_state(), initial)

            for region, role, configured, expected in (
                ("x", "y1", "y", "x"),
                ("y1", "y1", "x", "y1"),
                ("y2", "y2", "x", "y2"),
            ):
                with self.subTest(region=region):
                    window._apply_view_state(initial)
                    window.zoom_axis_combo.setCurrentIndex(
                        window.zoom_axis_combo.findData(configured)
                    )
                    start = event(region, role, 10.0, 100.0, 100.0, 100.0)
                    end = (
                        ScreenPointerEvent(
                            button=1, axis_role="outside", hit_region="",
                            canvas_x=300.0, canvas_y=300.0,
                            data_coordinates=((role, 20.0, 500.0),),
                        )
                        if expected == "y2"
                        else event(region, role, 20.0, 500.0, 300.0, 300.0)
                    )
                    preview.handle_event("button_press_event", start)
                    self.assertEqual(
                        preview._zoom_drag["mode"],
                        "x" if expected == "x" else "y",
                    )
                    preview.handle_event("motion_notify_event", end)
                    self.assertTrue(preview.consumer.zoom_rectangle.isVisible())
                    preview.handle_event("button_release_event", end)
                    changed = window._screen_view_state()
                    self.assertEqual(
                        changed.x, (10.0, 20.0) if expected == "x" else initial.x
                    )
                    self.assertEqual(
                        changed.y1,
                        (100.0, 500.0) if expected == "y1" else initial.y1,
                    )
                    self.assertEqual(
                        changed.y2,
                        (100.0, 500.0) if expected == "y2" else initial.y2,
                    )
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_overview_drag_zooms_detail_without_recentering_clicks(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.view_mode_combo.setCurrentIndex(
                window.view_mode_combo.findData("overview_detail")
            )
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            initial = window._screen_view_state()
            window._apply_view_state(replace(initial, x=(5.0, 25.0)))
            window.mouse_mode_combo.setCurrentIndex(
                window.mouse_mode_combo.findData("zoom")
            )

            def overview_event(x, pixel, axis_role="overview_y1"):
                return ScreenPointerEvent(
                    button=1, axis_role=axis_role,
                    hit_region="x" if axis_role == "overview_y1" else "",
                    canvas_x=pixel, canvas_y=20.0,
                    data_coordinates=(("overview_y1", x, 0.0),),
                )

            preview.handle_event(
                "button_press_event", overview_event(10.0, 100.0)
            )
            preview.handle_event(
                "motion_notify_event", overview_event(20.0, 300.0, "outside")
            )
            self.assertIsNotNone(preview._zoom_drag)
            preview.handle_event(
                "button_release_event", overview_event(20.0, 300.0, "outside")
            )
            self.assertEqual(window._current_overview_x(), window._full_x_bounds())
            self.assertEqual(window._screen_view_state().x, (10.0, 20.0))

            click = overview_event(30.0, 200.0)
            preview.handle_event("button_press_event", click)
            preview.handle_event("button_release_event", click)
            self.assertEqual(window._screen_view_state().x, (10.0, 20.0))

            window.mouse_mode_combo.setCurrentIndex(
                window.mouse_mode_combo.findData("normal")
            )
            window._zoom_overview(0.8)
            history_before_pan = preview.navigation.history.count
            preview.handle_event(
                "button_press_event", overview_event(40.0, 200.0)
            )
            overview_before_pan = window._current_overview_x()
            preview.handle_event(
                "motion_notify_event", overview_event(35.0, 100.0, "outside")
            )
            preview.handle_event(
                "button_release_event", overview_event(35.0, 100.0, "outside")
            )
            self.assertNotEqual(window._current_overview_x(), overview_before_pan)
            self.assertEqual(window._screen_view_state().x, (10.0, 20.0))
            self.assertEqual(preview.navigation.history.count, history_before_pan)
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_text_label_drag_hit_testing_and_persistence(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        from hplc_app.project_io import load_project, save_project
        window = self.make_window()
        try:
            first, second = window.project.datasets
            annotations = [
                TextAnnotation(text="Y1 label", x_min=7.0, y_value=1000.0,
                               dataset_id=first.id, y_axis=1, font_size=13.0),
                TextAnnotation(text="Y2 label", x_min=8.0, y_value=1500.0,
                               dataset_id=second.id, y_axis=2, font_size=11.0),
            ]
            window.project.annotations = annotations
            window.view_mode_combo.setCurrentIndex(window.view_mode_combo.findData("split_y_axes"))
            window._plot()
            window.show()
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            consumer = preview.consumer
            core, gui = consumer.qt_core, consumer.qt_gui
            viewport = consumer.widget.viewport()
            raw = [d.intensity_uv.copy() for d in window.project.datasets]
            for source_annotation, role in zip(annotations, ("y1", "y2")):
                with self.subTest(role=role):
                    preview = window._screen_preview
                    consumer = preview.consumer
                    annotation = next(
                        item for item in window.project.annotations
                        if item.id == source_annotation.id
                    )
                    item = consumer.annotation_items[annotation.id]
                    rect = item.sceneBoundingRect()
                    start_scene = rect.center()
                    hit = consumer.pointer_event(start_scene, button=1)
                    self.assertEqual((hit.hit_kind, hit.hit_id), ("annotation", annotation.id))
                    view = consumer.secondary if role == "y2" else consumer.primary.vb
                    start_data = hit.data_for(role)
                    end_scene = view.mapViewToScene(core.QPointF(
                        start_data[0] + 0.75, start_data[1] + 325.0
                    ))
                    start = consumer.widget.mapFromScene(start_scene)
                    end = consumer.widget.mapFromScene(end_scene)
                    end_event = consumer.pointer_event(end_scene)
                    dx = end_event.data_for(role)[0] - start_data[0]
                    dy = end_event.data_for(role)[1] - start_data[1]
                    before = deepcopy(annotation)
                    undo_count = len(window._undo_stack)
                    window.project.dirty = False
                    def mouse(kind, position, button=core.Qt.MouseButton.NoButton,
                              held=core.Qt.MouseButton.NoButton):
                        self.app.sendEvent(viewport, gui.QMouseEvent(
                            kind, core.QPointF(position), core.QPointF(viewport.mapToGlobal(position)),
                            button, held, core.Qt.KeyboardModifier.NoModifier))
                    if role == "y1":
                        mouse(core.QEvent.Type.MouseButtonPress, start,
                              core.Qt.MouseButton.LeftButton, core.Qt.MouseButton.LeftButton)
                    else:
                        preview.handle_event("button_press_event", hit)
                    self.assertIsNotNone(preview._annotation_target)
                    if role == "y1":
                        mouse(core.QEvent.Type.MouseMove, end, held=core.Qt.MouseButton.LeftButton)
                    else:
                        preview.handle_event("motion_notify_event", ScreenPointerEvent(
                            **dict(vars(end_event), button=1)))
                    self.assertNotEqual((annotation.x_min, annotation.y_value),
                                        (before.x_min, before.y_value))
                    self.assertAlmostEqual(item.pos().x(), annotation.x_min, places=6)
                    self.assertEqual(len(window._undo_stack), undo_count)
                    self.assertFalse(window.project.dirty)
                    if role == "y1":
                        mouse(core.QEvent.Type.MouseButtonRelease, end, core.Qt.MouseButton.LeftButton)
                    else:
                        preview.handle_event("button_release_event", ScreenPointerEvent(
                            **dict(vars(end_event), button=1)))
                    self.assertIs(window._screen_preview, preview)
                    self.assertIsNone(preview._annotation_target)
                    self.assertEqual(len(window._undo_stack), undo_count + 1)
                    moved = (annotation.x_min, annotation.y_value)
                    window.undo()
                    restored_annotation = next(a for a in window.project.annotations if a.id == annotation.id)
                    self.assertEqual((restored_annotation.x_min, restored_annotation.y_value),
                                     (before.x_min, before.y_value))
                    window.redo()
                    annotation = next(a for a in window.project.annotations if a.id == annotation.id)
                    self.assertAlmostEqual(annotation.x_min, moved[0], places=6)
            with tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / "native-labels.hplcproj")
                save_project(path, window.project)
                restored = load_project(path)
                self.assertEqual(restored.annotations, window.project.annotations)
                output = Path(directory) / "labels.svg"
                window._save_figure_file(str(output))
                self.assertGreater(output.stat().st_size, 0)
            for dataset, values in zip(window.project.datasets, raw):
                np.testing.assert_array_equal(dataset.intensity_uv, values)
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_text_label_create_edit_delete_and_cancel(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.show()
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            consumer = preview.consumer
            core, gui = consumer.qt_core, consumer.qt_gui
            view = consumer.primary.vb
            def event_at(x, y, **changes):
                scene = view.mapViewToScene(core.QPointF(x, y))
                return ScreenPointerEvent(**dict(vars(consumer.pointer_event(scene, button=1)), **changes))
            def accept_create(dialog):
                dialog.annotation.text = "Native label"
                dialog.annotation.font_size = 14.0
                dialog.annotation.background_color = "#ffffcc"
                return 1
            window.annotation_action.setChecked(True)
            with patch("hplc_app.gui.dialog_exec", side_effect=accept_create):
                preview.handle_event("button_press_event", event_at(6.5, 750.0))
            self.assertEqual(len(window.project.annotations), 1)
            annotation = window.project.annotations[0]
            self.assertEqual(annotation.text, "Native label")
            self.assertFalse(window.annotation_action.isChecked())
            self.assertIn(annotation.id, consumer.annotation_items)
            undo_count = len(window._undo_stack)
            item = consumer.annotation_items[annotation.id]
            hit = consumer.pointer_event(item.sceneBoundingRect().center(), button=1,
                                         double_click=True)
            def accept_edit(dialog):
                dialog.annotation.text = "Edited native label"
                dialog.annotation.color = "#123456"
                return 1
            with patch("hplc_app.gui.dialog_exec", side_effect=accept_edit):
                preview.handle_event("button_press_event", hit)
            self.assertEqual(annotation.text, "Edited native label")
            self.assertEqual(len(window._undo_stack), undo_count + 1)
            window.undo()
            annotation = window.project.annotations[0]
            self.assertEqual(annotation.text, "Native label")
            window.redo()
            annotation = window.project.annotations[0]
            self.assertEqual(annotation.text, "Edited native label")
            # Escape/focus loss restore a live drag without history or dirty changes.
            preview = window._screen_preview
            consumer = preview.consumer
            item = consumer.annotation_items[annotation.id]
            start = consumer.pointer_event(item.sceneBoundingRect().center(), button=1)
            original = deepcopy(annotation)
            undo_count = len(window._undo_stack)
            window.project.dirty = False
            preview.handle_event("button_press_event", start)
            preview.handle_event("motion_notify_event", event_at(annotation.x_min + 1.0,
                                                                  annotation.y_value + 500.0))
            self.assertNotEqual((annotation.x_min, annotation.y_value),
                                (original.x_min, original.y_value))
            escape = gui.QKeyEvent(core.QEvent.Type.KeyPress, core.Qt.Key.Key_Escape,
                                   core.Qt.KeyboardModifier.NoModifier)
            self.app.sendEvent(consumer.widget.viewport(), escape)
            annotation = window.project.annotations[0]
            self.assertEqual(annotation, original)
            self.assertEqual(len(window._undo_stack), undo_count)
            self.assertFalse(window.project.dirty)
            # Renderer failure restores the transient model before fallback.
            preview = window._screen_preview
            consumer = preview.consumer
            item = consumer.annotation_items[annotation.id]
            start = consumer.pointer_event(item.sceneBoundingRect().center(), button=1)
            original = deepcopy(annotation)
            preview.handle_event("button_press_event", start)
            with patch.object(consumer, "set_annotation_position",
                              side_effect=RuntimeError("label failure")):
                preview.handle_event("motion_notify_event", event_at(
                    annotation.x_min + 1.0, annotation.y_value + 500.0
                ))
            self.assertIsNone(window._screen_preview)
            annotation = window.project.annotations[0]
            self.assertEqual(annotation, original)
            window.screen_preview_checkbox.setChecked(True)
            # Marker hit testing retains precedence where both targets overlap.
            window.project.vertical_markers = [VerticalMarker(x_min=annotation.x_min)]
            window._plot()
            preview = window._screen_preview
            item = preview.consumer.annotation_items[annotation.id]
            overlap_scene = preview.consumer.primary.vb.mapViewToScene(
                core.QPointF(annotation.x_min, annotation.y_value)
            )
            overlap = preview.consumer.pointer_event(overlap_scene, button=1)
            self.assertEqual(overlap.hit_kind, "vertical_marker")
            window.project.vertical_markers = []
            window._plot()
            preview = window._screen_preview
            consumer = preview.consumer
            item = consumer.annotation_items[annotation.id]
            delete_hit = consumer.pointer_event(item.sceneBoundingRect().center(), button=1,
                                                double_click=True)
            def accept_delete(dialog):
                dialog.delete_requested = True
                return 1
            with patch("hplc_app.gui.dialog_exec", side_effect=accept_delete):
                preview.handle_event("button_press_event", delete_hit)
            self.assertEqual(window.project.annotations, [])
            window.undo()
            self.assertEqual(len(window.project.annotations), 1)
            window.redo()
            self.assertEqual(window.project.annotations, [])
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_trace_move_matches_existing_callback_and_persistence(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        from hplc_app.project_io import load_project, save_project
        window = self.make_window()
        try:
            window.show()
            raw = [(d.time_min.copy(), d.intensity_uv.copy()) for d in window.project.datasets]
            cases = (("single", "both", 0, 0.75, 650.0),
                     ("overview_detail", "x", 0, -0.5, 0.0),
                     ("split_y_axes", "y", 1, 0.0, -425.0))
            for mode, direction, row, dx, dy in cases:
                with self.subTest(mode=mode, direction=direction):
                    window.dataset_table.selectRow(row)
                    window.view_mode_combo.setCurrentIndex(window.view_mode_combo.findData(mode))
                    window.move_axis_combo.setCurrentIndex(window.move_axis_combo.findData(direction))
                    window._plot()
                    selected = window.project.datasets[row]
                    before = deepcopy(selected)
                    other = _trace_edit_state([window.project.datasets[1 - row]])
                    window.move_trace_button.setChecked(True)
                    window.screen_preview_checkbox.setChecked(True)
                    preview = window._screen_preview
                    consumer = preview.consumer
                    core, gui = consumer.qt_core, consumer.qt_gui
                    role = "y2" if mode == "split_y_axes" and row else "y1"
                    view = consumer.secondary if role == "y2" else consumer.primary.vb
                    viewport = consumer.widget.viewport()
                    x0, y0 = 8.0, sum(view.viewRange()[1]) / 2.0
                    def point(x, y):
                        return consumer.widget.mapFromScene(view.mapViewToScene(core.QPointF(x, y)))
                    start, end = point(x0, y0), point(x0 + dx, y0 + dy)
                    start_event = consumer.pointer_event(consumer.widget.mapToScene(start))
                    end_event = consumer.pointer_event(consumer.widget.mapToScene(end))
                    effective_dx = (end_event.data_for(role)[0] - start_event.data_for(role)[0]
                                    if direction in ("x", "both") else 0.0)
                    effective_dy = (end_event.data_for(role)[1] - start_event.data_for(role)[1]
                                    if direction in ("y", "both") else 0.0)
                    expected = deepcopy(selected)
                    expected.x_shift_min += effective_dx
                    expected.offset += effective_dy
                    recalculate_dataset_peaks(expected)
                    item = consumer.trace_items[selected.id]
                    overview_item = consumer.overview_trace_items[selected.id]
                    undo_count = len(window._undo_stack)
                    state = window._screen_view_state()
                    window.project.dirty = False
                    def mouse(kind, position, button=core.Qt.MouseButton.NoButton,
                              held=core.Qt.MouseButton.NoButton):
                        self.app.sendEvent(viewport, gui.QMouseEvent(
                            kind, core.QPointF(position), core.QPointF(viewport.mapToGlobal(position)),
                            button, held, core.Qt.KeyboardModifier.NoModifier))
                    mouse(core.QEvent.Type.MouseButtonPress, start,
                          core.Qt.MouseButton.LeftButton, core.Qt.MouseButton.LeftButton)
                    mouse(core.QEvent.Type.MouseMove, end, held=core.Qt.MouseButton.LeftButton)
                    self.assertIsNotNone(preview._move_target)
                    self.assertAlmostEqual(selected.x_shift_min, expected.x_shift_min, places=6)
                    self.assertAlmostEqual(selected.offset, expected.offset, places=5)
                    self.assertAlmostEqual(item.pos().x(), effective_dx, places=6)
                    self.assertAlmostEqual(item.pos().y(), effective_dy, places=5)
                    self.assertAlmostEqual(overview_item.pos().x(), effective_dx, places=6)
                    self.assertEqual(len(window._undo_stack), undo_count)
                    self.assertFalse(window.project.dirty)
                    mouse(core.QEvent.Type.MouseButtonRelease, end, core.Qt.MouseButton.LeftButton)
                    self.assertIs(window._screen_preview, preview)
                    self.assertIsNone(preview._move_target)
                    self.assertIsNone(window._move_drag)
                    self.assertEqual(len(window._undo_stack), undo_count + 1)
                    # A changed panel ratio can shift the Qt pointer transform
                    # by a sub-pixel. Recalculate from the committed movement,
                    # rather than requiring the pre-dispatch estimate to be
                    # bit-for-bit identical.
                    expected.x_shift_min = selected.x_shift_min
                    expected.offset = selected.offset
                    recalculate_dataset_peaks(expected)
                    self.assertEqual(selected.peaks, expected.peaks)
                    self.assertEqual(_trace_edit_state([window.project.datasets[1 - row]]), other)
                    self.assertEqual(window._screen_view_state(), state)
                    self.assertAlmostEqual(
                        float(window.dataset_table.item(row, DATASET_X_SHIFT_COLUMN).text()),
                        selected.x_shift_min, places=4,
                    )
                    window.undo()
                    self.assertEqual(selected.x_shift_min, before.x_shift_min)
                    self.assertEqual(selected.offset, before.offset)
                    window.redo()
                    self.assertAlmostEqual(selected.x_shift_min, expected.x_shift_min, places=6)
                    self.assertAlmostEqual(selected.offset, expected.offset, places=5)
            with tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / "native-moved.hplcproj")
                save_project(path, window.project)
                restored = load_project(path)
                self.assertEqual([(d.x_shift_min, d.offset, d.peaks) for d in restored.datasets],
                                 [(d.x_shift_min, d.offset, d.peaks) for d in window.project.datasets])
                with patch.object(window.canvas.callbacks, "exception_handler",
                                  side_effect=AssertionError("Unexpected render callback error")):
                    output = Path(directory) / "moved.svg"
                    window._save_figure_file(str(output))
                self.assertGreater(output.stat().st_size, 0)
            for dataset, (times, values) in zip(window.project.datasets, raw):
                np.testing.assert_array_equal(dataset.time_min, times)
                np.testing.assert_array_equal(dataset.intensity_uv, values)
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_trace_move_supports_second_axis_in_single_view(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.show()
            # The second dataset is assigned to Y2 while the single panel keeps
            # every trace on one view, so the pointer always reports role "y1".
            row = 1
            selected = window.project.datasets[row]
            self.assertEqual(selected.y_axis, 2)
            window.dataset_table.selectRow(row)
            window.view_mode_combo.setCurrentIndex(
                window.view_mode_combo.findData("single")
            )
            window.move_axis_combo.setCurrentIndex(
                window.move_axis_combo.findData("both")
            )
            window._plot()
            before = deepcopy(selected)
            other = _trace_edit_state([window.project.datasets[0]])
            window.move_trace_button.setChecked(True)
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            consumer = preview.consumer
            core, gui = consumer.qt_core, consumer.qt_gui
            self.assertFalse(consumer.split_y_axes)

            def raw_digest():
                digest = hashlib.sha256()
                for current in window.project.datasets:
                    digest.update(np.ascontiguousarray(current.time_min).tobytes())
                    digest.update(np.ascontiguousarray(current.intensity_uv).tobytes())
                return digest.hexdigest()

            before_digest = raw_digest()
            view = consumer.secondary
            viewport = consumer.widget.viewport()
            dx, dy = 0.75, -425.0
            x0, y0 = 8.0, sum(view.viewRange()[1]) / 2.0

            def point(x, y):
                return consumer.widget.mapFromScene(
                    view.mapViewToScene(core.QPointF(x, y))
                )

            start, end = point(x0, y0), point(x0 + dx, y0 + dy)
            start_event = consumer.pointer_event(consumer.widget.mapToScene(start))
            end_event = consumer.pointer_event(consumer.widget.mapToScene(end))
            # The regression this covers: the drag ran on "y2" data while every
            # pointer sample reported the single panel's "y1" role.
            self.assertEqual(start_event.axis_role, "y1")
            self.assertEqual(end_event.axis_role, "y1")
            effective_dx = end_event.data_for("y2")[0] - start_event.data_for("y2")[0]
            effective_dy = end_event.data_for("y2")[1] - start_event.data_for("y2")[1]
            expected = deepcopy(selected)
            expected.x_shift_min += effective_dx
            expected.offset += effective_dy
            recalculate_dataset_peaks(expected)
            item = consumer.trace_items[selected.id]
            undo_count = len(window._undo_stack)
            state = window._screen_view_state()
            window.project.dirty = False

            def mouse(kind, position, button=core.Qt.MouseButton.NoButton,
                      held=core.Qt.MouseButton.NoButton):
                self.app.sendEvent(viewport, gui.QMouseEvent(
                    kind, core.QPointF(position),
                    core.QPointF(viewport.mapToGlobal(position)),
                    button, held, core.Qt.KeyboardModifier.NoModifier))

            mouse(core.QEvent.Type.MouseButtonPress, start,
                  core.Qt.MouseButton.LeftButton, core.Qt.MouseButton.LeftButton)
            mouse(core.QEvent.Type.MouseMove, end,
                  held=core.Qt.MouseButton.LeftButton)
            self.assertIsNotNone(preview._move_target)
            self.assertAlmostEqual(
                selected.x_shift_min, expected.x_shift_min, places=6
            )
            self.assertAlmostEqual(selected.offset, expected.offset, places=5)
            self.assertAlmostEqual(item.pos().x(), effective_dx, places=6)
            self.assertAlmostEqual(item.pos().y(), effective_dy, places=5)
            mouse(core.QEvent.Type.MouseButtonRelease, end,
                  core.Qt.MouseButton.LeftButton)
            self.assertIsNone(preview._move_target)
            self.assertIsNone(window._move_drag)
            self.assertEqual(len(window._undo_stack), undo_count + 1)
            self.assertEqual(selected.peaks, expected.peaks)
            self.assertEqual(
                _trace_edit_state([window.project.datasets[0]]), other
            )
            self.assertEqual(window._screen_view_state(), state)
            self.assertEqual(raw_digest(), before_digest)
            window.undo()
            self.assertEqual(selected.x_shift_min, before.x_shift_min)
            self.assertEqual(selected.offset, before.offset)
            window.redo()
            self.assertAlmostEqual(
                selected.x_shift_min, expected.x_shift_min, places=6
            )
            self.assertAlmostEqual(selected.offset, expected.offset, places=5)
            self.assertEqual(raw_digest(), before_digest)
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_trace_move_reset_restores_exact_display_and_raw_digest(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.show()
            window.view_mode_combo.setCurrentIndex(
                window.view_mode_combo.findData("overview_detail")
            )
            window.dataset_table.selectRow(0)
            window.move_axis_combo.setCurrentIndex(
                window.move_axis_combo.findData("both")
            )
            window.screen_preview_checkbox.setChecked(True)
            window.move_trace_button.setChecked(True)
            preview = window._screen_preview
            consumer = preview.consumer
            dataset = window.project.datasets[0]

            def raw_digest():
                digest = hashlib.sha256()
                for current in window.project.datasets:
                    digest.update(np.ascontiguousarray(current.time_min).tobytes())
                    digest.update(np.ascontiguousarray(current.intensity_uv).tobytes())
                return digest.hexdigest()

            before_digest = raw_digest()
            initial_detail = tuple(
                values.copy() for values in consumer.trace_items[dataset.id].getData()
            )
            initial_overview = tuple(
                values.copy()
                for values in consumer.overview_trace_items[dataset.id].getData()
            )
            press = ScreenPointerEvent(
                button=1, axis_role="y1", hit_region="plot",
                canvas_x=100.0, canvas_y=100.0,
                data_coordinates=(("y1", 1.0, 100.0),),
            )
            moved = ScreenPointerEvent(
                button=1, axis_role="y1", hit_region="plot",
                canvas_x=140.0, canvas_y=140.0,
                data_coordinates=(("y1", 1.5, 1100.0),),
            )
            preview.handle_event("button_press_event", press)
            preview.handle_event("motion_notify_event", moved)
            self.assertEqual(
                (consumer.trace_items[dataset.id].pos().x(),
                 consumer.trace_items[dataset.id].pos().y()),
                (0.5, 1000.0),
            )
            preview.handle_event("button_release_event", moved)
            self.assertEqual(dataset.x_shift_min, 0.5)
            self.assertEqual(dataset.offset, 1000.0)
            self.assertEqual(
                (consumer.trace_items[dataset.id].pos().x(),
                 consumer.trace_items[dataset.id].pos().y()),
                (0.0, 0.0),
            )
            self.assertEqual(
                (consumer.overview_trace_items[dataset.id].pos().x(),
                 consumer.overview_trace_items[dataset.id].pos().y()),
                (0.0, 0.0),
            )

            window.dataset_table.item(0, DATASET_X_SHIFT_COLUMN).setText("0")
            window.dataset_table.item(0, DATASET_OFFSET_COLUMN).setText("0")
            self.app.processEvents()
            self.assertEqual((dataset.x_shift_min, dataset.offset), (0.0, 0.0))
            final_detail = consumer.trace_items[dataset.id].getData()
            final_overview = consumer.overview_trace_items[dataset.id].getData()
            for actual, expected in zip(final_detail, initial_detail):
                np.testing.assert_array_equal(actual, expected)
            for actual, expected in zip(final_overview, initial_overview):
                np.testing.assert_array_equal(actual, expected)
            self.assertEqual(
                (consumer.trace_items[dataset.id].pos().x(),
                 consumer.trace_items[dataset.id].pos().y()),
                (0.0, 0.0),
            )
            self.assertEqual(raw_digest(), before_digest)
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_trace_move_cancellation_and_target_guards(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.show()
            window.view_mode_combo.setCurrentIndex(window.view_mode_combo.findData("split_y_axes"))
            window.screen_preview_checkbox.setChecked(True)
            window.move_trace_button.setChecked(True)
            preview = window._screen_preview
            consumer = preview.consumer
            core, gui = consumer.qt_core, consumer.qt_gui
            def event(x, y=100.0, role="y1", **changes):
                view = consumer.secondary if role == "y2" else consumer.primary.vb
                position = view.mapViewToScene(core.QPointF(x, y))
                return ScreenPointerEvent(**dict(vars(consumer.pointer_event(position, button=1)), **changes))
            def begin():
                window.move_trace_button.setChecked(True)
                preview.handle_event("button_press_event", event(1.0))
                self.assertIsNotNone(preview._move_target)
                preview.handle_event("motion_notify_event", event(1.5, 1100.0))
            original = _trace_edit_state(window.project.datasets)
            undo_count = len(window._undo_stack)
            window.project.dirty = False
            for invalid in (event(1.0, role="y2"), event(1.0, hit_region="x"),
                            event(1.0, button=3), event(1.0, double_click=True),
                            event(1.0, hit_kind="vertical_marker", hit_id="m")):
                preview.handle_event("button_press_event", invalid)
                self.assertIsNone(preview._move_target)
            for cancel in (core.QEvent(core.QEvent.Type.FocusOut),
                           gui.QKeyEvent(core.QEvent.Type.KeyPress, core.Qt.Key.Key_Escape,
                                         core.Qt.KeyboardModifier.NoModifier)):
                begin()
                self.app.sendEvent(consumer.widget.viewport(), cancel)
                self.assertIsNone(preview._move_target)
                self.assertEqual(_trace_edit_state(window.project.datasets), original)
            for finish in (event(2.0, role="y2"), event(2.0, hit_region=""),
                           event(2.0, button=3)):
                begin()
                preview.handle_event("button_release_event", finish)
                self.assertIsNone(preview._move_target)
                self.assertEqual(_trace_edit_state(window.project.datasets), original)
            for change in ("dataset", "direction", "visibility", "mode", "plot"):
                begin()
                if change == "dataset":
                    window.dataset_table.selectRow(1)
                elif change == "direction":
                    window.move_axis_combo.setCurrentIndex(window.move_axis_combo.findData("x"))
                    preview.handle_event("motion_notify_event", event(2.0))
                elif change == "visibility":
                    window.project.datasets[0].visible = False
                    preview.handle_event("motion_notify_event", event(2.0))
                elif change == "mode":
                    window.move_trace_button.setChecked(False)
                else:
                    window._plot()
                self.assertIsNone(preview._move_target)
                self.assertEqual(_trace_edit_state(window.project.datasets), original)
                window.dataset_table.selectRow(0)
                window.project.datasets[0].visible = True
                window.move_axis_combo.setCurrentIndex(window.move_axis_combo.findData("both"))
                window._plot()
            self.assertEqual(len(window._undo_stack), undo_count)
            self.assertFalse(window.project.dirty)
            # A click without movement is not an edit.
            window.move_trace_button.setChecked(True)
            preview.handle_event("button_press_event", event(1.0))
            preview.handle_event("button_release_event", event(1.0))
            self.assertEqual(len(window._undo_stack), undo_count)
            self.assertFalse(window.project.dirty)
            begin()
            window.screen_preview_checkbox.setChecked(False)
            self.assertEqual(_trace_edit_state(window.project.datasets), original)
            self.assertTrue(window.move_trace_button.isChecked())
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            consumer = preview.consumer
            begin()
            with patch.object(consumer, "set_trace_translation", side_effect=RuntimeError("move failure")):
                preview.handle_event("motion_notify_event", event(2.0))
            self.assertIsNone(window._screen_preview)
            self.assertEqual(_trace_edit_state(window.project.datasets), original)
        finally:
            window.project.dirty = False
            window.close()

    def test_trace_move_recalculation_failure_is_atomic_in_both_renderers(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            for native in (False, True):
                with self.subTest(native=native):
                    window.screen_preview_checkbox.setChecked(native)
                    window.dataset_table.selectRow(0)
                    window.move_trace_button.setChecked(True)
                    dataset = window.project.datasets[0]
                    before = _trace_edit_state(window.project.datasets)
                    undo_count = len(window._undo_stack)
                    window.project.dirty = False
                    press = ScreenPointerEvent(button=1, axis_role="y1", hit_region="plot",
                                               data_coordinates=(("y1", 1.0, 100.0),))
                    motion = ScreenPointerEvent(button=1, axis_role="y1", hit_region="plot",
                                                data_coordinates=(("y1", 1.5, 1100.0),))
                    release = ScreenPointerEvent(button=1, axis_role="y1", hit_region="plot",
                                                 data_coordinates=(("y1", 1.5, 1100.0),))
                    handler = window._screen_preview.handle_event if native else None
                    (handler("button_press_event", press) if native else window._on_canvas_press(press))
                    (handler("motion_notify_event", motion) if native else window._on_canvas_motion(motion))
                    with patch.object(QtWidgets.QMessageBox, "warning") as warning:
                        with patch("hplc_app.gui.recalculate_dataset_peaks",
                                   side_effect=ValueError("failure")):
                            (handler("button_release_event", release)
                             if native else window._on_canvas_release(release))
                    warning.assert_called_once()
                    self.assertEqual(_trace_edit_state(window.project.datasets), before)
                    self.assertEqual(len(window._undo_stack), undo_count)
                    self.assertFalse(window.project.dirty)
                    self.assertIsNone(window._move_drag)
                    if native:
                        self.assertIsNotNone(window._screen_preview)
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_peak_split_matches_existing_calculation(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        from hplc_app.analysis import split_peak_region
        from hplc_app.project_io import load_project, save_project
        window = self.make_window()
        try:
            window.show()
            raw = [(d.time_min.copy(), d.intensity_uv.copy()) for d in window.project.datasets]
            cases = (("single", "linear", 0), ("overview_detail", "edge_average", 1),
                     ("split_y_axes", "constant_start", 1), ("single", "manual", 1),
                     ("split_y_axes", "zero", 0))
            for mode, baseline, row in cases:
                with self.subTest(mode=mode, baseline=baseline):
                    selected = window.project.datasets[row]
                    selected.x_shift_min = 3.25
                    selected.offset = 1000.0
                    selected.peaks = [PeakRegion(start_min=5.0, end_min=10.0,
                                                baseline_mode=baseline, notes="keep me",
                                                integration_source="auto"),
                                      PeakRegion(start_min=15.0, end_min=20.0)]
                    if baseline == "manual":
                        selected.peaks[0].baseline_start_uv = 12.5
                        selected.peaks[0].baseline_end_uv = 24.5
                    recalculate_dataset_peaks(selected)
                    parent = deepcopy(selected.peaks[0])
                    window.dataset_table.selectRow(row)
                    window.view_mode_combo.setCurrentIndex(window.view_mode_combo.findData(mode))
                    window._refresh_peak_table([parent.id])
                    window._plot()
                    window.split_peak_button.setChecked(True)
                    window.screen_preview_checkbox.setChecked(True)
                    preview = window._screen_preview
                    self.assertIsNotNone(preview)
                    consumer = preview.consumer
                    core, gui = consumer.qt_core, consumer.qt_gui
                    role = "y2" if mode == "split_y_axes" and row else "y1"
                    view = consumer.secondary if role == "y2" else consumer.primary.vb
                    viewport = consumer.widget.viewport()
                    def mouse(kind, position, button=core.Qt.MouseButton.NoButton,
                              held=core.Qt.MouseButton.NoButton):
                        self.app.sendEvent(viewport, gui.QMouseEvent(
                            kind, core.QPointF(position), core.QPointF(viewport.mapToGlobal(position)),
                            button, held, core.Qt.KeyboardModifier.NoModifier))
                    # Repeat on the selected right child, preserving its split group.
                    for time in (10.75, 11.75):
                        parent = deepcopy(selected.peaks[window.peak_table.currentRow()])
                        position = consumer.widget.mapFromScene(view.mapViewToScene(
                            core.QPointF(time, sum(view.viewRange()[1]) / 2)))
                        event = consumer.pointer_event(consumer.widget.mapToScene(position))
                        expected = deepcopy(selected)
                        index = window.peak_table.currentRow()
                        children = split_peak_region(expected, expected.peaks[index],
                                                     event.data_for(role)[0] - selected.x_shift_min)
                        expected.peaks[index:index + 1] = children
                        before = deepcopy(selected.peaks)
                        untouched = deepcopy(window.project.datasets[1 - row].peaks)
                        undo_count = len(window._undo_stack)
                        state = window._screen_view_state()
                        window.project.dirty = False
                        mouse(core.QEvent.Type.MouseMove, position)
                        self.assertTrue(consumer.pointer_cursor.isVisible())
                        self.assertEqual(selected.peaks, before)
                        self.assertFalse(window.project.dirty)
                        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
                            mouse(core.QEvent.Type.MouseButtonPress, position,
                                  core.Qt.MouseButton.LeftButton, core.Qt.MouseButton.LeftButton)
                            mouse(core.QEvent.Type.MouseButtonRelease, position, core.Qt.MouseButton.LeftButton)
                        warning.assert_not_called()
                        self.assertIs(window._screen_preview, preview)
                        self.assertTrue(window.split_peak_button.isChecked())
                        self.assertEqual(len(window._undo_stack), undo_count + 1)
                        new = sorted((p for p in selected.peaks if p.id not in {b.id for b in before}),
                                     key=lambda p: p.start_min)
                        self.assertEqual(len(new), 2)
                        for child, actual in zip(children, new):
                            child.id = actual.id
                            self.assertEqual(actual.split_group_id, parent.split_group_id or parent.id)
                            self.assertEqual(actual.notes, parent.notes)
                            self.assertEqual(actual.integration_source, parent.integration_source)
                        recalculate_dataset_peaks(expected)
                        self.assertEqual(selected.peaks, expected.peaks)
                        area_difference = abs(
                            sum(p.raw_area_uv_sec for p in new) - parent.raw_area_uv_sec
                        )
                        # A real pointer position is rounded to a display pixel and
                        # then to the nearest acquired sample.  Shimadzu timestamps
                        # are not perfectly uniform, so splitting the piecewise
                        # linear baseline at an adjacent sample can leave a
                        # few-parts-per-million trapezoid difference even though
                        # both children preserve the parent's baseline endpoints.
                        self.assertLess(
                            area_difference,
                            max(0.01, abs(parent.raw_area_uv_sec) * 5.0e-6),
                        )
                        self.assertEqual(window.project.datasets[1 - row].peaks, untouched)
                        self.assertEqual(window._screen_view_state(), state)
                        self.assertEqual(window._selected_peak_ids(), [new[1].id])
                        self.assertTrue({p.id for p in new}.issubset(
                            {p.peak_id for p in window._screen_scene.peak_overlays}))
                        window.undo()
                        self.assertEqual(selected.peaks, before)
                        window.redo()
                        self.assertEqual(selected.peaks, expected.peaks)
                        window._refresh_peak_table([new[1].id])
            with tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / "native-split.hplcproj")
                save_project(path, window.project)
                restored = load_project(path)
                self.assertEqual([d.peaks for d in restored.datasets], [d.peaks for d in window.project.datasets])
                with patch.object(window.canvas.callbacks, "exception_handler",
                                  side_effect=AssertionError("Unexpected render callback error")):
                    output = Path(directory) / "split.svg"
                    window._save_figure_file(str(output))
                self.assertGreater(output.stat().st_size, 0)
            for dataset, (times, values) in zip(window.project.datasets, raw):
                np.testing.assert_array_equal(dataset.time_min, times)
                np.testing.assert_array_equal(dataset.intensity_uv, values)
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_peak_split_target_and_input_guards(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.show()
            window.peak_table.selectRow(0)
            window.view_mode_combo.setCurrentIndex(window.view_mode_combo.findData("split_y_axes"))
            window.screen_preview_checkbox.setChecked(True)
            window.split_peak_button.setChecked(True)
            preview = window._screen_preview
            consumer = preview.consumer
            core = consumer.qt_core
            def event(time=7.5, role="y1", **changes):
                view = consumer.secondary if role == "y2" else consumer.primary.vb
                position = view.mapViewToScene(core.QPointF(time, sum(view.viewRange()[1]) / 2))
                return ScreenPointerEvent(**dict(vars(consumer.pointer_event(position, button=1)), **changes))
            dataset = window.project.datasets[0]
            original = deepcopy(dataset.peaks)
            window.project.dirty = False
            undo_count = len(window._undo_stack)
            state = window._screen_view_state()
            with patch.object(QtWidgets.QMessageBox, "warning") as unexpected_warning:
                for invalid in (event(role="y2"), event(hit_region="x"), event(button=3),
                                event(double_click=True), event(hit_region="overview"),
                                event(data_coordinates=(("y1", float("nan"), 0.0),))):
                    with patch.object(preview.navigation, "handle_event") as navigation:
                        preview.handle_event("button_press_event", invalid)
                    navigation.assert_not_called()
                preview.handle_event("motion_notify_event", event())
                self.assertTrue(consumer.pointer_cursor.isVisible())
                preview.handle_event("motion_notify_event", event(role="y2"))
                self.assertFalse(consumer.pointer_cursor.isVisible())
                dataset.visible = False
                preview.handle_event("button_press_event", event())
                dataset.visible = True
                window.peak_table.clearSelection()
                preview.handle_event("button_press_event", event())
                window.peak_table.selectRow(0)
                # Stale table identity must not accidentally split a replaced peak.
                old_id = dataset.peaks[0].id
                dataset.peaks[0].id = "replacement"
                preview.handle_event("button_press_event", event())
                dataset.peaks[0].id = old_id
                removed = dataset.peaks.pop()
                preview.handle_event("button_press_event", event())
                dataset.peaks.append(removed)
                window.dataset_table.selectRow(1)
                preview.handle_event("button_press_event", event(role="y2"))
                window.dataset_table.selectRow(0)
                window.peak_table.selectRow(0)
                window.toolbar.pan()
                preview.handle_event("button_press_event", event())
                preview.handle_event("button_release_event", event())
                window.toolbar.pan()
            unexpected_warning.assert_not_called()
            self.assertEqual(dataset.peaks, original)
            self.assertFalse(window.project.dirty)
            self.assertEqual(len(window._undo_stack), undo_count)
            self.assertEqual(window._screen_view_state(), state)
            # Split clicks bypass marker/annotation hit handlers, but no double split.
            window.split_peak_button.setChecked(True)
            with patch.object(window, "_split_selected_peak_at") as split:
                preview.handle_event("button_press_event", event(hit_kind="vertical_marker", hit_id="marker"))
                preview.handle_event("button_press_event", event(double_click=True))
                preview.handle_event("button_release_event", event())
            split.assert_called_once()
            window.screen_preview_checkbox.setChecked(False)
            self.assertTrue(window.split_peak_button.isChecked())
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            consumer = preview.consumer
            with patch.object(consumer, "set_pointer_cursor", side_effect=RuntimeError("cursor failure")):
                preview.handle_event("motion_notify_event", event())
            self.assertIsNone(window._screen_preview)
            self.assertTrue(window.split_peak_button.isChecked())
            self.assertEqual(dataset.peaks, original)
        finally:
            window.project.dirty = False
            window.close()

    def test_peak_split_invalid_and_recalculation_failure_are_atomic(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            dataset = window.project.datasets[0]
            for native in (False, True):
                with self.subTest(native=native):
                    window.screen_preview_checkbox.setChecked(native)
                    with patch.object(QtWidgets.QMessageBox, "information") as information:
                        window.split_peak_button.setChecked(False)
                        window.peak_table.setCurrentCell(-1, -1)
                        window.split_peak_button.setChecked(True)
                    information.assert_called_once()
                    self.assertFalse(window.split_peak_button.isChecked())
                    for failure in ("narrow", "recalculation"):
                        dataset.peaks = [PeakRegion(start_min=5.0, end_min=10.0)]
                        if failure == "narrow":
                            dataset.peaks[0].start_min = float(dataset.time_min[20])
                            dataset.peaks[0].end_min = float(dataset.time_min[22])
                        recalculate_dataset_peaks(dataset)
                        window._refresh_peak_table([dataset.peaks[0].id])
                        window._plot()
                        window.split_peak_button.setChecked(True)
                        before = deepcopy(dataset.peaks)
                        undo_count = len(window._undo_stack)
                        window.project.dirty = False
                        def click():
                            if native:
                                window._screen_preview.handle_event("button_press_event", ScreenPointerEvent(
                                    button=1, axis_role="y1", hit_region="plot",
                                    data_coordinates=(("y1", 7.5, 0.0),)))
                            else:
                                window._split_selected_peak_at(7.5)
                        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
                            if failure == "recalculation":
                                with patch("hplc_app.gui.recalculate_dataset_peaks", side_effect=ValueError("failure")):
                                    click()
                            else:
                                click()
                        warning.assert_called_once()
                        self.assertEqual(dataset.peaks, before)
                        self.assertEqual(len(window._undo_stack), undo_count)
                        self.assertFalse(window.project.dirty)
                        if native:
                            self.assertIsNotNone(window._screen_preview)
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_peak_range_edit_matches_existing_calculation(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        from hplc_app.project_io import load_project, save_project
        window = self.make_window()
        try:
            window.show()
            raw = [(d.time_min.copy(), d.intensity_uv.copy()) for d in window.project.datasets]
            for dataset in window.project.datasets:
                dataset.x_shift_min = 3.25
                dataset.offset = 1000.0
            cases = (("single", "linear", 0), ("overview_detail", "edge_average", 1),
                     ("split_y_axes", "constant_start", 1), ("single", "manual", 1),
                     ("split_y_axes", "zero", 0))
            for mode, baseline, row in cases:
                with self.subTest(mode=mode, baseline=baseline):
                    selected = window.project.datasets[row]
                    selected.peaks = [PeakRegion(start_min=5.0, end_min=10.0,
                                                baseline_mode=baseline, notes="keep me"),
                                      PeakRegion(start_min=15.0, end_min=20.0)]
                    if baseline == "manual":
                        selected.peaks[0].baseline_start_uv = 12.5
                        selected.peaks[0].baseline_end_uv = 24.5
                    recalculate_dataset_peaks(selected)
                    peak_id = selected.peaks[0].id
                    window.dataset_table.selectRow(row)
                    window.view_mode_combo.setCurrentIndex(window.view_mode_combo.findData(mode))
                    window._refresh_peak_table([peak_id])
                    window._plot()
                    window.edit_peak_button.setChecked(True)
                    window.screen_preview_checkbox.setChecked(True)
                    self.assertIsNone(window._span_selector)
                    preview = window._screen_preview
                    self.assertIsNotNone(preview)
                    consumer = preview.consumer
                    core, gui = consumer.qt_core, consumer.qt_gui
                    role = "y2" if mode == "split_y_axes" and row else "y1"
                    view = consumer.secondary if role == "y2" else consumer.primary.vb
                    viewport = consumer.widget.viewport()
                    def point(time):
                        return consumer.widget.mapFromScene(view.mapViewToScene(
                            core.QPointF(time, sum(view.viewRange()[1]) / 2)))
                    def mouse(kind, position, button=core.Qt.MouseButton.NoButton,
                              held=core.Qt.MouseButton.NoButton):
                        self.app.sendEvent(viewport, gui.QMouseEvent(
                            kind, core.QPointF(position), core.QPointF(viewport.mapToGlobal(position)),
                            button, held, core.Qt.KeyboardModifier.NoModifier))
                    start, end = (point(14), point(8)) if row else (point(8), point(14))
                    bounds = sorted(consumer.pointer_event(consumer.widget.mapToScene(p)).data_for(role)[0]
                                    - selected.x_shift_min for p in (start, end))
                    before = deepcopy(selected.peaks)
                    expected = deepcopy(selected)
                    expected.peaks[0].start_min, expected.peaks[0].end_min = bounds
                    recalculate_dataset_peaks(expected)
                    untouched = deepcopy(window.project.datasets[1 - row].peaks)
                    undo_count = len(window._undo_stack)
                    state = window._screen_view_state()
                    window.project.dirty = False
                    mouse(core.QEvent.Type.MouseButtonPress, start,
                          core.Qt.MouseButton.LeftButton, core.Qt.MouseButton.LeftButton)
                    mouse(core.QEvent.Type.MouseMove, end, held=core.Qt.MouseButton.LeftButton)
                    self.assertTrue(consumer.span_selection.isVisible())
                    self.assertEqual(consumer.span_selection.brush.color().name(), "#f59e0b")
                    self.assertIs(consumer._span_view, view)
                    self.assertEqual(selected.peaks, before)
                    self.assertFalse(window.project.dirty)
                    self.assertEqual(len(window._undo_stack), undo_count)
                    with patch.object(QtWidgets.QMessageBox, "warning") as warning:
                        mouse(core.QEvent.Type.MouseButtonRelease, end, core.Qt.MouseButton.LeftButton)
                    warning.assert_not_called()
                    self.assertIs(window._screen_preview, preview)
                    self.assertIsNone(preview._span_drag)
                    self.assertFalse(consumer.span_selection.isVisible())
                    self.assertFalse(window.edit_peak_button.isChecked())
                    self.assertEqual(len(window._undo_stack), undo_count + 1)
                    self.assertEqual(selected.peaks, expected.peaks)
                    self.assertEqual(window.project.datasets[1 - row].peaks, untouched)
                    self.assertEqual(window._screen_view_state(), state)
                    self.assertIn(peak_id, window._selected_peak_ids())
                    self.assertIn(peak_id, {p.peak_id for p in window._screen_scene.peak_overlays})
                    window.undo()
                    self.assertEqual(selected.peaks, before)
                    window.redo()
                    self.assertEqual(selected.peaks, expected.peaks)
            with tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / "native-range.hplcproj")
                save_project(path, window.project)
                restored = load_project(path)
                self.assertEqual([d.peaks for d in restored.datasets], [d.peaks for d in window.project.datasets])
                window.edit_peak_button.setChecked(True)
                with patch.object(window.canvas.callbacks, "exception_handler",
                                  side_effect=AssertionError("Unexpected render callback error")):
                    output = Path(directory) / "range.svg"
                    window._save_figure_file(str(output))
                self.assertGreater(output.stat().st_size, 0)
            for dataset, (times, values) in zip(window.project.datasets, raw):
                np.testing.assert_array_equal(dataset.time_min, times)
                np.testing.assert_array_equal(dataset.intensity_uv, values)
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_peak_range_target_and_cancellation_guards(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.show()
            dataset = window.project.datasets[0]
            dataset.peaks.append(PeakRegion(start_min=15.0, end_min=20.0))
            recalculate_dataset_peaks(dataset)
            window._refresh_peak_table([dataset.peaks[0].id])
            window.view_mode_combo.setCurrentIndex(window.view_mode_combo.findData("split_y_axes"))
            window.screen_preview_checkbox.setChecked(True)
            window.edit_peak_button.setChecked(True)
            preview = window._screen_preview
            consumer = preview.consumer
            core, gui = consumer.qt_core, consumer.qt_gui
            def event(time, role="y1", **changes):
                view = consumer.secondary if role == "y2" else consumer.primary.vb
                position = view.mapViewToScene(core.QPointF(time, sum(view.viewRange()[1]) / 2))
                return ScreenPointerEvent(**dict(vars(consumer.pointer_event(position, button=1)), **changes))
            def begin():
                window.edit_peak_button.setChecked(True)
                preview.handle_event("button_press_event", event(8.0))
                self.assertIsNotNone(preview._span_drag)
            original = deepcopy(dataset.peaks)
            window.project.dirty = False
            undo_count = len(window._undo_stack)
            for start in (event(8.0, "y2"), event(8.0, hit_region="x"), event(8.0, button=3)):
                preview.handle_event("button_press_event", start)
                self.assertIsNone(preview._span_drag)
            for cancel in (core.QEvent(core.QEvent.Type.FocusOut),
                           gui.QKeyEvent(core.QEvent.Type.KeyPress, core.Qt.Key.Key_Escape,
                                         core.Qt.KeyboardModifier.NoModifier)):
                begin()
                self.app.sendEvent(consumer.widget.viewport(), cancel)
                preview.handle_event("button_release_event", event(14.0))
                self.assertIsNone(preview._span_drag)
            for end in (event(14.0, "y2"), event(14.0, hit_region=""), event(8.0)):
                begin()
                preview.handle_event("button_release_event", end)
                self.assertIsNone(preview._span_drag)
            # Mutations without a redraw are also guarded by the captured peak content.
            for field, value in (("id", "replaced"), ("start_min", 6.0), ("baseline_mode", "zero"),
                                 ("baseline_start_uv", 3.0)):
                begin()
                peak = dataset.peaks[0]
                old = getattr(peak, field)
                setattr(peak, field, value)
                preview.handle_event("button_release_event", event(14.0))
                setattr(peak, field, old)
                self.assertIsNone(preview._span_drag)
            begin()
            window.peak_table.selectRow(1)
            preview.handle_event("button_release_event", event(14.0))
            self.assertEqual(dataset.peaks, original)
            begin()
            window.peak_table.clearSelection()
            preview.handle_event("button_release_event", event(14.0))
            preview.handle_event("button_press_event", event(8.0))
            self.assertIsNone(preview._span_drag)
            window.peak_table.selectRow(0)
            begin()
            removed = dataset.peaks.pop(0)
            preview.handle_event("button_release_event", event(14.0))
            dataset.peaks.insert(0, removed)
            self.assertIsNone(preview._span_drag)
            begin()
            window.integrate_button.setChecked(True)
            preview.handle_event("button_release_event", event(14.0))
            self.assertEqual(dataset.peaks, original)
            self.assertFalse(window.project.dirty)
            self.assertEqual(len(window._undo_stack), undo_count)
            window.edit_peak_button.setChecked(True)
            window.screen_preview_checkbox.setChecked(False)
            self.assertEqual(window._span_selector_mode, "edit")
            self.assertTrue(window._span_selector.active)
            window.screen_preview_checkbox.setChecked(True)
            self.assertIsNone(window._span_selector)
            preview = window._screen_preview
            consumer = preview.consumer
            with patch.object(consumer, "set_span_selection", side_effect=RuntimeError("selection failure")):
                preview.handle_event("button_press_event", event(8.0))
            self.assertIsNone(window._screen_preview)
            self.assertEqual(window._span_selector_mode, "edit")
            self.assertTrue(window._span_selector.active)
            self.assertEqual(dataset.peaks, original)
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_peak_range_invalid_range_rolls_back(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.screen_preview_checkbox.setChecked(True)
            with patch.object(QtWidgets.QMessageBox, "information") as information:
                window.peak_table.setCurrentCell(-1, -1)
                window.edit_peak_button.setChecked(True)
            information.assert_called_once()
            self.assertFalse(window.edit_peak_button.isChecked())
            self.assertIsNotNone(window._screen_preview)
            window.peak_table.selectRow(0)
            window.edit_peak_button.setChecked(True)
            preview = window._screen_preview
            before = deepcopy(window.project.datasets[0].peaks)
            window.project.dirty = False
            undo_count = len(window._undo_stack)
            def event(time, pixel):
                return ScreenPointerEvent(button=1, axis_role="y1", hit_region="plot",
                                          canvas_x=pixel, data_coordinates=(("y1", time, 0.0),))
            with patch.object(QtWidgets.QMessageBox, "warning") as warning:
                preview.handle_event("button_press_event", event(-20.0, 50.0))
                preview.handle_event("button_release_event", event(-10.0, 100.0))
            warning.assert_called_once()
            self.assertIs(window._screen_preview, preview)
            self.assertIsNone(preview._span_drag)
            self.assertEqual(window.project.datasets[0].peaks, before)
            self.assertEqual(len(window._undo_stack), undo_count)
            self.assertFalse(window.project.dirty)
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_manual_integration_matches_existing_calculation(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        from hplc_app.analysis import integrate_peak
        from hplc_app.project_io import load_project, save_project
        window = self.make_window()
        try:
            window.show()
            raw = [dataset.intensity_uv.copy() for dataset in window.project.datasets]
            for dataset in window.project.datasets:
                dataset.x_shift_min = 3.25
                dataset.offset = 1000.0
            window.integrate_button.setChecked(True)
            window.screen_preview_checkbox.setChecked(True)
            self.assertIsNone(window._span_selector)
            cases = (("single", "linear", 0), ("overview_detail", "edge_average", 1),
                     ("split_y_axes", "constant_start", 1), ("single", "manual", 1),
                     ("split_y_axes", "zero", 0))
            for mode, baseline, row in cases:
                with self.subTest(mode=mode, baseline=baseline):
                    window.dataset_table.selectRow(row)
                    window.baseline_combo.setCurrentIndex(window.baseline_combo.findData(baseline))
                    window.view_mode_combo.setCurrentIndex(window.view_mode_combo.findData(mode))
                    window._plot()
                    preview = window._screen_preview
                    self.assertIsNotNone(preview)
                    consumer = preview.consumer
                    core, gui = consumer.qt_core, consumer.qt_gui
                    viewport = consumer.widget.viewport()
                    selected = window.project.datasets[row]
                    expected_dataset = deepcopy(selected)
                    untouched = deepcopy(window.project.datasets[1 - row].peaks)
                    role = "y2" if mode == "split_y_axes" and row == 1 else "y1"
                    view = consumer.secondary if role == "y2" else consumer.primary.vb
                    def point(time):
                        return consumer.widget.mapFromScene(view.mapViewToScene(
                            core.QPointF(time, sum(view.viewRange()[1]) / 2.0)))
                    def mouse(kind, position, button=core.Qt.MouseButton.NoButton,
                              held=core.Qt.MouseButton.NoButton):
                        self.app.sendEvent(viewport, gui.QMouseEvent(
                            kind, core.QPointF(position), core.QPointF(viewport.mapToGlobal(position)),
                            button, held, core.Qt.KeyboardModifier.NoModifier))
                    start, end = (point(14.0), point(8.0)) if row else (point(8.0), point(14.0))
                    bounds = sorted(consumer.pointer_event(consumer.widget.mapToScene(p)).data_for(role)[0]
                                    - selected.x_shift_min for p in (start, end))
                    before_ids = {peak.id for peak in selected.peaks}
                    undo_count = len(window._undo_stack)
                    window.project.dirty = False
                    state = window._screen_view_state()
                    mouse(core.QEvent.Type.MouseButtonPress, start,
                          core.Qt.MouseButton.LeftButton, core.Qt.MouseButton.LeftButton)
                    mouse(core.QEvent.Type.MouseMove, end, held=core.Qt.MouseButton.LeftButton)
                    self.assertTrue(consumer.span_selection.isVisible())
                    self.assertEqual(consumer.span_selection.brush.color().name(), "#2563eb")
                    self.assertEqual(selected.peaks, expected_dataset.peaks)
                    self.assertEqual(len(window._undo_stack), undo_count)
                    self.assertFalse(window.project.dirty)
                    with patch.object(QtWidgets.QMessageBox, "warning") as warning:
                        mouse(core.QEvent.Type.MouseButtonRelease, end, core.Qt.MouseButton.LeftButton)
                    warning.assert_not_called()
                    self.assertIs(window._screen_preview, preview)
                    self.assertIsNone(preview._span_drag)
                    self.assertFalse(consumer.span_selection.isVisible())
                    self.assertEqual(len(window._undo_stack), undo_count + 1)
                    new_peak = next(peak for peak in selected.peaks if peak.id not in before_ids)
                    expected_dataset.peaks.append(integrate_peak(expected_dataset, PeakRegion(
                        id=new_peak.id, start_min=bounds[0], end_min=bounds[1], baseline_mode=baseline)))
                    recalculate_dataset_peaks(expected_dataset)
                    self.assertEqual(selected.peaks, expected_dataset.peaks)
                    self.assertEqual(window.project.datasets[1 - row].peaks, untouched)
                    self.assertEqual(window._screen_view_state(), state)
                    self.assertEqual(window._peak_overlay_artists, {})
                    self.assertIn(new_peak.id, {p.peak_id for p in window._screen_scene.peak_overlays})
                    self.assertEqual(
                        consumer.last_evidence["counts"]["peak_overlays"],
                        len(window._screen_scene.peak_overlays),
                    )
                    window.undo()
                    self.assertEqual({p.id for p in window.project.datasets[row].peaks}, before_ids)
                    window.redo()
                    self.assertEqual(window.project.datasets[row].peaks, expected_dataset.peaks)
            with tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / "native-integrated.hplcproj")
                save_project(path, window.project)
                restored = load_project(path)
                self.assertEqual([d.peaks for d in restored.datasets], [d.peaks for d in window.project.datasets])
                output = Path(directory) / "integrated.svg"
                with patch.object(window.canvas.callbacks, "exception_handler",
                                  side_effect=AssertionError("Unexpected render callback error")):
                    window._save_figure_file(str(output))
                self.assertGreater(output.stat().st_size, 0)
            for dataset, values in zip(window.project.datasets, raw):
                np.testing.assert_array_equal(dataset.intensity_uv, values)
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_manual_integration_target_and_cancellation_guards(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.show()
            window.view_mode_combo.setCurrentIndex(window.view_mode_combo.findData("split_y_axes"))
            window.screen_preview_checkbox.setChecked(True)
            window.integrate_button.setChecked(True)
            preview = window._screen_preview
            consumer = preview.consumer
            core, gui = consumer.qt_core, consumer.qt_gui
            def event(time, role="y1", **changes):
                view = consumer.secondary if role == "y2" else consumer.primary.vb
                position = view.mapViewToScene(core.QPointF(time, sum(view.viewRange()[1]) / 2))
                value = consumer.pointer_event(position, button=1)
                return ScreenPointerEvent(**dict(vars(value), **changes))
            def begin():
                window.integrate_button.setChecked(True)
                preview.handle_event("button_press_event", event(8.0))
                self.assertIsNotNone(preview._span_drag)
            original = [deepcopy(d.peaks) for d in window.project.datasets]
            window.project.dirty = False
            undo_count = len(window._undo_stack)
            for start in (event(8.0, "y2"), event(8.0, hit_region="x"), event(8.0, button=3)):
                preview.handle_event("button_press_event", start)
                self.assertIsNone(preview._span_drag)
            with patch.object(preview.navigation, "handle_event") as navigation:
                preview.handle_event("button_press_event", event(8.0, double_click=True))
            navigation.assert_not_called()
            self.assertIsNone(preview._span_drag)
            for cancel in (core.QEvent(core.QEvent.Type.FocusOut),
                           gui.QKeyEvent(core.QEvent.Type.KeyPress, core.Qt.Key.Key_Escape,
                                         core.Qt.KeyboardModifier.NoModifier)):
                begin()
                self.app.sendEvent(consumer.widget.viewport(), cancel)
                preview.handle_event("button_release_event", event(14.0))
                self.assertIsNone(preview._span_drag)
            for end in (event(14.0, "y2"), event(14.0, axis_role="outside", hit_region=""), event(8.0)):
                begin()
                preview.handle_event("button_release_event", end)
                self.assertIsNone(preview._span_drag)
            # A changed target/shift/baseline cannot redirect a pending drag, even before replot.
            dataset = window.project.datasets[0]
            for field, value in (("visible", False), ("x_shift_min", 2.0), ("y_axis", 2)):
                begin()
                previous = getattr(dataset, field)
                setattr(dataset, field, value)
                preview.handle_event("button_release_event", event(14.0))
                setattr(dataset, field, previous)
                self.assertIsNone(preview._span_drag)
            begin()
            previous = window.project.method.baseline_mode
            window.project.method.baseline_mode = "zero"
            preview.handle_event("button_release_event", event(14.0))
            window.project.method.baseline_mode = previous
            self.assertIsNone(preview._span_drag)
            self.assertEqual([d.peaks for d in window.project.datasets], original)
            self.assertFalse(window.project.dirty)
            self.assertEqual(len(window._undo_stack), undo_count)
            begin()
            window.dataset_table.selectRow(1)
            preview.handle_event("button_release_event", event(14.0))
            self.assertEqual([d.peaks for d in window.project.datasets], original)
            window.dataset_table.selectRow(0)
            dataset.visible = False
            window._plot()
            preview.handle_event("button_press_event", event(8.0))
            self.assertIsNone(preview._span_drag)
            dataset.visible = True
            window._plot()
            begin()
            window.mouse_mode_combo.setCurrentIndex(
                window.mouse_mode_combo.findData("select")
            )
            preview.handle_event("button_release_event", event(14.0))
            self.assertEqual(window.project.fraction_regions, [])
            self.assertEqual([d.peaks for d in window.project.datasets], original)
            window.integrate_button.setChecked(True)
            window.screen_preview_checkbox.setChecked(False)
            self.assertIsNotNone(window._span_selector)
            self.assertEqual(window._span_selector_mode, "integrate")
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            consumer = preview.consumer
            with patch.object(consumer, "set_span_selection", side_effect=RuntimeError("selection failure")):
                preview.handle_event("button_press_event", event(8.0))
            self.assertIsNone(window._screen_preview)
            self.assertTrue(window.integrate_button.isChecked())
            self.assertTrue(window._span_selector.active)
            self.assertEqual([d.peaks for d in window.project.datasets], original)
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_manual_integration_rejects_insufficient_points(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.show()
            window.screen_preview_checkbox.setChecked(True)
            window.integrate_button.setChecked(True)
            preview = window._screen_preview
            before = deepcopy(window.project.datasets[0].peaks)
            window.project.dirty = False
            undo_count = len(window._undo_stack)
            def event(time, pixel):
                return ScreenPointerEvent(button=1, axis_role="y1", hit_region="plot",
                                          canvas_x=pixel, data_coordinates=(("y1", time, 0.0),))
            with patch.object(QtWidgets.QMessageBox, "warning") as warning:
                preview.handle_event("button_press_event", event(-20.0, 50.0))
                preview.handle_event("button_release_event", event(-10.0, 100.0))
            warning.assert_called_once()
            self.assertIs(window._screen_preview, preview)
            self.assertIsNone(preview._span_drag)
            self.assertEqual(window.project.datasets[0].peaks, before)
            self.assertEqual(len(window._undo_stack), undo_count)
            self.assertFalse(window.project.dirty)
        finally:
            window.project.dirty = False
            window.close()

    def test_fraction_mouse_mode_and_inline_interval_are_not_exposed(self):
        window = self.make_window()
        try:
            self.assertEqual(window.mouse_mode_combo.findData("fraction"), -1)
            self.assertFalse(hasattr(window, "fraction_button"))
            self.assertFalse(hasattr(window, "fraction_interval_spin"))
            self.assertTrue(window.fraction_numeric_button.isVisibleTo(window))
            expected_labels = {
                "ja": (
                    "すべてのピークを選択",
                    "フラクション範囲を数値入力・編集…",
                ),
                "en": (
                    "Select all peaks",
                    "Add / edit fraction range numerically…",
                ),
            }
            for language, expected in expected_labels.items():
                with self.subTest(language=language):
                    window.set_language(language)
                    self.assertEqual(
                        window.select_all_peaks_button.text(), expected[0]
                    )
                    self.assertEqual(
                        window.fraction_numeric_button.text(), expected[1]
                    )
        finally:
            window.project.dirty = False
            window.close()

    def test_mouse_mode_selects_integration_area_and_time_range(self):
        window = self.make_window()
        dataset = window.project.datasets[0]
        wide = dataset.peaks[0]
        narrow = PeakRegion(start_min=6.0, end_min=8.0)
        dataset.peaks.append(narrow)
        recalculate_dataset_peaks(dataset)
        window._refresh_peak_table()
        window._plot()

        self.assertEqual(window.mouse_mode_combo.currentData(), "normal")
        window.integrate_button.setChecked(True)
        self.assertEqual(window.mouse_mode_combo.currentData(), "integrate")
        select_index = window.mouse_mode_combo.findData("select")
        window.mouse_mode_combo.setCurrentIndex(select_index)
        self.assertFalse(window.integrate_button.isChecked())
        self.assertEqual(window._mouse_mode, "select")
        self.assertEqual(window._span_selector_mode, "select")

        event = ScreenPointerEvent(
            button=1,
            axis_role="y1",
            hit_region="plot",
            data_coordinates=(("y1", 7.0, 0.0),),
        )
        hit = window._integration_peak_hit_target(event)
        self.assertEqual(hit, ("integration_peak", narrow.id))
        window._on_canvas_press(event.with_hit_target(*hit))
        self.assertEqual(window._selected_dataset().id, dataset.id)
        self.assertEqual(window._selected_peak_ids(), [narrow.id])
        self.assertNotIn(wide.id, window._selected_peak_ids())

        range_signal = Mock()
        window.timeRangeSelected.connect(range_signal)
        dirty = window.project.dirty
        window._on_selection_span_selected(9.0, 3.0)
        self.assertEqual(window.selected_time_range, (3.0, 9.0))
        self.assertEqual(tuple(window._span_selector.extents), (3.0, 9.0))
        self.assertTrue(all(
            artist.get_visible() for artist in window._span_selector.artists
        ))
        range_signal.assert_called_once_with(3.0, 9.0)
        self.assertEqual(window.project.dirty, dirty)
        window.mouse_mode_combo.setCurrentIndex(
            window.mouse_mode_combo.findData("normal")
        )
        self.assertIsNone(window.selected_time_range)
        window.project.dirty = False
        window.close()

    def test_peak_range_dialog_enters_mouse_edit_and_restores_previous_mode(self):
        window = self.make_window()
        try:
            dataset = window.project.datasets[0]
            peak = dataset.peaks[0]
            dialog = PeakRangeDialog(
                peak,
                float(dataset.time_min[0]),
                float(dataset.time_min[-1]),
                language="ja",
                parent=window,
            )
            dialog.mouse_selection_button.click()
            self.assertTrue(dialog.mouse_selection_requested)

            select_index = window.mouse_mode_combo.findData("select")
            window.mouse_mode_combo.setCurrentIndex(select_index)
            self.assertEqual(window._mouse_mode, "select")
            window.peak_table.selectRow(0)

            mouse_dialog = PeakRangeDialog(
                peak,
                float(dataset.time_min[0]),
                float(dataset.time_min[-1]),
                language="en",
                parent=window,
            )
            mouse_dialog.mouse_selection_requested = True
            with patch("hplc_app.gui.PeakRangeDialog", return_value=mouse_dialog), \
                    patch("hplc_app.gui.dialog_exec", return_value=True):
                window.edit_peak_properties()

            self.assertTrue(window.edit_peak_button.isChecked())
            self.assertEqual(window._mouse_mode, "edit_peak")
            self.assertEqual(window.mouse_mode_combo.currentData(), "edit_peak")
            self.assertEqual(window._edit_range_peak_id, peak.id)
            self.assertTrue(window.statusBar().currentMessage())

            window._on_edit_span_selected(4.0, 9.0)
            self.assertFalse(window.edit_peak_button.isChecked())
            self.assertEqual(window._mouse_mode, "select")
            self.assertEqual(window.mouse_mode_combo.currentData(), "select")
            self.assertEqual((peak.start_min, peak.end_min), (4.0, 9.0))

            before_cancel = (peak.start_min, peak.end_min)
            with patch("hplc_app.gui.PeakRangeDialog", return_value=mouse_dialog), \
                    patch("hplc_app.gui.dialog_exec", return_value=True):
                window.edit_peak_properties()
            key_press = (
                QtCore.QEvent.Type.KeyPress
                if QT_API == 6 else QtCore.QEvent.KeyPress
            )
            escape_key = (
                QtCore.Qt.Key.Key_Escape
                if QT_API == 6 else QtCore.Qt.Key_Escape
            )
            no_modifier = (
                QtCore.Qt.KeyboardModifier.NoModifier
                if QT_API == 6 else QtCore.Qt.NoModifier
            )
            window.keyPressEvent(
                QtGui.QKeyEvent(key_press, escape_key, no_modifier)
            )
            self.assertFalse(window.edit_peak_button.isChecked())
            self.assertEqual(window._mouse_mode, "select")
            self.assertEqual((peak.start_min, peak.end_min), before_cancel)
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_peak_selection_restyles_without_scene_rebuild(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            dataset = window.project.datasets[0]
            first = dataset.peaks[0]
            raw_before = (
                dataset.time_min.copy(), dataset.intensity_uv.copy()
            )
            second = PeakRegion(start_min=10.0, end_min=13.0)
            dataset.peaks.append(second)
            recalculate_dataset_peaks(dataset)
            window._refresh_peak_table([first.id])
            window._plot()
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            consumer = preview.consumer
            overlay_items = {
                peak_id: {
                    name: tuple(value) if isinstance(value, list) else value
                    for name, value in items.items()
                    if name != "base_color"
                }
                for peak_id, items in consumer.peak_overlay_items.items()
            }
            before_view = consumer.capture_view_state()
            first_region = consumer.peak_overlay_items[first.id]["region"]
            second_region = consumer.peak_overlay_items[second.id]["region"]

            with patch.object(consumer, "render", wraps=consumer.render) as render, \
                    patch.object(window, "_plot", wraps=window._plot) as plot, \
                    patch.object(
                        first_region, "update", wraps=first_region.update
                    ) as first_update, patch.object(
                        second_region, "update", wraps=second_region.update
                    ) as second_update:
                window.peak_table.selectRow(1)
                self.app.processEvents()
                self.assertEqual(render.call_count, 0)
                self.assertEqual(plot.call_count, 0)
                self.assertEqual(first_update.call_count, 1)
                self.assertEqual(second_update.call_count, 1)
                self.assertEqual(consumer.capture_view_state(), before_view)
                for peak_id, previous in overlay_items.items():
                    current = consumer.peak_overlay_items[peak_id]
                    for name, item in previous.items():
                        if isinstance(item, tuple):
                            self.assertEqual(tuple(current[name]), item)
                        else:
                            self.assertIs(current[name], item)

                first_brush = consumer.peak_overlay_items[first.id]["region"].brush
                second_brush = consumer.peak_overlay_items[second.id]["region"].brush
                self.assertNotEqual(first_brush.color().name(), "#f59e0b")
                self.assertEqual(second_brush.color().name(), "#f59e0b")
                self.assertAlmostEqual(second_brush.color().alphaF(), 0.24, places=2)

                window.open_integration_list()
                detached = window._integration_list_dialog
                detached.table.selectRow(0)
                self.app.processEvents()
                self.assertEqual(window._selected_peak_ids(), [first.id])
                self.assertEqual(render.call_count, 0)
                self.assertEqual(plot.call_count, 0)
                self.assertEqual(first_update.call_count, 2)
                self.assertEqual(second_update.call_count, 2)
                self.assertEqual(consumer.capture_view_state(), before_view)
                self.assertEqual(
                    consumer.peak_overlay_items[first.id]["region"]
                    .brush.color().name(),
                    "#f59e0b",
                )
                self.assertNotEqual(
                    consumer.peak_overlay_items[second.id]["region"]
                    .brush.color().name(),
                    "#f59e0b",
                )

                detached.table.clearSelection()
                self.app.processEvents()
                self.assertEqual(window._selected_peak_ids(), [])
                self.assertEqual(render.call_count, 0)
                self.assertEqual(plot.call_count, 0)
                self.assertEqual(first_update.call_count, 3)
                self.assertEqual(second_update.call_count, 3)
                self.assertEqual(consumer.capture_view_state(), before_view)
                for peak_id in (first.id, second.id):
                    brush = consumer.peak_overlay_items[peak_id]["region"].brush
                    self.assertNotEqual(brush.color().name(), "#f59e0b")
                    self.assertAlmostEqual(brush.color().alphaF(), 0.08, places=2)
            self.assertTrue(np.array_equal(dataset.time_min, raw_before[0]))
            self.assertTrue(np.array_equal(dataset.intensity_uv, raw_before[1]))

            window.peak_table.selectRow(0)
            window.edit_peak_button.setChecked(True)
            core = consumer.qt_core
            gui = consumer.qt_gui
            self.app.sendEvent(
                consumer.widget.viewport(),
                gui.QKeyEvent(
                    core.QEvent.Type.KeyPress,
                    core.Qt.Key.Key_Escape,
                    core.Qt.KeyboardModifier.NoModifier,
                ),
            )
            self.assertFalse(window.edit_peak_button.isChecked())
            self.assertEqual(window._mouse_mode, "normal")
        finally:
            window.project.dirty = False
            window.close()

    def test_vertical_pointer_additive_selection_bulk_delete_and_undo(self):
        window = self.make_window()
        first = VerticalMarker(x_min=6.0)
        second = VerticalMarker(x_min=8.0)
        window.project.vertical_markers = [first, second]
        window._selected_vertical_marker_ids.clear()
        window._selected_vertical_marker_id = ""
        window._plot()
        window.pointer_action.setChecked(True)
        for marker in (first, second):
            window._on_canvas_press(
                ScreenPointerEvent(
                    button=1,
                    axis_role="y1",
                    hit_region="plot",
                    data_coordinates=(("y1", marker.x_min, 0.0),),
                    hit_kind="vertical_marker",
                    hit_id=marker.id,
                )
            )
        self.assertEqual(window._selected_vertical_marker_ids, {first.id, second.id})
        self.assertTrue(window.delete_selected_vertical_marker())
        self.assertEqual(window.project.vertical_markers, [])
        window.undo()
        self.assertEqual(
            {marker.id for marker in window.project.vertical_markers},
            {first.id, second.id},
        )
        window.project.dirty = False
        window.close()

    def test_numeric_fraction_range_add_edit_and_undo(self):
        from hplc_app.project_io import load_project, save_project

        window = self.make_window()
        add_dialog = FractionRangeDialog(
            [], 1.0, 0.0, 20.0, language="en", parent=window
        )
        self.assertEqual(add_dialog.interval.suffix(), " s")
        self.assertEqual(add_dialog.interval.minimum(), 1.0)
        self.assertEqual(add_dialog.interval.value(), 60.0)
        add_dialog.start.setValue(2.5)
        add_dialog.end.setValue(9.5)
        add_dialog.interval.setValue(75.0)
        with patch("hplc_app.gui.FractionRangeDialog", return_value=add_dialog), \
                patch("hplc_app.gui.dialog_exec", return_value=True):
            window.edit_fraction_range_numeric()
        self.assertEqual(len(window.project.fraction_regions), 1)
        region = window.project.fraction_regions[0]
        region_id = region.id
        self.assertEqual(
            (region.start_min, region.end_min, region.interval_min),
            (2.5, 9.5, 1.25),
        )

        edit_dialog = FractionRangeDialog(
            window.project.fraction_regions,
            1.0,
            0.0,
            20.0,
            language="en",
            parent=window,
        )
        edit_dialog.select_region(region_id)
        self.assertEqual(edit_dialog.interval.value(), 75.0)
        edit_dialog.start.setValue(3.0)
        edit_dialog.end.setValue(8.0)
        edit_dialog.interval.setValue(30.0)
        with patch("hplc_app.gui.FractionRangeDialog", return_value=edit_dialog), \
                patch("hplc_app.gui.dialog_exec", return_value=True):
            window.edit_fraction_range_numeric()
        self.assertEqual(len(window.project.fraction_regions), 1)
        self.assertEqual(window.project.fraction_regions[0].id, region_id)
        self.assertEqual(
            (
                window.project.fraction_regions[0].start_min,
                window.project.fraction_regions[0].end_min,
                window.project.fraction_regions[0].interval_min,
            ),
            (3.0, 8.0, 0.5),
        )
        window.undo()
        restored = window.project.fraction_regions[0]
        self.assertEqual(restored.id, region_id)
        self.assertEqual(
            (restored.start_min, restored.end_min, restored.interval_min),
            (2.5, 9.5, 1.25),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "fraction-seconds.hplcproj")
            save_project(path, window.project)
            loaded = load_project(path)
        loaded_region = loaded.fraction_regions[0]
        self.assertEqual(
            (
                loaded_region.start_min,
                loaded_region.end_min,
                loaded_region.interval_min,
            ),
            (2.5, 9.5, 1.25),
        )
        window.project.dirty = False
        window.close()

    def test_selection_range_deletes_peaks_and_markers_in_one_undo_step(self):
        window = self.make_window()
        try:
            dataset = window.project.datasets[0]
            inside_peak = dataset.peaks[0]
            outside_peak = PeakRegion(start_min=20.0, end_min=22.0)
            dataset.peaks.append(outside_peak)
            recalculate_dataset_peaks(dataset)
            inside_marker = VerticalMarker(x_min=7.0)
            outside_marker = VerticalMarker(x_min=15.0)
            window.project.vertical_markers = [inside_marker, outside_marker]
            raw_time = dataset.time_min.copy()
            raw_intensity = dataset.intensity_uv.copy()
            window._refresh_peak_table()
            window._plot()
            window.mouse_mode_combo.setCurrentIndex(
                window.mouse_mode_combo.findData("select")
            )
            window.project.dirty = False
            undo_count = len(window._undo_stack)

            window._on_selection_span_selected(4.0, 11.0)

            self.assertEqual(window._selected_peak_ids(), [inside_peak.id])
            self.assertEqual(
                window._selected_vertical_marker_ids, {inside_marker.id}
            )
            self.assertEqual(window.selected_time_range, (4.0, 11.0))
            self.assertTrue(window.delete_selected_plot_items())
            self.assertIsNone(window.selected_time_range)
            self.assertTrue(all(
                not artist.get_visible()
                for artist in window._span_selector.artists
            ))
            self.assertEqual(
                [peak.id for peak in dataset.peaks], [outside_peak.id]
            )
            self.assertEqual(
                [marker.id for marker in window.project.vertical_markers],
                [outside_marker.id],
            )
            self.assertEqual(len(window._undo_stack), undo_count + 1)
            np.testing.assert_array_equal(dataset.time_min, raw_time)
            np.testing.assert_array_equal(dataset.intensity_uv, raw_intensity)

            window.undo()
            restored = window._selected_dataset()
            self.assertEqual(
                {peak.id for peak in restored.peaks},
                {inside_peak.id, outside_peak.id},
            )
            self.assertEqual(
                {marker.id for marker in window.project.vertical_markers},
                {inside_marker.id, outside_marker.id},
            )
            window.redo()
            self.assertEqual(
                [peak.id for peak in window._selected_dataset().peaks],
                [outside_peak.id],
            )
            self.assertEqual(
                [marker.id for marker in window.project.vertical_markers],
                [outside_marker.id],
            )
        finally:
            window.project.dirty = False
            window.close()

    def test_numeric_fraction_range_rejects_too_many_divider_lines(self):
        dialog = FractionRangeDialog(
            [], 1.0, 0.0, 100.0, language="en"
        )
        dialog.start.setValue(0.0)
        dialog.end.setValue(100.0)
        dialog.interval.setValue(1.0)
        with patch.object(QtWidgets.QMessageBox, "warning") as warning, \
                patch.object(dialog, "accept") as accept:
            dialog._accept()
        warning.assert_called_once()
        accept.assert_not_called()
        dialog.close()

    def test_fraction_divider_limit_applies_across_all_ranges(self):
        first = FractionRegion(
            start_min=0.0,
            end_min=50.0,
            interval_min=1.0 / 60.0,
        )
        dialog = FractionRangeDialog(
            [first], 1.0, 60.0, 100.0, language="en"
        )
        dialog.start.setValue(60.0)
        dialog.end.setValue(100.0)
        dialog.interval.setValue(1.0)
        with patch.object(QtWidgets.QMessageBox, "warning") as warning, \
                patch.object(dialog, "accept") as accept:
            dialog._accept()
        warning.assert_called_once()
        accept.assert_not_called()
        dialog.close()

        project = Project(
            fraction_regions=[
                first,
                FractionRegion(
                    start_min=60.0,
                    end_min=110.0,
                    interval_min=1.0 / 60.0,
                ),
            ]
        )
        scene = compose_base_screen_scene(
            project, color_resolver=lambda _dataset, _index: "#000000"
        )
        self.assertEqual(
            sum(len(region.boundary_values) for region in scene.fraction_regions),
            MAX_FRACTION_BOUNDARY_LINES,
        )

    def test_preview_selection_range_uses_shared_non_mutating_contract(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.screen_preview_checkbox.setChecked(True)
            index = window.mouse_mode_combo.findData("select")
            window.mouse_mode_combo.setCurrentIndex(index)
            preview = window._screen_preview
            signal = Mock()
            window.timeRangeSelected.connect(signal)
            undo_count = len(window._undo_stack)
            window.project.dirty = False

            def event(time, pixel):
                return ScreenPointerEvent(
                    button=1,
                    axis_role="y1",
                    hit_region="plot",
                    canvas_x=float(pixel),
                    data_coordinates=(("y1", float(time), 0.0),),
                )

            preview.handle_event("button_press_event", event(2.0, 20.0))
            preview.handle_event("motion_notify_event", event(4.0, 80.0))
            preview.handle_event("button_release_event", event(4.0, 80.0))
            self.assertEqual(window.selected_time_range, (2.0, 4.0))
            signal.assert_called_once_with(2.0, 4.0)
            self.assertEqual(len(window._undo_stack), undo_count)
            self.assertFalse(window.project.dirty)
            self.assertIsNone(preview._span_drag)
            self.assertTrue(preview.consumer.span_selection.isVisible())
            self.assertEqual(
                tuple(preview.consumer.span_selection.getRegion()), (2.0, 4.0)
            )

            preview.handle_event("button_press_event", event(30.0, 120.0))
            self.assertIsNone(window.selected_time_range)
            self.assertEqual(
                tuple(preview.consumer.span_selection.getRegion()), (30.0, 30.0)
            )
            preview.handle_event("button_release_event", event(30.0, 120.0))
            self.assertFalse(preview.consumer.span_selection.isVisible())

            preview.handle_event("button_press_event", event(2.0, 20.0))
            preview.handle_event("motion_notify_event", event(4.0, 80.0))
            preview.handle_event("button_release_event", event(4.0, 80.0))

            escape_key = (
                QtCore.Qt.Key.Key_Escape if QT_API == 6
                else QtCore.Qt.Key_Escape
            )
            QtTest.QTest.keyClick(preview.consumer.widget, escape_key)
            self.app.processEvents()
            self.assertIsNone(window.selected_time_range)
            self.assertFalse(preview.consumer.span_selection.isVisible())

            preview.handle_event("button_press_event", event(3.0, 30.0))
            preview.handle_event("motion_notify_event", event(5.0, 90.0))
            preview.handle_event("button_release_event", event(5.0, 90.0))
            self.assertEqual(window.selected_time_range, (3.0, 5.0))
            window.mouse_mode_combo.setCurrentIndex(
                window.mouse_mode_combo.findData("normal")
            )
            self.assertIsNone(window.selected_time_range)
            self.assertFalse(preview.consumer.span_selection.isVisible())
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_click_selects_the_same_overlapping_integration_area(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            dataset = window.project.datasets[0]
            wide = dataset.peaks[0]
            narrow = PeakRegion(start_min=6.0, end_min=8.0)
            dataset.peaks.append(narrow)
            recalculate_dataset_peaks(dataset)
            window._refresh_peak_table([wide.id])
            window._plot()
            window.screen_preview_checkbox.setChecked(True)
            index = window.mouse_mode_combo.findData("select")
            window.mouse_mode_combo.setCurrentIndex(index)
            event = ScreenPointerEvent(
                button=1,
                axis_role="y1",
                hit_region="plot",
                canvas_x=50.0,
                canvas_y=50.0,
                data_coordinates=(("y1", 7.0, 0.0),),
            )
            self.assertTrue(
                window._screen_preview.handle_event("button_press_event", event)
            )
            self.assertEqual(window._selected_peak_ids(), [narrow.id])
            self.assertNotIn(wide.id, window._selected_peak_ids())
            self.assertIsNotNone(window._screen_preview)
        finally:
            window.project.dirty = False
            window.close()

    def test_numeric_auv_and_independent_time_shift(self):
        window = self.make_window()
        dataset = window.project.datasets[0]
        dialog = MetadataDialog(dataset, "ja")
        dialog.aux_edit.setText("2.75")
        dialog._accept()
        self.assertEqual(dataset.measurement.aux_range_au_per_v, 2.75)
        window.dataset_table.item(0, DATASET_X_SHIFT_COLUMN).setText("0.4")
        self.app.processEvents()
        self.assertAlmostEqual(dataset.x_shift_min, 0.4, places=6)
        x_values = window._dataset_lines[dataset.id].get_xdata()
        self.assertAlmostEqual(float(x_values[0]), float(dataset.time_min[0]) + 0.4, places=6)
        window.project.dirty = False
        window.close()

    def test_selected_integration_is_highlighted_and_overlays_can_be_hidden(self):
        window = self.make_window()
        window.peak_table.selectRow(0)
        self.app.processEvents()
        self.assertGreater(len(window.axes.patches), 0)
        selected_color = window.axes.patches[0].get_facecolor()
        self.assertGreater(selected_color[0], selected_color[1])
        peak = window.project.datasets[0].peaks[0]
        boundary_lines = []
        retention_lines = []
        for line in window.axes.lines:
            x_values = np.asarray(line.get_xdata(), dtype=float)
            if x_values.size != 2 or not np.allclose(x_values[0], x_values[1]):
                continue
            if np.isclose(x_values[0], peak.start_min) or np.isclose(
                x_values[0], peak.end_min
            ):
                boundary_lines.append(line)
            if np.isclose(x_values[0], peak.retention_time_min):
                retention_lines.append(line)
        self.assertEqual(len(boundary_lines), 2)
        self.assertTrue(
            all(line.get_color() == "#9ca3af" for line in boundary_lines)
        )
        self.assertTrue(retention_lines)
        self.assertTrue(
            all(line.get_color() != "#9ca3af" for line in retention_lines)
        )
        window.show_integration_checkbox.setChecked(False)
        self.app.processEvents()
        self.assertEqual(len(window.axes.patches), 0)
        self.assertFalse(any(line.get_visible() for line in window.axes.get_xgridlines()))
        window.show_grid_checkbox.setChecked(True)
        self.app.processEvents()
        self.assertTrue(any(line.get_visible() for line in window.axes.get_xgridlines()))
        self.assertTrue(window.project.method.show_major_grid)
        with tempfile.TemporaryDirectory() as directory:
            for suffix in ("png", "svg", "pdf"):
                path = Path(directory) / ("figure." + suffix)
                window.figure.savefig(str(path))
                self.assertGreater(path.stat().st_size, 0)
        window.project.dirty = False
        window.close()

    def test_retention_label_toggle_and_interaction_cursor(self):
        window = self.make_window()
        retention = window.project.datasets[0].peaks[0].retention_time_min
        window.show_retention_checkbox.setChecked(True)
        self.app.processEvents()
        self.assertIn("%.2f" % retention, [item.get_text() for item in window.axes.texts])

        window.toolbar.zoom()
        self.assertTrue(str(window.toolbar.mode))
        window.integrate_button.setChecked(True)
        self.app.processEvents()
        self.assertFalse(str(window.toolbar.mode))
        window._on_canvas_motion(SimpleNamespace(xdata=7.25, inaxes=window.axes))
        self.assertTrue(window._interaction_cursor.get_visible())
        self.assertAlmostEqual(float(window._interaction_cursor.get_xdata()[0]), 7.25, places=6)
        window.integrate_button.setChecked(False)
        window.peak_table.selectRow(0)
        window.toolbar.zoom()
        window.split_peak_button.setChecked(True)
        self.app.processEvents()
        self.assertFalse(str(window.toolbar.mode))
        window._on_canvas_motion(SimpleNamespace(xdata=7.5, inaxes=window.axes))
        self.assertTrue(window._interaction_cursor.get_visible())
        window.split_peak_button.setChecked(False)
        window.project.dirty = False
        window.close()

    def test_full_view_preserves_trace_and_axis_settings(self):
        window = self.make_window()
        selected = window.project.datasets[0]
        selected.x_shift_min = 0.5
        selected.offset = 1234.0
        window.project.method.x_axis_label = "Custom time"
        window.project.method.y_axis_1_label = "Custom signal"
        window._plot(preserve_view=False)
        window.axes.set_xlim(5.0, 12.0)
        window._reset_view()
        self.assertEqual(selected.x_shift_min, 0.5)
        self.assertEqual(selected.offset, 1234.0)
        self.assertEqual(window.axes.get_xlabel(), "Custom time")
        self.assertEqual(window.axes.get_ylabel(), "Custom signal")
        self.assertGreaterEqual(window.axes.get_xlim()[1], float(selected.time_min[-1]) + 0.5)
        window.project.dirty = False
        window.close()

    def test_view_shortcuts_reuse_the_full_view_buttons_in_both_renderers(self):
        window = self.make_window()
        window.show()
        self.app.processEvents()
        window._plot(preserve_view=False)
        shortcuts = {
            shortcut.key().toString().lower(): shortcut
            for shortcut in window._view_shortcuts
        }
        self.assertEqual(sorted(shortcuts), ["w", "x", "y"])

        def zoom_in():
            window.axes.set_xlim(5.0, 12.0)
            window.axes.set_ylim(-10.0, 10.0)
            window.axes_right.set_ylim(-20.0, 20.0)

        # Each key must land on exactly the state its button produces.
        for key, button in (
            ("x", window.reset_x_view_button),
            ("y", window.reset_y_view_button),
            ("w", window.reset_view_button),
        ):
            with self.subTest(key=key):
                zoom_in()
                button.click()
                by_button = (
                    window.axes.get_xlim(),
                    window.axes.get_ylim(),
                    window.axes_right.get_ylim(),
                )
                zoom_in()
                shortcuts[key].activated.emit()
                self.app.processEvents()
                self.assertEqual(
                    (
                        window.axes.get_xlim(),
                        window.axes.get_ylim(),
                        window.axes_right.get_ylim(),
                    ),
                    by_button,
                )
                self.assertIn(key.upper(), button.toolTip())

        # A shortcut consumes its key before the focused widget sees it, so the
        # single letters have to be disabled outright while text is edited.
        editor = QtWidgets.QLineEdit(window)
        window._update_view_shortcuts(None, editor)
        for shortcut in window._view_shortcuts:
            self.assertFalse(shortcut.isEnabled())
        window._update_view_shortcuts(None, window.dataset_table)
        for shortcut in window._view_shortcuts:
            self.assertTrue(shortcut.isEnabled())
        window._update_view_shortcuts(None, window.view_mode_combo)
        for shortcut in window._view_shortcuts:
            self.assertFalse(shortcut.isEnabled())
        editor.deleteLater()

        if pyqtgraph_scene_available():
            # The same keys must work while the native renderer is on screen.
            window.screen_preview_checkbox.setChecked(True)
            self.app.processEvents()
            preview = window._screen_preview
            self.assertIsNotNone(preview)
            window._update_view_shortcuts(None, preview.consumer.widget)
            for shortcut in window._view_shortcuts:
                self.assertTrue(shortcut.isEnabled())
            state = preview.consumer.capture_view_state()
            narrowed = ScreenViewState(
                x=(state.x[0] + 1.0, state.x[1] - 1.0),
                y1=state.y1, y2=state.y2, gradient=state.gradient,
            )
            preview.consumer.apply_view_state(
                narrowed, compose_overview_state(False, state.x, narrowed.x)
            )
            self.assertNotEqual(
                preview.consumer.capture_view_state().x, window._full_x_bounds()
            )
            shortcuts["x"].activated.emit()
            self.app.processEvents()
            self.assertEqual(
                preview.consumer.capture_view_state().x, window._full_x_bounds()
            )
        window.project.dirty = False
        window.close()

    def test_screen_grid_draws_vertical_lines_only_in_both_renderers(self):
        window = self.make_window()
        window.show()
        self.app.processEvents()
        for enabled in (True, False):
            with self.subTest(show_major_grid=enabled):
                window.project.method.show_major_grid = enabled
                window._plot(preserve_view=False)
                self.app.processEvents()
                # The existing setting keeps its on/off meaning for the X lines.
                self.assertEqual(
                    [line.get_visible() for line in window.axes.get_xgridlines()],
                    [enabled] * len(window.axes.get_xgridlines()),
                )
                # No horizontal grid line is ever drawn now.
                self.assertFalse(
                    any(line.get_visible() for line in window.axes.get_ygridlines())
                )

        if not pyqtgraph_scene_available():
            window.project.dirty = False
            window.close()
            return
        window.screen_preview_checkbox.setChecked(True)
        self.app.processEvents()
        preview = window._screen_preview
        self.assertIsNotNone(preview)
        for view_mode in ("single", "split_y_axes"):
            for enabled in (True, False):
                with self.subTest(view_mode=view_mode, show_major_grid=enabled):
                    window.project.method.view_mode = view_mode
                    window.project.method.show_major_grid = enabled
                    window._plot()
                    self.app.processEvents()
                    consumer = window._screen_preview.consumer
                    plots = [consumer.primary]
                    if consumer.secondary_plot is not None:
                        plots.append(consumer.secondary_plot)
                    for plot in plots:
                        self.assertEqual(plot.ctrl.xGridCheck.isChecked(), enabled)
                        self.assertFalse(plot.ctrl.yGridCheck.isChecked())
        window.project.dirty = False
        window.close()

    def test_screen_grid_does_not_break_plot_hit_region_in_either_view_mode(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.show()
            window.screen_preview_checkbox.setChecked(True)
            self.app.processEvents()
            window.project.method.show_major_grid = True
            for view_mode in ("single", "split_y_axes"):
                with self.subTest(view_mode=view_mode):
                    window.project.method.view_mode = view_mode
                    window._plot()
                    self.app.processEvents()
                    consumer = window._screen_preview.consumer
                    # Issue #236 / 9.2: with the grid on, AxisItem's
                    # boundingRect() reports the union with the whole
                    # ViewBox (it draws the grid lines), so a point well
                    # inside the plot must still resolve to the plot itself,
                    # not the axis band that used to win.
                    if view_mode == "split_y_axes":
                        checks = (
                            (consumer.primary, "y1", "plot_y1"),
                            (consumer.secondary_plot, "y2", "plot_y2"),
                        )
                    else:
                        checks = ((consumer.primary, "y1", "plot"),)
                    for plot, role, region in checks:
                        center = plot.vb.sceneBoundingRect().center()
                        self.assertEqual(
                            consumer._pointer_region(center), (role, region)
                        )
                    # A point genuinely below the plot's own ViewBox (on the
                    # bottom axis's tick-label strip, not merely within its
                    # grid-inflated bounding rect) still resolves to the
                    # axis -- single-axis band panning is not regressed by
                    # checking the plot rect first. In split mode, the
                    # visible x-tick labels live on the bottom (secondary)
                    # plot -- the primary plot's own bottom axis is blanked
                    # (setStyle(showValues=False)) since the axis is shared.
                    axis_plot = (
                        consumer.secondary_plot
                        if view_mode == "split_y_axes"
                        else consumer.primary
                    )
                    plot_rect = axis_plot.vb.sceneBoundingRect()
                    axis_point = consumer.qt_core.QPointF(
                        plot_rect.center().x(), plot_rect.bottom() + 15.0
                    )
                    self.assertFalse(
                        consumer._point_in_rect(axis_point, plot_rect)
                    )
                    _axis_role, hit_region = consumer._pointer_region(axis_point)
                    self.assertEqual(hit_region, "x")
        finally:
            window.project.dirty = False
            window.close()

    def test_native_scene_refresh_never_processes_an_auto_ranged_frame(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            consumer = preview.consumer
            original = window._screen_view_state()
            narrowed = ScreenViewState(
                x=(6.0, 14.0),
                y1=original.y1,
                y2=original.y2,
                gradient=original.gradient,
            )
            window._apply_view_state(narrowed)

            def verify_refresh(action):
                render = Mock(wraps=consumer.render)
                processed_ranges = []

                def record_processed_range():
                    processed_ranges.append(
                        tuple(consumer.primary.viewRange()[0])
                    )

                with patch.object(consumer, "render", render), \
                        patch.object(consumer.primary, "enableAutoRange") as primary_auto, \
                        patch.object(consumer.secondary, "enableAutoRange") as secondary_auto, \
                        patch.object(consumer.overview, "enableAutoRange") as overview_auto, \
                        patch.object(
                            consumer.application, "processEvents",
                            side_effect=record_processed_range,
                        ):
                    action()
                self.assertGreaterEqual(render.call_count, 1)
                for call in render.call_args_list:
                    self.assertEqual(call.kwargs["view_state"].x, narrowed.x)
                    self.assertIsNotNone(call.kwargs["overview_state"])
                for auto_range in (primary_auto, secondary_auto, overview_auto):
                    for call in auto_range.call_args_list:
                        self.assertTrue(call.args or call.kwargs)
                        self.assertFalse(any(
                            value is True
                            for value in call.args + tuple(call.kwargs.values())
                        ))
                for processed in processed_ranges:
                    self.assertAlmostEqual(processed[0], narrowed.x[0])
                    self.assertAlmostEqual(processed[1], narrowed.x[1])
                self.assertEqual(window._screen_view_state().x, narrowed.x)

            def hide_second_dataset():
                window.dataset_table.item(
                    1, DATASET_VISIBLE_COLUMN
                ).setCheckState(UNCHECKED)

            verify_refresh(hide_second_dataset)
            verify_refresh(lambda: window._on_span_selected(2.0, 4.0))
            verify_refresh(lambda: window._split_selected_peak_at(3.0))
            verify_refresh(lambda: window.view_mode_combo.setCurrentIndex(
                window.view_mode_combo.findData("overview_detail")
            ))
        finally:
            window.project.dirty = False
            window.close()

    def test_native_display_toggles_do_not_rebuild_scene_or_mouse_owner(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.show()
            window.screen_preview_checkbox.setChecked(True)
            self.app.processEvents()
            preview = window._screen_preview
            self.assertIsNotNone(preview)
            consumer = preview.consumer
            scene = window._screen_scene
            pointer_handler = consumer._pointer_handler
            render = Mock(wraps=consumer.render)
            consumer.render = render
            original = window._screen_view_state()
            narrowed = ScreenViewState(
                x=(6.0, 14.0),
                y1=original.y1,
                y2=original.y2,
                gradient=original.gradient,
            )
            window._apply_view_state(narrowed)
            window.dataset_table.selectRow(0)
            window.peak_table.selectRow(0)

            for checkbox in (
                window.show_integration_checkbox,
                window.show_retention_checkbox,
                window.show_gradient_checkbox,
            ):
                checkbox.setChecked(not checkbox.isChecked())
                self.app.processEvents()
                self.assertIs(window._screen_preview, preview)
                self.assertIs(window._screen_scene, scene)
                self.assertEqual(render.call_count, 0)
                self.assertEqual(window._screen_view_state().x, narrowed.x)
                checkbox.setChecked(not checkbox.isChecked())
                self.app.processEvents()
                self.assertEqual(render.call_count, 0)

            for mode in MOUSE_MODE_IDS:
                with self.subTest(mouse_mode=mode):
                    # Individual interaction tests cover each tool's signal
                    # path. Here the shared mode owner is held constant while
                    # the grid setting refreshes the native display.
                    window._set_mouse_mode_display(mode)
                    self.assertEqual(window._mouse_mode, mode)
                    for enabled in (True, False):
                        window.show_grid_checkbox.setChecked(enabled)
                        self.app.processEvents()
                        self.assertEqual(window._mouse_mode, mode)
                        self.assertIs(consumer._pointer_handler, pointer_handler)
                        self.assertIs(window._screen_scene, scene)
                        self.assertEqual(render.call_count, 0)
                        self.assertEqual(
                            consumer.primary.ctrl.xGridCheck.isChecked(),
                            enabled,
                        )
        finally:
            window.project.dirty = False
            window.close()

    def test_native_split_divider_drags_and_retains_ratio_during_view(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.view_mode_combo.setCurrentIndex(
                window.view_mode_combo.findData("split_y_axes")
            )
            window.screen_preview_checkbox.setChecked(True)
            window.show()
            self.app.processEvents()
            preview = window._screen_preview
            consumer = preview.consumer
            viewport = consumer.widget.viewport()
            handle_center = consumer.widget.mapFromScene(
                consumer.split_handle.sceneBoundingRect().center()
            )
            target = handle_center + QtCore.QPoint(0, 90)
            types = QtCore.QEvent.Type
            buttons = QtCore.Qt.MouseButton

            def mouse(kind, point, button, held):
                self.app.sendEvent(
                    viewport,
                    QtGui.QMouseEvent(
                        kind,
                        QtCore.QPointF(point),
                        QtCore.QPointF(viewport.mapToGlobal(point)),
                        button,
                        held,
                        QtCore.Qt.KeyboardModifier.NoModifier,
                    ),
                )

            before_state = window._screen_view_state()
            render = Mock(wraps=consumer.render)
            consumer.render = render
            mouse(
                types.MouseButtonPress,
                handle_center,
                buttons.LeftButton,
                buttons.LeftButton,
            )
            mouse(
                types.MouseMove,
                target,
                buttons.NoButton,
                buttons.LeftButton,
            )
            mouse(
                types.MouseButtonRelease,
                target,
                buttons.LeftButton,
                buttons.NoButton,
            )
            self.app.processEvents()
            dragged_ratio = consumer.split_ratio
            self.assertGreater(dragged_ratio, 0.5)
            self.assertEqual(render.call_count, 0)
            self.assertEqual(window._screen_view_state(), before_state)

            window._plot()
            self.app.processEvents()
            self.assertIs(window._screen_preview, preview)
            self.assertAlmostEqual(consumer.split_ratio, dragged_ratio)
        finally:
            window.project.dirty = False
            window.close()

    def test_native_overview_divider_clamps_and_survives_scene_refresh(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.view_mode_combo.setCurrentIndex(
                window.view_mode_combo.findData("overview_detail")
            )
            window.screen_preview_checkbox.setChecked(True)
            window.show()
            self.app.processEvents()
            consumer = window._screen_preview.consumer
            self.assertTrue(consumer.overview_handle.isVisible())
            viewport = consumer.widget.viewport()
            handle_center = consumer.widget.mapFromScene(
                consumer.overview_handle.sceneBoundingRect().center()
            )
            target = handle_center + QtCore.QPoint(0, 60)
            for kind, point, button, held in (
                (QtCore.QEvent.Type.MouseButtonPress, handle_center,
                 QtCore.Qt.MouseButton.LeftButton,
                 QtCore.Qt.MouseButton.LeftButton),
                (QtCore.QEvent.Type.MouseMove, target,
                 QtCore.Qt.MouseButton.NoButton,
                 QtCore.Qt.MouseButton.LeftButton),
                (QtCore.QEvent.Type.MouseButtonRelease, target,
                 QtCore.Qt.MouseButton.LeftButton,
                 QtCore.Qt.MouseButton.NoButton),
            ):
                self.app.sendEvent(viewport, QtGui.QMouseEvent(
                    kind, QtCore.QPointF(point),
                    QtCore.QPointF(viewport.mapToGlobal(point)),
                    button, held, QtCore.Qt.KeyboardModifier.NoModifier,
                ))
            self.assertGreater(consumer.overview_ratio, 0.25)
            self.assertEqual(
                window._overview_split_ratio, consumer.overview_ratio
            )
            consumer.set_overview_ratio(0.02)
            self.assertEqual(consumer.overview_ratio, 0.15)
            self.assertEqual(window._overview_split_ratio, 0.15)
            consumer.set_overview_ratio(0.72)
            window._plot()
            self.app.processEvents()
            self.assertAlmostEqual(consumer.overview_ratio, 0.72)
            self.assertIsNotNone(consumer.overview.vb.border)
            rect = consumer.overview_secondary_region.rect()
            state = window._screen_view_state()
            self.assertEqual((rect.top(), rect.bottom()), state.y2)
            consumer.apply_view_state(
                replace(state, y2=None), consumer.overview_state
            )
            self.assertFalse(consumer.overview_secondary_region.isVisible())
            consumer.apply_view_state(state, consumer.overview_state)
            full_before = consumer.overview_state.full_x
            midpoint = sum(full_before) / 2.0
            detail_before = (midpoint - 5.0, midpoint + 5.0)
            window._apply_view_state(replace(state, x=detail_before))
            consumer.overview_buttons[0].click()
            self.app.processEvents()
            full_after = consumer.overview_state.full_x
            self.assertLess(
                full_after[1] - full_after[0],
                full_before[1] - full_before[0],
            )
            self.assertEqual(window._screen_view_state().x, detail_before)
            consumer.overview_buttons[2].click()
            self.app.processEvents()
            self.assertEqual(consumer.overview_state.full_x, window._full_x_bounds())
            self.assertTrue(consumer.overview_scrollbar.isVisible())
            self.assertGreater(consumer.overview_scrollbar.pageStep(), 0)
            consumer.overview_buttons[0].click()
            self.app.processEvents()
            detail_before_scroll = window._screen_view_state().x
            history_before_scroll = window._screen_preview.navigation.history.count
            consumer.overview_scrollbar.setValue(
                consumer.overview_scrollbar.maximum()
            )
            self.app.processEvents()
            self.assertAlmostEqual(
                consumer.overview_state.full_x[1], window._full_x_bounds()[1]
            )
            self.assertEqual(window._screen_view_state().x, detail_before_scroll)
            self.assertEqual(
                window._screen_preview.navigation.history.count,
                history_before_scroll,
            )
        finally:
            window.project.dirty = False
            window.close()

    def test_native_second_axis_title_stays_clear_of_the_gradient_axis(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.project.method.show_gradient_b = True
            window.project.method.y_axis_2_label = "吸光度 214 nm (mAU)"
            window.show()
            self.app.processEvents()
            window.screen_preview_checkbox.setChecked(True)
            self.app.processEvents()
            preview = window._screen_preview
            self.assertIsNotNone(preview)

            def title_gap(host, gradient_axis):
                """Scene distance from the Y2 title's right edge to the B% axis."""
                axis = host.getAxis("right")
                label = axis.label
                label_right = axis.mapRectFromItem(
                    label, label.boundingRect()
                ).right()
                title_edge = axis.mapToScene(
                    QtCore.QPointF(label_right, 0.0)
                ).x()
                gradient_edge = gradient_axis.mapToScene(
                    QtCore.QPointF(0.0, 0.0)
                ).x()
                return gradient_edge - title_edge

            for view_mode in ("single", "split_y_axes"):
                with self.subTest(view_mode=view_mode):
                    window.project.method.view_mode = view_mode
                    window._plot()
                    self.app.processEvents()
                    consumer = window._screen_preview.consumer
                    left = consumer.primary.getAxis("left")
                    font = left.style["tickFont"]
                    metrics = consumer.qt_gui.QFontMetricsF(font)
                    legacy_width = max(
                        80,
                        metrics.horizontalAdvance("-12345.6789")
                        + metrics.height() + 16,
                    )
                    self.assertLess(left.width(), legacy_width)
                    if consumer.secondary_plot is not None:
                        lower_left = consumer.secondary_plot.getAxis("left")
                        self.assertEqual(left.width(), lower_left.width())
                        width_before = left.width()
                        state = window._screen_view_state()
                        window._apply_view_state(replace(
                            state,
                            x=window._scaled_limits(state.x, 0.8),
                        ))
                        self.assertEqual(left.width(), width_before)
                        self.assertEqual(lower_left.width(), width_before)
                    for _view, gradient_axis, host in consumer.gradient_layers:
                        self.assertTrue(gradient_axis.isVisible())
                        self.assertLess(gradient_axis.width(), legacy_width)
                        # A right AxisItem draws its rotated title past its own
                        # edge, so without reserved spacing this is negative.
                        self.assertGreater(title_gap(host, gradient_axis), 0.0)
                        self.assertEqual(
                            host.layout.columnSpacing(2),
                            consumer.GRADIENT_AXIS_COLUMN_SPACING,
                        )
        finally:
            window.project.dirty = False
            window.close()
            # PySide2 defers destruction of the replaced single-view scene;
            # drain it before QApplication teardown so stale wrappers do not
            # make the 32-bit test process exit non-zero after a passed test.
            self.app.processEvents()

    def test_full_x_and_full_y_buttons_reset_only_requested_axes(self):
        window = self.make_window()
        window._plot(preserve_view=False)
        full_x = window.axes.get_xlim()

        def expected_visible_bounds(axis_id, x_range):
            visible_values = []
            for trace in window._screen_scene.traces:
                if trace.axis_id != axis_id:
                    continue
                mask = (
                    np.isfinite(trace.x_values)
                    & np.isfinite(trace.y_values)
                    & (trace.x_values >= x_range[0])
                    & (trace.x_values <= x_range[1])
                )
                visible_values.extend(trace.y_values[mask])
            minimum = float(np.min(visible_values))
            maximum = float(np.max(visible_values))
            span = maximum - minimum
            padding = (
                span * 0.05
                if span > 0.0
                else max(abs(minimum) * 0.05, 1.0)
            )
            return (minimum - padding, maximum + padding)

        window.axes.set_xlim(5.0, 12.0)
        window.axes.set_ylim(-10.0, 10.0)
        window.axes_right.set_ylim(-20.0, 20.0)
        window._reset_x_view()
        self.assertEqual(window.axes.get_xlim(), full_x)
        self.assertEqual(window.axes.get_ylim(), (-10.0, 10.0))
        self.assertEqual(window.axes_right.get_ylim(), (-20.0, 20.0))

        window.axes.set_xlim(7.0, 11.0)
        window.axes.set_ylim(-10.0, 10.0)
        window.axes_right.set_ylim(-20.0, 20.0)
        expected_y1 = expected_visible_bounds("y1", window.axes.get_xlim())
        expected_y2 = expected_visible_bounds("y2", window.axes.get_xlim())
        window._reset_y_view()
        self.assertEqual(window.axes.get_xlim(), (7.0, 11.0))
        self.assertTrue(np.allclose(window.axes.get_ylim(), expected_y1))
        self.assertTrue(np.allclose(window.axes_right.get_ylim(), expected_y2))

        window.axes.set_xlim(200.0, 210.0)
        window.axes.set_ylim(-3.0, 4.0)
        window.axes_right.set_ylim(-5.0, 6.0)
        window._reset_y_view()
        self.assertEqual(window.axes.get_xlim(), (200.0, 210.0))
        self.assertEqual(window.axes.get_ylim(), (-3.0, 4.0))
        self.assertEqual(window.axes_right.get_ylim(), (-5.0, 6.0))

        window.axes.set_xlim(5.0, 12.0)
        window.axes.set_ylim(-10.0, 10.0)
        window.axes_right.set_ylim(-20.0, 20.0)
        window.reset_view_button.click()
        button_reset = window._screen_view_state()
        window.axes.set_xlim(8.0, 10.0)
        window.axes.set_ylim(-1.0, 1.0)
        window.axes_right.set_ylim(-2.0, 2.0)
        window.toolbar.home()
        self.assertEqual(window._screen_view_state(), button_reset)
        self.assertEqual(window.reset_view_button.text(), "全体表示")
        self.assertEqual(window.reset_x_view_button.text(), "X軸全体")
        self.assertEqual(window.reset_y_view_button.text(), "Y軸全体")
        window.project.dirty = False
        window.close()

    def test_preview_full_y_uses_scene_bounds_and_preserves_x_and_raw_data(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.view_mode_combo.setCurrentIndex(
                window.view_mode_combo.findData("split_y_axes")
            )
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            digest = hashlib.sha256()
            for dataset in window.project.datasets:
                digest.update(np.ascontiguousarray(dataset.time_min).tobytes())
                digest.update(np.ascontiguousarray(dataset.intensity_uv).tobytes())
            before_digest = digest.hexdigest()
            original = window._screen_view_state()
            narrowed = ScreenViewState(
                x=(7.0, 11.0), y1=(-10.0, 10.0), y2=(-20.0, 20.0),
                gradient=original.gradient,
            )
            expected_y1 = window._scene_y_limits(
                window._screen_scene, "y1", narrowed.x
            )
            expected_y2 = window._scene_y_limits(
                window._screen_scene, "y2", narrowed.x
            )
            window._apply_view_state(narrowed)
            window._reset_y_view()
            reset = window._screen_view_state()
            self.assertEqual(reset.x, narrowed.x)
            np.testing.assert_allclose(reset.y1, expected_y1)
            np.testing.assert_allclose(reset.y2, expected_y2)
            self.assertEqual(reset.gradient, narrowed.gradient)
            self.assertEqual(preview.consumer.capture_view_state(), reset)
            digest = hashlib.sha256()
            for dataset in window.project.datasets:
                digest.update(np.ascontiguousarray(dataset.time_min).tobytes())
                digest.update(np.ascontiguousarray(dataset.intensity_uv).tobytes())
            self.assertEqual(digest.hexdigest(), before_digest)
        finally:
            window.project.dirty = False
            window.close()

    def test_move_mode_changes_only_selected_trace_and_disables_pan(self):
        window = self.make_window()
        selected = window.project.datasets[0]
        other = window.project.datasets[1]
        other_state = (other.x_shift_min, other.offset)
        other_x = window._dataset_lines[other.id].get_xdata().copy()
        other_y = window._dataset_lines[other.id].get_ydata().copy()
        window.toolbar.pan()
        window.move_trace_button.setChecked(True)
        self.assertFalse(str(window.toolbar.mode))
        window._move_drag = {
            "dataset": selected,
            "start_x": 0.0,
            "start_y": 0.0,
            "initial_x_shift": selected.x_shift_min,
            "initial_offset": selected.offset,
        }
        pixel_x, pixel_y = window.axes.transData.transform((0.5, 1000.0))
        window._on_canvas_motion(SimpleNamespace(x=pixel_x, y=pixel_y))
        self.assertAlmostEqual(selected.x_shift_min, 0.5, places=5)
        self.assertAlmostEqual(selected.offset, 1000.0, places=3)
        self.assertEqual((other.x_shift_min, other.offset), other_state)
        self.assertTrue((window._dataset_lines[other.id].get_xdata() == other_x).all())
        self.assertTrue((window._dataset_lines[other.id].get_ydata() == other_y).all())
        window._on_canvas_release(SimpleNamespace())
        window.project.dirty = False
        window.close()

    def test_reset_trace_position_restores_exact_display_and_is_one_undo_step(self):
        window = self.make_window()
        window.show()
        self.app.processEvents()
        # Issue #236/9.18: the move/reset row moved below the view-reset row.
        move_row = window.navigation_group.layout().itemAtPosition(5, 0).layout()
        self.assertGreaterEqual(move_row.indexOf(window.move_trace_button), 0)
        self.assertGreaterEqual(move_row.indexOf(window.reset_trace_position_button), 0)
        for button in (window.move_trace_button, window.reset_trace_position_button):
            self.assertGreaterEqual(
                button.contentsRect().width(),
                button.fontMetrics().horizontalAdvance(button.text()),
            )
        selected, other = window.project.datasets
        original_x = window._dataset_lines[selected.id].get_xdata().copy()
        original_y = window._dataset_lines[selected.id].get_ydata().copy()
        raw_time = selected.time_min.copy()
        raw_values = selected.intensity_uv.copy()
        other_state = (other.x_shift_min, other.offset)
        selected.x_shift_min = 0.75
        selected.offset = 1250.0
        recalculate_dataset_peaks(selected)
        window._plot()
        window._reset_undo_history()
        window.project.dirty = False

        window.reset_trace_position_button.click()
        self.assertEqual((selected.x_shift_min, selected.offset), (0.0, 0.0))
        self.assertEqual((other.x_shift_min, other.offset), other_state)
        np.testing.assert_array_equal(
            window._dataset_lines[selected.id].get_xdata(), original_x
        )
        np.testing.assert_array_equal(
            window._dataset_lines[selected.id].get_ydata(), original_y
        )
        np.testing.assert_array_equal(selected.time_min, raw_time)
        np.testing.assert_array_equal(selected.intensity_uv, raw_values)
        self.assertEqual(len(window._undo_stack), 1)

        window.undo()
        self.assertEqual((selected.x_shift_min, selected.offset), (0.75, 1250.0))
        window.redo()
        self.assertEqual((selected.x_shift_min, selected.offset), (0.0, 0.0))
        undo_count = len(window._undo_stack)
        window.reset_trace_position_button.click()
        self.assertEqual(len(window._undo_stack), undo_count)
        window.project.dirty = False
        window.close()

    def test_gradient_bcd_edit_recalculates_a(self):
        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        dialog = GradientDialog(dataset, "ja")
        dialog.table.item(0, 2).setText("25")
        dialog.table.item(0, 3).setText("5")
        dialog.table.item(0, 4).setText("10")
        self.app.processEvents()
        self.assertEqual(dialog.table.item(0, 1).text(), "60")
        dialog.reject()

    def test_preset_save_dialogs_prefill_loaded_names_and_ignore_labels(self):
        window = self.make_window()
        selected = window.project.datasets[0]
        selected.label = selected.short_label = "Keep this label"
        window.project.condition_presets = {
            "280 nm C4": {
                "label": "Do not apply",
                "short_label": "Do not apply",
                "wavelength_nm": 280.0,
                "column_name": "C4",
            }
        }
        condition_dialog = BatchMetadataDialog(
            window.project, selected.id, "ja"
        )
        self.assertNotIn("label", condition_dialog.presets["280 nm C4"])
        self.assertNotIn("short_label", condition_dialog.presets["280 nm C4"])
        condition_dialog.preset_combo.setCurrentText("280 nm C4")
        condition_dialog._apply_preset()
        condition_id = condition_dialog.preset_metadata["conditions"]["280 nm C4"]["id"]
        self.assertTrue(
            condition_dialog.preset_metadata["conditions"]["280 nm C4"][
                "last_used_at"
            ]
        )
        self.assertEqual(condition_dialog.table.item(0, 1).text(), "Keep this label")
        with patch.object(
            QtWidgets.QInputDialog,
            "getText",
            return_value=("280 nm C4 revised", True),
        ) as get_text:
            condition_dialog._save_preset()
        self.assertEqual(get_text.call_args.args[4], "280 nm C4")
        self.assertIn("280 nm C4 revised", condition_dialog.presets)
        self.assertNotIn("280 nm C4", condition_dialog.presets)
        self.assertEqual(
            condition_dialog.preset_metadata["conditions"]["280 nm C4 revised"][
                "id"
            ],
            condition_id,
        )
        self.assertNotIn("label", condition_dialog.presets["280 nm C4 revised"])
        condition_dialog.reject()

        gradient_name = "10-90 B"
        selected.gradient_preset_name = gradient_name
        gradient_presets = {
            gradient_name: {
                "gradient": [
                    {
                        "time_min": 0.0,
                        "a_pct": 90.0,
                        "b_pct": 10.0,
                        "c_pct": 0.0,
                        "d_pct": 0.0,
                        "flow_ml_min": 1.0,
                    }
                ],
                "solvents": {},
            }
        }
        gradient_dialog = GradientDialog(
            selected, "ja", presets=gradient_presets
        )
        gradient_dialog._apply_preset()
        gradient_id = gradient_dialog.preset_metadata["gradients"][gradient_name]["id"]
        gradient_dialog.table.item(0, 2).setText("20")
        self.assertEqual(gradient_dialog.applied_preset_name, "")
        self.assertEqual(gradient_dialog.last_loaded_preset_name, gradient_name)
        with patch.object(
            QtWidgets.QInputDialog,
            "getText",
            return_value=("10-90 B revised", True),
        ) as get_text:
            gradient_dialog._save_preset()
        self.assertEqual(get_text.call_args.args[4], gradient_name)
        self.assertIn("10-90 B revised", gradient_dialog.presets)
        self.assertNotIn(gradient_name, gradient_dialog.presets)
        self.assertEqual(
            gradient_dialog.preset_metadata["gradients"]["10-90 B revised"][
                "id"
            ],
            gradient_id,
        )
        gradient_dialog.reject()
        window.project.dirty = False
        window.close()

    def test_condition_preset_preview_compares_pending_values_without_mutation(self):
        window = self.make_window()
        selected = window.project.datasets[0]
        window.project.condition_presets = {
            "C4 280": {
                "wavelength_nm": 280.0,
                "flow_rate_ml_min": 0.8,
                "column_name": "",
            }
        }
        dialog = BatchMetadataDialog(window.project, selected.id, "ja")
        dialog.preset_combo.setCurrentText("C4 280")
        dialog.table.item(0, dialog.FIELD_COLUMNS["wavelength_nm"]).setText("214")
        metadata_before = deepcopy(dialog.preset_metadata)
        measurement_before = deepcopy(selected.measurement)

        def inspect_preview(preview):
            self.assertIsInstance(preview, PresetPreviewDialog)
            self.assertIn("C4 280", preview.windowTitle())
            values = {
                preview.table.item(row, 0).text(): tuple(
                    preview.table.item(row, column).text() for column in range(1, 4)
                )
                for row in range(preview.table.rowCount())
            }
            self.assertEqual(values["波長 (nm)"], ("214", "280", "280"))
            self.assertEqual(values["カラム"][1], "（維持）")
            self.assertEqual(values["カラム"][0], values["カラム"][2])
            return 0

        with patch("hplc_app.dialogs.dialog_exec", side_effect=inspect_preview):
            dialog._preview_condition_preset()
        self.assertEqual(dialog.preset_metadata, metadata_before)
        self.assertEqual(selected.measurement, measurement_before)
        dialog.reject()
        window.project.dirty = False
        window.close()

    def test_gradient_preset_preview_shows_program_and_solvents_without_use(self):
        window = self.make_window()
        selected = window.project.datasets[0]
        window.project.gradient_presets = {
            "ACN fast": {
                "gradient": [
                    {
                        "time_min": 0.0,
                        "a_pct": 80.0,
                        "b_pct": 20.0,
                        "c_pct": 0.0,
                        "d_pct": 0.0,
                        "flow_ml_min": 0.8,
                    }
                ],
                "solvents": {
                    "B": {"name": "ACN", "composition": "0.1% TFA"}
                },
            }
        }
        dialog = BatchMetadataDialog(window.project, selected.id, "en")
        dialog.gradient_preset_combo.setCurrentText("ACN fast")
        metadata_before = deepcopy(dialog.preset_metadata)
        gradient_before = deepcopy(selected.measurement.gradient)

        def inspect_preview(preview):
            self.assertIsInstance(preview, PresetPreviewDialog)
            self.assertEqual(preview.table.horizontalHeaderItem(3).text(), "Result")
            values = {
                preview.table.item(row, 0).text(): tuple(
                    preview.table.item(row, column).text() for column in range(1, 4)
                )
                for row in range(preview.table.rowCount())
            }
            self.assertEqual(values["Solvent B"][1:], ("ACN / 0.1% TFA", "ACN / 0.1% TFA"))
            self.assertIn("B=20", values["Time point 1"][1])
            self.assertIn("Flow=0.8", values["Time point 1"][2])
            return 0

        with patch("hplc_app.dialogs.dialog_exec", side_effect=inspect_preview):
            dialog._preview_gradient_preset()
        self.assertEqual(dialog.preset_metadata, metadata_before)
        self.assertEqual(selected.measurement.gradient, gradient_before)
        dialog.reject()
        window.project.dirty = False
        window.close()

    def test_gradient_editor_can_preview_invalid_current_program_without_loading(self):
        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        presets = {
            "Preview only": {
                "gradient": [
                    {
                        "time_min": 10.0,
                        "a_pct": 50.0,
                        "b_pct": 50.0,
                        "c_pct": 0.0,
                        "d_pct": 0.0,
                        "flow_ml_min": 1.0,
                    }
                ],
                "solvents": {},
            }
        }
        dialog = GradientDialog(dataset, "en", presets=presets)
        dialog.preset_combo.setCurrentText("Preview only")
        dialog.table.item(0, 0).setText("not-a-number")
        metadata_before = deepcopy(dialog.preset_metadata)
        dataset_before = deepcopy(dataset)

        def inspect_preview(preview):
            self.assertIsInstance(preview, PresetPreviewDialog)
            self.assertIn("not-a-number", preview.table.item(4, 1).text())
            self.assertIn("10 min", preview.table.item(4, 2).text())
            return 0

        with patch("hplc_app.dialogs.dialog_exec", side_effect=inspect_preview):
            dialog._preview_preset()
        self.assertEqual(dialog.preset_metadata, metadata_before)
        self.assertEqual(dataset.measurement, dataset_before.measurement)
        dialog.reject()

    def test_preset_lists_sort_filter_and_refresh_recent_use_without_mutation(self):
        window = self.make_window()
        selected = window.project.datasets[0]
        window.project.condition_presets = {
            "Legacy z": {"wavelength_nm": 220.0},
            "beta": {"wavelength_nm": 280.0},
            "Alpha": {"wavelength_nm": 214.0},
        }
        window.project.gradient_presets = {
            "Gradient z": {"gradient": [], "solvents": {}},
            "Gradient A": {"gradient": [], "solvents": {}},
        }
        metadata = {
            "conditions": {
                "Alpha": {
                    "created_at": "2026-08-25T09:00:00+09:00",
                    "updated_at": "2026-08-27T09:00:00+09:00",
                    "last_used_at": "2026-08-26T11:00:00+09:00",
                },
                "beta": {
                    "created_at": "2026-08-26T09:00:00+09:00",
                    "updated_at": "2026-08-26T09:00:00+09:00",
                    "last_used_at": "",
                },
                "Legacy z": {},
            },
            "gradients": {
                "Gradient A": {
                    "created_at": "2026-08-25T09:00:00+09:00",
                    "last_used_at": "",
                },
                "Gradient z": {
                    "created_at": "2026-08-26T09:00:00+09:00",
                    "last_used_at": "2026-08-27T09:00:00+09:00",
                },
            },
        }
        dialog = BatchMetadataDialog(
            window.project, selected.id, "en", preset_metadata=metadata
        )
        names = lambda combo: [
            combo.itemText(index) for index in range(combo.count())
        ]
        self.assertEqual(
            names(dialog.preset_combo), ["beta", "Alpha", "Legacy z"]
        )
        metadata_before = deepcopy(dialog.preset_metadata)
        name_index = dialog.condition_preset_sort.findData("name")
        dialog.condition_preset_sort.setCurrentIndex(name_index)
        self.assertEqual(
            names(dialog.preset_combo), ["Alpha", "beta", "Legacy z"]
        )
        dialog.condition_preset_filter.setText("BET")
        self.assertEqual(names(dialog.preset_combo), ["beta"])
        dialog.condition_preset_filter.clear()
        used_index = dialog.condition_preset_sort.findData("used")
        dialog.condition_preset_sort.setCurrentIndex(used_index)
        self.assertEqual(names(dialog.preset_combo)[0], "Alpha")
        dialog.preset_combo.setCurrentText("beta")
        dialog._apply_preset()
        self.assertEqual(names(dialog.preset_combo)[0], "beta")

        gradient_name_index = dialog.gradient_preset_sort.findData("name")
        dialog.gradient_preset_sort.setCurrentIndex(gradient_name_index)
        self.assertEqual(
            names(dialog.gradient_preset_combo), ["Gradient A", "Gradient z"]
        )
        dialog.gradient_preset_filter.setText(" Z")
        self.assertEqual(names(dialog.gradient_preset_combo), ["Gradient z"])
        self.assertEqual(
            dialog.preset_metadata["conditions"]["Alpha"],
            metadata_before["conditions"]["Alpha"],
        )
        dialog.reject()

        gradient_dialog = GradientDialog(
            selected,
            "ja",
            presets=window.project.gradient_presets,
            preset_metadata=metadata,
        )
        gradient_dialog.preset_sort.setCurrentIndex(
            gradient_dialog.preset_sort.findData("name")
        )
        self.assertEqual(
            names(gradient_dialog.preset_combo), ["Gradient A", "Gradient z"]
        )
        gradient_dialog.preset_filter.setText("gradient a")
        self.assertEqual(names(gradient_dialog.preset_combo), ["Gradient A"])
        gradient_dialog.reject()
        window.project.dirty = False
        window.close()

    def test_batch_table_applies_named_conditions_to_checked_rows(self):
        window = self.make_window()
        selected = window.project.datasets[0]
        dialog = BatchMetadataDialog(window.project, selected.id, "ja")
        dialog.presets["shared"] = {
            "wavelength_nm": 280.0,
            "aux_range_au_per_v": 2.0,
            "flow_rate_ml_min": 1.0,
            "cell_path_length_cm": 1.0,
            "column_name": "C4",
            "column_temperature_c": 25.0,
            "injection_volume_ul": 20.0,
            "analyte_name": "LL-37",
            "molar_absorptivity_214": 120000.0,
            "molar_absorptivity_280": 5500.0,
            "molecular_weight_g_mol": 4493.0,
        }
        dialog._refresh_presets("shared")
        dialog._apply_preset()
        self.assertEqual(dialog.table.item(0, 5).text(), "2")
        self.assertEqual(dialog.table.item(1, 5).text(), "")
        dialog._accept()
        self.assertEqual(selected.measurement.aux_range_au_per_v, 2.0)
        self.assertEqual(selected.measurement.column_name, "C4")
        self.assertIn("shared", window.project.condition_presets)
        window.project.dirty = False
        window.close()

    def test_condition_copy_defaults_to_auv_flow_and_column_only(self):
        options = [
            (field, ja, en, default_checked)
            for field, _column, ja, en, default_checked
            in BatchMetadataDialog.COPY_FIELDS
        ]
        dialog = ConditionCopyDialog(options, "en")
        self.assertEqual(
            set(dialog.selected_fields()),
            {"aux_range_au_per_v", "flow_rate_ml_min", "column_name"},
        )
        dialog.field_checks["label"].setChecked(True)
        dialog.field_checks["flow_rate_ml_min"].setChecked(False)
        self.assertEqual(
            set(dialog.selected_fields()),
            {"label", "aux_range_au_per_v", "column_name"},
        )
        dialog.close()

    def test_batch_dialog_source_path_elides_left_at_resized_widths(self):
        window = self.make_window()
        source_path = (
            r"G:\laboratory\projects\2026\September\run-001\sample-one.txt"
        )
        window.project.datasets[0].original_path = source_path
        dialog = BatchMetadataDialog(
            window.project, window.project.datasets[0].id, "en"
        )
        source_item = dialog.table.item(0, dialog.SOURCE_COLUMN)
        delegate = dialog.table.itemDelegateForColumn(dialog.SOURCE_COLUMN)
        home_delegate = window.dataset_table.itemDelegateForColumn(
            DATASET_SOURCE_COLUMN
        )
        self.assertEqual(type(delegate).__module__, "hplc_app.dialogs")
        self.assertIs(type(home_delegate), type(delegate))
        self.assertEqual(source_item.toolTip(), source_path)

        displayed = []
        for width in (90, 180):
            option = QtWidgets.QStyleOptionViewItem()
            option.rect = QtCore.QRect(0, 0, width, 24)
            option.widget = dialog.table
            displayed_option = delegate.display_option(
                option, dialog.table.model().index(0, dialog.SOURCE_COLUMN)
            )
            text = displayed_option.text
            displayed.append(text)
            self.assertNotEqual(text, source_path)
            self.assertTrue(source_path.endswith(text.lstrip(".…")))
            no_elide = (
                QtCore.Qt.TextElideMode.ElideNone
                if QT_API == 6
                else QtCore.Qt.ElideNone
            )
            self.assertEqual(displayed_option.textElideMode, no_elide)
        self.assertNotEqual(displayed[0], displayed[1])
        dialog.close()
        window.project.dirty = False
        window.close()

    def test_batch_dialog_run_id_edit_is_staged_shared_and_one_undo_step(self):
        window = self.make_window()
        first, second = window.project.datasets
        original_id = first.run_id
        shared_run = window.project.run_for(first)
        second.bind_run(shared_run)
        window.project.runs = [shared_run]
        window.project.rebuild_run_index(create_missing=False)

        def accept_rename(dialog):
            item = dialog.table.item(0, dialog.RUN_ID_COLUMN)
            self.assertTrue(bool(item.flags() & ITEM_IS_EDITABLE))
            with patch.object(dialog.table, "editItem") as edit_item, \
                    patch.object(dialog, "_edit_selected_details") as details:
                dialog._cell_double_clicked(0, dialog.RUN_ID_COLUMN)
            edit_item.assert_called_once_with(item)
            details.assert_not_called()

            item.setText("run-renamed")
            self.app.processEvents()
            self.assertEqual(
                [
                    dialog.table.item(row, dialog.RUN_ID_COLUMN).text()
                    for row in range(2)
                ],
                ["run-renamed", "run-renamed"],
            )
            self.assertEqual(
                [dataset.run_id for dataset in window.project.datasets],
                [original_id, original_id],
            )
            dialog._accept()
            return dialog.result()

        with patch("hplc_app.gui.dialog_exec", side_effect=accept_rename):
            window.edit_batch_metadata()

        self.assertEqual(
            [dataset.run_id for dataset in window.project.datasets],
            ["run-renamed", "run-renamed"],
        )
        self.assertEqual(len(window._undo_stack), 1)
        window.undo()
        self.assertEqual(
            [dataset.run_id for dataset in window.project.datasets],
            [original_id, original_id],
        )
        window.redo()
        self.assertEqual(
            [dataset.run_id for dataset in window.project.datasets],
            ["run-renamed", "run-renamed"],
        )
        window.project.dirty = False
        window.close()

    def test_batch_dialog_run_id_rejects_empty_and_duplicate_atomically(self):
        window = self.make_window()
        original_ids = [dataset.run_id for dataset in window.project.datasets]
        for candidate, key in (
            (original_ids[1], "run_id_duplicate"),
            ("  ", "run_id_empty"),
        ):
            with self.subTest(candidate=candidate):
                dialog = BatchMetadataDialog(
                    window.project, window.project.datasets[0].id, "en"
                )
                dialog.table.item(0, dialog.RUN_ID_COLUMN).setText(candidate)
                with patch.object(QtWidgets.QMessageBox, "warning") as warning:
                    dialog._accept()
                warning.assert_called_once()
                self.assertIn(
                    Translator("en")(key), warning.call_args.args[2]
                )
                self.assertNotEqual(
                    dialog.result(), QtWidgets.QDialog.Accepted
                )
                self.assertEqual(
                    [dataset.run_id for dataset in window.project.datasets],
                    original_ids,
                )
                dialog.reject()
        self.assertEqual(window._undo_stack, [])
        self.assertFalse(window.project.dirty)
        window.close()

    def test_batch_dialog_selection_sync_and_control_group_order(self):
        window = self.make_window()
        dialog = BatchMetadataDialog(
            window.project, window.project.datasets[0].id, "en"
        )
        selection = dialog.table.selectionModel()
        model = dialog.table.model()
        select = (
            QtCore.QItemSelectionModel.SelectionFlag.Select
            if QT_API == 6
            else QtCore.QItemSelectionModel.Select
        )
        selection.clearSelection()
        selection.select(model.index(0, 4), select)
        selection.select(model.index(1, 4), select)
        self.app.processEvents()
        self.assertEqual(
            [
                dialog.table.item(row, 0).checkState()
                for row in range(dialog.table.rowCount())
            ],
            [CHECKED, CHECKED],
        )

        selection.clearSelection()
        selection.select(model.index(1, 4), select)
        self.app.processEvents()
        self.assertEqual(
            [dialog.table.item(row, 0).checkState() for row in range(2)],
            [UNCHECKED, CHECKED],
        )
        dialog._set_all_checks(True)
        self.assertEqual(dialog._checked_rows(), [0, 1])
        dialog._check_same_group()
        self.assertEqual(dialog._checked_rows(), [1])
        dialog._set_all_checks(False)
        self.assertEqual(dialog._checked_rows(), [])

        root = dialog.layout()
        self.assertEqual(
            [root.itemAt(index).widget() for index in range(3)],
            [dialog.selection_group, dialog.operation_group, dialog.preset_group],
        )
        self.assertEqual(
            [
                dialog.selection_group.layout().itemAt(index).widget()
                for index in range(3)
            ],
            [
                dialog.check_all_button,
                dialog.check_group_button,
                dialog.clear_checks_button,
            ],
        )
        self.assertEqual(
            [
                dialog.operation_group.layout().itemAt(index).widget()
                for index in range(6)
            ],
            [
                dialog.group_runs_button,
                dialog.ungroup_runs_button,
                dialog.edit_details_button,
                dialog.edit_gradient_button,
                dialog.copy_conditions_button,
                dialog.paste_conditions_button,
            ],
        )
        preset_layout = dialog.preset_group.layout()
        self.assertGreaterEqual(
            preset_layout.itemAt(0).layout().indexOf(dialog.preset_combo), 0
        )
        self.assertGreaterEqual(
            preset_layout.itemAt(1).layout().indexOf(
                dialog.gradient_preset_combo
            ),
            0,
        )
        dialog.close()
        window.project.dirty = False
        window.close()

    def test_condition_copy_requires_one_source_and_handles_empty_session(self):
        window = self.make_window()
        dialog = BatchMetadataDialog(
            window.project, window.project.datasets[0].id, "en"
        )
        dialog.table.item(1, 0).setCheckState(CHECKED)
        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
            dialog._copy_conditions()
        warning.assert_called_once()
        self.assertIn("exactly one", warning.call_args.args[2])
        self.assertIsNone(dialog._condition_copy_payload)

        dialog._condition_copy_payload = None
        with patch.object(QtWidgets.QMessageBox, "information") as information:
            dialog._paste_conditions()
        information.assert_called_once()
        self.assertIn("before pasting", information.call_args.args[2])
        dialog.reject()
        window.project.dirty = False
        window.close()

    def test_condition_copy_pastes_only_chosen_values_as_one_undo_step(self):
        window = self.make_window()
        first, second = window.project.datasets
        original = deepcopy(window.project)
        second_before = deepcopy(second)
        second_aux = second.measurement.aux_range_au_per_v
        second_label = second.label

        def accept_copy(dialog):
            self.assertIsInstance(dialog, BatchMetadataDialog)
            dialog.table.item(0, 2).setText("Copied group")
            dialog.table.item(0, 4).setText("251")
            dialog.table.item(0, 5).setText("9")

            def choose_fields(copy_dialog):
                self.assertIsInstance(copy_dialog, ConditionCopyDialog)
                self.assertEqual(
                    set(copy_dialog.field_checks),
                    {
                        field
                        for field, _column, _ja, _en, _default
                        in dialog.COPY_FIELDS
                    },
                )
                for check in copy_dialog.field_checks.values():
                    check.setChecked(False)
                copy_dialog.field_checks["group"].setChecked(True)
                copy_dialog.field_checks["wavelength_nm"].setChecked(True)
                return True

            with patch(
                "hplc_app.dialogs.dialog_exec", side_effect=choose_fields
            ):
                dialog._copy_conditions()
            self.assertEqual(
                set(dialog._condition_copy_payload["columns"]),
                {"group", "wavelength_nm"},
            )
            self.assertNotIn("gradient", dialog._condition_copy_payload)

            # Keep the source selected and add a second destination: paste is
            # intentionally valid for multiple selected rows.
            dialog.table.item(1, 0).setCheckState(CHECKED)
            dialog._paste_conditions()
            self.assertEqual(dialog.table.item(1, 2).text(), "Copied group")
            self.assertEqual(dialog.table.item(1, 4).text(), "251")
            self.assertEqual(dialog.table.item(1, 1).text(), second_label)
            self.assertEqual(
                dialog.table.item(1, 5).text(),
                "" if second_aux is None else "%g" % second_aux,
            )
            dialog._accept()
            return dialog.result()

        with patch("hplc_app.gui.dialog_exec", side_effect=accept_copy):
            window.edit_batch_metadata()

        self.assertEqual(first.measurement.group, "Copied group")
        self.assertEqual(second.measurement.group, "Copied group")
        self.assertEqual(first.measurement.wavelength_nm, 251.0)
        self.assertEqual(second.measurement.wavelength_nm, 251.0)
        self.assertEqual(second.measurement.aux_range_au_per_v, second_aux)
        self.assertEqual(second.label, second_label)
        self.assertEqual(second.y_axis, second_before.y_axis)
        for field, _column, _ja, _en, _default in BatchMetadataDialog.COPY_FIELDS:
            if field in {
                "label", "group", "y_axis", "wavelength_nm", "gradient"
            }:
                continue
            self.assertEqual(
                getattr(second.measurement, field),
                getattr(second_before.measurement, field),
            )
        self.assertEqual(
            second.measurement.gradient, second_before.measurement.gradient
        )
        self.assertEqual(
            second.measurement.solvents, second_before.measurement.solvents
        )
        self.assertEqual(len(window._undo_stack), 1)
        window.undo()
        self.assertEqual(
            [dataset.measurement.group for dataset in window.project.datasets],
            [dataset.measurement.group for dataset in original.datasets],
        )
        self.assertEqual(
            [dataset.measurement.wavelength_nm for dataset in window.project.datasets],
            [dataset.measurement.wavelength_nm for dataset in original.datasets],
        )
        window.redo()
        self.assertEqual(
            [dataset.measurement.wavelength_nm for dataset in window.project.datasets],
            [251.0, 251.0],
        )
        window.project.dirty = False
        window.close()

    def test_condition_copy_can_stage_only_the_gradient_program(self):
        window = self.make_window()
        first, second = window.project.datasets
        first.gradient_preset_name = "Copied gradient"
        second.measurement.gradient = []
        second.gradient_preset_name = ""
        second_wavelength = second.measurement.wavelength_nm
        dialog = BatchMetadataDialog(window.project, first.id, "en")

        def choose_gradient(copy_dialog):
            for check in copy_dialog.field_checks.values():
                check.setChecked(False)
            copy_dialog.field_checks["gradient"].setChecked(True)
            return True

        with patch(
            "hplc_app.dialogs.dialog_exec", side_effect=choose_gradient
        ):
            dialog._copy_conditions()
        dialog._set_all_checks(False)
        dialog.table.item(1, 0).setCheckState(CHECKED)
        dialog._paste_conditions()
        self.assertEqual(
            dialog.table.item(1, dialog.GRADIENT_COLUMN).text(),
            "Copied gradient",
        )
        dialog._accept()
        self.assertEqual(second.measurement.gradient, first.measurement.gradient)
        self.assertEqual(second.measurement.solvents, first.measurement.solvents)
        self.assertEqual(second.gradient_preset_name, "Copied gradient")
        self.assertEqual(second.measurement.wavelength_nm, second_wavelength)
        window.project.dirty = False
        window.close()

    def test_metadata_sections_no_moving_average_or_dilution_and_help_equation(self):
        window = self.make_window()
        dialog = MetadataDialog(window.project.datasets[0], "ja")
        titles = [group.title() for group in dialog.findChildren(QtWidgets.QGroupBox)]
        self.assertIn("サンプル", titles)
        self.assertIn("測定条件", titles)
        self.assertIn("試料情報", titles)
        self.assertNotIn("dilution", dialog.fields)
        self.assertEqual(dialog.fields["cell"].text(), "1")
        self.assertFalse(hasattr(window, "smoothing_spin"))
        dialog.reject()

        help_dialog = QuantitationHelpDialog("ja")
        help_text = help_dialog.findChild(QtWidgets.QTextBrowser).toPlainText()
        self.assertIn("n (nmol)", help_text)
        self.assertIn("分子量", help_text)
        self.assertIn("mAU·sec", help_text)
        self.assertIn("60", help_text)
        self.assertIn("飽和区間だけ", help_text)
        self.assertIn("推定値", help_text)
        self.assertNotIn("mAU·min", help_text)
        help_dialog.reject()
        window.project.dirty = False
        window.close()

    def test_analyte_snapshot_fields_are_saved_from_metadata_dialog(self):
        window = self.make_window()
        dialog = MetadataDialog(window.project.datasets[0], "en")
        dialog.fields["analyte"].setText("LL-37")
        dialog.fields["analyte_id"].setText("analyte-ll37")
        dialog.fields["analyte_aliases"].setText("CAP18, hCAP-18")
        dialog.fields["analyte_source"].setText("UniProt P49913")
        original_unit = window.project.datasets[0].measurement.extinction_coefficient_unit
        self.assertNotIn("epsilon_unit", dialog.fields)
        dialog._accept()
        metadata = window.project.datasets[0].measurement
        self.assertEqual(metadata.analyte_id, "analyte-ll37")
        self.assertEqual(metadata.analyte_aliases, ["CAP18", "hCAP-18"])
        self.assertEqual(metadata.analyte_source, "UniProt P49913")
        self.assertEqual(metadata.extinction_coefficient_unit, "M^-1 cm^-1")
        self.assertEqual(metadata.extinction_coefficient_unit, original_unit)
        window.project.dirty = False
        window.close()

    def test_metadata_analyte_preset_selection_overwrites_all_three_values(self):
        window = self.make_window()
        dataset = window.project.datasets[0]
        dataset.measurement.molar_absorptivity_214 = 1.0
        dataset.measurement.molar_absorptivity_280 = 2.0
        dataset.measurement.molecular_weight_g_mol = 3.0
        dialog = MetadataDialog(
            dataset,
            "en",
            analyte_presets={
                "LL-37": {
                    "molar_absorptivity_214": 12500.0,
                    "molar_absorptivity_280": None,
                    "molecular_weight_g_mol": 4493.3,
                }
            },
        )
        index = dialog.analyte_combo.findData("LL-37")
        dialog._analyte_preset_activated(index)
        self.assertEqual(dialog.fields["eps214"].text(), "12500")
        self.assertEqual(dialog.fields["eps280"].text(), "")
        self.assertEqual(dialog.fields["mw"].text(), "4493.3")
        dialog._accept()
        self.assertEqual(dataset.measurement.analyte_name, "LL-37")
        self.assertEqual(dataset.measurement.molar_absorptivity_214, 12500.0)
        self.assertIsNone(dataset.measurement.molar_absorptivity_280)
        self.assertEqual(dataset.measurement.molecular_weight_g_mol, 4493.3)
        window.project.dirty = False
        window.close()

    def test_metadata_can_register_analyte_preset_for_application_store(self):
        window = self.make_window()
        dialog = MetadataDialog(window.project.datasets[0], "en")

        def register(registration):
            self.assertIsInstance(registration, AnalytePresetRegistrationDialog)
            registration.name_edit.setText("New analyte")
            registration.eps214_edit.setText("1234")
            registration.eps280_edit.setText("5678")
            registration.mw_edit.setText("901.2")
            registration._accept()
            return True

        with patch("hplc_app.dialogs.dialog_exec", side_effect=register):
            dialog._analyte_preset_activated(0)
        self.assertEqual(
            dialog.analyte_presets["New analyte"]["molecular_weight_g_mol"],
            901.2,
        )
        self.assertIn("New analyte", dialog.preset_metadata["analytes"])
        dialog.reject()
        window.project.dirty = False
        window.close()

    def test_editing_details_keeps_condition_and_gradient_preset_metadata(self):
        window = self.make_window()
        window._global_condition_presets = {"C18": {"column_name": "C18"}}
        window._global_gradient_presets = {"fast": {"gradient": []}}
        window._global_analyte_presets = {
            "LL-37": {
                "molar_absorptivity_214": 12500.0,
                "molar_absorptivity_280": None,
                "molecular_weight_g_mol": 4493.3,
            }
        }
        window._global_preset_metadata = normalize_preset_metadata(
            window._global_condition_presets,
            window._global_gradient_presets,
            None,
            window._global_analyte_presets,
        )
        before = deepcopy(window._global_preset_metadata)
        window.dataset_table.selectRow(0)

        with patch("hplc_app.gui.dialog_exec", return_value=True):
            window.edit_metadata()

        # The details dialog owns the analyte records only. Accepting it must not
        # erase the identity and history of the other application-wide kinds.
        for kind in ("conditions", "gradients"):
            with self.subTest(kind=kind):
                self.assertEqual(
                    window._global_preset_metadata[kind], before[kind]
                )
        stored = load_complete_preset_store()
        self.assertEqual(sorted(stored[3]["conditions"]), ["C18"])
        self.assertEqual(sorted(stored[3]["gradients"]), ["fast"])
        self.assertEqual(sorted(stored[2]), ["LL-37"])
        window.project.dirty = False
        window.close()

    def test_managing_only_analyte_presets_leaves_the_project_unchanged(self):
        window = self.make_window()
        window._global_condition_presets = {"C18": {"column_name": "C18"}}
        window._global_gradient_presets = {"fast": {"gradient": []}}
        window.project.condition_presets = deepcopy(window._global_condition_presets)
        window.project.gradient_presets = deepcopy(window._global_gradient_presets)
        window._global_analyte_presets = {}
        window._global_preset_metadata = normalize_preset_metadata(
            window._global_condition_presets,
            window._global_gradient_presets,
            None,
            {},
        )
        window.project.dirty = False
        analyte = {
            "LL-37": {
                "molar_absorptivity_214": 12500.0,
                "molar_absorptivity_280": None,
                "molecular_weight_g_mol": 4493.3,
            }
        }

        def manage_analytes(dialog):
            dialog.analytes = deepcopy(analyte)
            return True

        with patch("hplc_app.gui.dialog_exec", side_effect=manage_analytes):
            self.assertTrue(window.manage_presets())

        # Analyte presets are application-wide and never enter the Project, so
        # managing them alone must not report unsaved Project changes.
        self.assertEqual(window._global_analyte_presets, analyte)
        self.assertFalse(window.project.dirty)
        self.assertEqual(load_complete_preset_store()[2], analyte)

        def manage_conditions(dialog):
            dialog.conditions = {"C4": {"column_name": "C4"}}
            return True

        with patch("hplc_app.gui.dialog_exec", side_effect=manage_conditions):
            self.assertTrue(window.manage_presets())
        self.assertTrue(window.project.dirty)
        self.assertEqual(sorted(window.project.condition_presets), ["C4"])
        window.project.dirty = False
        window.close()

    def test_analyte_preset_with_same_constants_keeps_quantitation_result(self):
        window = self.make_window()
        dataset = window.project.datasets[0]
        metadata = dataset.measurement
        metadata.aux_range_au_per_v = 1.0
        metadata.flow_rate_ml_min = 1.0
        metadata.cell_path_length_cm = 1.0
        metadata.molar_absorptivity_280 = 5500.0
        metadata.molecular_weight_g_mol = 4493.3
        recalculate_dataset_peaks(dataset)
        before = dataset.peaks[0].amount_nmol
        self.assertIsNotNone(before)

        dialog = MetadataDialog(
            dataset,
            "en",
            analyte_presets={
                "LL-37": {
                    "molar_absorptivity_214": None,
                    "molar_absorptivity_280": 5500.0,
                    "molecular_weight_g_mol": 4493.3,
                }
            },
        )
        dialog._analyte_preset_activated(dialog.analyte_combo.findData("LL-37"))
        dialog._accept()
        recalculate_dataset_peaks(dataset)
        self.assertAlmostEqual(dataset.peaks[0].amount_nmol, before, places=12)
        window.project.dirty = False
        window.close()

    def test_batch_table_is_editable_and_empty_preset_values_do_not_erase(self):
        window = self.make_window()
        selected = window.project.datasets[0]
        selected.measurement.column_name = "C4"
        selected.measurement.molar_absorptivity_280 = 5500.0
        dialog = BatchMetadataDialog(window.project, selected.id, "ja")
        dialog.presets["partial"] = {"wavelength_nm": 280.0, "column_name": "", "molar_absorptivity_280": None}
        dialog._refresh_presets("partial")
        dialog._apply_preset()
        self.assertTrue(bool(dialog.table.item(0, 8).flags() & ITEM_IS_EDITABLE))
        self.assertFalse(bool(dialog.table.item(0, 0).flags() & ITEM_IS_EDITABLE))
        self.assertFalse(bool(dialog.table.item(0, 15).flags() & ITEM_IS_EDITABLE))
        dialog._accept()
        self.assertEqual(selected.measurement.column_name, "C4")
        self.assertEqual(selected.measurement.molar_absorptivity_280, 5500.0)
        window.project.dirty = False
        window.close()

    def test_batch_table_direct_edits_sync_run_fields_but_keep_channel_fields_independent(self):
        window = self.make_window()
        first, second = window.project.datasets
        shared_run = window.project.run_for(first)
        second.bind_run(shared_run)
        window.project.runs = [shared_run]
        window.project.rebuild_run_index(create_missing=False)
        original_first_wavelength = first.measurement.wavelength_nm

        dialog = BatchMetadataDialog(window.project, second.id, "en")
        dialog.table.item(1, 1).setText("Shared direct label")
        dialog.table.item(1, 2).setText("direct-group")
        dialog.table.item(1, 6).setText("1.25")
        dialog.table.item(1, 4).setText("230")
        self.app.processEvents()

        self.assertEqual(dialog.table.item(0, 1).text(), "Shared direct label")
        self.assertEqual(dialog.table.item(0, 2).text(), "direct-group")
        self.assertEqual(dialog.table.item(0, 6).text(), "1.25")
        self.assertEqual(
            dialog.table.item(0, 4).text(),
            "" if original_first_wavelength is None else "%g" % original_first_wavelength,
        )
        dialog._accept()

        self.assertEqual([dataset.label for dataset in (first, second)], ["Shared direct label"] * 2)
        self.assertEqual([dataset.measurement.group for dataset in (first, second)], ["direct-group"] * 2)
        self.assertEqual([dataset.measurement.flow_rate_ml_min for dataset in (first, second)], [1.25] * 2)
        self.assertEqual(first.measurement.wavelength_nm, original_first_wavelength)
        self.assertEqual(second.measurement.wavelength_nm, 230.0)
        from hplc_app.project_io import load_project, save_project

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "batch-direct-edit.hplcproj"
            save_project(str(path), window.project)
            reloaded = load_project(str(path))
        self.assertEqual(
            [dataset.measurement.flow_rate_ml_min for dataset in reloaded.datasets],
            [1.25] * 2,
        )
        self.assertEqual(
            [dataset.measurement.wavelength_nm for dataset in reloaded.datasets],
            [original_first_wavelength, 230.0],
        )
        window.project.dirty = False
        window.close()

    def test_batch_table_invalid_cell_prevents_every_project_change(self):
        window = self.make_window()
        first, second = window.project.datasets
        original_label = first.label
        original_flow = second.measurement.flow_rate_ml_min
        dialog = BatchMetadataDialog(window.project, first.id, "en")
        dialog.table.item(0, 1).setText("Must not be applied")
        dialog.table.item(1, 6).setText("not-a-number")

        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
            dialog._accept()

        self.assertEqual(first.label, original_label)
        self.assertEqual(second.measurement.flow_rate_ml_min, original_flow)
        self.assertNotEqual(dialog.result(), QtWidgets.QDialog.Accepted)
        self.assertEqual((dialog.table.currentRow(), dialog.table.currentColumn()), (1, 6))
        self.assertEqual(dialog.table.item(1, 6).background().color().name(), "#ffd9d9")
        warning.assert_called_once()
        self.assertIn("Row 2", warning.call_args.args[2])
        self.assertIn("Flow", warning.call_args.args[2])
        dialog.reject()
        window.project.dirty = False
        window.close()

    def test_batch_table_optional_numbers_allow_blank_but_reject_nonpositive_values(self):
        window = self.make_window()
        first = window.project.datasets[0]
        original_flow = first.measurement.flow_rate_ml_min
        dialog = BatchMetadataDialog(window.project, first.id, "en")
        dialog.table.item(0, 6).setText("")
        dialog.table.item(0, 7).setText("0")

        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
            dialog._accept()

        self.assertEqual(first.measurement.flow_rate_ml_min, original_flow)
        self.assertEqual((dialog.table.currentRow(), dialog.table.currentColumn()), (0, 7))
        self.assertIn("greater than zero", warning.call_args.args[2])

        dialog.table.item(0, 7).setText("")
        dialog._accept()
        self.assertEqual(first.measurement.flow_rate_ml_min, None)
        self.assertEqual(first.measurement.cell_path_length_cm, None)
        window.project.dirty = False
        window.close()

    def test_batch_table_extended_selection_copies_only_complete_rectangles_as_tsv(self):
        window = self.make_window()
        dialog = BatchMetadataDialog(window.project, window.project.datasets[0].id, "en")
        self.assertEqual(
            dialog.table.selectionMode(),
            QtWidgets.QAbstractItemView.ExtendedSelection,
        )
        self.assertEqual(
            dialog.table.selectionBehavior(),
            QtWidgets.QAbstractItemView.SelectItems,
        )
        dialog.table.clearSelection()
        dialog.table.setCurrentCell(0, 4)
        for row in (0, 1):
            for column in (4, 5):
                dialog.table.item(row, column).setSelected(True)

        copy_event = QtGui.QKeyEvent(
            QtCore.QEvent.KeyPress,
            QtCore.Qt.Key_C,
            QtCore.Qt.ControlModifier,
        )
        dialog.table.keyPressEvent(copy_event)
        self.assertEqual(
            QtWidgets.QApplication.clipboard().text(),
            "280\t\n214\t",
        )

        dialog.table.clearSelection()
        dialog.table.item(0, 4).setSelected(True)
        dialog.table.item(1, 5).setSelected(True)
        QtWidgets.QApplication.clipboard().setText("keep clipboard")
        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
            dialog._copy_selected_cells()
        warning.assert_called_once()
        self.assertIn("rectangular", warning.call_args.args[2])
        self.assertEqual(QtWidgets.QApplication.clipboard().text(), "keep clipboard")
        dialog.reject()
        window.project.dirty = False
        window.close()

    def test_batch_table_tsv_paste_is_atomic_and_keeps_dataset_fields_independent(self):
        window = self.make_window()
        first, second = window.project.datasets
        dialog = BatchMetadataDialog(window.project, first.id, "en")
        dialog.table.setCurrentCell(0, 4)
        original = [
            [dialog.table.item(row, column).text() for column in (4, 5)]
            for row in (0, 1)
        ]
        QtWidgets.QApplication.clipboard().setText("230\t2\nnot-a-number\t1")
        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
            dialog._paste_clipboard()
        warning.assert_called_once()
        self.assertEqual(
            [
                [dialog.table.item(row, column).text() for column in (4, 5)]
                for row in (0, 1)
            ],
            original,
        )

        dialog.table.setCurrentCell(0, 4)
        QtWidgets.QApplication.clipboard().setText("230\t2\n214\t1")
        paste_event = QtGui.QKeyEvent(
            QtCore.QEvent.KeyPress,
            QtCore.Qt.Key_V,
            QtCore.Qt.ControlModifier,
        )
        dialog.table.keyPressEvent(paste_event)
        self.assertEqual(
            [
                [dialog.table.item(row, column).text() for column in (4, 5)]
                for row in (0, 1)
            ],
            [["230", "2"], ["214", "1"]],
        )
        self.assertEqual(len(dialog.table.selectedIndexes()), 4)
        dialog._accept()
        self.assertEqual(first.measurement.wavelength_nm, 230.0)
        self.assertEqual(first.measurement.aux_range_au_per_v, 2.0)
        self.assertEqual(second.measurement.wavelength_nm, 214.0)
        self.assertEqual(second.measurement.aux_range_au_per_v, 1.0)
        window.project.dirty = False
        window.close()

    def test_batch_table_tsv_paste_rejects_noneditable_range_and_run_conflicts(self):
        window = self.make_window()
        first, second = window.project.datasets
        shared_run = window.project.run_for(first)
        second.bind_run(shared_run)
        window.project.runs = [shared_run]
        window.project.rebuild_run_index(create_missing=False)
        dialog = BatchMetadataDialog(window.project, first.id, "en")

        original_check = dialog.table.item(0, 0).checkState()
        dialog.table.setCurrentCell(0, 0)
        QtWidgets.QApplication.clipboard().setText("1")
        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
            dialog._paste_clipboard()
        warning.assert_called_once()
        self.assertEqual(dialog.table.item(0, 0).checkState(), original_check)

        dialog.table.setCurrentCell(1, dialog.table.columnCount() - 1)
        QtWidgets.QApplication.clipboard().setText("1\t2\t3")
        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
            dialog._paste_clipboard()
        warning.assert_called_once()
        self.assertIn("beyond", warning.call_args.args[2])

        original_flow = dialog.table.item(0, 6).text()
        dialog.table.setCurrentCell(0, 6)
        QtWidgets.QApplication.clipboard().setText("1\n2")
        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
            dialog._paste_clipboard()
        warning.assert_called_once()
        self.assertIn("Conflicting", warning.call_args.args[2])
        self.assertEqual(dialog.table.item(0, 6).text(), original_flow)
        self.assertEqual(dialog.table.item(1, 6).text(), original_flow)
        dialog.reject()
        window.project.dirty = False
        window.close()

    def test_batch_table_cancel_is_non_mutating_and_accept_is_one_undo_step(self):
        window = self.make_window()
        first = window.project.datasets[0]
        original_wavelength = first.measurement.wavelength_nm

        canceled = BatchMetadataDialog(window.project, first.id, "en")
        canceled.table.item(0, 4).setText("250")
        canceled.reject()
        self.assertEqual(first.measurement.wavelength_nm, original_wavelength)
        self.assertEqual(window._undo_stack, [])

        def accept_paste(dialog):
            dialog.table.setCurrentCell(0, 4)
            QtWidgets.QApplication.clipboard().setText("260")
            dialog._paste_clipboard()
            dialog._accept()
            return dialog.result()

        with patch("hplc_app.gui.dialog_exec", side_effect=accept_paste):
            window.edit_batch_metadata()

        self.assertEqual(first.measurement.wavelength_nm, 260.0)
        self.assertEqual(len(window._undo_stack), 1)
        self.assertIn("条件の一括編集", window.undo_action.text())
        window.undo()
        self.assertEqual(window.project.datasets[0].measurement.wavelength_nm, original_wavelength)
        window.redo()
        self.assertEqual(window.project.datasets[0].measurement.wavelength_nm, 260.0)
        window.project.dirty = False
        window.close()

    def test_batch_table_selects_whole_rows_and_opens_detail_editor(self):
        window = self.make_window()
        dialog = BatchMetadataDialog(
            window.project, window.project.datasets[0].id, "ja"
        )
        dialog.table.clearSelection()
        dialog.table.selectRow(1)
        self.assertEqual(len(dialog.table.selectionModel().selectedRows()), 1)
        self.assertEqual(
            len(dialog.table.selectedIndexes()), dialog.table.columnCount()
        )

        def edit_details(metadata_dialog):
            metadata_dialog.fields["sample_name"].setText("Edited from batch")
            metadata_dialog.fields["wavelength"].setText("230")
            metadata_dialog.fields["column"].setText("C18")
            metadata_dialog._accept()
            return 1

        with patch("hplc_app.dialogs.dialog_exec", side_effect=edit_details):
            dialog._edit_selected_details(1)
        self.assertEqual(dialog.table.item(1, 4).text(), "230")
        self.assertEqual(dialog.table.item(1, 8).text(), "C18")
        dialog._accept()
        edited = window.project.datasets[1]
        self.assertEqual(edited.measurement.sample_name, "Edited from batch")
        self.assertEqual(edited.measurement.wavelength_nm, 230.0)
        self.assertEqual(edited.measurement.column_name, "C18")
        window.project.dirty = False
        window.close()

    def test_batch_detail_editor_keeps_shared_run_labels_synchronized(self):
        window = self.make_window()
        shared_run = window.project.run_for(window.project.datasets[0])
        window.project.datasets[1].bind_run(shared_run)
        window.project.runs = [shared_run]
        window.project.rebuild_run_index(create_missing=False)
        dialog = BatchMetadataDialog(
            window.project, window.project.datasets[0].id, "ja"
        )

        def edit_details(metadata_dialog):
            metadata_dialog.fields["label"].setText("Shared batch label")
            metadata_dialog.fields["short_label"].setText("Shared")
            metadata_dialog._accept()
            return 1

        with patch("hplc_app.dialogs.dialog_exec", side_effect=edit_details):
            dialog._edit_selected_details(0)
        self.assertEqual(dialog.table.item(0, 1).text(), "Shared batch label")
        self.assertEqual(dialog.table.item(1, 1).text(), "Shared batch label")
        dialog._accept()
        self.assertEqual(
            [dataset.label for dataset in window.project.datasets],
            ["Shared batch label", "Shared batch label"],
        )
        self.assertEqual(
            [dataset.short_label for dataset in window.project.datasets],
            ["Shared", "Shared"],
        )

        window.project.dirty = False
        window.close()

    def test_batch_gradient_editor_stages_shared_run_until_parent_accept_and_round_trips(self):
        window = self.make_window()
        first, second = window.project.datasets
        shared_run = window.project.run_for(first)
        second.bind_run(shared_run)
        window.project.runs = [shared_run]
        window.project.rebuild_run_index(create_missing=False)
        original_b = first.measurement.gradient[0].b_pct

        def edit_gradient(gradient_dialog):
            gradient_dialog.table.item(0, 2).setText("25")
            gradient_dialog.solvent_fields["B"][0].setText("Acetonitrile")
            gradient_dialog.applied_preset_name = "Batch custom"
            gradient_dialog._accept()
            return gradient_dialog.result()

        canceled = BatchMetadataDialog(window.project, first.id, "en")
        with patch("hplc_app.dialogs.dialog_exec", side_effect=edit_gradient):
            canceled._edit_selected_gradient(1)
        self.assertEqual(canceled.table.item(0, 15).text(), "Batch custom")
        self.assertEqual(canceled.table.item(1, 15).text(), "Batch custom")
        self.assertEqual(first.measurement.gradient[0].b_pct, original_b)
        canceled.reject()
        self.assertEqual(first.measurement.gradient[0].b_pct, original_b)

        dialog = BatchMetadataDialog(window.project, first.id, "en")
        with patch("hplc_app.dialogs.dialog_exec", side_effect=edit_gradient):
            dialog._cell_double_clicked(0, dialog.GRADIENT_COLUMN)
        dialog._accept()
        self.assertEqual(
            [dataset.measurement.gradient[0].b_pct for dataset in (first, second)],
            [25.0, 25.0],
        )
        self.assertEqual(
            [dataset.measurement.solvents["B"].name for dataset in (first, second)],
            ["Acetonitrile", "Acetonitrile"],
        )
        self.assertEqual(
            [dataset.gradient_preset_name for dataset in (first, second)],
            ["Batch custom", "Batch custom"],
        )

        from hplc_app.project_io import load_project, save_project

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "batch-gradient.hplcproj"
            save_project(str(path), window.project)
            reloaded = load_project(str(path))
        self.assertEqual(
            [dataset.measurement.gradient[0].b_pct for dataset in reloaded.datasets],
            [25.0, 25.0],
        )
        self.assertEqual(
            reloaded.datasets[0].measurement.solvents["B"].name,
            "Acetonitrile",
        )
        window.project.dirty = False
        window.close()

    def test_batch_gradient_editor_accept_is_one_undo_step(self):
        window = self.make_window()
        original_b = window.project.datasets[0].measurement.gradient[0].b_pct

        def edit_gradient(gradient_dialog):
            gradient_dialog.table.item(0, 2).setText("30")
            gradient_dialog._accept()
            return gradient_dialog.result()

        def accept_batch(batch_dialog):
            with patch("hplc_app.dialogs.dialog_exec", side_effect=edit_gradient):
                batch_dialog._edit_selected_gradient(0)
            batch_dialog._accept()
            return batch_dialog.result()

        with patch("hplc_app.gui.dialog_exec", side_effect=accept_batch):
            window.edit_batch_metadata()
        self.assertEqual(window.project.datasets[0].measurement.gradient[0].b_pct, 30.0)
        self.assertEqual(len(window._undo_stack), 1)
        window.undo()
        self.assertEqual(
            window.project.datasets[0].measurement.gradient[0].b_pct,
            original_b,
        )
        window.redo()
        self.assertEqual(window.project.datasets[0].measurement.gradient[0].b_pct, 30.0)
        window.project.dirty = False
        window.close()

    def test_gradient_preset_can_be_applied_in_batch_and_axis_label_persists(self):
        window = self.make_window()
        window.project.gradient_presets["ACN method"] = {
            "gradient": [
                {"time_min": 0.0, "a_pct": 90.0, "b_pct": 10.0, "c_pct": 0.0, "d_pct": 0.0, "flow_ml_min": 1.0},
                {"time_min": 90.0, "a_pct": 10.0, "b_pct": 90.0, "c_pct": 0.0, "d_pct": 0.0, "flow_ml_min": 1.0},
            ],
            "solvents": {"A": {"name": "Water", "composition": "0.1% TFA"}, "B": {"name": "ACN", "composition": "0.1% TFA"}},
        }
        dialog = BatchMetadataDialog(window.project, window.project.datasets[0].id, "ja")
        dialog.gradient_preset_combo.setCurrentText("ACN method")
        dialog._apply_gradient_preset()
        dialog._accept()
        self.assertEqual(window.project.datasets[0].gradient_preset_name, "ACN method")
        self.assertEqual(window.project.datasets[0].measurement.gradient[-1].b_pct, 90.0)
        window.project.method.gradient_axis_label = "ACN (%)"
        window._plot()
        window.dataset_table.selectRow(1)
        self.app.processEvents()
        window.dataset_table.selectRow(0)
        self.app.processEvents()
        self.assertEqual(window.axes_gradient.get_ylabel(), "ACN (%)")
        window.project.dirty = False
        window.close()

    def test_controls_are_three_groups_without_obsolete_actions(self):
        window = self.make_window()
        self.assertEqual(window.project.method.axis_label_font_family, "Arial")
        self.assertEqual(window.project.method.tick_label_font_family, "Arial")
        self.assertEqual(window.project.method.legend_font_family, "Arial")
        self.assertEqual(window.project.method.retention_label_font_family, "Arial")
        self.assertEqual(window.annotation_action.text(), "テキストラベルを追加")
        self.assertEqual(
            [
                window.display_group.title(),
                window.navigation_group.title(),
                window.integration_group.title(),
            ],
            ["表示", "移動・ズーム", "積分"],
        )
        self.assertFalse(hasattr(window, "zoom_in_button"))
        self.assertFalse(hasattr(window, "zoom_out_button"))
        self.assertFalse(hasattr(window, "save_method_action"))
        self.assertFalse(hasattr(window, "load_method_action"))
        self.assertFalse(hasattr(window, "plot_english_action"))
        self.assertEqual(window.axes.get_xlabel(), "Retention time (min)")
        self.assertIn("Intensity", window.axes.get_ylabel())
        self.assertEqual(window.peak_table.horizontalHeaderItem(5).text(), "面積 (µV·sec)")
        self.assertEqual(window.peak_table.horizontalHeaderItem(7).text(), "面積 (mAU·sec)")
        window.project.dirty = False
        window.close()

    def test_retention_font_size_pointer_and_shifted_gradient(self):
        window = self.make_window()
        dataset = window.project.datasets[0]
        old_b = dataset.peaks[0].gradient_b_pct
        window.show_retention_checkbox.setChecked(True)
        window.project.method.retention_label_font_size = 12.5
        window.project.method.retention_label_color = "#000000"
        window._plot()
        self.app.processEvents()
        self.assertTrue(window.axes.texts)
        self.assertTrue(all(abs(text.get_fontsize() - 12.5) < 0.01 for text in window.axes.texts))
        self.assertTrue(all(text.get_color() == "#000000" for text in window.axes.texts))

        window.dataset_table.item(0, DATASET_X_SHIFT_COLUMN).setText("5")
        self.app.processEvents()
        self.assertNotEqual(dataset.peaks[0].gradient_b_pct, old_b)

        self.assertEqual(window.pointer_toolbar_button.text(), "縦線ポインター")
        self.assertEqual(window.pointer_control_button.text(), "縦線ポインター")
        self.assertFalse(window.pointer_toolbar_button.icon().isNull())
        self.assertFalse(window.pointer_control_button.icon().isNull())
        icon_only = (
            QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly
            if QT_API == 6
            else QtCore.Qt.ToolButtonIconOnly
        )
        self.assertEqual(window.pointer_toolbar_button.toolButtonStyle(), icon_only)
        self.assertLess(window.pointer_toolbar_button.minimumWidth(), 145)
        self.assertFalse(window.pointer_action.toolTip() == "")
        self.assertIn(window.pointer_toolbar_widget_action, window.toolbar.actions())
        window.pointer_action.setChecked(True)
        self.assertTrue(window.pointer_toolbar_button.isChecked())
        self.assertTrue(window.pointer_control_button.isChecked())
        window._on_canvas_motion(SimpleNamespace(xdata=7.0, inaxes=window.axes))
        self.assertTrue(window._interaction_cursor.get_visible())
        self.assertAlmostEqual(float(window._interaction_cursor.get_xdata()[0]), 7.0, places=6)
        window.pointer_action.setChecked(False)
        window.project.dirty = False
        window.close()

    def test_auto_detection_multiselect_delete_undo_and_redo(self):
        window = self.make_window()
        dataset = window.project.datasets[0]
        rng = np.random.default_rng(42)
        time = np.linspace(0.0, 10.0, 10001)
        dataset.time_min = time
        dataset.intensity_uv = (
            100.0
            + 900.0 * np.exp(-0.5 * ((time - 3.0) / 0.15) ** 2)
            + 700.0 * np.exp(-0.5 * ((time - 7.0) / 0.22) ** 2)
            + rng.normal(0.0, 2.0, time.size)
        )
        dataset.peaks = []
        method = window.project.method
        method.auto_peak_snr_threshold = 5.0
        method.auto_peak_min_prominence_uv = 20.0
        method.auto_peak_smoothing_min = 0.01
        method.auto_peak_min_width_min = 0.02
        method.auto_peak_max_width_min = 1.0
        method.auto_peak_min_distance_min = 0.2
        window._refresh_all(0)
        with patch("hplc_app.gui.dialog_exec", return_value=True):
            window.auto_detect_peaks()
        self.assertEqual(len(dataset.peaks), 2)
        self.assertEqual([peak.integration_source for peak in dataset.peaks], ["auto", "auto"])
        self.assertEqual(window._selected_peak_rows(), [0, 1])
        window.delete_peak()
        self.assertEqual(len(dataset.peaks), 0)
        window.undo()
        self.assertEqual(len(dataset.peaks), 2)
        window.redo()
        self.assertEqual(len(dataset.peaks), 0)
        self.assertEqual(window.undo_action.shortcut().toString(), "Ctrl+Z")
        self.assertEqual(window.redo_action.shortcut().toString(), "Ctrl+Y")
        window.project.dirty = False
        window.close()

    def test_auto_detection_dialog_disables_missing_range_and_returns_choices(self):
        unavailable = AutoPeakDetectionDialog(None, "en")
        self.assertFalse(unavailable.selected_range_radio.isEnabled())
        self.assertTrue(unavailable.full_range_radio.isChecked())
        unavailable.reject()

        dialog = AutoPeakDetectionDialog((8.0, 3.0), "en")
        self.assertTrue(dialog.selected_range_radio.isEnabled())
        dialog.selected_range_radio.setChecked(True)
        dialog.sensitivity_buttons["high"].setChecked(True)
        dialog._accept()
        self.assertEqual(dialog.time_range, (3.0, 8.0))
        self.assertEqual(dialog.sensitivity_id, "high")

    def test_auto_detection_cancel_does_not_detect_or_change_method(self):
        window = self.make_window()
        method_before = deepcopy(window.project.method)
        peaks_before = deepcopy(window.project.datasets[0].peaks)
        with patch("hplc_app.gui.dialog_exec", return_value=False), patch(
            "hplc_app.gui.detect_peaks"
        ) as detector:
            window.auto_detect_peaks()
        detector.assert_not_called()
        self.assertEqual(window.project.method, method_before)
        self.assertEqual(window.project.datasets[0].peaks, peaks_before)
        self.assertFalse(window._undo_stack)
        window.project.dirty = False
        window.close()

    def test_ranged_sensitivity_detection_is_one_undo_step_and_preserves_outside_peak(self):
        window = self.make_window()
        dataset = window.project.datasets[0]
        dataset.x_shift_min = 10.0
        window._selected_time_range = (12.0, 14.0)
        method_before = deepcopy(window.project.method)
        outside_before = deepcopy(dataset.peaks[0])
        candidate = PeakRegion(
            start_min=2.2,
            end_min=2.8,
            retention_time_min=2.5,
            integration_source="auto",
        )
        fake_dialog = SimpleNamespace(
            time_range=(12.0, 14.0),
            sensitivity_id="high",
        )
        with patch(
            "hplc_app.gui.AutoPeakDetectionDialog", return_value=fake_dialog
        ) as dialog_class, patch(
            "hplc_app.gui.dialog_exec", return_value=True
        ), patch(
            "hplc_app.gui.detect_peaks", return_value=[candidate]
        ) as detector:
            window.auto_detect_peaks()

        self.assertEqual(dialog_class.call_args.args[0], (12.0, 14.0))
        self.assertEqual(detector.call_args.kwargs["time_range"], (2.0, 4.0))
        applied_method = detector.call_args.args[1]
        high = default_auto_peak_sensitivity_presets()["high"]
        self.assertEqual(applied_method.auto_peak_snr_threshold, high["auto_peak_snr_threshold"])
        self.assertEqual(len(dataset.peaks), 2)
        outside_after = next(peak for peak in dataset.peaks if peak.id == outside_before.id)
        self.assertEqual(outside_after.start_min, outside_before.start_min)
        self.assertEqual(outside_after.end_min, outside_before.end_min)
        self.assertAlmostEqual(outside_after.retention_time_min, outside_before.retention_time_min)
        self.assertAlmostEqual(outside_after.raw_area_uv_sec, outside_before.raw_area_uv_sec)
        self.assertEqual(len(window._undo_stack), 1)

        window.undo()
        self.assertEqual(window.project.method, method_before)
        self.assertEqual(len(dataset.peaks), 1)
        window.redo()
        self.assertEqual(
            window.project.method.auto_peak_snr_threshold,
            high["auto_peak_snr_threshold"],
        )
        self.assertEqual(len(dataset.peaks), 2)
        window.project.dirty = False
        window.close()

    def test_delete_key_removes_selected_peak_rows_and_supports_undo(self):
        window = self.make_window()
        dataset = window.project.datasets[0]
        self.assertEqual(len(dataset.peaks), 1)
        window.peak_table.selectRow(0)
        key_press_type = (
            QtCore.QEvent.Type.KeyPress if QT_API == 6 else QtCore.QEvent.KeyPress
        )
        delete_key = (
            QtCore.Qt.Key.Key_Delete if QT_API == 6 else QtCore.Qt.Key_Delete
        )
        no_modifier = (
            QtCore.Qt.KeyboardModifier.NoModifier
            if QT_API == 6
            else QtCore.Qt.NoModifier
        )
        event = QtGui.QKeyEvent(key_press_type, delete_key, no_modifier)
        QtWidgets.QApplication.sendEvent(window.peak_table, event)
        self.assertTrue(event.isAccepted())
        self.assertEqual(len(dataset.peaks), 0)
        window.undo()
        self.assertEqual(len(dataset.peaks), 1)
        window.project.dirty = False
        window.close()

    def test_show_hide_all_and_shift_wheel_horizontal_scroll(self):
        window = self.make_window()
        window.set_all_datasets_visible(False)
        self.assertFalse(any(dataset.visible for dataset in window.project.datasets))
        window.undo()
        self.assertTrue(all(dataset.visible for dataset in window.project.datasets))
        window.redo()
        self.assertFalse(any(dataset.visible for dataset in window.project.datasets))

        bar = window.dataset_table.horizontalScrollBar()
        bar.setRange(0, 1000)
        bar.setValue(500)
        wheel_type = QtCore.QEvent.Type.Wheel if QT_API == 6 else QtCore.QEvent.Wheel
        shift_modifier = (
            QtCore.Qt.KeyboardModifier.ShiftModifier
            if QT_API == 6
            else QtCore.Qt.ShiftModifier
        )

        class WheelEvent:
            accepted = False

            def type(self):
                return wheel_type

            def modifiers(self):
                return shift_modifier

            def angleDelta(self):
                return QtCore.QPoint(0, 120)

            def accept(self):
                self.accepted = True

        event = WheelEvent()
        self.assertTrue(window.eventFilter(window.dataset_table.viewport(), event))
        self.assertTrue(event.accepted)
        self.assertLess(bar.value(), 500)
        window.project.dirty = False
        window.close()

    def test_solo_column_is_session_only_and_preserves_visibility(self):
        window = self.make_window()
        try:
            window.project.datasets[0].visible = False
            window._refresh_all(0)
            visibility = [
                dataset.visible for dataset in window.project.datasets
            ]
            dirty = window.project.dirty
            header = window.dataset_table.horizontalHeader()
            self.assertEqual(
                header.visualIndex(DATASET_SOLO_COLUMN),
                header.visualIndex(DATASET_VISIBLE_COLUMN) + 1,
            )

            first = window.dataset_table.item(0, DATASET_SOLO_COLUMN)
            first.setCheckState(CHECKED)
            self.assertEqual(window._solo_dataset_id, window.project.datasets[0].id)
            self.assertEqual(
                [trace.dataset_id for trace in window._screen_scene.traces],
                [window.project.datasets[0].id],
            )
            self.assertEqual(
                [dataset.visible for dataset in window.project.datasets], visibility
            )
            self.assertEqual(window.project.dirty, dirty)

            # Solo is display-only: the project stores the ordinary Show
            # choices and opening it starts with no temporary solo target.
            from hplc_app.project_io import save_project

            with tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / "solo-session-only.hplcproj")
                save_project(path, window.project)
                self.assertTrue(window._open_project_path(path))
            self.assertEqual(window._solo_dataset_id, "")
            self.assertEqual(
                [dataset.visible for dataset in window.project.datasets], visibility
            )

            second = window.dataset_table.item(1, DATASET_SOLO_COLUMN)
            second.setCheckState(CHECKED)
            self.assertEqual(window._solo_dataset_id, window.project.datasets[1].id)
            self.assertEqual(
                [trace.dataset_id for trace in window._screen_scene.traces],
                [window.project.datasets[1].id],
            )
            self.assertEqual(
                [dataset.visible for dataset in window.project.datasets], visibility
            )

            second = window.dataset_table.item(1, DATASET_SOLO_COLUMN)
            second.setCheckState(UNCHECKED)
            self.assertEqual(window._solo_dataset_id, "")
            self.assertEqual(
                [trace.dataset_id for trace in window._screen_scene.traces],
                [window.project.datasets[1].id],
            )

            window.dataset_table.item(0, DATASET_SOLO_COLUMN).setCheckState(CHECKED)
            window.set_all_datasets_visible(True)
            self.assertEqual(window._solo_dataset_id, "")
            self.assertTrue(all(
                dataset.visible for dataset in window.project.datasets
            ))
            window.dataset_table.item(1, DATASET_SOLO_COLUMN).setCheckState(CHECKED)
            window.set_all_datasets_visible(False)
            self.assertEqual(window._solo_dataset_id, "")
            self.assertFalse(any(
                dataset.visible for dataset in window.project.datasets
            ))
        finally:
            window.project.dirty = False
            window.close()

    def test_chromatogram_order_buttons_reorder_plot_and_support_undo(self):
        window = self.make_window()
        original_ids = [dataset.id for dataset in window.project.datasets]
        original_labels = [dataset.short_label for dataset in window.project.datasets]
        window.dataset_table.selectRow(1)
        self.assertTrue(window.move_dataset_up_button.isEnabled())
        window.move_selected_dataset(-1)
        self.assertEqual(
            [dataset.id for dataset in window.project.datasets],
            list(reversed(original_ids)),
        )
        self.assertEqual(window.dataset_table.currentRow(), 0)
        legend = window.axes.get_legend()
        self.assertIsNotNone(legend)
        labels = [text.get_text() for text in legend.get_texts()]
        expected = [dataset.legend_label() for dataset in window.project.datasets]
        self.assertEqual(labels[:2], expected)
        window.undo()
        self.assertEqual(
            [dataset.id for dataset in window.project.datasets], original_ids
        )
        self.assertEqual(
            [dataset.short_label for dataset in window.project.datasets],
            original_labels,
        )
        window.project.dirty = False
        window.close()

    def test_remove_dataset_confirmation_no_preserves_project_state(self):
        window = self.make_window()
        window.dataset_table.selectRow(0)
        dataset_ids = [dataset.id for dataset in window.project.datasets]
        window._undo_stack = ["keep undo"]
        window._redo_stack = ["keep redo"]

        with patch.object(
            QtWidgets.QMessageBox,
            "question",
            return_value=QtWidgets.QMessageBox.No,
        ) as question:
            window.remove_dataset()

        question.assert_called_once()
        self.assertIn("Ch1", question.call_args.args[2])
        self.assertIn("1件", question.call_args.args[2])
        self.assertEqual(
            [dataset.id for dataset in window.project.datasets], dataset_ids
        )
        self.assertFalse(window.project.dirty)
        self.assertEqual(window.dataset_table.currentRow(), 0)
        self.assertEqual(window._undo_stack, ["keep undo"])
        self.assertEqual(window._redo_stack, ["keep redo"])
        window.close()

    def test_remove_dataset_confirmation_yes_deletes_only_selected_dataset(self):
        window = self.make_window()
        window.dataset_table.selectRow(1)
        window._undo_stack = ["discard undo"]
        window._redo_stack = ["discard redo"]

        with patch.object(
            QtWidgets.QMessageBox,
            "question",
            return_value=QtWidgets.QMessageBox.Yes,
        ) as question:
            window.remove_dataset()

        question.assert_called_once()
        self.assertIn("Ch2", question.call_args.args[2])
        self.assertEqual(
            [dataset.label for dataset in window.project.datasets], ["Ch1"]
        )
        self.assertTrue(window.project.dirty)
        # Removal is now one undoable step appended to the existing history.
        self.assertEqual(window._undo_stack[0], "discard undo")
        self.assertEqual(len(window._undo_stack), 2)
        self.assertEqual(window._redo_stack, [])
        window.undo()
        self.assertEqual(
            [dataset.label for dataset in window.project.datasets], ["Ch1", "Ch2"]
        )
        window.project.dirty = False
        window.close()

    def test_remove_dataset_without_selection_does_nothing(self):
        window = self.make_window()
        window.dataset_table.clearSelection()
        window.dataset_table.setCurrentCell(-1, -1)
        dataset_ids = [dataset.id for dataset in window.project.datasets]

        with patch.object(QtWidgets.QMessageBox, "question") as question:
            window.remove_dataset()

        question.assert_not_called()
        self.assertEqual(
            [dataset.id for dataset in window.project.datasets], dataset_ids
        )
        self.assertFalse(window.project.dirty)
        window.close()

    def test_chromatogram_drag_reorders_project_plot_and_supports_undo_redo(self):
        window = self.make_window()
        window.show()
        self.app.processEvents()
        original_ids = [dataset.id for dataset in window.project.datasets]
        window.dataset_table.selectRow(0)
        target_rect = window.dataset_table.visualItemRect(
            window.dataset_table.item(1, 0)
        )
        drop_position = QtCore.QPoint(
            target_rect.center().x(), target_rect.bottom() - 1
        )
        event = RowDropEvent(drop_position)

        window.dataset_table.dropEvent(event)

        self.assertTrue(event.accepted)
        self.assertEqual(
            [dataset.id for dataset in window.project.datasets],
            list(reversed(original_ids)),
        )
        self.assertEqual(window.dataset_table.currentRow(), 1)
        labels = [text.get_text() for text in window.axes.get_legend().get_texts()]
        self.assertEqual(
            labels[:2],
            [dataset.legend_label() for dataset in window.project.datasets],
        )
        self.assertTrue(window.project.dirty)

        window.undo()
        self.assertEqual(
            [dataset.id for dataset in window.project.datasets], original_ids
        )
        window.redo()
        self.assertEqual(
            [dataset.id for dataset in window.project.datasets],
            list(reversed(original_ids)),
        )
        window.project.dirty = False
        window.close()

    def test_chromatogram_drag_same_position_is_no_op(self):
        window = self.make_window()
        window.show()
        self.app.processEvents()
        original_ids = [dataset.id for dataset in window.project.datasets]
        window.dataset_table.selectRow(0)
        source_rect = window.dataset_table.visualItemRect(
            window.dataset_table.item(0, 0)
        )
        event = RowDropEvent(source_rect.center())

        window.dataset_table.dropEvent(event)

        self.assertTrue(event.accepted)
        self.assertEqual(
            [dataset.id for dataset in window.project.datasets], original_ids
        )
        self.assertFalse(window.project.dirty)
        self.assertEqual(window._undo_stack, [])
        self.assertEqual(window._redo_stack, [])
        window.close()

    def test_chromatogram_drag_can_move_a_row_upward(self):
        window = self.make_window()
        window.show()
        self.app.processEvents()
        original_ids = [dataset.id for dataset in window.project.datasets]
        window.dataset_table.selectRow(1)
        target_rect = window.dataset_table.visualItemRect(
            window.dataset_table.item(0, 0)
        )
        drop_position = QtCore.QPoint(target_rect.center().x(), target_rect.top() + 1)

        window.dataset_table.dropEvent(RowDropEvent(drop_position))

        self.assertEqual(
            [dataset.id for dataset in window.project.datasets],
            list(reversed(original_ids)),
        )
        self.assertEqual(window.dataset_table.currentRow(), 0)
        window.project.dirty = False
        window.close()

    def test_remove_dataset_deletes_every_selected_row_in_one_undo_step(self):
        window = self.make_window()
        third = load_ascii_file(str(SAMPLES / "191720.TXT"))
        third.label = third.short_label = "Ch3"
        window.project.add_dataset(third)
        window._refresh_all(0)
        original = [dataset.id for dataset in window.project.datasets]
        original_labels = [dataset.label for dataset in window.project.datasets]
        # The selection checkbox is the other way of picking rows, so drive the
        # multiple selection through it rather than through Shift/Ctrl.
        window.dataset_table.item(0, DATASET_SELECTED_COLUMN).setCheckState(CHECKED)
        window.dataset_table.item(2, DATASET_SELECTED_COLUMN).setCheckState(CHECKED)
        self.assertEqual(window._selected_dataset_rows(), [0, 2])
        undo_depth = len(window._undo_stack)

        with patch.object(
            QtWidgets.QMessageBox,
            "question",
            return_value=QtWidgets.QMessageBox.No,
        ) as question:
            window.remove_dataset()

        question.assert_called_once()
        self.assertIn("2", question.call_args.args[2])
        self.assertEqual(
            [dataset.id for dataset in window.project.datasets], original
        )
        self.assertEqual(len(window._undo_stack), undo_depth)

        with patch.object(
            QtWidgets.QMessageBox,
            "question",
            return_value=QtWidgets.QMessageBox.Yes,
        ) as question:
            window.remove_dataset()

        self.assertIn("2", question.call_args.args[2])
        self.assertEqual(
            [dataset.label for dataset in window.project.datasets], ["Ch2"]
        )
        self.assertEqual(len(window._undo_stack), undo_depth + 1)
        self.assertEqual(window.dataset_table.rowCount(), 1)

        window.undo()
        self.assertEqual(
            [dataset.id for dataset in window.project.datasets], original
        )
        self.assertEqual(
            [dataset.label for dataset in window.project.datasets], original_labels
        )
        window.redo()
        self.assertEqual(
            [dataset.label for dataset in window.project.datasets], ["Ch2"]
        )
        window.project.dirty = False
        window.close()

    def test_chromatogram_drag_keeps_every_row_and_ignores_the_drop_action(self):
        window = self.make_window()
        third = load_ascii_file(str(SAMPLES / "191720.TXT"))
        third.label = third.short_label = "Ch3"
        window.project.add_dataset(third)
        window._refresh_all(0)
        window.show()
        self.app.processEvents()
        original = [dataset.id for dataset in window.project.datasets]
        window.dataset_table.selectRow(0)
        target_rect = window.dataset_table.visualItemRect(
            window.dataset_table.item(1, 0)
        )
        event = RowDropEvent(
            QtCore.QPoint(target_rect.center().x(), target_rect.bottom() - 1)
        )

        window.dataset_table.dropEvent(event)

        ignore_action = (
            QtCore.Qt.DropAction.IgnoreAction
            if QT_API == 6
            else QtCore.Qt.IgnoreAction
        )
        # Reporting the drop as ignored stops QAbstractItemView.startDrag from
        # deleting the source row out of the already rebuilt table.
        self.assertEqual(event.drop_action, ignore_action)
        self.assertTrue(event.accepted)
        self.assertEqual(
            [dataset.id for dataset in window.project.datasets],
            [original[1], original[0], original[2]],
        )
        self.assertEqual(window.dataset_table.rowCount(), 3)
        self.assertEqual(
            sorted(
                window.dataset_table.item(row, DATASET_SELECTED_COLUMN).data(
                    USER_ROLE
                )
                for row in range(window.dataset_table.rowCount())
            ),
            sorted(original),
        )
        window.project.dirty = False
        window.close()

    def test_chromatogram_drag_refuses_a_drop_onto_another_row(self):
        window = self.make_window()
        window.show()
        self.app.processEvents()
        table = window.dataset_table
        table.selectRow(0)
        model = table.model()
        target_rect = table.visualItemRect(table.item(1, 0))
        move_action = (
            QtCore.Qt.DropAction.MoveAction if QT_API == 6 else QtCore.Qt.MoveAction
        )
        left_button = (
            QtCore.Qt.MouseButton.LeftButton if QT_API == 6 else QtCore.Qt.LeftButton
        )
        no_modifier = (
            QtCore.Qt.KeyboardModifier.NoModifier
            if QT_API == 6
            else QtCore.Qt.NoModifier
        )
        positions = (
            QtWidgets.QAbstractItemView.DropIndicatorPosition
            if QT_API == 6
            else QtWidgets.QAbstractItemView
        )

        def drag_event(point):
            # PySide2 transfers ownership of QMimeData to QDragMoveEvent and may
            # destroy it after the event is handled.  Use a fresh instance for
            # every event so this test exercises the same behavior on Qt 5/6.
            mime = model.mimeData([model.index(0, 0)])
            return QtGui.QDragMoveEvent(
                point, move_action, mime, left_button, no_modifier
            )

        # Qt computes the indicator position from the live drag; drive that one
        # value directly so the decision this class owns is what gets tested.
        for indicator, rejected in (
            (positions.OnItem, True),
            (positions.AboveItem, False),
            (positions.BelowItem, False),
            (positions.OnViewport, False),
        ):
            with self.subTest(indicator=indicator):
                with patch.object(
                    type(table), "dropIndicatorPosition", return_value=indicator
                ):
                    event = drag_event(target_rect.center())
                    self.assertEqual(table._drops_onto_row(event), rejected)
                    table.dragMoveEvent(event)
                    if rejected:
                        self.assertFalse(event.isAccepted())

        # A file drop keeps its own path and is never judged by the indicator.
        urls = QtCore.QMimeData()
        urls.setUrls([QtCore.QUrl.fromLocalFile(str(SAMPLES / "210601.TXT"))])
        with patch.object(
            type(table), "dropIndicatorPosition", return_value=positions.OnItem
        ):
            self.assertFalse(
                table._drops_onto_row(
                    QtGui.QDragMoveEvent(
                        target_rect.center(),
                        move_action,
                        urls,
                        left_button,
                        no_modifier,
                    )
                )
            )
        self.assertEqual(len(window.project.datasets), 2)
        window.project.dirty = False
        window.close()

    def test_batch_dialog_shows_the_run_id_of_every_row(self):
        window = self.make_window()
        first, second = window.project.datasets
        dialog = BatchMetadataDialog(window.project, first.id, "en")

        header = dialog.table.horizontalHeader()
        self.assertEqual(
            dialog.table.horizontalHeaderItem(dialog.RUN_ID_COLUMN).text(), "Run ID"
        )
        self.assertEqual(
            dialog.table.horizontalHeaderItem(dialog.TIMESTAMP_COLUMN).text(),
            "Timestamp",
        )
        self.assertEqual(
            dialog.table.horizontalHeaderItem(0).text(), "Select"
        )
        self.assertEqual(dialog.table.item(0, 0).checkState(), CHECKED)
        self.assertEqual(dialog.table.item(1, 0).checkState(), UNCHECKED)
        # Appended logically, shown next to Select so one Run identity remains
        # readable without disturbing the existing editable column numbers.
        self.assertEqual(header.visualIndex(dialog.RUN_ID_COLUMN), 1)
        self.assertEqual(header.visualIndex(dialog.TIMESTAMP_COLUMN), 2)
        self.assertEqual(
            [
                dialog.table.item(row, dialog.RUN_ID_COLUMN).text()
                for row in range(dialog.table.rowCount())
            ],
            [first.run_id, second.run_id],
        )
        self.assertEqual(
            [
                dialog.table.item(row, dialog.TIMESTAMP_COLUMN).text()
                for row in range(dialog.table.rowCount())
            ],
            [
                first.measurement.acquisition_datetime,
                second.measurement.acquisition_datetime,
            ],
        )
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertTrue(
            bool(dialog.table.item(0, dialog.RUN_ID_COLUMN).flags() & ITEM_IS_EDITABLE)
        )
        self.assertNotIn(dialog.RUN_ID_COLUMN, dialog.EDITABLE_COLUMNS)
        self.assertNotIn(dialog.TIMESTAMP_COLUMN, dialog.EDITABLE_COLUMNS)
        dialog.close()

        shared_run = window.project.run_for(first)
        second.bind_run(shared_run)
        window.project.runs = [shared_run]
        window.project.rebuild_run_index(create_missing=False)
        grouped = BatchMetadataDialog(window.project, first.id, "en")
        self.assertEqual(
            [
                grouped.table.item(row, grouped.RUN_ID_COLUMN).text()
                for row in range(grouped.table.rowCount())
            ],
            [shared_run.id, shared_run.id],
        )
        grouped.close()
        window.project.dirty = False
        window.close()

    def test_import_leaves_the_label_blank_and_shows_the_acquisition_timestamp(self):
        window = self.make_window()
        while window.project.datasets:
            window.project.remove_dataset_at(0)
        window._refresh_all(0)

        imported = window._import_chromatogram_paths([str(SAMPLES / "210601.TXT")])

        self.assertEqual(imported, 1)
        dataset = window.project.datasets[0]
        run = window.project.run_for(dataset)
        self.assertEqual(dataset.label, "")
        self.assertEqual(dataset.short_label, "")
        self.assertEqual(run.label, "")
        self.assertEqual(
            window.dataset_table.item(0, DATASET_LABEL_COLUMN).text(), ""
        )
        timestamp = dataset.measurement.acquisition_datetime
        self.assertTrue(timestamp)
        self.assertEqual(
            window.dataset_table.item(0, DATASET_TIMESTAMP_COLUMN).text(), timestamp
        )
        # A blank label costs no identity: the Run ID and the legend keep their
        # own filename fallback.
        self.assertTrue(dataset.run_id)
        self.assertIn("210601", dataset.run_id)
        self.assertIn("210601", window.project.legend_label_for(dataset))

        # The label stays a user field: typing one keeps it, and reopening the
        # saved project restores both the label and the timestamp.
        window.dataset_table.item(0, DATASET_LABEL_COLUMN).setText("Fraction A")
        window.dataset_table.item(0, DATASET_TIMESTAMP_COLUMN).setText(
            "2026-08-31T10:11:12"
        )
        self.assertEqual(window.project.datasets[0].label, "Fraction A")
        from hplc_app.project_io import load_project, save_project

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "labelled.hplcproj")
            save_project(path, window.project)
            restored = load_project(path)
        self.assertEqual(restored.datasets[0].label, "Fraction A")
        self.assertEqual(
            restored.datasets[0].measurement.acquisition_datetime,
            "2026-08-31T10:11:12",
        )
        window.project.dirty = False
        window.close()

    def test_trace_colors_prefer_explicit_values_then_channel_defaults(self):
        window = self.make_window()
        first, second = window.project.datasets
        first.color = ""
        second.color = ""
        # Deliberately cross the old wavelength families: the channel now owns
        # the default colour decision.
        first.measurement.wavelength_nm = 214.0
        second.measurement.wavelength_nm = 280.0
        self.assertEqual(dataset_display_color(first, 0), "#1d4ed8")
        self.assertEqual(dataset_display_color(second, 0), "#a16207")
        first.color = "#123456"
        self.assertEqual(dataset_display_color(first, 0), "#123456")
        second.y_axis = 3
        self.assertEqual(dataset_display_color(second, 2), "#2ca02c")
        window.project.dirty = False
        window.close()

    def test_import_assigns_distinct_defaults_within_the_same_channel(self):
        window = self.make_window()
        while window.project.datasets:
            window.project.remove_dataset_at(0)
        window._refresh_all(0)
        first = load_ascii_file(str(SAMPLES / "210601.TXT"))
        second = load_ascii_file(str(SAMPLES / "225120.TXT"))
        first.y_axis = second.y_axis = 1
        first.measurement.wavelength_nm = 214.0
        second.measurement.wavelength_nm = 280.0
        first.color = second.color = ""

        with patch(
            "hplc_app.gui.load_chromatogram_file",
            side_effect=[first, second],
        ):
            imported = window._import_chromatogram_paths(["first.txt", "second.txt"])

        self.assertEqual(imported, 2)
        self.assertEqual(
            [dataset.color for dataset in window.project.datasets],
            ["#1d4ed8", "#2563eb"],
        )
        window.project.dirty = False
        window.close()

    def test_dataset_table_forwards_external_file_drops_to_main_window(self):
        window = self.make_window()
        mime_data = QtCore.QMimeData()
        mime_data.setUrls(
            [QtCore.QUrl.fromLocalFile(str(SAMPLES / "210601.TXT"))]
        )
        event = Mock()
        event.mimeData.return_value = mime_data

        with patch.object(window, "dropEvent") as main_drop:
            window.dataset_table.dropEvent(event)

        main_drop.assert_called_once_with(event)
        self.assertFalse(window.project.dirty)
        window.close()

    def test_dataset_table_supports_range_and_non_contiguous_row_selection(self):
        window = self.make_window()
        third = load_ascii_file(str(SAMPLES / "191720.TXT"))
        third.label = third.short_label = "Ch3"
        window.project.add_dataset(third)
        window._refresh_all(0)
        extended_selection = (
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
            if QT_API == 6
            else QtWidgets.QAbstractItemView.ExtendedSelection
        )
        self.assertEqual(window.dataset_table.selectionMode(), extended_selection)
        visibility = [dataset.visible for dataset in window.project.datasets]
        model = window.dataset_table.model()
        selection_model = window.dataset_table.selectionModel()
        select = (
            QtCore.QItemSelectionModel.SelectionFlag.Select
            if QT_API == 6
            else QtCore.QItemSelectionModel.Select
        )
        toggle = (
            QtCore.QItemSelectionModel.SelectionFlag.Toggle
            if QT_API == 6
            else QtCore.QItemSelectionModel.Toggle
        )
        rows = (
            QtCore.QItemSelectionModel.SelectionFlag.Rows
            if QT_API == 6
            else QtCore.QItemSelectionModel.Rows
        )

        selection_model.clearSelection()
        selection_model.select(
            QtCore.QItemSelection(
                model.index(0, 0), model.index(2, model.columnCount() - 1)
            ),
            select | rows,
        )
        self.assertEqual(window._selected_dataset_rows(), [0, 1, 2])

        selection_model.clearSelection()
        selection_model.select(model.index(0, 0), select | rows)
        selection_model.select(model.index(2, 0), select | rows)
        self.assertEqual(window._selected_dataset_rows(), [0, 2])
        selection_model.select(model.index(0, 0), toggle | rows)
        self.assertEqual(window._selected_dataset_rows(), [2])
        self.assertEqual(
            [dataset.visible for dataset in window.project.datasets], visibility
        )
        self.assertFalse(window.project.dirty)
        window.close()

    def test_dataset_table_default_columns_hide_run_metadata_and_keep_it_available(self):
        window = self.make_window()
        timestamp = "2026-08-30T09:10:11+09:00"
        window.project.run_for(window.project.datasets[0]).timestamp = timestamp
        window.project.method.legend_components = ["timestamp"]
        window._refresh_all(0)
        header = window.dataset_table.horizontalHeader()
        visible_order = [
            DATASET_COLUMN_IDS[header.logicalIndex(position)]
            for position in range(header.count())
            if not window.dataset_table.isColumnHidden(
                header.logicalIndex(position)
            )
        ]

        self.assertTrue(header.sectionsMovable())
        self.assertEqual(visible_order, list(DEFAULT_DATASET_COLUMN_ORDER))
        self.assertEqual(
            visible_order[:7],
            [
                "selected", "visible", "solo", "label", "color", "run_id",
                "timestamp",
            ],
        )
        self.assertFalse(window.dataset_table.isColumnHidden(DATASET_TIMESTAMP_COLUMN))
        self.assertTrue(window.dataset_table.isColumnHidden(DATASET_GROUP_COLUMN))
        self.assertTrue(window.dataset_table.isColumnHidden(DATASET_SOURCE_COLUMN))
        self.assertEqual(
            window.dataset_table.item(0, DATASET_TIMESTAMP_COLUMN).text(), timestamp
        )
        self.assertEqual(
            window.project.datasets[0].measurement.acquisition_datetime, timestamp
        )
        self.assertTrue(
            any(
                timestamp in text.get_text()
                for text in window.axes.get_legend().get_texts()
            )
        )
        window.project.dirty = False
        window.close()

    def test_hidden_source_path_is_available_in_conditions_input(self):
        window = self.make_window()
        dataset = window.project.datasets[0]
        expected = dataset.original_path or dataset.original_filename
        dialog = BatchMetadataDialog(window.project, dataset.id, "en")

        self.assertEqual(
            dialog.table.horizontalHeaderItem(dialog.SOURCE_COLUMN).text(),
            "Source file",
        )
        source = dialog.table.item(0, dialog.SOURCE_COLUMN)
        self.assertEqual(source.text(), expected)
        self.assertEqual(source.toolTip(), expected)
        self.assertFalse(source.flags() & ITEM_IS_EDITABLE)

        dialog.reject()
        window.project.dirty = False
        window.close()

    def test_dataset_column_drag_order_persists_as_application_setting(self):
        window = self.make_window()
        header = window.dataset_table.horizontalHeader()
        header.moveSection(
            header.visualIndex(DATASET_COLOR_COLUMN),
            header.visualIndex(DATASET_SELECTED_COLUMN),
        )
        self.app.processEvents()
        expected = ["color"] + [
            column_id
            for column_id in DEFAULT_DATASET_COLUMN_ORDER
            if column_id != "color"
        ]
        self.assertEqual(window._current_dataset_column_order(), expected)
        self.assertEqual(window._settings.get(DATASET_COLUMN_ORDER), expected)
        self.assertFalse(window.project.dirty)
        window.close()

        restored = self.make_window()
        self.assertEqual(restored._current_dataset_column_order(), expected)
        self.assertFalse(
            restored.dataset_table.isColumnHidden(DATASET_TIMESTAMP_COLUMN)
        )
        self.assertTrue(
            restored.dataset_table.isColumnHidden(DATASET_SOURCE_COLUMN)
        )
        restored.project.dirty = False
        restored.close()

    def test_selected_dataset_keeps_trace_color_cell_background(self):
        window = self.make_window()
        window.dataset_table.selectRow(0)
        self.app.processEvents()
        item = window.dataset_table.item(0, DATASET_COLOR_COLUMN)
        delegate = window.dataset_table.itemDelegateForColumn(DATASET_COLOR_COLUMN)
        option = QtWidgets.QStyleOptionViewItem()
        if QT_API == 6:
            selected = QtWidgets.QStyle.StateFlag.State_Selected
            focus = QtWidgets.QStyle.StateFlag.State_HasFocus
        else:
            selected = QtWidgets.QStyle.State_Selected
            focus = QtWidgets.QStyle.State_HasFocus
        option.state |= selected | focus
        index = window.dataset_table.model().index(0, DATASET_COLOR_COLUMN)
        styled = delegate.display_option(option, index)

        self.assertFalse(styled.state & selected)
        self.assertFalse(styled.state & focus)
        self.assertEqual(
            styled.backgroundBrush.color().name(), item.background().color().name()
        )

        window.project.dirty = False
        window.close()

    def test_dataset_selection_checkboxes_and_row_selection_stay_synchronized(self):
        window = self.make_window()
        third = load_ascii_file(str(SAMPLES / "191720.TXT"))
        window.project.add_dataset(third)
        window._refresh_all(0)
        window.show()
        self.app.processEvents()
        visibility = [dataset.visible for dataset in window.project.datasets]

        self.assertEqual(
            [
                window.dataset_table.item(row, DATASET_SELECTED_COLUMN).checkState()
                for row in range(3)
            ],
            [CHECKED, UNCHECKED, UNCHECKED],
        )
        left_button = (
            QtCore.Qt.MouseButton.LeftButton
            if QT_API == 6
            else QtCore.Qt.LeftButton
        )

        def click_selection_checkbox(row):
            item = window.dataset_table.item(row, DATASET_SELECTED_COLUMN)
            rect = window.dataset_table.visualItemRect(item)
            QtTest.QTest.mouseClick(
                window.dataset_table.viewport(),
                left_button,
                pos=QtCore.QPoint(rect.left() + 10, rect.center().y()),
            )
            self.app.processEvents()

        click_selection_checkbox(1)
        self.assertEqual(window._selected_dataset_rows(), [0, 1])
        click_selection_checkbox(0)
        self.assertEqual(window._selected_dataset_rows(), [])

        selection = window.dataset_table.selectionModel()
        model = window.dataset_table.model()
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
        selection.clearSelection()
        selection.select(
            QtCore.QItemSelection(
                model.index(0, DATASET_SELECTED_COLUMN),
                model.index(2, DATASET_SELECTED_COLUMN),
            ),
            select | rows,
        )
        self.assertEqual(window._selected_dataset_rows(), [0, 1, 2])
        self.assertEqual(
            [
                window.dataset_table.item(row, DATASET_SELECTED_COLUMN).checkState()
                for row in range(3)
            ],
            [CHECKED, CHECKED, CHECKED],
        )
        self.assertEqual(
            [dataset.visible for dataset in window.project.datasets], visibility
        )
        self.assertFalse(window.project.dirty)
        window.close()

    def test_selected_visibility_checkbox_updates_all_rows_as_one_undo_step(self):
        window = self.make_window()
        window.show()
        window.dataset_table.selectAll()
        self.app.processEvents()
        self.assertEqual(window._selected_dataset_rows(), [0, 1])
        self.assertTrue(all(dataset.visible for dataset in window.project.datasets))

        item = window.dataset_table.item(0, DATASET_VISIBLE_COLUMN)
        rect = window.dataset_table.visualItemRect(item)
        left_button = (
            QtCore.Qt.MouseButton.LeftButton
            if QT_API == 6
            else QtCore.Qt.LeftButton
        )
        QtTest.QTest.mouseClick(
            window.dataset_table.viewport(),
            left_button,
            pos=QtCore.QPoint(rect.left() + 10, rect.center().y()),
        )
        self.app.processEvents()

        self.assertFalse(any(dataset.visible for dataset in window.project.datasets))
        self.assertEqual(len(window._undo_stack), 1)
        window.undo()
        self.assertTrue(all(dataset.visible for dataset in window.project.datasets))
        window.redo()
        self.assertFalse(any(dataset.visible for dataset in window.project.datasets))
        window.project.dirty = False
        window.close()

    def test_selected_selection_checkbox_clears_all_rows_without_project_edit(self):
        window = self.make_window()
        window.show()
        window.dataset_table.selectAll()
        self.app.processEvents()
        visibility = [dataset.visible for dataset in window.project.datasets]

        item = window.dataset_table.item(0, DATASET_SELECTED_COLUMN)
        rect = window.dataset_table.visualItemRect(item)
        left_button = (
            QtCore.Qt.MouseButton.LeftButton
            if QT_API == 6
            else QtCore.Qt.LeftButton
        )
        QtTest.QTest.mouseClick(
            window.dataset_table.viewport(),
            left_button,
            pos=QtCore.QPoint(rect.left() + 10, rect.center().y()),
        )
        self.app.processEvents()

        self.assertEqual(window._selected_dataset_rows(), [])
        self.assertEqual(
            [
                window.dataset_table.item(
                    row, DATASET_SELECTED_COLUMN
                ).checkState()
                for row in range(window.dataset_table.rowCount())
            ],
            [UNCHECKED, UNCHECKED],
        )
        self.assertEqual(
            [dataset.visible for dataset in window.project.datasets], visibility
        )
        self.assertEqual(window._undo_stack, [])
        self.assertFalse(window.project.dirty)
        window.close()

    def test_dataset_buttons_are_grouped_in_the_requested_analysis_order(self):
        window = self.make_window()
        self.assertEqual(window.dataset_data_group.title(), "データ")
        self.assertEqual(window.dataset_analysis_group.title(), "解析")
        self.assertEqual(window.spectrum_display_group.title(), "クロマトグラム表示")
        data_order = (
            window.import_button,
            window.remove_button,
        )
        for index, button in enumerate(data_order):
            self.assertIs(
                window.dataset_data_layout.itemAtPosition(
                    index // 2, index % 2
                ).widget(),
                button,
            )
        analysis_order = (
            window.batch_metadata_button,
            window.color_button,
            window.group_run_button,
            window.ungroup_run_button,
        )
        for index, button in enumerate(analysis_order):
            self.assertIs(
                window.dataset_analysis_layout.itemAtPosition(
                    index // 2, index % 2
                ).widget(),
                button,
            )
        self.assertIs(
            window.spectrum_display_group.layout().itemAtPosition(0, 0).widget(),
            window.show_all_button,
        )
        self.assertIs(
            window.spectrum_display_group.layout().itemAtPosition(0, 1).widget(),
            window.hide_all_button,
        )
        self.assertEqual(
            [button.text() for button in analysis_order[:2]],
            [
                "測定・試料条件の入力",
                "表示設定",
            ],
        )
        self.assertFalse(hasattr(window, "metadata_button"))
        self.assertFalse(hasattr(window, "gradient_button"))
        conditions = BatchMetadataDialog(
            window.project, window.project.datasets[0].id, "ja"
        )
        self.assertGreaterEqual(
            conditions.operation_group.layout().indexOf(
                conditions.edit_details_button
            ),
            0,
        )
        self.assertGreaterEqual(
            conditions.operation_group.layout().indexOf(
                conditions.edit_gradient_button
            ),
            0,
        )
        conditions.close()
        window.set_language("en")
        self.assertEqual(window.dataset_data_group.title(), "Data")
        self.assertEqual(window.dataset_analysis_group.title(), "Analysis")
        self.assertEqual(window.spectrum_display_group.title(), "Chromatogram display")
        japanese = Translator("ja")
        english = Translator("en")
        self.assertEqual(japanese("change_color"), "トレース色")
        self.assertEqual(japanese("mouse_mode_move_trace"), "トレース移動")
        self.assertEqual(japanese("move_trace"), "トレース移動")
        self.assertEqual(english("spectrum_display_group"), "Chromatogram display")
        self.assertEqual(
            [button.text() for button in analysis_order[:2]],
            ["Measurement / sample conditions input", "Display settings"],
        )
        window.project.dirty = False
        window.close()

    def test_display_settings_single_color_and_all_line_styles_are_available(self):
        window = self.make_window()
        first, second = window.project.datasets
        resolved = {
            first.id: "#1d4ed8",
            second.id: "#a16207",
        }
        dialog = DisplaySettingsDialog(
            first, [first, second], resolved, "en"
        )
        self.assertEqual(
            [
                dialog.line_style_combo.itemData(index)
                for index in range(dialog.line_style_combo.count())
            ],
            ["solid", "dashed", "dotted", "dash_dot"],
        )
        with patch.object(
            QtWidgets.QColorDialog,
            "getColor",
            return_value=QtGui.QColor("#123456"),
        ):
            dialog._pick_single_color()
        changes = dialog.changes()
        self.assertEqual(changes[first.id]["color"], "#123456")
        self.assertNotIn(second.id, changes)
        dialog.reject()
        window.project.dirty = False
        window.close()

    def test_display_settings_target_lists_all_traces_and_applies_one_undo_step(self):
        window = self.make_window()
        first, second = window.project.datasets
        first.color = "#112233"
        first.line_style = "dashed"
        second.color = ""
        second.line_style = "dotted"
        window._refresh_all(0)

        def configure(dialog):
            self.assertEqual(
                [
                    dialog.target_combo.itemData(index)
                    for index in range(dialog.target_combo.count())
                ],
                [first.id, second.id],
            )
            dialog.single_color = "#abcdef"
            dialog.line_style_combo.setCurrentIndex(
                dialog.line_style_combo.findData("dash_dot")
            )
            dialog.target_combo.setCurrentIndex(1)
            self.assertIs(dialog.dataset, second)
            self.assertEqual(dialog.single_color, "")
            self.assertEqual(
                dialog.single_color_button.text(),
                dialog.resolved_colors[second.id],
            )
            self.assertEqual(
                dialog.line_style_combo.currentData(), "dotted"
            )
            dialog.single_color = "#123456"
            dialog.line_style_combo.setCurrentIndex(
                dialog.line_style_combo.findData("solid")
            )
            return True

        with patch("hplc_app.gui.dialog_exec", side_effect=configure):
            window.change_color()

        self.assertEqual(first.color, "#112233")
        self.assertEqual(first.line_style, "dashed")
        self.assertEqual(second.color, "#123456")
        self.assertEqual(second.line_style, "solid")
        self.assertEqual(len(window._undo_stack), 1)
        window.undo()
        self.assertEqual(second.color, "")
        self.assertEqual(second.line_style, "dotted")
        window.redo()
        self.assertEqual(second.color, "#123456")
        self.assertEqual(second.line_style, "solid")
        window.project.dirty = False
        window.close()

    def test_display_settings_gradient_uses_table_order_and_one_undo_step(self):
        window = self.make_window()
        first, second = window.project.datasets
        raw = [
            (dataset.time_min.copy(), dataset.intensity_uv.copy())
            for dataset in (first, second)
        ]
        model = window.dataset_table.model()
        selection = window.dataset_table.selectionModel()
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
        selection.clearSelection()
        window.dataset_table.setCurrentCell(0, DATASET_SELECTED_COLUMN)
        selection.select(
            QtCore.QItemSelection(
                model.index(0, DATASET_SELECTED_COLUMN),
                model.index(1, DATASET_SELECTED_COLUMN),
            ),
            select | rows,
        )
        expected_colors = []

        def configure(dialog):
            self.assertEqual(
                [item.id for item in dialog.selected_datasets],
                [first.id, second.id],
            )
            dialog.line_style_combo.setCurrentIndex(
                dialog.line_style_combo.findData("dash_dot")
            )
            dialog.color_mode_combo.setCurrentIndex(
                dialog.color_mode_combo.findData("gradient")
            )
            dialog.colormap_combo.setCurrentText("Blues")
            dialog.density_slider.setValue(40)
            expected_colors.extend(dialog._gradient_color_names())
            return True

        undo_count = len(window._undo_stack)
        with patch("hplc_app.gui.dialog_exec", side_effect=configure):
            window.change_color()

        self.assertEqual([first.color, second.color], expected_colors)
        self.assertEqual(first.line_style, "dash_dot")
        self.assertEqual(second.line_style, "solid")
        self.assertEqual(len(window._undo_stack), undo_count + 1)
        self.assertEqual(window._undo_stack[-1][0], "表示設定")
        self.assertEqual(window._dataset_lines[first.id].get_linestyle(), "-.")
        window.undo()
        self.assertEqual([first.color, second.color], ["", ""])
        self.assertEqual(first.line_style, "solid")
        window.redo()
        self.assertEqual([first.color, second.color], expected_colors)
        self.assertEqual(first.line_style, "dash_dot")
        expected_rgba = gradient_colors("Blues", 40, 2)
        self.assertEqual(
            expected_colors,
            [
                QtGui.QColor.fromRgbF(*color[:3]).name()
                for color in expected_rgba
            ],
        )
        for dataset, (time_values, intensity_values) in zip((first, second), raw):
            np.testing.assert_array_equal(dataset.time_min, time_values)
            np.testing.assert_array_equal(dataset.intensity_uv, intensity_values)
        window.project.dirty = False
        window.close()

    def test_run_group_and_ungroup_preserve_channel_values_and_are_undoable(self):
        window = self.make_window()
        first, second = window.project.datasets
        first.label = "Target label"
        first.measurement.column_name = "C4"
        second.measurement.column_name = "Other"
        first.measurement.acquisition_datetime = "2026-01-02 03:04:05"
        second.measurement.acquisition_datetime = "2026-02-03 04:05:06"
        target_timestamp = first.measurement.acquisition_datetime
        source_timestamp = second.measurement.acquisition_datetime
        first_wavelength = first.measurement.wavelength_nm
        second_wavelength = second.measurement.wavelength_nm
        original_run_ids = [first.run_id, second.run_id]
        window._refresh_dataset_table(0)
        model = window.dataset_table.model()
        selection = window.dataset_table.selectionModel()
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
        window.dataset_table.setCurrentCell(0, 0)
        selection.select(model.index(1, 0), select | rows)
        with patch.object(
            QtWidgets.QMessageBox,
            "question",
            return_value=QtWidgets.QMessageBox.Yes,
        ) as question:
            window.group_selected_runs()
        question_text = question.call_args.args[2]
        self.assertIn("Target label", question_text)
        self.assertIn("統合先 Run", question_text)
        self.assertNotIn("正本Run", question_text)
        for run_id in original_run_ids:
            self.assertIn(run_id, question_text)
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(second.label, "Target label")
        self.assertEqual(second.measurement.column_name, "C4")
        self.assertEqual(first.measurement.acquisition_datetime, target_timestamp)
        self.assertEqual(second.measurement.acquisition_datetime, target_timestamp)
        self.assertEqual(first.measurement.wavelength_nm, first_wavelength)
        self.assertEqual(second.measurement.wavelength_nm, second_wavelength)
        self.assertEqual(len(window.project.runs), 1)
        self.assertEqual(len(window._undo_stack), 1)
        self.assertEqual(window._undo_stack[-1][0], "統合先Runへ統合")
        self.assertNotIn("正本", window._undo_stack[-1][0])
        window.undo()
        self.assertEqual(
            [dataset.run_id for dataset in window.project.datasets],
            original_run_ids,
        )
        self.assertEqual(
            [
                dataset.measurement.acquisition_datetime
                for dataset in window.project.datasets
            ],
            [target_timestamp, source_timestamp],
        )
        window.redo()
        self.assertEqual(
            window.project.datasets[0].run_id,
            window.project.datasets[1].run_id,
        )

        window.dataset_table.clearSelection()
        window.dataset_table.setCurrentCell(1, 0)
        with patch.object(
            QtWidgets.QMessageBox,
            "question",
            return_value=QtWidgets.QMessageBox.Yes,
        ):
            window.ungroup_selected_runs()
        self.assertNotEqual(
            window.project.datasets[0].run_id,
            window.project.datasets[1].run_id,
        )
        self.assertEqual(window.project.datasets[1].label, "Target label")
        self.assertEqual(window.project.datasets[1].measurement.column_name, "C4")
        split_id = window.project.datasets[1].run_id
        next_number = window.project.next_run_number
        window.undo()
        self.assertEqual(
            window.project.datasets[0].run_id,
            window.project.datasets[1].run_id,
        )
        self.assertEqual(window.project.next_run_number, next_number)
        window.redo()
        self.assertEqual(window.project.datasets[1].run_id, split_id)
        self.assertEqual(window.project.next_run_number, next_number)
        window.project.dirty = False
        window.close()

    def test_conditions_dialog_routes_run_group_and_ungroup_to_mainwindow(self):
        window = self.make_window()
        first, second = window.project.datasets
        first.measurement.acquisition_datetime = "2026-03-04 05:06:07"
        second.measurement.acquisition_datetime = "2026-04-05 06:07:08"
        target_timestamp = first.measurement.acquisition_datetime
        original_ids = [first.run_id, second.run_id]

        def group_from_dialog(dialog):
            self.assertTrue(dialog.group_runs_button.isEnabled())
            self.assertTrue(dialog.ungroup_runs_button.isEnabled())
            dialog.table.setCurrentCell(0, 1)
            dialog.table.item(1, 0).setCheckState(CHECKED)
            dialog._group_checked_runs()
            self.assertEqual(
                [
                    dialog.table.item(row, dialog.RUN_ID_COLUMN).text()
                    for row in range(2)
                ],
                [original_ids[0], original_ids[0]],
            )
            self.assertEqual(
                [
                    dialog.table.item(row, dialog.TIMESTAMP_COLUMN).text()
                    for row in range(2)
                ],
                [target_timestamp, target_timestamp],
            )
            return False

        with patch.object(
            QtWidgets.QMessageBox,
            "question",
            return_value=QtWidgets.QMessageBox.Yes,
        ), patch("hplc_app.gui.dialog_exec", side_effect=group_from_dialog):
            window.edit_batch_metadata()

        self.assertEqual(
            window.project.datasets[0].run_id,
            window.project.datasets[1].run_id,
        )
        self.assertEqual(
            window.project.datasets[0].measurement.acquisition_datetime,
            target_timestamp,
        )
        self.assertEqual(
            window.project.datasets[1].measurement.acquisition_datetime,
            target_timestamp,
        )
        self.assertEqual(len(window._undo_stack), 1)

        def ungroup_from_dialog(dialog):
            dialog._set_all_checks(False)
            dialog.table.item(1, 0).setCheckState(CHECKED)
            dialog.table.setCurrentCell(1, 1)
            dialog._ungroup_checked_runs()
            self.assertNotEqual(
                dialog.table.item(0, dialog.RUN_ID_COLUMN).text(),
                dialog.table.item(1, dialog.RUN_ID_COLUMN).text(),
            )
            return False

        with patch.object(
            QtWidgets.QMessageBox,
            "question",
            return_value=QtWidgets.QMessageBox.Yes,
        ), patch("hplc_app.gui.dialog_exec", side_effect=ungroup_from_dialog):
            window.edit_batch_metadata()
        self.assertNotEqual(
            window.project.datasets[0].run_id,
            window.project.datasets[1].run_id,
        )
        self.assertEqual(len(window._undo_stack), 2)
        window.project.dirty = False
        window.close()

    def test_multiple_selected_dataset_rows_cannot_be_drag_reordered(self):
        window = self.make_window()
        window.show()
        self.app.processEvents()
        original_ids = [dataset.id for dataset in window.project.datasets]
        window.dataset_table.selectRow(0)
        selection_model = window.dataset_table.selectionModel()
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
        selection_model.select(
            window.dataset_table.model().index(1, 0), select | rows
        )
        self.assertEqual(window._selected_dataset_rows(), [0, 1])
        target_rect = window.dataset_table.visualItemRect(
            window.dataset_table.item(1, 0)
        )
        event = RowDropEvent(target_rect.bottomRight() - QtCore.QPoint(1, 1))

        window.dataset_table.dropEvent(event)

        self.assertTrue(event.ignored)
        self.assertFalse(event.accepted)
        self.assertEqual(
            [dataset.id for dataset in window.project.datasets], original_ids
        )
        self.assertFalse(window.project.dirty)
        self.assertEqual(window._undo_stack, [])
        window.close()

    def test_chromatogram_drag_without_source_row_is_ignored(self):
        window = self.make_window()
        window.show()
        self.app.processEvents()
        original_ids = [dataset.id for dataset in window.project.datasets]
        window.dataset_table.clearSelection()
        window.dataset_table.setCurrentCell(-1, -1)
        event = RowDropEvent(QtCore.QPoint(1, 1))

        window.dataset_table.dropEvent(event)

        self.assertTrue(event.ignored)
        self.assertFalse(event.accepted)
        self.assertEqual(
            [dataset.id for dataset in window.project.datasets], original_ids
        )
        self.assertFalse(window.project.dirty)
        window.close()

    def test_plot_and_analysis_sections_are_separated_by_resizable_splitter(self):
        window = self.make_window()
        window.show()
        self.app.processEvents()
        self.assertEqual(window.right_splitter.count(), 2)
        self.assertFalse(window.right_splitter.childrenCollapsible())
        before = window.right_splitter.sizes()
        window.right_splitter.setSizes((700, 180))
        self.app.processEvents()
        after = window.right_splitter.sizes()
        self.assertNotEqual(before, after)
        self.assertGreater(after[0], after[1])
        window.project.dirty = False
        window.close()

    def test_mouse_range_edit_preserves_view_and_selects_edited_peak(self):
        window = self.make_window()
        dataset = window.project.datasets[0]
        peak_id = dataset.peaks[0].id
        window.axes.set_xlim(4.0, 12.0)
        window.peak_table.selectRow(0)
        window.edit_peak_button.setChecked(True)
        self.assertEqual(window._span_selector_mode, "edit")
        self.assertEqual(window._edit_range_peak_id, peak_id)
        window._on_edit_span_selected(6.0, 9.0)
        edited = next(peak for peak in dataset.peaks if peak.id == peak_id)
        self.assertAlmostEqual(edited.start_min, 6.0, places=5)
        self.assertAlmostEqual(edited.end_min, 9.0, places=5)
        self.assertFalse(window.edit_peak_button.isChecked())
        self.assertEqual(window._selected_peak_ids(), [peak_id])
        self.assertAlmostEqual(window.axes.get_xlim()[0], 4.0, places=6)
        self.assertAlmostEqual(window.axes.get_xlim()[1], 12.0, places=6)
        window.project.dirty = False
        window.close()

    def test_axis_label_dialog_applies_text_tick_and_retention_styles(self):
        window = self.make_window()
        dialog = AxisLabelsDialog(window.project.method, "ja")
        self.assertEqual(dialog.windowTitle(), "軸・ラベル・書式設定")
        self.assertEqual(window.axis_labels_button.text(), "軸・ラベル・書式設定…")
        self.assertEqual(
            window.toolbar._actions["configure_subplots"].toolTip(),
            "軸・ラベル・書式設定…",
        )
        self.assertAlmostEqual(dialog.x_major_tick.minimum(), 0.1)
        self.assertAlmostEqual(dialog.x_minor_tick.minimum(), 0.1)
        self.assertEqual(dialog.x_major_tick.decimals(), 1)
        self.assertEqual(dialog.x_minor_tick.decimals(), 1)
        dialog.x_label.setText("Time after injection (min)")
        dialog.tick_mode_combo.setCurrentIndex(
            dialog.tick_mode_combo.findData("manual")
        )
        dialog.x_major_tick.setValue(2.0)
        dialog.x_minor_tick.setValue(0.5)
        dialog.axis_font_size.setValue(13.0)
        dialog.tick_font_size.setValue(8.0)
        dialog.retention_font_size.setValue(11.0)
        dialog.line_width.setValue(2.4)
        dialog.axis_color_button.color_name = "#123456"
        dialog.tick_color_button.color_name = "#654321"
        dialog.retention_color_button.color_name = "#000000"
        dialog.apply_to_method(window.project.method)
        window.project.method.show_retention_labels = True
        window._plot()
        self.assertEqual(window.axes.get_xlabel(), "Time after injection (min)")
        self.assertAlmostEqual(window.axes.xaxis.label.get_fontsize(), 13.0, places=6)
        self.assertEqual(window.axes.xaxis.label.get_color(), "#123456")
        self.assertTrue(window.axes.texts)
        self.assertTrue(all(text.get_color() == "#000000" for text in window.axes.texts))
        self.assertAlmostEqual(window.project.method.line_width, 2.4)
        self.assertTrue(
            any(abs(line.get_linewidth() - 2.4) < 1.0e-9 for line in window.axes.lines)
        )
        major = window.axes.xaxis.get_major_locator().tick_values(0.0, 10.0)
        minor = window.axes.xaxis.get_minor_locator().tick_values(0.0, 10.0)
        self.assertAlmostEqual(major[1] - major[0], 2.0, places=8)
        self.assertAlmostEqual(minor[1] - minor[0], 0.5, places=8)
        dialog.reject()
        window.project.dirty = False
        window.close()

    def test_axis_format_settings_warn_before_excessive_tick_count(self):
        window = self.make_window()
        dialog = AxisLabelsDialog(
            window.project.method,
            "ja",
            x_span_min=1000.0,
        )
        dialog.tick_mode_combo.setCurrentIndex(
            dialog.tick_mode_combo.findData("manual")
        )
        dialog.x_major_tick.setValue(0.2)
        dialog.x_minor_tick.setValue(0.1)
        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
            dialog._accept()
        warning.assert_called_once()
        self.assertIn("5,000", warning.call_args.args[2])
        self.assertIn("15,004", warning.call_args.args[2])
        self.assertEqual(dialog.result(), 0)
        dialog.reject()
        english = AxisLabelsDialog(window.project.method, "en")
        self.assertEqual(english.windowTitle(), "Axes, labels and formatting")
        english.reject()
        window.set_language("en")
        self.assertEqual(
            window.axis_labels_button.text(), "Axes, labels and formatting…"
        )
        self.assertEqual(
            window.toolbar._actions["configure_subplots"].toolTip(),
            "Axes, labels and formatting…",
        )
        window.project.dirty = False
        window.close()

    def test_unsafe_persisted_tick_spacing_falls_back_without_mutating_method(self):
        window = self.make_window()
        method = window.project.method
        window.axes.set_xlim(0.0, 1000.0)
        method.x_tick_mode = "manual"
        method.x_major_tick_min = 0.05
        method.x_minor_tick_min = 0.01
        with patch("hplc_app.gui.MultipleLocator", wraps=MultipleLocator) as locator:
            window._set_dynamic_x_ticks()
        spacings = [call.args[0] for call in locator.call_args_list]
        self.assertNotIn(0.05, spacings)
        self.assertNotIn(0.01, spacings)
        self.assertEqual(method.x_major_tick_min, 0.05)
        self.assertEqual(method.x_minor_tick_min, 0.01)

        method.x_major_tick_min = 0.2
        method.x_minor_tick_min = 0.1
        with patch("hplc_app.gui.MultipleLocator", wraps=MultipleLocator) as locator:
            window._set_dynamic_x_ticks()
        widened_spacings = [call.args[0] for call in locator.call_args_list]
        self.assertNotIn(0.2, widened_spacings)
        self.assertNotIn(0.1, widened_spacings)
        self.assertEqual(method.x_major_tick_min, 0.2)
        self.assertEqual(method.x_minor_tick_min, 0.1)

        if pyqtgraph_scene_available():
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            bottom = preview.consumer.primary.getAxis("bottom")
            with patch.object(bottom, "setTickSpacing") as set_spacing:
                preview.refresh()
            set_spacing.assert_called_once_with()
        window.project.dirty = False
        window.close()

    def test_axis_format_line_width_is_one_undo_step_with_renamed_history(self):
        window = self.make_window()
        original = window.project.method.line_width

        class AcceptedDialog:
            def apply_to_method(self, method):
                method.line_width = 2.7

        with patch("hplc_app.gui.AxisLabelsDialog", return_value=AcceptedDialog()), patch(
            "hplc_app.gui.dialog_exec", return_value=True
        ):
            window.edit_axis_labels()
        self.assertAlmostEqual(window.project.method.line_width, 2.7)
        self.assertEqual(window._undo_stack[-1][0], "軸・ラベル・書式設定")
        self.assertTrue(
            all(
                abs(trace.line_width - 2.7) < 1.0e-9
                for trace in window._screen_scene.traces
            )
        )
        window.undo()
        self.assertAlmostEqual(window.project.method.line_width, original)
        window.redo()
        self.assertAlmostEqual(window.project.method.line_width, 2.7)
        window.project.dirty = False
        window.close()

    def test_preview_toolbar_configuration_reuses_persisted_axis_dialog(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            original_label = window.project.method.x_axis_label
            undo_count = len(window._undo_stack)

            class AcceptedDialog:
                def apply_to_method(self, method):
                    method.x_axis_label = "Native configured X"

            with patch("hplc_app.gui.AxisLabelsDialog", return_value=AcceptedDialog()), \
                    patch("hplc_app.gui.dialog_exec", return_value=True):
                window.toolbar.edit_parameters()
            self.assertIs(window._screen_preview, preview)
            self.assertEqual(window.project.method.x_axis_label, "Native configured X")
            self.assertEqual(len(window._undo_stack), undo_count + 1)
            self.assertTrue(window.project.dirty)
            self.assertEqual(
                window._screen_preview.consumer.primary.getAxis("bottom").labelText,
                "Native configured X",
            )
            window.undo()
            self.assertEqual(window.project.method.x_axis_label, original_label)
            self.assertIsNotNone(window._screen_preview)

            class SubplotDialog:
                def apply_to_method(self, method):
                    method.x_axis_label = "Native subplot entry"

            with patch("hplc_app.gui.AxisLabelsDialog", return_value=SubplotDialog()), \
                    patch("hplc_app.gui.dialog_exec", return_value=True):
                window.toolbar.configure_subplots()
            self.assertEqual(window.project.method.x_axis_label, "Native subplot entry")
            self.assertIsNotNone(window._screen_preview)

            before = deepcopy(window.project.method)
            before_undo = len(window._undo_stack)
            window.project.dirty = False
            with patch("hplc_app.gui.AxisLabelsDialog", return_value=SubplotDialog()), \
                    patch("hplc_app.gui.dialog_exec", return_value=False):
                window.toolbar.edit_parameters()
            self.assertEqual(window.project.method, before)
            self.assertEqual(len(window._undo_stack), before_undo)
            self.assertFalse(window.project.dirty)
            self.assertIsNotNone(window._screen_preview)

            # Issue #236/9.16: Subplots and the retained-but-hidden Customize
            # action always open the application's own axis dialog now, on
            # both renderers -- not only while the PyQtGraph preview is on.
            window.screen_preview_checkbox.setChecked(False)
            self.assertIsNone(window._screen_preview)

            class MatplotlibEditDialog:
                def apply_to_method(self, method):
                    method.x_axis_label = "Matplotlib edit entry"

            with patch(
                "hplc_app.gui.AxisLabelsDialog", return_value=MatplotlibEditDialog()
            ), patch("hplc_app.gui.dialog_exec", return_value=True), patch(
                "hplc_app.gui.NavigationToolbar.edit_parameters"
            ) as legacy_edit:
                window.toolbar.edit_parameters()
            self.assertEqual(
                window.project.method.x_axis_label, "Matplotlib edit entry"
            )
            legacy_edit.assert_not_called()

            class MatplotlibSubplotDialog:
                def apply_to_method(self, method):
                    method.x_axis_label = "Matplotlib subplot entry"

            with patch(
                "hplc_app.gui.AxisLabelsDialog", return_value=MatplotlibSubplotDialog()
            ), patch("hplc_app.gui.dialog_exec", return_value=True), patch(
                "hplc_app.gui.NavigationToolbar.configure_subplots"
            ) as legacy_layout:
                window.toolbar.configure_subplots()
            self.assertEqual(
                window.project.method.x_axis_label, "Matplotlib subplot entry"
            )
            legacy_layout.assert_not_called()
        finally:
            window.project.dirty = False
            window.close()

    def test_preview_matches_persisted_legend_axis_and_tick_styles(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            method = window.project.method
            method.x_axis_label = "Configured X"
            method.gradient_axis_label = "Configured B (%)"
            method.axis_label_font_family = "Arial"
            method.axis_label_font_size = 13.0
            method.axis_label_color = "#123456"
            method.tick_label_font_family = "Arial"
            method.tick_label_font_size = 8.0
            method.tick_label_color = "#654321"
            method.legend_font_family = "Arial"
            method.legend_font_size = 11.0
            method.legend_font_color = "#abcdef"
            method.x_tick_mode = "manual"
            method.x_major_tick_min = 2.0
            method.x_minor_tick_min = 0.5
            method.legend_location = "outside right"
            raw = [dataset.intensity_uv.copy() for dataset in window.project.datasets]
            window._plot()
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            consumer = preview.consumer

            bottom = consumer.primary.getAxis("bottom")
            self.assertEqual(bottom.labelText, "Configured X")
            self.assertEqual(bottom.labelStyle["font-family"], "Arial")
            self.assertEqual(bottom.labelStyle["font-size"], "13pt")
            self.assertEqual(bottom.labelStyle["color"], "#123456")
            self.assertEqual(bottom._tickSpacing, [(2.0, 0), (0.5, 0)])
            self.assertEqual(bottom.style["tickFont"].family(), "Arial")
            self.assertAlmostEqual(bottom.style["tickFont"].pointSizeF(), 8.0)
            self.assertEqual(bottom.textPen().color().name(), "#654321")
            gradient_axis = consumer.gradient_layers[0][1]
            self.assertEqual(gradient_axis.labelText, "Configured B (%)")
            self.assertEqual(gradient_axis.labelStyle["font-family"], "Arial")
            self.assertTrue(preview._legend_outside)
            self.assertIn(preview._legend, consumer.widget.ci.items)
            self.assertFalse(window._current_view_pixmap().isNull())
            for _sample, label in preview._legend.items:
                self.assertEqual(label.opts["family"], "Arial")
                self.assertEqual(label.opts["size"], "11pt")
                self.assertEqual(label.opts["color"], "#abcdef")

            anchors = {
                "best": ((1, 0), (1, 0), (-10, 10)),
                "upper right": ((1, 0), (1, 0), (-10, 10)),
                "upper left": ((0, 0), (0, 0), (10, 10)),
                "lower right": ((1, 1), (1, 1), (-10, -10)),
                "lower left": ((0, 1), (0, 1), (10, -10)),
            }
            for location, expected in anchors.items():
                with self.subTest(location=location):
                    method.legend_location = location
                    window._plot()
                    legend = window._screen_preview._legend
                    self.assertFalse(window._screen_preview._legend_outside)
                    self.assertIs(legend.parentItem(), window._screen_preview.consumer.primary.vb)
                    self.assertEqual(
                        legend._GraphicsWidgetAnchor__itemAnchor, expected[0]
                    )
                    self.assertEqual(
                        legend._GraphicsWidgetAnchor__parentAnchor, expected[1]
                    )
                    self.assertEqual(
                        legend._GraphicsWidgetAnchor__offset, expected[2]
                    )

            method.legend_location = "outside right"
            window._plot()
            self.assertTrue(window._screen_preview._legend_outside)
            self.assertIn(
                window._screen_preview._legend,
                window._screen_preview.consumer.widget.ci.items,
            )
            method.x_tick_mode = "auto"
            window._plot()
            self.assertIsNone(
                window._screen_preview.consumer.primary.getAxis("bottom")._tickSpacing
            )
            window.view_mode_combo.setCurrentIndex(
                window.view_mode_combo.findData("split_y_axes")
            )
            split = window._screen_preview.consumer
            self.assertTrue(window._screen_preview._legend_outside)
            self.assertIn(window._screen_preview._legend, split.widget.ci.items)
            self.assertEqual(split.primary.getAxis("bottom").labelText, "")
            self.assertEqual(split.secondary_plot.getAxis("bottom").labelText,
                             "Configured X")
            self.assertEqual(split.secondary_plot.getAxis("bottom").labelStyle["font-family"],
                             "Arial")
            self.assertIsNone(split.secondary_plot.getAxis("bottom")._tickSpacing)
            for dataset, values in zip(window.project.datasets, raw):
                np.testing.assert_array_equal(dataset.intensity_uv, values)
        finally:
            window.project.dirty = False
            window.close()

    def test_retention_labels_are_drawn_for_every_selected_chromatogram(self):
        window = self.make_window()
        first, second = window.project.datasets
        second.peaks = [PeakRegion(start_min=5.0, end_min=10.0)]
        recalculate_dataset_peaks(second)
        window.dataset_table.clearSelection()
        window.dataset_table.setCurrentCell(0, 0)
        for row in (0, 1):
            selection = QtWidgets.QTableWidgetSelectionRange(
                row, 0, row, window.dataset_table.columnCount() - 1
            )
            window.dataset_table.setRangeSelected(selection, True)
        window.project.method.show_retention_labels = True
        window._plot()

        first_label = "%.2f" % first.peaks[0].retention_time_min
        second_label = "%.2f" % second.peaks[0].retention_time_min
        self.assertIn(first_label, [text.get_text() for text in window.axes.texts])
        self.assertIn(
            second_label,
            [text.get_text() for text in window.axes_right.texts],
        )
        self.assertEqual(window._selected_dataset_rows(), [0, 1])
        window.project.dirty = False
        window.close()

    def test_gradient_legend_can_hide_or_show_chromatogram_name(self):
        window = self.make_window()
        window.show()
        self.app.processEvents()
        self.assertFalse(window.project.method.gradient_legend_include_dataset_name)
        self.assertFalse(hasattr(window, "gradient_legend_name_checkbox"))
        self.assertEqual(window.legend_axis_button_row.indexOf(window.legend_settings_button), 0)
        self.assertEqual(window.legend_axis_button_row.indexOf(window.axis_labels_button), 1)
        for button in (window.legend_settings_button, window.axis_labels_button):
            self.assertGreaterEqual(
                button.contentsRect().width(),
                button.fontMetrics().horizontalAdvance(button.text()),
            )
        labels = window.axes_gradient.get_legend_handles_labels()[1]
        self.assertEqual(labels, ["%B"])

        dialog = LegendComposerDialog(window.project.method, "ja")
        dialog.gradient_name_checkbox.setChecked(True)
        with patch("hplc_app.gui.LegendComposerDialog", return_value=dialog), patch(
            "hplc_app.gui.dialog_exec", return_value=True
        ):
            window.edit_legend_composer()
        labels = window.axes_gradient.get_legend_handles_labels()[1]
        self.assertEqual(
            labels, ["%B ({})".format(window.project.datasets[0].legend_label())]
        )
        self.assertTrue(window.project.method.gradient_legend_include_dataset_name)
        window.project.dirty = False
        window.close()

    def _saturated_window(self):
        """A window holding one clipped Gaussian and its integration."""
        window = self.make_window()
        while window.project.datasets:
            window.project.remove_dataset_at(0)
        time = np.linspace(4.0, 6.0, 1201)
        clean = 20000.0 * gaussian_profile(time, 5.0, 0.06)
        dataset = Dataset(
            label="clipped",
            short_label="clipped",
            time_min=time,
            intensity_uv=np.minimum(clean, 12000.0),
        )
        dataset.measurement.aux_range_au_per_v = 2.0
        dataset.peaks = [PeakRegion(start_min=4.0, end_min=6.0)]
        window.project.add_dataset(dataset)
        recalculate_dataset_peaks(dataset)
        window._refresh_all(0)
        window.peak_table.selectRow(0)
        return window, dataset

    def test_saturated_peak_correction_adds_an_estimated_fitted_row(self):
        window, dataset = self._saturated_window()
        parent = dataset.peaks[0]
        measured = (
            parent.raw_area_uv_min,
            parent.raw_height_uv,
            parent.retention_time_min,
            parent.area_percent,
        )
        raw_signal = dataset.intensity_uv.copy()
        true_area = 20000.0 * 0.06 * math.sqrt(2.0 * math.pi)

        with patch.object(
            QtWidgets.QInputDialog, "getItem", return_value=("Gaussian", True)
        ), patch("hplc_app.gui.dialog_exec", return_value=True), patch.object(
            SaturatedRangeDialog,
            "saturated_range",
            new_callable=PropertyMock,
            return_value=(4.94, 5.06),
        ):
            window.correct_saturated_peak()

        self.assertEqual(len(dataset.fitted_peaks), 1)
        fitted = dataset.fitted_peaks[0]
        recalculated_parent = dataset.peaks[0]
        self.assertTrue(is_saturation_corrected(fitted))
        self.assertEqual(fitted.parent_peak_id, parent.id)
        self.assertAlmostEqual(fitted.raw_area_uv_min / true_area, 1.0, delta=0.02)
        self.assertAlmostEqual(fitted.raw_height_uv / 20000.0, 1.0, delta=0.02)
        self.assertAlmostEqual(fitted.retention_time_min, 5.0, delta=0.01)
        self.assertIn("saturated_point_count", fitted.fit_parameters)

        # The measurement stays untouched; its clipped share is replaced by
        # the explicitly estimated correction row.
        np.testing.assert_array_equal(dataset.intensity_uv, raw_signal)
        self.assertEqual(
            (
                recalculated_parent.raw_area_uv_min,
                recalculated_parent.raw_height_uv,
                recalculated_parent.retention_time_min,
            ),
            measured[:3],
        )
        self.assertIsNone(recalculated_parent.area_percent)
        self.assertEqual(fitted.area_percent, 100.0)
        self.assertGreater(fitted.raw_area_uv_min, parent.raw_area_uv_min)

        # The table names the row and marks every estimated cell.
        row = next(
            index
            for index in range(window.peak_table.rowCount())
            if window.peak_table.item(index, 0).data(USER_ROLE) == fitted.id
        )
        self.assertIn(
            "飽和補正", window.peak_table.item(row, PEAK_TYPE_COLUMN).text()
        )
        for column in (3, 4, 5, 6, 7, 8, 9, 14, 15):
            with self.subTest(column=column):
                item = window.peak_table.item(row, column)
                self.assertEqual(
                    item.background().color().name(), "#fdf2ff"
                )
                self.assertEqual(
                    item.toolTip(), window.translator("estimated_from_fit")
                )
        self.assertEqual(window.peak_table.item(row, 8).text(), "100")

        window.undo()
        self.assertEqual(dataset.fitted_peaks, [])
        self.assertEqual(dataset.peaks[0].area_percent, 100.0)
        window.redo()
        self.assertEqual(len(dataset.fitted_peaks), 1)
        self.assertEqual(dataset.fitted_peaks[0].area_percent, 100.0)
        window.project.dirty = False
        window.close()

    def test_saturation_correction_warns_when_unsaturated_flanks_are_limited(self):
        window, dataset = self._saturated_window()
        parent = dataset.peaks[0]
        result = PeakFitResult(
            "gaussian",
            {"amplitude_uv": 20000.0, "center_min": 5.0, "sigma_min": 0.06},
            5.0,
            1.0,
            0.99,
            1.0,
            20,
        )
        span = SaturatedSpan(4.2, 5.8, 12)
        with patch.object(
            QtWidgets.QInputDialog, "getItem", return_value=("Gaussian", True)
        ), patch("hplc_app.gui.dialog_exec", return_value=True), patch.object(
            SaturatedRangeDialog,
            "saturated_range",
            new_callable=PropertyMock,
            return_value=(4.2, 5.8),
        ), patch("hplc_app.gui.fit_saturated_peak", return_value=(result, span)), patch.object(
            QtWidgets.QMessageBox, "warning"
        ) as warning:
            window.correct_saturated_peak()

        warning.assert_called_once()
        self.assertIn("裾", warning.call_args.args[2])
        self.assertIn("limited unsaturated flanks", dataset.fitted_peaks[0].notes)
        window.project.dirty = False
        window.close()

    def test_saturation_correction_always_requests_a_manual_range(self):
        window = self.make_window()
        dataset = window.project.datasets[0]
        window.peak_table.selectRow(0)
        before = len(dataset.fitted_peaks)

        with patch.object(
            QtWidgets.QInputDialog, "getItem", return_value=("Gaussian", True)
        ), patch("hplc_app.gui.dialog_exec", return_value=False) as dialog_exec_mock, patch.object(
            SaturatedRangeDialog, "__init__", return_value=None
        ) as dialog_init, patch("hplc_app.gui.fit_saturated_peak") as fit:
            window.correct_saturated_peak()

        dialog_exec_mock.assert_called_once()
        dialog_init.assert_called_once_with(
            dataset.peaks[0].start_min,
            dataset.peaks[0].end_min,
            window._application_language,
            window,
            initial=(dataset.peaks[0].start_min, dataset.peaks[0].end_min),
        )
        fit.assert_not_called()
        self.assertEqual(len(dataset.fitted_peaks), before)

        # An explicitly named range is used even when automatic detection would
        # have rejected this otherwise unsaturated peak.
        with patch.object(
            QtWidgets.QInputDialog, "getItem", return_value=("Gaussian", True)
        ), patch("hplc_app.gui.dialog_exec", return_value=True), patch.object(
            SaturatedRangeDialog,
            "saturated_range",
            new_callable=PropertyMock,
            return_value=(
                dataset.peaks[0].start_min + 0.4,
                dataset.peaks[0].start_min + 0.6,
            ),
        ):
            window.correct_saturated_peak()
        self.assertEqual(len(dataset.fitted_peaks), before + 1)
        self.assertTrue(is_saturation_corrected(dataset.fitted_peaks[-1]))
        window.project.dirty = False
        window.close()

    def test_estimated_peak_values_are_marked_in_csv_and_report(self):
        window, dataset = self._saturated_window()
        with patch.object(
            QtWidgets.QInputDialog, "getItem", return_value=("Gaussian", True)
        ), patch("hplc_app.gui.dialog_exec", return_value=True), patch.object(
            SaturatedRangeDialog,
            "saturated_range",
            new_callable=PropertyMock,
            return_value=(4.94, 5.06),
        ):
            window.correct_saturated_peak()
        fitted = dataset.fitted_peaks[0]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "peaks.csv"
            export_peak_csv(str(path), [dataset])
            rows = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines()))
        header = rows[0]
        self.assertIn("area_source", header)
        source_column = header.index("area_source")
        kind_column = header.index("peak_type")
        area_column = header.index("raw_area_uV_sec")
        body = {row[1]: row for row in rows[1:]}
        self.assertEqual(body["1"][source_column], "measured")
        self.assertEqual(body["1"][kind_column], "integrated")
        self.assertEqual(
            body["F1"][source_column],
            "measured_plus_fitted_saturated_span",
        )
        self.assertEqual(body["F1"][kind_column], "fitted_saturation_corrected")
        self.assertAlmostEqual(
            float(body["F1"][area_column]), fitted.raw_area_uv_sec, places=6
        )

        figures = analysis_report_figures(window.project, [dataset], "en")
        cells = [
            cell.get_text().get_text()
            for figure in figures
            for axis in figure.axes
            for table in axis.tables
            for cell in table.get_celld().values()
        ]
        legend_labels = [
            line.get_label()
            for figure in figures
            for axis in figure.axes
            for line in axis.lines
        ]
        self.assertTrue(
            any("estimated" in value for value in cells),
            "the report table must mark the fitted row as an estimate",
        )
        self.assertTrue(any("sat." in value for value in cells))
        self.assertTrue(
            any("estimated" in str(label) for label in legend_labels),
            "the report legend must mark the fitted curve as an estimate",
        )
        report_text = [
            text.get_text()
            for figure in figures
            for text in figure.texts
        ]
        self.assertTrue(any("fitted saturated span" in value for value in report_text))
        for figure in figures:
            figure.clear()
        window.project.dirty = False
        window.close()

    def test_peak_fit_result_is_saved_plotted_and_undoable(self):
        window = self.make_window()
        parent = window.project.datasets[0].peaks[0]
        original_metrics = (
            parent.retention_time_min,
            parent.raw_area_uv_sec,
            parent.area_mau_sec,
            parent.area_percent,
            parent.fwhm_min,
        )
        window.peak_table.selectRow(0)
        result = PeakFitResult(
            model="gaussian",
            parameters={
                "amplitude_uv": 1000.0,
                "center_min": 7.0,
                "sigma_min": 0.5,
            },
            retention_time_min=7.0,
            rmse_uv=2.5,
            r_squared=0.999,
            aic=12.0,
            point_count=50,
        )
        with patch.object(
            QtWidgets.QInputDialog,
            "getItem",
            return_value=("Automatic", True),
        ), patch("hplc_app.gui.fit_peak", return_value=result):
            window._application_language = "en"
            window.fit_selected_peak()
        dataset = window.project.datasets[0]
        self.assertEqual(len(dataset.peaks), 1)
        self.assertEqual(len(dataset.fitted_peaks), 1)
        peak = dataset.fitted_peaks[0]
        self.assertTrue(peak.is_fitted)
        self.assertEqual(peak.parent_peak_id, parent.id)
        self.assertEqual(peak.fit_model, "gaussian")
        self.assertEqual(peak.fit_parameters["sigma_min"], 0.5)
        self.assertAlmostEqual(peak.fit_r_squared, 0.999)
        self.assertEqual(
            (
                parent.retention_time_min,
                parent.raw_area_uv_sec,
                parent.area_mau_sec,
                parent.area_percent,
                parent.fwhm_min,
            ),
            original_metrics,
        )
        self.assertEqual(window.peak_table.rowCount(), 2)
        self.assertEqual(window.peak_table.item(1, 0).text(), "F1")
        self.assertEqual(window.peak_table.item(1, 20).text(), "#1")
        self.assertIsNotNone(
            window._peak_overlay_artists[peak.id]["fit_line"]
        )
        self.assertTrue(
            any("Fit GAUSSIAN (#1, estimated)" in text.get_text()
                for text in window.axes.get_legend().get_texts())
        )
        window.undo()
        self.assertEqual(window.project.datasets[0].peaks[0].fit_model, "")
        self.assertEqual(window.project.datasets[0].fitted_peaks, [])
        self.assertEqual(window.peak_table.rowCount(), 1)
        window.project.dirty = False
        window.close()

    def test_fitted_peak_recalculates_and_deletes_independently(self):
        window = self.make_window()
        dataset = window.project.datasets[0]
        parent_id = dataset.peaks[0].id
        original_area = dataset.peaks[0].raw_area_uv_sec

        def current_parent():
            return next(peak for peak in dataset.peaks if peak.id == parent_id)

        first_result = PeakFitResult(
            "gaussian",
            {"amplitude_uv": 900.0, "center_min": 7.0, "sigma_min": 0.4},
            7.0,
            3.0,
            0.99,
            14.0,
            50,
        )
        second_result = PeakFitResult(
            "gaussian",
            {"amplitude_uv": 950.0, "center_min": 7.2, "sigma_min": 0.45},
            7.2,
            2.0,
            0.995,
            11.0,
            50,
        )
        with patch.object(
            QtWidgets.QInputDialog, "getItem", return_value=("Automatic", True)
        ), patch("hplc_app.gui.fit_peak", side_effect=(first_result, second_result)):
            window._application_language = "en"
            window.peak_table.selectRow(0)
            window.fit_selected_peak()
            child_id = dataset.fitted_peaks[0].id
            window.peak_table.selectRow(1)
            window.fit_selected_peak()
        self.assertEqual(len(dataset.fitted_peaks), 1)
        self.assertEqual(dataset.fitted_peaks[0].id, child_id)
        self.assertEqual(dataset.fitted_peaks[0].retention_time_min, 7.2)
        # Fitting a child recalculates the dataset, so the parent identified by
        # id -- not the object captured above -- carries the measured area.
        self.assertEqual(current_parent().raw_area_uv_sec, original_area)

        window.peak_table.selectRow(1)
        window.delete_peak()
        self.assertEqual(len(dataset.peaks), 1)
        self.assertEqual(dataset.fitted_peaks, [])
        self.assertEqual(current_parent().raw_area_uv_sec, original_area)
        self.assertEqual(current_parent().fit_model, "")
        window.undo()
        self.assertEqual(len(window.project.datasets[0].fitted_peaks), 1)
        window.project.dirty = False
        window.close()

    def test_detached_integration_list_stays_synchronized_without_data_loss(self):
        window = self.make_window()
        dataset = window.project.datasets[0]
        parent = dataset.peaks[0]
        result = PeakFitResult(
            "gaussian",
            {"amplitude_uv": 1000.0, "center_min": 7.0, "sigma_min": 0.5},
            7.0,
            2.5,
            0.999,
            12.0,
            50,
        )
        child = fitted_peak_from_result(parent, result)
        dataset.fitted_peaks.append(child)
        mirror_fitted_peak_for_legacy(parent, child)
        window._refresh_peak_table()

        window.open_integration_list()
        dialog = window._integration_list_dialog
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.table.rowCount(), 2)
        dialog.table.selectRow(1)
        self.app.processEvents()
        self.assertEqual(window._selected_peak_ids(), [child.id])
        window.peak_table.selectRow(0)
        self.app.processEvents()
        self.assertEqual(
            window._selected_peak_ids_from_table(dialog.table), [parent.id]
        )

        dialog.table.item(1, PEAK_NOTES_COLUMN).setText("confirmed fit")
        self.app.processEvents()
        self.assertEqual(child.notes, "confirmed fit")
        self.assertEqual(
            window.peak_table.item(1, PEAK_NOTES_COLUMN).text(),
            "confirmed fit",
        )
        dialog.close()
        self.app.processEvents()
        self.assertFalse(dialog.isVisible())
        self.assertEqual(len(dataset.fitted_peaks), 1)
        window.project.dirty = False
        window.close()

    def test_native_preview_distinguishes_fitted_peak_in_curve_and_legend(self):
        if not pyqtgraph_scene_available():
            self.skipTest("optional modern renderer unavailable")
        window = self.make_window()
        try:
            dataset = window.project.datasets[0]
            child = fitted_peak_from_result(
                dataset.peaks[0],
                PeakFitResult(
                    "gaussian",
                    {
                        "amplitude_uv": 1000.0,
                        "center_min": 7.0,
                        "sigma_min": 0.5,
                    },
                    7.0,
                    2.5,
                    0.999,
                    12.0,
                    50,
                ),
            )
            dataset.fitted_peaks.append(child)
            mirror_fitted_peak_for_legacy(dataset.peaks[0], child)
            window._refresh_peak_table([child.id])
            window.screen_preview_checkbox.setChecked(True)
            preview = window._screen_preview
            self.assertIn(child.id, preview.consumer.fit_items)
            labels = [label.text for _sample, label in preview._legend.items]
            self.assertTrue(
                any("Fit GAUSSIAN (#1, estimated)" in label for label in labels)
            )
            self.assertEqual(
                window.peak_table.item(1, PEAK_TYPE_COLUMN).text(), "フィット"
            )
        finally:
            window.project.dirty = False
            window.close()

    def test_presets_are_remembered_across_projects(self):
        first = MainWindow()
        first.project.condition_presets = {
            "280 nm C4": {"wavelength_nm": 280.0, "column_name": "C4"}
        }
        first.project.gradient_presets = {
            "10-90 B": {
                "gradient": [
                    {"time_min": 0.0, "a_pct": 90.0, "b_pct": 10.0, "c_pct": 0.0, "d_pct": 0.0, "flow_ml_min": 1.0}
                ],
                "solvents": {},
            }
        }
        first._global_analyte_presets = {
            "LL-37": {
                "molar_absorptivity_214": 12500.0,
                "molar_absorptivity_280": None,
                "molecular_weight_g_mol": 4493.3,
            }
        }
        first._persist_global_presets()
        _conditions, _gradients, analytes, metadata = load_complete_preset_store()
        condition_id = metadata["conditions"]["280 nm C4"]["id"]
        self.assertTrue(metadata["conditions"]["280 nm C4"]["created_at"])
        self.assertEqual(analytes, first._global_analyte_presets)
        first.project.dirty = False
        first.close()

        second = MainWindow()
        self.assertIn("280 nm C4", second.project.condition_presets)
        self.assertIn("10-90 B", second.project.gradient_presets)
        self.assertIn("LL-37", second._global_analyte_presets)
        self.assertEqual(
            second._global_preset_metadata["conditions"]["280 nm C4"]["id"],
            condition_id,
        )
        second.new_project()
        self.assertIn("280 nm C4", second.project.condition_presets)
        self.assertIn("10-90 B", second.project.gradient_presets)
        second.project.dirty = False
        second.close()

    def test_new_project_can_open_in_independent_window_with_shared_presets(self):
        parent = self.make_window()
        parent.project.condition_presets = {
            "Shared C4": {"wavelength_nm": 280.0, "column_name": "C4"}
        }
        parent._persist_global_presets()
        parent.project.dirty = True
        parent_undo_count = len(parent._undo_stack)
        parent_dataset_ids = [dataset.id for dataset in parent.project.datasets]

        with patch.object(parent, "_confirm_unsaved") as confirm_unsaved:
            child = parent.new_project_in_new_window()
        confirm_unsaved.assert_not_called()
        self.assertIsNot(child, parent)
        self.assertTrue(child.isVisible())
        self.assertIn(child, MainWindow._open_windows)
        self.assertEqual(len(child.project.datasets), 0)
        self.assertFalse(child.project.dirty)
        self.assertIn("Shared C4", child.project.condition_presets)
        self.assertEqual(
            [dataset.id for dataset in parent.project.datasets],
            parent_dataset_ids,
        )
        self.assertTrue(parent.project.dirty)
        self.assertEqual(len(parent._undo_stack), parent_undo_count)

        child.project.title = "Child only"
        self.assertNotEqual(child.project.title, parent.project.title)
        child.set_language("en")
        self.assertEqual(
            child.new_window_action.text(), "New project in separate window"
        )
        self.assertEqual(parent.new_window_action.text(), "新規プロジェクトを別ウィンドウで開く")

        child.project.dirty = True
        with patch.object(child, "_confirm_unsaved", return_value=False):
            child.close()
        self.assertIn(child, MainWindow._open_windows)
        child.project.dirty = False
        child.close()
        self.app.processEvents()
        self.assertNotIn(child, MainWindow._open_windows)
        self.assertIn(parent, self.app.topLevelWidgets())
        parent.project.dirty = False
        parent.close()

    def test_v114_qsettings_presets_migrate_to_stable_preset_file(self):
        conditions = {
            "280 nm C4": {"wavelength_nm": 280.0, "column_name": "C4"}
        }
        gradients = {
            "10-90 B": {
                "gradient": [
                    {
                        "time_min": 0.0,
                        "a_pct": 90.0,
                        "b_pct": 10.0,
                        "c_pct": 0.0,
                        "d_pct": 0.0,
                        "flow_ml_min": 1.0,
                    }
                ],
                "solvents": {},
            }
        }
        self.settings.setValue(
            "presets/conditions", json.dumps(conditions, ensure_ascii=False)
        )
        self.settings.setValue(
            "presets/gradients", json.dumps(gradients, ensure_ascii=False)
        )
        self.settings.sync()

        first = MainWindow()
        self.assertTrue(preset_store_path().exists())
        stored_conditions, stored_gradients = load_preset_store()
        self.assertIn("280 nm C4", stored_conditions)
        self.assertIn("10-90 B", stored_gradients)
        first.project.dirty = False
        first.close()

        self.settings.clear()
        self.settings.sync()
        second = MainWindow()
        self.assertIn("280 nm C4", second.project.condition_presets)
        self.assertIn("10-90 B", second.project.gradient_presets)
        second.project.dirty = False
        second.close()

    def test_overview_detail_mode_keeps_overview_full_and_detail_interactive(self):
        window = self.make_window()
        window.project.method.view_mode = "overview_detail"
        window._plot()
        self.assertIsNotNone(window.axes_overview)
        self.assertTrue(window._overview_window_state.enabled)
        full_bounds = window._full_x_bounds()
        self.assertAlmostEqual(window.axes_overview.get_xlim()[0], full_bounds[0], places=6)
        self.assertAlmostEqual(window.axes_overview.get_xlim()[1], full_bounds[1], places=6)
        window.axes.set_xlim(4.0, 12.0)
        window.axes.set_ylim(100.0, 500.0)
        self.assertEqual(window._overview_window_state.detail_x, (4.0, 12.0))
        self.assertEqual(
            tuple(window._overview_view_patch.get_bbox().bounds),
            (4.0, 100.0, 8.0, 400.0),
        )
        window._on_scroll(
            SimpleNamespace(
                button="up",
                xdata=8.0,
                ydata=None,
                inaxes=window.axes_overview,
            )
        )
        self.assertEqual(tuple(window.axes.get_xlim()), (4.0, 12.0))
        self.assertLess(
            window.axes_overview.get_xlim()[1]
            - window.axes_overview.get_xlim()[0],
            full_bounds[1] - full_bounds[0],
        )
        self.assertIsNotNone(window._overview_view_patch)
        window.mouse_mode_combo.setCurrentIndex(
            window.mouse_mode_combo.findData("zoom")
        )
        window._on_canvas_press(ScreenPointerEvent(
            button=1, axis_role="overview_y1", hit_region="x",
            canvas_x=100.0, canvas_y=20.0,
            data_coordinates=(("overview_y1", 5.0, 0.0),),
        ))
        window._on_canvas_motion(ScreenPointerEvent(
            axis_role="outside", canvas_x=300.0, canvas_y=20.0,
            data_coordinates=(("overview_y1", 15.0, 0.0),),
        ))
        self.assertIsNotNone(window._overview_zoom_drag)
        window._on_canvas_release(ScreenPointerEvent(
            axis_role="outside", canvas_x=300.0, canvas_y=20.0,
            data_coordinates=(("overview_y1", 15.0, 0.0),),
        ))
        self.assertNotEqual(tuple(window.axes_overview.get_xlim()), (5.0, 15.0))
        self.assertEqual(tuple(window.axes.get_xlim()), (5.0, 15.0))
        window._center_detail_on(20.0)
        self.assertAlmostEqual(sum(window.axes.get_xlim()) / 2.0, 20.0, places=5)
        window.canvas.draw()
        boundary = float(window.axes_overview.bbox.y0)
        top = float(window.axes_overview.bbox.y1)
        bottom = float(window.axes.bbox.y0)
        window._on_canvas_press(ScreenPointerEvent(
            button=1, axis_role="outside", canvas_x=100.0,
            canvas_y=boundary,
        ))
        window._on_canvas_release(ScreenPointerEvent(
            axis_role="outside", canvas_x=100.0,
            canvas_y=top - 0.6 * (top - bottom),
        ))
        self.assertAlmostEqual(window._overview_split_ratio, 0.6)
        window.project.dirty = False
        window.close()

    def test_toolbar_navigation_cancels_custom_modes_after_replots(self):
        window = self.make_window()
        window.integrate_button.setChecked(True)
        self.assertTrue(window.integrate_button.isChecked())
        window.toolbar._actions["pan"].trigger()
        self.app.processEvents()
        self.assertFalse(window.integrate_button.isChecked())
        self.assertTrue(str(window.toolbar.mode))
        window.pointer_action.setChecked(True)
        self.assertFalse(str(window.toolbar.mode))
        for _index in range(3):
            window._plot()
            window.axes.set_xlim(5.0, 15.0)
            self.assertAlmostEqual(window.axes.get_xlim()[0], 5.0, places=6)
            self.assertIsNotNone(window.axes.xaxis.get_major_locator())
        window.pointer_action.setChecked(False)
        window.project.dirty = False
        window.close()

    def test_preferences_accept_folders_database_and_detection_settings(self):
        window = self.make_window()
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as save_directory:
            database_path = str(Path(save_directory) / "lab.sqlite3")
            dialog = PreferencesDialog(
                window.project.method,
                directory,
                "ja",
                save_directory=save_directory,
                database_path=database_path,
            )
            self.assertEqual(dialog.detection_tabs.count(), 3)
            low_snr = dialog.detection_widgets["low"]["auto_peak_snr_threshold"]
            low_snr.setValue(99.0)
            dialog._reset_detection_stage("low")
            self.assertEqual(
                low_snr.value(),
                default_auto_peak_sensitivity_presets()["low"][
                    "auto_peak_snr_threshold"
                ],
            )
            dialog.snr.setValue(8.5)
            dialog.max_count.setValue(75)
            dialog._accept()
            self.assertEqual(dialog.import_directory_value, directory)
            self.assertEqual(dialog.save_directory_value, save_directory)
            self.assertEqual(dialog.database_path_value, database_path)
            self.assertEqual(dialog.detection_values["auto_peak_snr_threshold"], 8.5)
            self.assertEqual(dialog.detection_values["auto_peak_max_count"], 75)
            self.assertEqual(
                dialog.auto_peak_sensitivity_presets_value["medium"][
                    "auto_peak_snr_threshold"
                ],
                8.5,
            )
            window._import_directory = directory
            with patch.object(
                QtWidgets.QFileDialog,
                "getOpenFileNames",
                return_value=([], ""),
            ) as chooser:
                window.import_ascii()
            self.assertEqual(chooser.call_args.args[2], directory)
            window._save_directory = save_directory
            suggested = Path(window._default_save_path("project.hplcproj"))
            self.assertEqual(suggested.parent, Path(save_directory))
            self.assertRegex(
                suggested.name,
                r"^project_\d{8}_\d{6}\.hplcproj$",
            )
        window.project.dirty = False
        window.close()

    def test_preferences_cancel_is_staged_and_accept_persists_sensitivities_globally(self):
        window = self.make_window()
        original = deepcopy(window._auto_peak_sensitivity_presets)
        with patch("hplc_app.gui.dialog_exec", return_value=False):
            window.edit_preferences()
        self.assertEqual(window._auto_peak_sensitivity_presets, original)

        updated = deepcopy(original)
        updated["medium"]["auto_peak_snr_threshold"] = 9.25
        fake_dialog = SimpleNamespace(
            import_directory_value=window._import_directory,
            save_directory_value=window._save_directory,
            database_path_value=window._database_path,
            automatic_update_check_value=window._automatic_update_check,
            render_quality_value=window._render_quality,
            auto_peak_sensitivity_presets_value=updated,
        )
        with patch(
            "hplc_app.gui.PreferencesDialog", return_value=fake_dialog
        ), patch("hplc_app.gui.dialog_exec", return_value=True):
            window.edit_preferences()
        self.assertEqual(
            ApplicationSettings().get(AUTO_PEAK_SENSITIVITY_PRESETS)["medium"][
                "auto_peak_snr_threshold"
            ],
            9.25,
        )
        self.assertFalse(window.project.dirty)
        window.close()

        second = MainWindow()
        self.assertEqual(
            second._auto_peak_sensitivity_presets["medium"][
                "auto_peak_snr_threshold"
            ],
            9.25,
        )
        second.project.dirty = False
        second.close()

    def test_import_accepts_mixed_gcd_ascii_and_reports_partial_failure(self):
        window = self.make_window()
        with tempfile.TemporaryDirectory() as directory:
            valid_gcd = Path(directory) / "valid.gcd"
            broken_gcd = Path(directory) / "broken.gcd"
            valid_gcd.write_bytes(synthetic_gcd_bytes())
            broken_gcd.write_bytes(synthetic_gcd_bytes()[:128])
            selected_paths = [
                str(valid_gcd),
                str(SAMPLES / "191720.TXT"),
                str(broken_gcd),
            ]
            with patch.object(
                QtWidgets.QFileDialog,
                "getOpenFileNames",
                return_value=(selected_paths, ""),
            ) as chooser, patch.object(QtWidgets.QMessageBox, "warning") as warning:
                window.import_ascii()

            self.assertIn("*.gcd", chooser.call_args.args[3])
            self.assertEqual(len(window.project.datasets), 4)
            self.assertEqual(
                [dataset.original_filename for dataset in window.project.datasets[-2:]],
                ["valid.gcd", "191720.TXT"],
            )
            self.assertTrue(window.project.dirty)
            self.assertIn("2", window.statusBar().currentMessage())
            warning.assert_called_once()
            self.assertIn("broken.gcd", warning.call_args.args[2])
            self.assertIn("GCD is not an OLE compound file", warning.call_args.args[2])
        window.project.dirty = False
        window.close()

    def test_directory_import_dialog_previews_relative_paths_and_empty_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "B.TXT").write_text("b", encoding="utf-8")
            (root / "a.gcd").write_text("a", encoding="utf-8")
            (root / "A.txt").write_text("duplicate", encoding="utf-8")
            (root / "ignored.csv").write_text("ignored", encoding="utf-8")
            nested = root / "sub"
            nested.mkdir()
            (nested / "c.TXT").write_text("c", encoding="utf-8")
            (nested / "a.txt").write_text("nested", encoding="utf-8")

            dialog = DirectoryImportDialog(str(root), "en")
            self.assertEqual(dialog.directory_path, root)
            self.assertEqual(dialog.group_label, root.name)
            self.assertEqual(
                [
                    dialog.preview_list.item(index).text()
                    for index in range(dialog.preview_list.count())
                ],
                ["a.gcd", "B.TXT"],
            )
            self.assertTrue(dialog.import_button.isEnabled())
            self.assertIn(".gcd is preferred", dialog.preference_label.text())
            self.assertIn("skipped 1", dialog.summary_label.text())

            dialog.recursive_checkbox.setChecked(True)
            self.app.processEvents()
            self.assertEqual(
                [
                    dialog.preview_list.item(index).text()
                    for index in range(dialog.preview_list.count())
                ],
                ["a.gcd", "B.TXT", "sub/a.txt", "sub/c.TXT"],
            )
            self.assertEqual(dialog.duplicate_txt_skip_count, 1)
            self.assertTrue(dialog.register_work_directory_checkbox.isChecked())
            self.assertTrue(dialog.register_work_directory)
            self.assertIn("work directory", dialog.register_work_directory_checkbox.text())
            dialog.close()

        with tempfile.TemporaryDirectory() as empty_directory:
            empty_dialog = DirectoryImportDialog(empty_directory, "en")
            self.assertEqual(empty_dialog.preview_list.count(), 0)
            self.assertFalse(empty_dialog.import_button.isEnabled())
            with patch.object(QtWidgets.QMessageBox, "warning") as warning:
                empty_dialog._accept()
            warning.assert_called_once()
            self.assertIn("No TXT/GCD", warning.call_args.args[2])
            empty_dialog.close()

    def test_directory_import_keeps_group_blank_and_registers_directory_label(self):
        window = self.make_window()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            later = root / "B.TXT"
            earlier = root / "a.TXT"
            later.write_bytes((SAMPLES / "225120.TXT").read_bytes())
            earlier.write_bytes((SAMPLES / "210601.TXT").read_bytes())
            fake_dialog = SimpleNamespace(
                directory_path=root,
                group_label="pac1",
                files=[earlier, later],
                recursive=True,
                duplicate_txt_skip_count=1,
            )
            FakeProgressDialog.cancel_after = None
            FakeProgressDialog.instances = []
            with patch(
                "hplc_app.gui.DirectoryImportDialog",
                return_value=fake_dialog,
            ), patch(
                "hplc_app.gui.dialog_exec",
                return_value=True,
            ), patch.object(
                QtWidgets,
                "QProgressDialog",
                FakeProgressDialog,
            ), patch.object(
                QtWidgets.QMessageBox,
                "information",
            ) as result_dialog:
                imported = window.import_directory()

            self.assertEqual(imported, 2)
            self.assertEqual(
                [
                    dataset.original_filename
                    for dataset in window.project.datasets[-2:]
                ],
                ["a.TXT", "B.TXT"],
            )
            self.assertEqual(
                [
                    dataset.measurement.group
                    for dataset in window.project.datasets[-2:]
                ],
                ["", ""],
            )
            self.assertEqual(
                self.settings.value("paths/last_import_directory"),
                str(root),
            )
            self.assertEqual(FakeProgressDialog.instances[0].values, [0, 1, 2, 2])
            self.assertTrue(FakeProgressDialog.instances[0].closed)
            self.assertEqual(
                window.project.work_directories,
                [
                    WorkDirectory(
                        path=str(root),
                        label="pac1",
                        recursive=True,
                    )
                ],
            )
            work_dialog = WorkDirectoriesDialog(
                window.project.work_directories, "en"
            )
            self.assertEqual(work_dialog.table.rowCount(), 1)
            self.assertEqual(work_dialog.table.item(0, 1).text(), "pac1")
            work_dialog.close()
            self.assertIn(
                window.translator("gcd_txt_skipped", count=1),
                result_dialog.call_args.args[2],
            )
            duplicate_dialog = SimpleNamespace(
                directory_path=root,
                group_label="duplicate",
                files=[earlier, later],
                recursive=False,
                duplicate_txt_skip_count=0,
            )
            with patch(
                "hplc_app.gui.DirectoryImportDialog",
                return_value=duplicate_dialog,
            ), patch(
                "hplc_app.gui.dialog_exec",
                return_value=True,
            ), patch.object(
                window,
                "_import_chromatogram_paths",
                return_value=0,
            ), patch.object(
                QtWidgets.QMessageBox,
                "information",
            ):
                self.assertEqual(window.import_directory(), 0)
            self.assertEqual(len(window.project.work_directories), 1)

            opt_out_root = root / "opt-out"
            opt_out_root.mkdir()
            opt_out_dialog = SimpleNamespace(
                directory_path=opt_out_root,
                group_label="not registered",
                files=[earlier],
                recursive=False,
                register_work_directory=False,
                duplicate_txt_skip_count=0,
            )
            with patch(
                "hplc_app.gui.DirectoryImportDialog",
                return_value=opt_out_dialog,
            ), patch(
                "hplc_app.gui.dialog_exec",
                return_value=True,
            ), patch.object(
                window,
                "_import_chromatogram_paths",
                return_value=1,
            ), patch.object(
                QtWidgets.QMessageBox,
                "information",
            ):
                self.assertEqual(window.import_directory(), 1)
            self.assertEqual(len(window.project.work_directories), 1)

            failed_root = root / "failed"
            failed_root.mkdir()
            failed_dialog = SimpleNamespace(
                directory_path=failed_root,
                group_label="failed import",
                files=[earlier],
                recursive=False,
                register_work_directory=True,
                duplicate_txt_skip_count=0,
            )
            with patch(
                "hplc_app.gui.DirectoryImportDialog",
                return_value=failed_dialog,
            ), patch(
                "hplc_app.gui.dialog_exec",
                return_value=True,
            ), patch.object(
                window,
                "_import_chromatogram_paths",
                return_value=0,
            ), patch.object(
                QtWidgets.QMessageBox,
                "information",
            ):
                self.assertEqual(window.import_directory(), 0)
            self.assertEqual(len(window.project.work_directories), 1)

            window.undo()
            self.assertEqual(window.project.work_directories, [])
            self.assertEqual(len(window.project.datasets), 4)
            window.redo()
            self.assertEqual(len(window.project.work_directories), 1)
            from hplc_app.project_io import load_project, save_project

            project_path = root / "batch-import.hplcproj"
            save_project(str(project_path), window.project)
            restored = load_project(str(project_path))
            self.assertEqual(
                restored.work_directories, window.project.work_directories
            )
        window.project.dirty = False
        window.close()

    def test_work_directory_reload_finds_only_new_files_and_holds_changed_paths(self):
        window = self.make_window()
        window.project = Project()
        with tempfile.TemporaryDirectory() as first_directory, tempfile.TemporaryDirectory() as second_directory:
            first = Path(first_directory) / "first.TXT"
            second = Path(second_directory) / "second.TXT"
            first.write_bytes((SAMPLES / "210601.TXT").read_bytes())
            second.write_bytes((SAMPLES / "225120.TXT").read_bytes())
            window.project.work_directories = [
                WorkDirectory(path=first_directory, label="pac1"),
                WorkDirectory(path=second_directory, label="pac2"),
            ]

            candidates, duplicates, changed, errors, skipped_txt_count = (
                window._work_directory_reload_candidates()
            )
            self.assertEqual(
                candidates,
                [(str(first), "pac1"), (str(second), "pac2")],
            )
            self.assertEqual((duplicates, changed, errors), (0, [], []))
            self.assertEqual(skipped_txt_count, 0)

            imported = window._import_chromatogram_paths(
                [path for path, _label in candidates],
            )
            self.assertEqual(imported, 2)
            self.assertEqual(
                [item.measurement.group for item in window.project.datasets],
                ["", ""],
            )
            candidates, duplicates, changed, errors, skipped_txt_count = (
                window._work_directory_reload_candidates()
            )
            self.assertEqual(candidates, [])
            self.assertEqual(duplicates, 2)
            self.assertEqual((changed, errors), ([], []))
            self.assertEqual(skipped_txt_count, 0)

            first.write_bytes((SAMPLES / "191720.TXT").read_bytes())
            candidates, duplicates, changed, errors, skipped_txt_count = (
                window._work_directory_reload_candidates()
            )
            self.assertEqual(candidates, [])
            self.assertEqual(duplicates, 1)
            self.assertEqual(changed, [str(first)])
            self.assertEqual(errors, [])
            self.assertEqual(skipped_txt_count, 0)
        window.project.dirty = False
        window.close()

    def test_work_directory_dialog_edits_flags_and_order(self):
        dialog = WorkDirectoriesDialog(
            [
                WorkDirectory(path="C:/HPLC/pac1", label="pac1"),
                WorkDirectory(path="C:/HPLC/pac2", label="pac2"),
            ],
            "en",
        )
        self.assertEqual(
            dialog.table.horizontalHeaderItem(3).text(), "Include subfolders"
        )
        dialog.table.item(0, 1).setText("first revised")
        dialog.table.item(0, 3).setCheckState(CHECKED)
        dialog.table.selectRow(0)
        dialog._move(1)
        dialog._accept()
        self.assertEqual(
            [entry.label for entry in dialog.directories],
            ["pac2", "first revised"],
        )
        self.assertTrue(dialog.directories[1].recursive)

    def test_directory_import_cancel_keeps_completed_files_and_skips_remaining(self):
        window = self.make_window()
        paths = [
            str(SAMPLES / "210601.TXT"),
            str(SAMPLES / "191720.TXT"),
        ]
        FakeProgressDialog.cancel_after = 1
        FakeProgressDialog.instances = []
        with patch.object(
            QtWidgets,
            "QProgressDialog",
            FakeProgressDialog,
        ), patch(
            "hplc_app.gui.load_chromatogram_file",
            wraps=load_ascii_file,
        ) as loader:
            imported = window._import_chromatogram_paths(
                paths,
                show_progress=True,
            )

        self.assertEqual(imported, 1)
        self.assertEqual(loader.call_count, 1)
        self.assertEqual(
            window.project.datasets[-1].measurement.group,
            "",
        )
        self.assertIn("1", window.statusBar().currentMessage())
        self.assertTrue(FakeProgressDialog.instances[0].closed)
        FakeProgressDialog.cancel_after = None
        window.project.dirty = False
        window.close()

    def test_window_accepts_file_drags_and_ignores_non_url_drags(self):
        window = self.make_window()
        self.assertTrue(window.acceptDrops())

        supported = self.drop_event(
            [QtCore.QUrl.fromLocalFile(str(SAMPLES / "210601.TXT"))]
        )
        window.dragEnterEvent(supported)
        supported.acceptProposedAction.assert_called_once()
        supported.ignore.assert_not_called()

        mime_data = QtCore.QMimeData()
        mime_data.setText("not a file")
        unsupported = Mock()
        unsupported.mimeData.return_value = mime_data
        window.dragEnterEvent(unsupported)
        unsupported.ignore.assert_called_once()
        unsupported.acceptProposedAction.assert_not_called()
        window.project.dirty = False
        window.close()

    def test_drop_imports_ascii_and_gcd_in_input_order(self):
        window = self.make_window()
        with tempfile.TemporaryDirectory() as directory:
            gcd_path = Path(directory) / "VALID.GCD"
            gcd_path.write_bytes(synthetic_gcd_bytes())
            event = self.drop_event(
                [
                    QtCore.QUrl.fromLocalFile(str(gcd_path)),
                    QtCore.QUrl.fromLocalFile(str(SAMPLES / "191720.TXT")),
                ]
            )

            window.dropEvent(event)

            self.assertEqual(
                [dataset.original_filename for dataset in window.project.datasets[-2:]],
                ["VALID.GCD", "191720.TXT"],
            )
            self.assertTrue(window.project.dirty)
            self.assertEqual(
                self.settings.value("paths/last_import_directory"), directory
            )
            event.acceptProposedAction.assert_called_once()
        window.project.dirty = False
        window.close()

    def test_drop_opens_one_project_through_shared_path_loader(self):
        window = self.make_window()
        with tempfile.TemporaryDirectory() as directory:
            project_path = Path(directory) / "dropped.HPLCPROJ"
            project_path.write_text("placeholder", encoding="utf-8")
            opened_project = Project(
                title="Dropped project", project_path=str(project_path)
            )
            event = self.drop_event(
                [QtCore.QUrl.fromLocalFile(str(project_path))]
            )
            with patch(
                "hplc_app.gui.load_project", return_value=opened_project
            ) as loader:
                window.dropEvent(event)

            loader.assert_called_once_with(str(project_path))
            self.assertIs(window.project, opened_project)
            self.assertEqual(
                self.settings.value("paths/last_project_directory"), directory
            )
            event.acceptProposedAction.assert_called_once()
        window.project.dirty = False
        window.close()

    def test_dirty_project_drop_cancel_keeps_current_project(self):
        window = self.make_window()
        original_project = window.project
        original_project.dirty = True
        with tempfile.TemporaryDirectory() as directory:
            project_path = Path(directory) / "cancelled.hplcproj"
            project_path.write_text("placeholder", encoding="utf-8")
            event = self.drop_event(
                [QtCore.QUrl.fromLocalFile(str(project_path))]
            )
            with patch.object(
                QtWidgets.QMessageBox,
                "question",
                return_value=QtWidgets.QMessageBox.Cancel,
            ), patch("hplc_app.gui.load_project") as loader:
                window.dropEvent(event)

            loader.assert_not_called()
            self.assertIs(window.project, original_project)
            self.assertTrue(window.project.dirty)
            event.acceptProposedAction.assert_called_once()
        window.project.dirty = False
        window.close()

    def test_dirty_project_drop_save_or_discard_opens_project(self):
        with tempfile.TemporaryDirectory() as directory:
            project_path = Path(directory) / "replacement.hplcproj"
            project_path.write_text("placeholder", encoding="utf-8")
            for answer in (
                QtWidgets.QMessageBox.Save,
                QtWidgets.QMessageBox.Discard,
            ):
                with self.subTest(answer=answer):
                    window = self.make_window()
                    window.project.dirty = True
                    opened_project = Project(
                        title="Replacement", project_path=str(project_path)
                    )
                    event = self.drop_event(
                        [QtCore.QUrl.fromLocalFile(str(project_path))]
                    )
                    with patch.object(
                        QtWidgets.QMessageBox, "question", return_value=answer
                    ), patch.object(
                        window, "save_project", return_value=True
                    ) as saver, patch(
                        "hplc_app.gui.load_project", return_value=opened_project
                    ):
                        window.dropEvent(event)

                    if answer == QtWidgets.QMessageBox.Save:
                        saver.assert_called_once()
                    else:
                        saver.assert_not_called()
                    self.assertIs(window.project, opened_project)
                    window.project.dirty = False
                    window.close()

    def test_failed_project_drop_keeps_current_project(self):
        window = self.make_window()
        original_project = window.project
        with tempfile.TemporaryDirectory() as directory:
            project_path = Path(directory) / "broken.hplcproj"
            project_path.write_text("broken", encoding="utf-8")
            event = self.drop_event(
                [QtCore.QUrl.fromLocalFile(str(project_path))]
            )
            with patch(
                "hplc_app.gui.load_project", side_effect=ValueError("broken project")
            ), patch.object(QtWidgets.QMessageBox, "critical") as critical:
                window.dropEvent(event)

            critical.assert_called_once()
            self.assertIn("broken project", critical.call_args.args[2])
            self.assertIs(window.project, original_project)
            self.assertFalse(window.project.dirty)
            event.acceptProposedAction.assert_called_once()
        window.close()

    def test_invalid_file_drop_is_rejected_without_project_changes(self):
        window = self.make_window()
        original_project = window.project
        original_count = len(window.project.datasets)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text_path = root / "trace.txt"
            project_path = root / "one.hplcproj"
            second_project_path = root / "two.hplcproj"
            unsupported_path = root / "notes.csv"
            for path in (
                text_path,
                project_path,
                second_project_path,
                unsupported_path,
            ):
                path.write_text("placeholder", encoding="utf-8")

            invalid_url_sets = (
                [
                    QtCore.QUrl.fromLocalFile(str(text_path)),
                    QtCore.QUrl.fromLocalFile(str(project_path)),
                ],
                [
                    QtCore.QUrl.fromLocalFile(str(project_path)),
                    QtCore.QUrl.fromLocalFile(str(second_project_path)),
                ],
                [QtCore.QUrl.fromLocalFile(str(root))],
                [QtCore.QUrl.fromLocalFile(str(unsupported_path))],
                [QtCore.QUrl("https://example.invalid/trace.txt")],
            )
            for urls in invalid_url_sets:
                with self.subTest(urls=[url.toString() for url in urls]):
                    event = self.drop_event(urls)
                    with patch.object(QtWidgets.QMessageBox, "warning") as warning:
                        window.dropEvent(event)
                    warning.assert_called_once()
                    self.assertIs(window.project, original_project)
                    self.assertEqual(len(window.project.datasets), original_count)
                    self.assertFalse(window.project.dirty)
                    event.acceptProposedAction.assert_called_once()
        window.close()

    def test_figure_export_is_menu_only_and_report_uses_save_folder(self):
        window = self.make_window()
        self.assertFalse(hasattr(window, "figure_format_combo"))
        self.assertFalse(hasattr(window, "export_figure_button"))
        self.assertIn(window.export_figure_action, window.file_menu.actions())
        self.assertIn(window.three_d_figure_action, window.file_menu.actions())
        self.assertIn(window.copy_view_action, window.file_menu.actions())
        self.assertIn(window.print_view_action, window.file_menu.actions())
        self.assertEqual(window._figure_export_format, "png")
        with tempfile.TemporaryDirectory() as directory:
            window._save_directory = directory
            destination = Path(directory) / "chosen_figure"
            with patch.object(
                QtWidgets.QFileDialog,
                "getSaveFileName",
                return_value=(str(destination), "PNG画像 (*.png)"),
            ) as chooser:
                window.export_figure()
            self.assertRegex(
                Path(chooser.call_args.args[2]).name,
                r"^chromatogram_\d{8}_\d{6}\.png$",
            )
            self.assertIn("*.png", chooser.call_args.args[3])
            self.assertIn("*.svg", chooser.call_args.args[3])
            self.assertIn("*.pdf", chooser.call_args.args[3])
            self.assertTrue(Path(str(destination) + ".png").exists())

            with patch.object(
                QtWidgets.QFileDialog,
                "getSaveFileName",
                return_value=("", ""),
            ) as report_chooser, patch(
                "hplc_app.gui.dialog_exec", return_value=True
            ):
                window.export_report()
            self.assertRegex(
                Path(report_chooser.call_args.args[2]).name,
                r"^analysis_report_Untitled-project_\d{8}_\d{6}\.pdf$",
            )
        window.project.dirty = False
        window.close()

    def test_report_pdf_default_name_carries_a_safe_project_name(self):
        window = self.make_window()
        # An unsaved project contributes its title; a saved one its file name.
        cases = (
            ("", "", "analysis_report_Untitled-project.pdf"),
            ("Untitled project", "", "analysis_report_Untitled-project.pdf"),
            ("Simple name", "", "analysis_report_Simple-name.pdf"),
            ('bad:name*with?"chars"', "", "analysis_report_bad-name-with-chars.pdf"),
            ("   ", "", "analysis_report_Untitled-project.pdf"),
            ("A" * 120, "", "analysis_report_%s.pdf" % ("A" * 40)),
        )
        for title, project_path, expected in cases:
            with self.subTest(title=title):
                window.project.title = title
                window.project.project_path = project_path
                self.assertEqual(window._report_pdf_filename(), expected)
                # Windows rejects these characters in any path component.
                for character in '<>:"/\\|?*':
                    self.assertNotIn(
                        character, window._report_pdf_filename()[: -len(".pdf")]
                    )

        window.project.project_path = str(Path("C:/data/2026 run: A.hplcproj"))
        window.project.title = "ignored while a file name exists"
        self.assertEqual(
            window._report_pdf_filename(), "analysis_report_2026-run-A.pdf"
        )
        window.project.dirty = False
        window.close()

    def test_all_primary_save_dialog_defaults_use_the_timestamp_helper(self):
        window = self.make_window()
        filenames = (
            "20260909_Title_Column_Condition_Author.hplcproj",
            "chromatogram.png",
            "chromatogram_3d.pdf",
            "analysis_report_Title.pdf",
            "peak_table.csv",
            "trace.csv",
            "sample_conditions.csv",
        )
        with patch(
            "hplc_app.gui.timestamped_filename",
            side_effect=lambda filename: "timestamped_" + filename,
        ) as timestamped:
            defaults = [window._default_save_path(filename) for filename in filenames]
        self.assertEqual(
            [Path(filename).name for filename in defaults],
            ["timestamped_" + filename for filename in filenames],
        )
        self.assertEqual(timestamped.call_args_list, [call(filename) for filename in filenames])
        window.project.dirty = False
        window.close()

    def test_report_print_previews_exactly_what_it_prints(self):
        window = self.make_window()
        window.dataset_table.selectAll()
        captured = {}

        class FakePreviewDialog:
            instances = []
            accepted = True

            def __init__(self, printer, parent=None):
                self.printer = printer
                self.title = ""
                self.painters = []
                FakePreviewDialog.instances.append(self)

            def setWindowTitle(self, title):
                self.title = title

            @property
            def paintRequested(self):
                dialog = self

                class Signal:
                    def connect(self, slot):
                        dialog.painters.append(slot)

                return Signal()

        def run_dialog(dialog):
            if isinstance(dialog, FakePreviewDialog):
                # The preview repaints on its own; the printed output uses the
                # very same slot, so painting twice must stay consistent.
                for slot in dialog.painters:
                    slot("preview-target")
                    slot("printer-target")
                return FakePreviewDialog.accepted
            return True

        def record(printer, pages):
            captured.setdefault("targets", []).append(printer)
            captured["pages"] = list(pages)

        with patch.object(
            QtPrintSupport, "QPrintPreviewDialog", FakePreviewDialog
        ), patch("hplc_app.gui.dialog_exec", side_effect=run_dialog), patch.object(
            window, "_draw_report_pages_to_printer", side_effect=record
        ):
            window.print_report()

        self.assertEqual(len(FakePreviewDialog.instances), 1)
        preview = FakePreviewDialog.instances[0]
        self.assertEqual(preview.title, window.translator("print_report"))
        # One painting routine served both the preview and the printer, with the
        # same rendered pages, so they cannot drift apart.
        self.assertEqual(captured["targets"], ["preview-target", "printer-target"])
        self.assertTrue(captured["pages"])
        self.assertTrue(
            all(page.lower().endswith(".png") for page in captured["pages"])
        )
        self.assertIn("印刷ジョブ", window.statusBar().currentMessage())

        # Cancelling the preview prints nothing and reports nothing.
        window.statusBar().clearMessage()
        FakePreviewDialog.instances = []
        FakePreviewDialog.accepted = False
        with patch.object(
            QtPrintSupport, "QPrintPreviewDialog", FakePreviewDialog
        ), patch("hplc_app.gui.dialog_exec", side_effect=run_dialog), patch.object(
            window, "_draw_report_pages_to_printer", side_effect=record
        ):
            window.print_report()
        self.assertEqual(window.statusBar().currentMessage(), "")
        FakePreviewDialog.accepted = True
        window.project.dirty = False
        window.close()

    def test_current_view_print_keeps_its_own_dialog(self):
        window = self.make_window()
        # The report gained a preview; printing the current view did not, and
        # the Issue explicitly leaves it alone.
        with patch.object(
            QtPrintSupport, "QPrintDialog"
        ) as print_dialog, patch(
            "hplc_app.gui.dialog_exec", return_value=False
        ):
            window.print_current_view()
        print_dialog.assert_called_once()
        window.project.dirty = False
        window.close()

    def test_report_scope_dialog_and_selection_cover_all_visible_and_selected(self):
        window = self.make_window()
        window.project.datasets[1].visible = False
        selected_rows = [1]
        expected = {
            "all": window.project.datasets,
            "visible": [window.project.datasets[0]],
            "selected": [window.project.datasets[1]],
        }
        for scope, datasets in expected.items():
            fake = Mock()
            fake.scope.return_value = scope
            with self.subTest(scope=scope), patch.object(
                window, "_selected_dataset_rows", return_value=selected_rows
            ), patch("hplc_app.gui.ReportScopeDialog", return_value=fake), patch(
                "hplc_app.gui.dialog_exec", return_value=True
            ):
                self.assertEqual(window._choose_report_datasets(), datasets)

        dialog = ReportScopeDialog(2, 1, 0, "en")
        self.assertTrue(dialog.visible_radio.isChecked())
        self.assertFalse(dialog.selected_radio.isEnabled())
        dialog.all_radio.setChecked(True)
        self.assertEqual(dialog.scope(), "all")
        dialog.close()

        initial_dialog = ReportOptionsDialog(
            "en",
            initial_values={
                "integration_range": False,
                "baseline": False,
                "retention_time": False,
                "gradient_b": True,
            },
        )
        self.assertFalse(initial_dialog.checkboxes["integration_range"].isChecked())
        self.assertFalse(initial_dialog.checkboxes["baseline"].isChecked())
        self.assertFalse(initial_dialog.checkboxes["retention_time"].isChecked())
        self.assertTrue(initial_dialog.checkboxes["gradient_b"].isChecked())
        self.assertEqual(initial_dialog.checkboxes["baseline"].text(), "Baselines")
        self.assertTrue(initial_dialog.checkboxes["gradient_conditions"].isChecked())
        self.assertTrue(initial_dialog.checkboxes["quantitation"].isChecked())
        initial_dialog.close()

        window.project.method.show_integration_areas = False
        window.project.method.show_retention_labels = False
        window.project.method.show_gradient_b = True
        options_dialog = ReportOptionsDialog("en")
        options_dialog.checkboxes["baseline"].setChecked(False)
        values = options_dialog.option_values()
        self.assertFalse(values["baseline"])
        self.assertTrue(values["retention_time"])
        with patch(
            "hplc_app.gui.ReportOptionsDialog", return_value=options_dialog
        ) as options_factory, patch(
            "hplc_app.gui.dialog_exec", return_value=True
        ):
            options = window._choose_report_options()
        self.assertEqual(
            options_factory.call_args.kwargs["initial_values"],
            {
                "integration_range": False,
                "baseline": False,
                "retention_time": False,
                "gradient_b": True,
                "gradient_conditions": True,
                "quantitation": True,
            },
        )
        self.assertFalse(options.baseline)
        self.assertTrue(options.integration_range)
        self.assertTrue(options.retention_time)
        options_dialog.close()
        window.project.dirty = False
        window.close()

    def test_3d_dialog_selects_grid_planes_and_resets_only_the_view_angles(self):
        window = self.make_window()
        rotated = ThreeDPlotOptions(
            y_axis_title="Sample order",
            z_min=-500.0,
            z_max=5000.0,
            elevation_deg=68.0,
            azimuth_deg=112.0,
            aspect_x=2.0,
            aspect_z=3.0,
            x_tick_interval=2.5,
            z_tick_interval=250.0,
            color_mode="gradient",
            colormap="Viridis",
            density_percent=40,
            axis_line_width=3.0,
            axis_label_font_size=12.5,
            tick_label_font_size=7.5,
        )
        dialog = ThreeDChromatogramDialog(
            window.project.datasets,
            window.project.method,
            (0.0, 30.0),
            rotated,
            "en",
        )
        try:
            self.assertEqual(
                tuple(round(value, 6) for value in dialog.figure.get_size_inches()),
                (
                    round(window.project.method.figure_width_mm / 25.4, 6),
                    round(window.project.method.figure_height_mm / 25.4, 6),
                ),
            )
            dialog.show()
            self.app.processEvents()
            self.assertEqual(
                tuple(round(value, 6) for value in dialog.figure.get_size_inches()),
                (
                    round(window.project.method.figure_width_mm / 25.4, 6),
                    round(window.project.method.figure_height_mm / 25.4, 6),
                ),
            )
            self.assertGreaterEqual(dialog.figure.get_dpi(), 30.0)
            self.assertTrue(dialog.show_x_label_checkbox.isChecked())
            self.assertTrue(dialog.show_y_label_checkbox.isChecked())
            self.assertTrue(dialog.show_z_label_checkbox.isChecked())
            self.assertTrue(dialog.show_x_tick_labels_checkbox.isChecked())
            self.assertTrue(dialog.show_y_tick_labels_checkbox.isChecked())
            self.assertTrue(dialog.show_z_tick_labels_checkbox.isChecked())
            self.assertTrue(dialog.grid_xy_checkbox.isChecked())
            self.assertEqual(dialog.axis_label_font_spin.value(), 12.5)
            self.assertEqual(dialog.tick_label_font_spin.value(), 7.5)
            self.assertFalse(dialog.grid_xz_checkbox.isChecked())
            self.assertFalse(dialog.grid_yz_checkbox.isChecked())
            self.assertEqual(
                {item.get_gid() for item in dialog.figure.axes[0].collections},
                {"hplc-grid-xy"},
            )

            dialog.grid_xy_checkbox.setChecked(False)
            dialog.grid_xz_checkbox.setChecked(True)
            dialog.grid_yz_checkbox.setChecked(True)
            self.app.processEvents()
            self.assertEqual(
                (
                    dialog.plot_options().grid_xy,
                    dialog.plot_options().grid_xz,
                    dialog.plot_options().grid_yz,
                ),
                (False, True, True),
            )
            self.assertEqual(
                {item.get_gid() for item in dialog.figure.axes[0].collections},
                {"hplc-grid-xz", "hplc-grid-yz"},
            )

            dialog.show_x_label_checkbox.setChecked(False)
            dialog.show_z_tick_labels_checkbox.setChecked(False)
            self.app.processEvents()
            self.assertFalse(dialog.plot_options().show_x_label)
            self.assertFalse(dialog.plot_options().show_z_tick_labels)
            self.assertFalse(dialog.figure.axes[0].xaxis.label.get_visible())
            self.assertTrue(
                all(
                    not label.get_visible()
                    for label in dialog.figure.axes[0].get_zticklabels()
                )
            )

            dialog.reset_view_button.click()
            self.app.processEvents()
            defaults = ThreeDPlotOptions()
            options = dialog.plot_options()
            self.assertEqual(options.elevation_deg, defaults.elevation_deg)
            self.assertEqual(options.azimuth_deg, defaults.azimuth_deg)
            self.assertEqual(
                (dialog.figure.axes[0].elev, dialog.figure.axes[0].azim),
                (defaults.elevation_deg, defaults.azimuth_deg),
            )
            # Reset touches the two angles and nothing else.
            self.assertEqual(options.y_axis_title, "Sample order")
            self.assertEqual((options.z_min, options.z_max), (-500.0, 5000.0))
            self.assertEqual(
                (options.aspect_x, options.aspect_y, options.aspect_z), (2.0, 1.0, 3.0)
            )
            self.assertEqual(
                (options.x_tick_interval, options.z_tick_interval), (2.5, 250.0)
            )
            self.assertEqual(options.color_mode, "gradient")
            self.assertEqual(options.colormap, "Viridis")
            self.assertEqual(options.density_percent, 40)
            self.assertEqual(options.axis_line_width, 3.0)
            self.assertEqual(options.axis_label_font_size, 12.5)
            self.assertEqual(options.tick_label_font_size, 7.5)
            self.assertFalse(options.show_x_label)
            self.assertFalse(options.show_z_tick_labels)
            self.assertEqual(
                (options.grid_xy, options.grid_xz, options.grid_yz),
                (False, True, True),
            )

            # Rotating again after the reset still works.
            self.assertTrue(dialog.elevation_spin.isEnabled())
            self.assertTrue(dialog.azimuth_spin.isEnabled())
            dialog.elevation_spin.setValue(45.0)
            dialog.azimuth_spin.setValue(-20.0)
            self.app.processEvents()
            self.assertEqual(
                (dialog.figure.axes[0].elev, dialog.figure.axes[0].azim), (45.0, -20.0)
            )
            self.assertTrue(dialog.export_button.isEnabled())
        finally:
            dialog.close()
            window.project.dirty = False
            window.close()

    def test_3d_dialog_uses_selected_table_order_session_settings_and_shared_export(self):
        window = self.make_window()
        window.show()
        self.app.processEvents()
        window.dataset_table.selectAll()
        before = _trace_edit_state(window.project.datasets)
        raw = [
            (dataset.time_min.copy(), dataset.intensity_uv.copy())
            for dataset in window.project.datasets
        ]
        x_limits = tuple(window._screen_view_state().x)
        captured = []

        def configure(dialog):
            captured.append(dialog)
            dialog.z_tick_spin.setValue(250.0)
            dialog.axis_label_font_spin.setValue(8.0)
            dialog.tick_label_font_spin.setValue(6.5)
            dialog.y_title_edit.setText("Injection order")
            dialog.color_mode_combo.setCurrentIndex(
                dialog.color_mode_combo.findData("gradient")
            )
            dialog.colormap_combo.setCurrentText("Viridis")
            dialog.density_slider.setValue(40)
            dialog.z_min_edit.setText("-100")
            dialog.z_max_edit.setText("5000")
            dialog.refresh_preview()
            return True

        window.project.dirty = False
        with patch("hplc_app.gui.dialog_exec", side_effect=configure):
            window.open_3d_chromatogram()
        self.assertEqual(len(captured), 1)
        dialog = captured[0]
        self.assertEqual(dialog.plot_options(), window._three_d_plot_options)
        self.assertEqual(dialog.plot_options().density_percent, 40)
        self.assertEqual(dialog.plot_options().axis_label_font_size, 8.0)
        self.assertEqual(dialog.plot_options().tick_label_font_size, 6.5)
        self.assertEqual(window.project.method.axis_label_font_size, 10.0)
        self.assertEqual(window.project.method.tick_label_font_size, 9.0)
        axis = dialog.figure.axes[0]
        self.assertEqual(
            [item.get_text() for item in axis.get_yticklabels()], ["Ch1", "Ch2"]
        )
        self.assertEqual(tuple(round(value, 6) for value in axis.get_xlim()),
                         tuple(round(value, 6) for value in sorted(x_limits)))
        self.assertEqual(len(axis.lines), 2)
        self.assertEqual(_trace_edit_state(window.project.datasets), before)
        self.assertFalse(window.project.dirty)
        for dataset, (time, intensity) in zip(window.project.datasets, raw):
            np.testing.assert_array_equal(dataset.time_min, time)
            np.testing.assert_array_equal(dataset.intensity_uv, intensity)

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "selected-3d.svg"
            with patch.object(
                QtWidgets.QFileDialog,
                "getSaveFileName",
                return_value=(str(destination), "SVG画像 (*.svg)"),
            ) as chooser, patch.object(
                window,
                "_save_static_figure",
                wraps=window._save_static_figure,
            ) as save_figure:
                window.export_3d_chromatogram(dialog)
            self.assertTrue(destination.exists())
            save_figure.assert_called_once_with(
                dialog.figure, str(destination), bbox_inches=None
            )
            self.assertRegex(
                Path(chooser.call_args.args[2]).name,
                r"^chromatogram_3d_\d{8}_\d{6}\.png$",
            )
            self.assertIn("<svg", destination.read_text(encoding="utf-8")[:500])
        dialog.close()

        window.dataset_table.clearSelection()
        window.dataset_table.selectRow(0)
        with patch.object(QtWidgets.QMessageBox, "information") as information:
            window.open_3d_chromatogram()
        information.assert_called_once()
        window.project.dirty = False
        window.close()

    def test_current_view_can_be_copied_and_printed_as_screen_snapshot(self):
        window = self.make_window()
        window.copy_view_to_clipboard()
        clipboard_pixmap = QtWidgets.QApplication.clipboard().pixmap()
        self.assertFalse(clipboard_pixmap.isNull())
        self.assertGreater(clipboard_pixmap.width(), 0)

        mode = (
            QtPrintSupport.QPrinter.PrinterMode.HighResolution
            if QT_API == 6
            else QtPrintSupport.QPrinter.HighResolution
        )
        with tempfile.TemporaryDirectory() as directory:
            printer = QtPrintSupport.QPrinter(mode)
            pdf_format = (
                QtPrintSupport.QPrinter.OutputFormat.PdfFormat
                if QT_API == 6
                else QtPrintSupport.QPrinter.PdfFormat
            )
            output = str(Path(directory) / "current-view.pdf")
            printer.setOutputFormat(pdf_format)
            printer.setOutputFileName(output)
            window._draw_view_pixmap_to_printer(
                printer, window._current_view_pixmap()
            )
            self.assertTrue(Path(output).read_bytes().startswith(b"%PDF"))
        window.project.dirty = False
        window.close()

    def test_project_naming_dialog_previews_standard_filename(self):
        dialog = ProjectNamingDialog(
            {
                "date": "20260809",
                "title": "LL-37 cleavage",
                "column": "C4",
                "condition": "10-90 B",
                "author": "M Shiba",
            },
            "ja",
        )
        self.assertEqual(
            dialog.preview.text(),
            "20260809_LL-37-cleavage_C4_10-90-B_M-Shiba.hplcproj",
        )
        dialog.title_edit.setText("LL-37 release")
        self.app.processEvents()
        self.assertIn("LL-37-release", dialog.preview.text())
        dialog._accept()
        self.assertEqual(dialog.name_parts()["author"], "M Shiba")
        dialog.close()

    def test_project_save_upserts_shared_database_and_manager_displays_it(self):
        window = self.make_window()
        window.project.title = "Database GUI test"
        window.project.analysis_date = "20260809"
        window.project.column_name = "C4"
        window.project.condition_name = "10-90 B"
        window.project.author = "Tester"
        with tempfile.TemporaryDirectory() as directory:
            window.project.project_path = str(Path(directory) / "database-test.hplcproj")
            window._database_path = str(Path(directory) / "lab.sqlite3")
            self.assertTrue(window.save_project())
            sections = database_sections(window._database_path)
            self.assertEqual(len(sections["projects"][1]), 1)
            self.assertEqual(len(sections["datasets"][1]), 2)
            window.project.title = "Database GUI updated"
            window.project.datasets[0].measurement.sample_name = "Updated sample"
            window.project.dirty = True
            self.assertTrue(window.save_project())
            sections = database_sections(window._database_path)
            self.assertEqual(len(sections["projects"][1]), 1)
            self.assertEqual(sections["projects"][1][0][1], "Database GUI updated")
            manager = LabDatabaseDialog(window._database_path, "ja")
            self.assertEqual(manager.tables["projects"].rowCount(), 1)
            self.assertEqual(manager.tables["datasets"].rowCount(), 2)
            self.assertEqual(manager.tables["gradients"].rowCount(), 1)
            self.assertNotIn("peaks", manager.tables)
            manager.close()
        window.project.dirty = False
        window.close()

    def test_save_action_uses_standard_shortcut_and_existing_project_path(self):
        window = self.make_window()
        self.assertEqual(
            window.save_action.shortcut(),
            QtGui.QKeySequence(STANDARD_SAVE_SHORTCUT),
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = str(Path(directory) / "shortcut-save.hplcproj")
            window.project.project_path = destination
            window.project.dirty = True
            window.save_action.trigger()
            self.assertTrue(Path(destination).exists())
            self.assertEqual(window.project.project_path, destination)
            self.assertFalse(window.project.dirty)
        window.close()

    def test_save_action_opens_save_as_and_cancel_preserves_project_state(self):
        window = self.make_window()
        window.project.project_path = ""
        window.project.dirty = True
        original_state = (
            window.project.project_path,
            window.project.title,
            window.project.author,
            window.project.dirty,
        )
        with patch("hplc_app.gui.dialog_exec", return_value=True), patch.object(
            QtWidgets.QFileDialog,
            "getSaveFileName",
            return_value=("", ""),
        ) as chooser:
            window.save_action.trigger()
        chooser.assert_called_once()
        self.assertEqual(
            (
                window.project.project_path,
                window.project.title,
                window.project.author,
                window.project.dirty,
            ),
            original_state,
        )
        window.project.dirty = False
        window.close()

    def test_save_action_reports_write_failure_without_clearing_dirty_state(self):
        window = self.make_window()
        window.project.project_path = "C:/unwritable/shortcut-save.hplcproj"
        window.project.dirty = True
        with patch(
            "hplc_app.gui.save_project",
            side_effect=OSError("simulated write failure"),
        ), patch.object(QtWidgets.QMessageBox, "critical") as critical:
            window.save_action.trigger()
        critical.assert_called_once()
        self.assertIn("simulated write failure", critical.call_args.args[2])
        self.assertEqual(
            window.project.project_path,
            "C:/unwritable/shortcut-save.hplcproj",
        )
        self.assertTrue(window.project.dirty)
        window.project.dirty = False
        window.close()

    def test_a4_report_pages_can_be_sent_to_qprinter_pdf(self):
        window = self.make_window()
        mode = (
            QtPrintSupport.QPrinter.PrinterMode.HighResolution
            if QT_API == 6
            else QtPrintSupport.QPrinter.HighResolution
        )
        with tempfile.TemporaryDirectory() as directory:
            pages = render_analysis_report_pages(
                directory, window.project, [window.project.datasets[0]], "en"
            )
            printer = QtPrintSupport.QPrinter(mode)
            pdf_format = (
                QtPrintSupport.QPrinter.OutputFormat.PdfFormat
                if QT_API == 6
                else QtPrintSupport.QPrinter.PdfFormat
            )
            printer.setOutputFormat(pdf_format)
            output = str(Path(directory) / "printed.pdf")
            printer.setOutputFileName(output)
            if QT_API == 6:
                printer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.PageSizeId.A4))
            else:
                printer.setPageSize(QtPrintSupport.QPrinter.A4)
            window._draw_report_pages_to_printer(printer, pages)
            self.assertTrue(Path(output).read_bytes().startswith(b"%PDF"))
        window.project.dirty = False
        window.close()


if __name__ == "__main__":
    unittest.main()
