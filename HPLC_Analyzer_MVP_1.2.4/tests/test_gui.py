from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from hplc_app.analysis import recalculate_dataset_peaks
from hplc_app.database import database_sections
from hplc_app.dialogs import (
    AxisLabelsDialog,
    BatchMetadataDialog,
    DirectoryImportDialog,
    GradientDialog,
    LabDatabaseDialog,
    LegendComposerDialog,
    MetadataDialog,
    PresetPreviewDialog,
    PresetManagerDialog,
    PreferencesDialog,
    ProjectNamingDialog,
    QuantitationHelpDialog,
    ReportOptionsDialog,
    ReportScopeDialog,
    TextAnnotationDialog,
    WorkDirectoriesDialog,
)
from hplc_app.gui import (
    DATASET_LABEL_COLUMN,
    DATASET_COLUMN_NAME_COLUMN,
    DATASET_RUN_ID_COLUMN,
    DATASET_SOURCE_COLUMN,
    DATASET_TIMESTAMP_COLUMN,
    DATASET_WAVELENGTH_COLUMN,
    DATASET_X_SHIFT_COLUMN,
    MainWindow,
)
from hplc_app.models import (
    FractionRegion,
    GradientPoint,
    PeakRegion,
    Project,
    TextAnnotation,
    VerticalMarker,
    WorkDirectory,
)
from hplc_app.parser import load_ascii_file
from hplc_app.peak_fitting import PeakFitResult
from hplc_app.preset_store import (
    load_preset_store,
    load_preset_store_with_metadata,
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
from hplc_app.report import render_analysis_report_pages
from hplc_app.screen_events import ScreenPointerEvent
from hplc_app.settings_store import ApplicationSettings
from hplc_app.rendering import HIGH_QUALITY, LIGHTWEIGHT
from hplc_app.update_ui import UpdateDownloadDialog, UpdateDownloadWorker
from hplc_app.pyqtgraph_scene import (
    PyQtGraphSceneConsumer,
    pyqtgraph_scene_available,
)
from tests.gcd_fixtures import synthetic_gcd_bytes


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "sample_data"


class RowDropEvent:
    def __init__(self, position):
        self._position = QtCore.QPoint(position)
        self._mime_data = QtCore.QMimeData()
        self.accepted = False
        self.ignored = False

    def mimeData(self):
        return self._mime_data

    def pos(self):
        return self._position

    def position(self):
        return QtCore.QPointF(self._position)

    def acceptProposedAction(self):
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

    @staticmethod
    def drop_event(urls):
        mime_data = QtCore.QMimeData()
        mime_data.setUrls(urls)
        event = Mock()
        event.mimeData.return_value = mime_data
        return event

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
            pixmap = consumer.snapshot()
            self.assertEqual(evidence["counts"]["traces"], 2)
            self.assertEqual(evidence["counts"]["gradients"], 1)
            self.assertEqual(evidence["counts"]["peak_overlays"], 1)
            self.assertEqual(evidence["counts"]["vertical_markers"], 1)
            self.assertEqual(evidence["counts"]["fraction_regions"], 1)
            self.assertEqual(evidence["counts"]["text_annotations"], 1)
            self.assertTrue(evidence["shared_x"])
            self.assertAlmostEqual(evidence["gradient_range"][0], 0.0, places=2)
            self.assertAlmostEqual(evidence["gradient_range"][1], 100.0, places=2)
            self.assertFalse(pixmap.isNull())
            self.assertGreater(pixmap.width(), 0)
            for trace, original in zip(window._screen_scene.traces, source_before):
                self.assertTrue(np.array_equal(trace.x_values, original))
        finally:
            consumer.close()
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

    def test_preset_manager_renames_duplicates_and_deletes_both_kinds(self):
        metadata = {
            "conditions": {
                "C18": {"id": "condition-id", "created_at": "", "updated_at": "", "last_used_at": ""}
            },
            "gradients": {
                "fast": {"id": "gradient-id", "created_at": "", "updated_at": "", "last_used_at": ""}
            },
        }
        dialog = PresetManagerDialog(
            {"C18": {"column_name": "C18"}},
            {"fast": {"gradient": []}},
            metadata,
            "en",
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
        with patch("hplc_app.gui.LegendComposerDialog", return_value=fake), patch(
            "hplc_app.gui.dialog_exec", return_value=True
        ):
            window.edit_legend_composer()
        self.assertEqual(window.project.method.legend_components, ["label"])
        self.assertTrue(window.project.dirty)
        window.undo()
        self.assertEqual(
            window.project.method.legend_components,
            ["run_id", "label", "timestamp"],
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

    def test_fraction_collector_range_draws_interval_lines_and_undoes(self):
        window = self.make_window()
        original_peaks = deepcopy(window.project.datasets[0].peaks)
        window.fraction_interval_spin.setValue(1.5)
        window.fraction_button.setChecked(True)
        self.assertEqual(window._span_selector_mode, "fraction")
        window._on_fraction_span_selected(2.0, 8.0)
        self.assertEqual(len(window.project.fraction_regions), 1)
        region = window.project.fraction_regions[0]
        self.assertEqual((region.start_min, region.end_min), (2.0, 8.0))
        self.assertEqual(region.interval_min, 1.5)
        cyan_lines = [
            line
            for line in window.axes.lines
            if line.get_color() == "#0891b2"
        ]
        positions = sorted(
            {round(float(line.get_xdata()[0]), 6) for line in cyan_lines}
        )
        self.assertEqual(positions, [2.0, 3.5, 5.0, 6.5, 8.0])
        self.assertEqual(window.project.datasets[0].peaks, original_peaks)
        window.clear_fraction_regions()
        self.assertEqual(window.project.fraction_regions, [])
        window.undo()
        self.assertEqual(len(window.project.fraction_regions), 1)
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

    def test_analyte_snapshot_fields_are_saved_from_metadata_dialog(self):
        window = self.make_window()
        dialog = MetadataDialog(window.project.datasets[0], "en")
        dialog.fields["analyte"].setText("LL-37")
        dialog.fields["analyte_id"].setText("analyte-ll37")
        dialog.fields["analyte_aliases"].setText("CAP18, hCAP-18")
        dialog.fields["analyte_source"].setText("UniProt P49913")
        dialog.fields["epsilon_unit"].setText("M^-1 cm^-1")
        dialog._accept()
        metadata = window.project.datasets[0].measurement
        self.assertEqual(metadata.analyte_id, "analyte-ll37")
        self.assertEqual(metadata.analyte_aliases, ["CAP18", "hCAP-18"])
        self.assertEqual(metadata.analyte_source, "UniProt P49913")
        self.assertEqual(metadata.extinction_coefficient_unit, "M^-1 cm^-1")
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

        dialog.table.setCurrentCell(1, 14)
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
        self.assertIn("元に戻せません", question.call_args.args[2])
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
        self.assertEqual(window._undo_stack, [])
        self.assertEqual(window._redo_stack, [])
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

    def test_run_group_and_ungroup_preserve_channel_values_and_are_undoable(self):
        window = self.make_window()
        first, second = window.project.datasets
        first.label = "Authoritative"
        first.measurement.column_name = "C4"
        second.measurement.column_name = "Other"
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
        self.assertIn("Authoritative", question.call_args.args[2])
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(second.label, "Authoritative")
        self.assertEqual(second.measurement.column_name, "C4")
        self.assertEqual(first.measurement.wavelength_nm, first_wavelength)
        self.assertEqual(second.measurement.wavelength_nm, second_wavelength)
        self.assertEqual(len(window.project.runs), 1)
        self.assertEqual(len(window._undo_stack), 1)
        window.undo()
        self.assertEqual(
            [dataset.run_id for dataset in window.project.datasets],
            original_run_ids,
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
        self.assertEqual(window.project.datasets[1].label, "Authoritative")
        self.assertEqual(window.project.datasets[1].measurement.column_name, "C4")
        window.undo()
        self.assertEqual(
            window.project.datasets[0].run_id,
            window.project.datasets[1].run_id,
        )
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
        self.assertFalse(window.project.method.gradient_legend_include_dataset_name)
        labels = window.axes_gradient.get_legend_handles_labels()[1]
        self.assertEqual(labels, ["%B"])

        window.gradient_legend_name_checkbox.setChecked(True)
        self.app.processEvents()
        labels = window.axes_gradient.get_legend_handles_labels()[1]
        self.assertEqual(
            labels, ["%B ({})".format(window.project.datasets[0].legend_label())]
        )
        self.assertTrue(window.project.method.gradient_legend_include_dataset_name)
        window.project.dirty = False
        window.close()

    def test_peak_fit_result_is_saved_plotted_and_undoable(self):
        window = self.make_window()
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
        peak = window.project.datasets[0].peaks[0]
        self.assertEqual(peak.fit_model, "gaussian")
        self.assertEqual(peak.fit_parameters["sigma_min"], 0.5)
        self.assertAlmostEqual(peak.fit_r_squared, 0.999)
        self.assertIsNotNone(
            window._peak_overlay_artists[peak.id]["fit_line"]
        )
        window.undo()
        self.assertEqual(window.project.datasets[0].peaks[0].fit_model, "")
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

    def test_directory_import_dialog_previews_relative_paths_and_empty_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "B.TXT").write_text("b", encoding="utf-8")
            (root / "a.gcd").write_text("a", encoding="utf-8")
            (root / "ignored.csv").write_text("ignored", encoding="utf-8")
            nested = root / "sub"
            nested.mkdir()
            (nested / "c.TXT").write_text("c", encoding="utf-8")

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

            dialog.recursive_checkbox.setChecked(True)
            self.app.processEvents()
            self.assertEqual(
                [
                    dialog.preview_list.item(index).text()
                    for index in range(dialog.preview_list.count())
                ],
                ["a.gcd", "B.TXT", "sub/c.TXT"],
            )
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

    def test_directory_import_sets_group_order_and_last_directory(self):
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
            ):
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
                ["pac1", "pac1"],
            )
            self.assertEqual(
                self.settings.value("paths/last_import_directory"),
                str(root),
            )
            self.assertEqual(FakeProgressDialog.instances[0].values, [0, 1, 2, 2])
            self.assertTrue(FakeProgressDialog.instances[0].closed)
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

            candidates, duplicates, changed, errors = (
                window._work_directory_reload_candidates()
            )
            self.assertEqual(
                candidates,
                [(str(first), "pac1"), (str(second), "pac2")],
            )
            self.assertEqual((duplicates, changed, errors), (0, [], []))

            imported = window._import_chromatogram_paths(
                [path for path, _label in candidates],
                group_labels=[label for _path, label in candidates],
            )
            self.assertEqual(imported, 2)
            self.assertEqual(
                [item.measurement.group for item in window.project.datasets],
                ["pac1", "pac2"],
            )
            candidates, duplicates, changed, errors = (
                window._work_directory_reload_candidates()
            )
            self.assertEqual(candidates, [])
            self.assertEqual(duplicates, 2)
            self.assertEqual((changed, errors), ([], []))

            first.write_bytes((SAMPLES / "191720.TXT").read_bytes())
            candidates, duplicates, changed, errors = (
                window._work_directory_reload_candidates()
            )
            self.assertEqual(candidates, [])
            self.assertEqual(duplicates, 1)
            self.assertEqual(changed, [str(first)])
            self.assertEqual(errors, [])
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
                group_label="cancel-group",
                show_progress=True,
            )

        self.assertEqual(imported, 1)
        self.assertEqual(loader.call_count, 1)
        self.assertEqual(
            window.project.datasets[-1].measurement.group,
            "cancel-group",
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
            ) as report_chooser, patch(
                "hplc_app.gui.dialog_exec", return_value=True
            ):
                window.export_report()
            self.assertEqual(
                report_chooser.call_args.args[2],
                str(Path(directory) / "analysis_report.pdf"),
            )
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

        options_dialog = ReportOptionsDialog("en")
        options_dialog.checkboxes["baseline"].setChecked(False)
        values = options_dialog.option_values()
        self.assertFalse(values["baseline"])
        self.assertTrue(values["retention_time"])
        with patch("hplc_app.gui.ReportOptionsDialog", return_value=options_dialog), patch(
            "hplc_app.gui.dialog_exec", return_value=True
        ):
            options = window._choose_report_options()
        self.assertFalse(options.baseline)
        self.assertTrue(options.retention_time)
        options_dialog.close()
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
