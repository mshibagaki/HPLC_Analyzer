from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from hplc_app.analysis import recalculate_dataset_peaks
from hplc_app.database import database_sections
from hplc_app.dialogs import (
    AxisLabelsDialog,
    BatchMetadataDialog,
    GradientDialog,
    LabDatabaseDialog,
    MetadataDialog,
    PreferencesDialog,
    ProjectNamingDialog,
    QuantitationHelpDialog,
    TextAnnotationDialog,
)
from hplc_app.gui import (
    DATASET_LABEL_COLUMN,
    DATASET_RUN_ID_COLUMN,
    DATASET_SOURCE_COLUMN,
    DATASET_WAVELENGTH_COLUMN,
    DATASET_X_SHIFT_COLUMN,
    MainWindow,
)
from hplc_app.models import GradientPoint, PeakRegion, Project, TextAnnotation
from hplc_app.parser import load_ascii_file
from hplc_app.preset_store import (
    load_preset_store,
    load_preset_store_with_metadata,
    preset_store_path,
)
from hplc_app.qt_compat import (
    ITEM_IS_EDITABLE,
    QT_API,
    QtCore,
    QtGui,
    QtPrintSupport,
    QtWidgets,
)
from hplc_app.report import render_analysis_report_pages
from hplc_app.settings_store import ApplicationSettings
from hplc_app.rendering import HIGH_QUALITY, LIGHTWEIGHT
from tests.gcd_fixtures import synthetic_gcd_bytes


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "sample_data"


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
        window.project.dirty = False
        window.close()

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

        def observe_save(path, **_kwargs):
            observed_counts.append(
                len(window._dataset_lines[dataset.id].get_xdata())
            )
            Path(path).write_bytes(b"verified full-data export")

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(window.figure, "savefig", side_effect=observe_save):
                for suffix in ("png", "svg", "pdf"):
                    window._save_figure_file(str(Path(directory) / ("figure." + suffix)))
        self.assertEqual(observed_counts, [dataset.time_min.size] * 3)
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
        self.assertFalse(bool(first.flags() & ITEM_IS_EDITABLE))
        self.assertTrue(
            bool(
                window.dataset_table.item(0, DATASET_LABEL_COLUMN).flags()
                & ITEM_IS_EDITABLE
            )
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
        window.project.method.zoom_axis = "x"
        window._zoom_view(0.8, center_x=8.0, source_axis=window.axes, center_y=original_y[0])
        self.assertLess(window.axes.get_xlim()[1] - window.axes.get_xlim()[0], original_x[1] - original_x[0])
        self.assertEqual(window.axes.get_ylim(), original_y)
        event = SimpleNamespace(button=1, xdata=8.0, dblclick=True)
        window._on_canvas_press(event)
        self.assertAlmostEqual(window.axes.get_xlim()[0], original_x[0], places=6)
        self.assertAlmostEqual(window.axes.get_xlim()[1], original_x[1], places=6)

        before_x = window.axes.get_xlim()
        before_y = window.axes.get_ylim()
        window.project.method.zoom_axis = "y"
        window._zoom_view(0.8, center_x=8.0, source_axis=window.axes, center_y=sum(before_y) / 2.0)
        self.assertEqual(window.axes.get_xlim(), before_x)
        self.assertLess(window.axes.get_ylim()[1] - window.axes.get_ylim()[0], before_y[1] - before_y[0])
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

    def test_split_mode_uses_clicked_time_without_resetting_view(self):
        window = self.make_window()
        window.project.datasets[0].peaks[0].start_min = 5.0
        window.project.datasets[0].peaks[0].end_min = 10.0
        window.axes.set_xlim(4.0, 12.0)
        window.peak_table.selectRow(0)
        window.split_peak_button.setChecked(True)
        window._split_selected_peak_at(7.5)
        self.assertEqual(len(window.project.datasets[0].peaks), 2)
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
        self.assertEqual(window.peak_table.columnCount(), 19)
        note_item = window.peak_table.item(0, 18)
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

    def test_full_x_and_full_y_buttons_reset_only_requested_axes(self):
        window = self.make_window()
        window._plot(preserve_view=False)
        full_x = window.axes.get_xlim()
        full_y1 = window.axes.get_ylim()
        full_y2 = window.axes_right.get_ylim()

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
        window._reset_y_view()
        self.assertEqual(window.axes.get_xlim(), (7.0, 11.0))
        self.assertTrue(np.allclose(window.axes.get_ylim(), full_y1))
        self.assertTrue(np.allclose(window.axes_right.get_ylim(), full_y2))
        self.assertEqual(window.reset_view_button.text(), "全体表示")
        self.assertEqual(window.reset_x_view_button.text(), "X軸全体")
        self.assertEqual(window.reset_y_view_button.text(), "Y軸全体")
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
        self.assertNotIn("mAU·min", help_text)
        help_dialog.reject()
        window.project.dirty = False
        window.close()

    def test_batch_table_is_read_only_and_empty_preset_values_do_not_erase(self):
        window = self.make_window()
        selected = window.project.datasets[0]
        selected.measurement.column_name = "C4"
        selected.measurement.molar_absorptivity_280 = 5500.0
        dialog = BatchMetadataDialog(window.project, selected.id, "ja")
        dialog.presets["partial"] = {"wavelength_nm": 280.0, "column_name": "", "molar_absorptivity_280": None}
        dialog._refresh_presets("partial")
        dialog._apply_preset()
        self.assertFalse(bool(dialog.table.item(0, 8).flags() & ITEM_IS_EDITABLE))
        dialog._accept()
        self.assertEqual(selected.measurement.column_name, "C4")
        self.assertEqual(selected.measurement.molar_absorptivity_280, 5500.0)
        window.project.dirty = False
        window.close()

    def test_batch_table_selects_whole_rows_and_opens_detail_editor(self):
        window = self.make_window()
        dialog = BatchMetadataDialog(
            window.project, window.project.datasets[0].id, "ja"
        )
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
        dialog.x_label.setText("Time after injection (min)")
        dialog.tick_mode_combo.setCurrentIndex(
            dialog.tick_mode_combo.findData("manual")
        )
        dialog.x_major_tick.setValue(2.0)
        dialog.x_minor_tick.setValue(0.5)
        dialog.axis_font_size.setValue(13.0)
        dialog.tick_font_size.setValue(8.0)
        dialog.retention_font_size.setValue(11.0)
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
        major = window.axes.xaxis.get_major_locator().tick_values(0.0, 10.0)
        minor = window.axes.xaxis.get_minor_locator().tick_values(0.0, 10.0)
        self.assertAlmostEqual(major[1] - major[0], 2.0, places=8)
        self.assertAlmostEqual(minor[1] - minor[0], 0.5, places=8)
        dialog.reject()
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
        first._persist_global_presets()
        _conditions, _gradients, metadata = load_preset_store_with_metadata()
        condition_id = metadata["conditions"]["280 nm C4"]["id"]
        self.assertTrue(metadata["conditions"]["280 nm C4"]["created_at"])
        first.project.dirty = False
        first.close()

        second = MainWindow()
        self.assertIn("280 nm C4", second.project.condition_presets)
        self.assertIn("10-90 B", second.project.gradient_presets)
        self.assertEqual(
            second._global_preset_metadata["conditions"]["280 nm C4"]["id"],
            condition_id,
        )
        second.new_project()
        self.assertIn("280 nm C4", second.project.condition_presets)
        self.assertIn("10-90 B", second.project.gradient_presets)
        second.project.dirty = False
        second.close()

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
        full_bounds = window._full_x_bounds()
        self.assertAlmostEqual(window.axes_overview.get_xlim()[0], full_bounds[0], places=6)
        self.assertAlmostEqual(window.axes_overview.get_xlim()[1], full_bounds[1], places=6)
        window.axes.set_xlim(4.0, 12.0)
        window._on_scroll(
            SimpleNamespace(
                button="up",
                xdata=8.0,
                ydata=None,
                inaxes=window.axes_overview,
            )
        )
        self.assertLess(window.axes.get_xlim()[1] - window.axes.get_xlim()[0], 8.0)
        self.assertAlmostEqual(window.axes_overview.get_xlim()[0], full_bounds[0], places=6)
        self.assertAlmostEqual(window.axes_overview.get_xlim()[1], full_bounds[1], places=6)
        self.assertIsNotNone(window._overview_view_patch)
        window._center_detail_on(20.0)
        self.assertAlmostEqual(sum(window.axes.get_xlim()) / 2.0, 20.0, places=5)
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
            dialog.snr.setValue(8.5)
            dialog.max_count.setValue(75)
            dialog._accept()
            self.assertEqual(dialog.import_directory_value, directory)
            self.assertEqual(dialog.save_directory_value, save_directory)
            self.assertEqual(dialog.database_path_value, database_path)
            self.assertEqual(dialog.detection_values["auto_peak_snr_threshold"], 8.5)
            self.assertEqual(dialog.detection_values["auto_peak_max_count"], 75)
            window._import_directory = directory
            with patch.object(
                QtWidgets.QFileDialog,
                "getOpenFileNames",
                return_value=([], ""),
            ) as chooser:
                window.import_ascii()
            self.assertEqual(chooser.call_args.args[2], directory)
            window._save_directory = save_directory
            self.assertEqual(
                window._default_save_path("project.hplcproj"),
                str(Path(save_directory) / "project.hplcproj"),
            )
        window.project.dirty = False
        window.close()

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

    def test_figure_export_is_menu_only_and_report_uses_save_folder(self):
        window = self.make_window()
        self.assertFalse(hasattr(window, "figure_format_combo"))
        self.assertFalse(hasattr(window, "export_figure_button"))
        self.assertIn(window.export_figure_action, window.file_menu.actions())
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
            self.assertEqual(
                chooser.call_args.args[2], str(Path(directory) / "chromatogram.png")
            )
            self.assertIn("*.png", chooser.call_args.args[3])
            self.assertIn("*.svg", chooser.call_args.args[3])
            self.assertIn("*.pdf", chooser.call_args.args[3])
            self.assertTrue(Path(str(destination) + ".png").exists())

            with patch.object(
                QtWidgets.QFileDialog,
                "getSaveFileName",
                return_value=("", ""),
            ) as report_chooser:
                window.export_report()
            self.assertEqual(
                report_chooser.call_args.args[2],
                str(Path(directory) / "analysis_report.pdf"),
            )
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
