from __future__ import annotations

from copy import deepcopy
from contextlib import closing
import csv
import ast
from datetime import datetime, timezone
import hashlib
import math
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from unittest import mock
import zipfile

import numpy as np

from hplc_app import APP_VERSION, PROJECT_FORMAT_MAJOR, PROJECT_SCHEMA_VERSION
from hplc_app.analysis import (
    _trapz,
    convert_uv,
    detect_peaks,
    gradient_at,
    integrate_peak,
    recalculate_dataset_peaks,
    split_peak_region,
    validate_gradient,
)
from hplc_app.auto_peak_settings import (
    apply_auto_peak_thresholds,
    default_auto_peak_sensitivity_presets,
)
from hplc_app.database import database_sections, export_database_csvs, sync_project_to_database
from hplc_app.exporters import (
    export_chromatogram_csv,
    export_chromatograms_csv,
    export_metadata_csv,
    export_peak_csv,
)
from hplc_app.models import (
    AnalysisMethod,
    Dataset,
    FractionRegion,
    GradientPoint,
    LINE_STYLE_IDS,
    MeasurementMetadata,
    PeakRegion,
    Project,
    Run,
    Solvent,
    TextAnnotation,
    WorkDirectory,
    VerticalMarker,
    normalize_line_style,
)
from hplc_app.naming import build_project_filename, suggest_project_name_parts, timestamped_filename
from hplc_app.gcd_parser import GcdParseError, parse_gcd_bytes, parse_gcd_streams
from hplc_app.parser import (
    dataset_from_bytes,
    infer_y_axis,
    load_ascii_file,
    load_chromatogram_file,
)
from hplc_app.peak_fitting import (
    PeakFitResult,
    _emg_true_amplitude,
    _model_grid,
    detect_saturated_span,
    emg_profile,
    evaluate_fit_profile,
    fit_peak,
    fit_saturated_peak,
    fitted_apex_min,
    fitted_area_uv_min,
    fitted_fwhm_min,
    fitted_peak_from_result,
    gaussian_profile,
    is_saturation_corrected,
    mirror_fitted_peak_for_legacy,
)
from hplc_app.preset_store import (
    apply_preset_operation,
    build_preset_package,
    filter_preset_names,
    load_complete_preset_store,
    load_preset_store,
    load_preset_store_with_metadata,
    merge_preset_sources,
    normalize_preset_metadata,
    merge_preset_package,
    record_preset_saved,
    record_preset_used,
    save_preset_store,
    stable_preset_names,
)
from hplc_app.project_io import ProjectError, load_project, save_project
from hplc_app.project_migrations import (
    ProjectMigrationError,
    migrate_project_manifest,
)
from hplc_app.report import (
    ReportOptions,
    analysis_report_figures,
    export_analysis_report_pdf,
    render_analysis_report_pages,
)
from hplc_app.rendering import (
    CHANNEL_COLOR_PALETTES,
    HIGH_QUALITY,
    LIGHTWEIGHT,
    ScreenRendererCapabilities,
    default_render_quality,
    default_trace_color,
    matplotlib_line_style,
    minmax_decimate,
    screen_series,
)
from hplc_app.renderer_benchmark import (
    RendererWorkload,
    arrays_digest,
    benchmark_backend,
    benchmark_suite,
    synthetic_chromatograms,
)
from hplc_app.renderer_parity import (
    FEATURES,
    three_axis_ranges_are_independent,
    unavailable_parity_report,
)
from hplc_app.pyqtgraph_scene import (
    OptionalRendererUnavailable,
    PyQtGraphSceneConsumer,
    pyqtgraph_scene_available,
)
from hplc_app.screen_scene import compose_base_screen_scene
from hplc_app.screen_events import (
    ScreenPointerEvent,
    integration_peak_hit_target,
    normalize_pointer_event,
)
from hplc_app.screen_navigation import (
    ScreenOverviewState,
    ScreenViewHistory,
    ScreenViewState,
    axis_pan_view,
    begin_axis_pan,
    compose_overview_state,
)
from hplc_app.timestamps import (
    ACQUISITION_TIMESTAMP_SOURCE_KEY,
    GCD_FILETIME_UTC_KEY,
    TIMESTAMP_SOURCE_FILE_MTIME,
    TIMESTAMP_SOURCE_FILENAME,
    TIMESTAMP_SOURCE_GCD_FILETIME,
    TIMESTAMP_SOURCE_UNAVAILABLE,
    TIMESTAMP_SOURCE_VENDOR,
    acquisition_timestamp,
    timestamp_from_filename,
)
from hplc_app.update_check import (
    check_for_updates,
    compare_semver,
    parse_semver,
    parse_stable_version,
)
from hplc_app.updater_download import (
    DownloadCancelled,
    fetch_bounded,
    normalize_signer_thumbprints,
    official_release_asset_url,
    parse_sha256_manifest,
    probe_authenticode,
    select_canonical_release_assets,
    signer_policy_result,
    stage_verified_installer,
)
from hplc_app.settings_store import (
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
    SETTING_SPECS,
    UI_LANGUAGE,
    default_screen_renderer,
)
from scripts.windows7_import_preflight import EVENT_LOG_COMMAND, PROBES
from scripts.inspect_gcd import _safe_dump_name
from scripts.create_upgrade_test_fixture import create_fixture
from scripts.installer_data_guard import (
    build_snapshot as build_installer_data_snapshot,
    compare_snapshots as compare_installer_data_snapshots,
    read_snapshot as read_installer_data_snapshot,
    write_snapshot as write_installer_data_snapshot,
)
from scripts.verify_windows7_x86 import PE_MACHINE_I386, read_pe_machine
from scripts.verify_windows7_offline_bundle import (
    EXPECTED_RELATIVE_FILES as WINDOWS7_OFFLINE_FILES,
    read_manifest as read_windows7_offline_manifest,
    verify_bundle as verify_windows7_offline_bundle,
)
from scripts.verify_windows7_wheelhouse import verify_dependency_closure
from scripts.verify_windows11_x64 import (
    PE_MACHINE_AMD64,
    read_pe_machine as read_pe_machine_x64,
)
from scripts.verify_installer import (
    APP_ID as INSTALLER_APP_ID,
    verify_installer_script,
    verify_setup_executable,
)
from tests.gcd_fixtures import (
    synthetic_gcd_bytes,
    with_difat_cycle,
    with_u16,
    with_u32,
    with_u64,
)
from scripts.package_windows7_offline_bundle import (
    ROOT_DIRECTORIES,
    ROOT_FILES,
    archive_root_name,
    default_archive_path,
    iter_source_files,
)
from scripts.artifact_names import artifact_filename, installer_basename
from scripts.read_version import (
    VersionError,
    read_version,
    windows_numeric_version,
)
from scripts.release_checksums import (
    read_sha256sums,
    verify_sha256sums,
    write_sha256sums,
)
from scripts.release_consistency import (
    read_pinned_requirements,
    verify_offline_archive,
    verify_release_assets,
    verify_source_consistency,
)
from scripts.write_windows_version_info import (
    expected_version_strings,
    render_version_info,
    write_version_info,
)


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "sample_data"


class ParserTests(unittest.TestCase):
    def test_real_shimadzu_files(self):
        expectations = {
            "210601.TXT": (54001, 1, "210601.GCM"),
            "191720.TXT": (54002, 15, "191720.GCM"),
            "225120.TXT": (54001, 8, "225120.GCM"),
        }
        for filename, (points, peaks, method) in expectations.items():
            dataset = load_ascii_file(str(SAMPLES / filename))
            self.assertEqual(dataset.time_min.size, points)
            self.assertEqual(dataset.intensity_uv.size, points)
            self.assertEqual(len(dataset.source_peak_table), peaks)
            self.assertEqual(dataset.measurement.method_name, method)
            self.assertEqual(dataset.measurement.instrument_name, "装置名1")
            self.assertEqual(len(dataset.sha256), 64)

    def test_gcd_stream_values_are_parsed_without_resampling(self):
        status = bytearray(12)
        struct.pack_into("<I", status, 0, 500)
        struct.pack_into("<I", status, 8, 3)
        peak_table = bytearray(4 + 236 + 4)
        struct.pack_into("<I", peak_table, 0, 1)
        struct.pack_into("<I", peak_table, 8, 16)
        struct.pack_into("<I", peak_table, 12, 576900)
        struct.pack_into("<d", peak_table, 16, 244213.4)
        struct.pack_into("<d", peak_table, 24, 4084.125)
        struct.pack_into("<I", peak_table, 48, 540400)
        struct.pack_into("<I", peak_table, 52, 723000)
        struct.pack_into("<d", peak_table, 56, 59.0)
        struct.pack_into("<d", peak_table, 156, 81.83238983154297)
        parsed = parse_gcd_streams(
            {
                "Status": bytes(status),
                "Intensity Data": struct.pack("<3d", -104.0, 12.5, 300.0),
                "Peak Table": bytes(peak_table),
                "System": "装置名1".encode("cp932") + b"\0",
            }
        )
        np.testing.assert_array_equal(parsed.intensity_uv, [-104.0, 12.5, 300.0])
        np.testing.assert_allclose(parsed.time_min, [0.0, 0.5 / 60.0, 1.0 / 60.0])
        self.assertEqual(parsed.metadata["Chromatogram.Interval(msec)"], "500")
        self.assertEqual(parsed.metadata["Configuration.Instrument Name"], "装置名1")
        self.assertEqual(parsed.peak_table[0]["R.Time"], "9.615")
        self.assertEqual(parsed.peak_table[0]["Area"], "244213")
        self.assertEqual(parsed.peak_table[0]["Conc."], "81.83239")
        for field in ("k'", "Plate #", "Plate Ht.", "Tailing", "Resolution", "Sep.Factor"):
            self.assertEqual(parsed.peak_table[0][field], "")

    def test_known_gcd_file_property_timestamp_is_explicit_and_version_gated(self):
        status = bytearray(12)
        struct.pack_into("<I", status, 0, 500)
        struct.pack_into("<I", status, 8, 3)
        expected_utc = datetime(2026, 8, 9, 17, 1, 6)
        delta = expected_utc - datetime(1601, 1, 1)
        filetime_ticks = (
            (delta.days * 86400 + delta.seconds) * 10_000_000
            + delta.microseconds * 10
        )
        file_property = bytearray(514)
        file_property[4:12] = b"2.32.00\0"
        struct.pack_into("<Q", file_property, 506, filetime_ticks)
        streams = {
            "Status": bytes(status),
            "Intensity Data": struct.pack("<3d", -104.0, 12.5, 300.0),
            "File Property": bytes(file_property),
        }

        parsed = parse_gcd_streams(streams)
        self.assertEqual(
            parsed.metadata[GCD_FILETIME_UTC_KEY], "2026-08-09T17:01:06Z"
        )
        expected_local = (
            expected_utc.replace(tzinfo=timezone.utc)
            .astimezone()
            .replace(tzinfo=None)
            .isoformat(timespec="seconds")
        )
        self.assertEqual(
            acquisition_timestamp(parsed.metadata, "ambiguous.gcd"),
            expected_local,
        )
        self.assertEqual(
            parsed.metadata[ACQUISITION_TIMESTAMP_SOURCE_KEY],
            TIMESTAMP_SOURCE_GCD_FILETIME,
        )

        unknown_version = bytearray(file_property)
        unknown_version[4:12] = b"9.99.99\0"
        streams["File Property"] = bytes(unknown_version)
        self.assertNotIn(
            GCD_FILETIME_UTC_KEY,
            parse_gcd_streams(streams).metadata,
        )

    def test_synthetic_cfb_exercises_fat_directory_and_mini_streams(self):
        raw = synthetic_gcd_bytes()
        parsed = parse_gcd_bytes(raw)
        np.testing.assert_array_equal(parsed.intensity_uv, [-104.0, 12.5, 300.0])
        np.testing.assert_allclose(
            parsed.time_min, [0.0, 0.5 / 60.0, 1.0 / 60.0]
        )
        self.assertEqual(parsed.metadata["Chromatogram.Interval(msec)"], "500")
        self.assertEqual(parsed.peak_table, [])
        dataset = dataset_from_bytes(raw, source_path="synthetic.gcd")
        with tempfile.TemporaryDirectory() as directory:
            project_path = os.path.join(directory, "synthetic-gcd.hplcproj")
            save_project(project_path, Project(datasets=[dataset]))
            restored = load_project(project_path).datasets[0]
        self.assertEqual(restored.raw_bytes, raw)
        self.assertEqual(restored.sha256, dataset.sha256)
        np.testing.assert_array_equal(restored.time_min, dataset.time_min)
        np.testing.assert_array_equal(restored.intensity_uv, dataset.intensity_uv)

    def test_cfb_rejects_invalid_headers(self):
        valid = synthetic_gcd_bytes()
        invalid_files = {
            "truncated header": valid[:511],
            "signature": b"not-cfb!" + valid[8:],
            "version": with_u16(valid, 26, 5),
            "sector shift": with_u16(valid, 30, 15),
            "version/sector mismatch": with_u16(valid, 30, 12),
            "mini sector shift": with_u16(valid, 32, 7),
            "missing FAT": with_u32(valid, 44, 0),
        }
        for description, raw in invalid_files.items():
            with self.subTest(description=description):
                with self.assertRaises(GcdParseError):
                    parse_gcd_bytes(raw)

    def test_cfb_rejects_cyclic_fat_and_mini_fat_chains(self):
        valid = synthetic_gcd_bytes()
        # FAT sector 0, entry 1: the directory sector points to itself.
        cyclic_fat = with_u32(valid, 512 + 1 * 4, 1)
        # mini-FAT sector 2, entry 0: the Status stream points to itself.
        cyclic_mini_fat = with_u32(valid, (2 + 1) * 512, 0)
        for description, raw in (
            ("FAT", cyclic_fat),
            ("mini FAT", cyclic_mini_fat),
        ):
            with self.subTest(description=description):
                with self.assertRaisesRegex(GcdParseError, "cyclic"):
                    parse_gcd_bytes(raw)

    def test_cfb_rejects_stream_size_larger_than_its_chain(self):
        valid = synthetic_gcd_bytes()
        directory_offset = (1 + 1) * 512
        status_size_offset = directory_offset + 128 + 120
        malformed = with_u64(valid, status_size_offset, 65)
        with self.assertRaisesRegex(GcdParseError, "shorter than declared"):
            parse_gcd_bytes(malformed)

    def test_cfb_rejects_inflated_difat_count_without_scanning(self):
        malformed = with_u32(synthetic_gcd_bytes(), 72, 0xFFFFFFFE)
        started = time.monotonic()
        with self.assertRaisesRegex(GcdParseError, "DIFAT sector count"):
            parse_gcd_bytes(malformed)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_cfb_rejects_cyclic_difat_chain_without_hanging(self):
        malformed = with_difat_cycle(synthetic_gcd_bytes())
        started = time.monotonic()
        with self.assertRaisesRegex(GcdParseError, "Cyclic OLE DIFAT"):
            parse_gcd_bytes(malformed)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_gcd_inspector_sanitizes_dump_filenames(self):
        self.assertEqual(_safe_dump_name("Status"), "Status")
        self.assertEqual(_safe_dump_name(".."), "unnamed")
        self.assertEqual(_safe_dump_name("../outside"), "_outside")
        self.assertEqual(_safe_dump_name("CON.txt"), "_CON.txt")

    def test_gcd_stream_size_mismatch_fails_clearly(self):
        status = bytearray(12)
        struct.pack_into("<I", status, 0, 100)
        struct.pack_into("<I", status, 8, 3)
        with self.assertRaisesRegex(GcdParseError, "intensity size mismatch"):
            parse_gcd_streams({"Status": bytes(status), "Intensity Data": struct.pack("<2d", 1, 2)})

    def test_supplied_gcd_files_match_their_ascii_exports(self):
        rawdata = ROOT / "rawdata"
        gcd_files = sorted(rawdata.rglob("*.gcd")) if rawdata.is_dir() else []
        if not gcd_files:
            self.skipTest("rawdata GCD fixtures are not present")
        for gcd_path in gcd_files:
            txt_path = gcd_path.with_suffix(".TXT")
            self.assertTrue(txt_path.is_file(), str(txt_path))
            gcd = load_chromatogram_file(str(gcd_path))
            ascii_export = load_ascii_file(str(txt_path))
            self.assertEqual(gcd.time_min.size, ascii_export.time_min.size)
            np.testing.assert_allclose(gcd.time_min, ascii_export.time_min, atol=0.0000051, rtol=0)
            rounded = np.where(
                gcd.intensity_uv >= 0,
                np.floor(gcd.intensity_uv + 0.5),
                np.ceil(gcd.intensity_uv - 0.5),
            )
            np.testing.assert_array_equal(rounded, ascii_export.intensity_uv)
            self.assertEqual(len(gcd.source_peak_table), len(ascii_export.source_peak_table))
            for gcd_peak, txt_peak in zip(gcd.source_peak_table, ascii_export.source_peak_table):
                for field in (
                    "Peak#",
                    "R.Time",
                    "I.Time",
                    "F.Time",
                    "Area",
                    "Height",
                    "A/H",
                    "Conc.",
                    "Mark",
                ):
                    self.assertEqual(gcd_peak[field], txt_peak[field])
            self.assertEqual(
                gcd.measurement.acquisition_datetime,
                ascii_export.measurement.acquisition_datetime,
            )
            self.assertEqual(
                gcd.source_metadata[ACQUISITION_TIMESTAMP_SOURCE_KEY],
                TIMESTAMP_SOURCE_GCD_FILETIME,
            )
        original = load_chromatogram_file(str(gcd_files[0]))
        with tempfile.TemporaryDirectory() as directory:
            project_path = os.path.join(directory, "gcd-roundtrip.hplcproj")
            save_project(project_path, Project(datasets=[original]))
            restored = load_project(project_path).datasets[0]
            self.assertEqual(restored.raw_bytes, original.raw_bytes)
            self.assertEqual(restored.sha256, original.sha256)
            np.testing.assert_array_equal(restored.time_min, original.time_min)
            np.testing.assert_array_equal(restored.intensity_uv, original.intensity_uv)
    def test_acquisition_timestamp_priority_is_explicit_and_unambiguous(self):
        metadata = {
            "Sample Information.Acquisition Date": "2026/05/07 19:33:54",
            "Header.Output Date": "2099/12/31",
            "Header.Output Time": "23:59:59",
        }
        self.assertEqual(
            acquisition_timestamp(metadata, "20260508_005353.TXT"),
            "2026-05-07T19:33:54",
        )
        self.assertEqual(
            metadata[ACQUISITION_TIMESTAMP_SOURCE_KEY], TIMESTAMP_SOURCE_VENDOR
        )
        self.assertEqual(
            acquisition_timestamp(
                {"Sample Information.Acquisition Date": "vendor-local-time"},
                "20260508_005353.TXT",
            ),
            "vendor-local-time",
        )
        self.assertEqual(
            acquisition_timestamp(
                {"Header.Output Date": "2026/05/08"},
                "20260507_193354.TXT",
            ),
            "2026-05-07T19:33:54",
        )
        self.assertEqual(
            acquisition_timestamp(
                {
                    "Header.Output Date": "2026/05/08",
                    "Header.Output Time": "00:53:53",
                },
                "210601.TXT",
            ),
            "",
        )
        for filename in (
            "20260507-193354.gcd",
            "2026-05-07_19-33-54.TXT",
            "2026_05_07_19_33_54.txt",
        ):
            self.assertEqual(
                timestamp_from_filename(filename), "2026-05-07T19:33:54"
            )
        for ambiguous in (
            "210601.TXT",
            "20260507193354.TXT",
            "sample_20260507_193354.TXT",
            "20261340_996099.TXT",
        ):
            self.assertEqual(timestamp_from_filename(ambiguous), "")

        filename_metadata = {}
        self.assertEqual(
            acquisition_timestamp(
                filename_metadata,
                "20260507_193354.gcd",
                "2099-01-02T03:04:05",
            ),
            "2026-05-07T19:33:54",
        )
        self.assertEqual(
            filename_metadata[ACQUISITION_TIMESTAMP_SOURCE_KEY],
            TIMESTAMP_SOURCE_FILENAME,
        )
        modified_metadata = {}
        self.assertEqual(
            acquisition_timestamp(
                modified_metadata,
                "ambiguous.gcd",
                "2026-08-10T04:05:06",
            ),
            "2026-08-10T04:05:06",
        )
        self.assertEqual(
            modified_metadata[ACQUISITION_TIMESTAMP_SOURCE_KEY],
            TIMESTAMP_SOURCE_FILE_MTIME,
        )
        unavailable_metadata = {}
        self.assertEqual(
            acquisition_timestamp(unavailable_metadata, "ambiguous.gcd"), ""
        )
        self.assertEqual(
            unavailable_metadata[ACQUISITION_TIMESTAMP_SOURCE_KEY],
            TIMESTAMP_SOURCE_UNAVAILABLE,
        )

    def test_gcd_file_mtime_fallback_and_channel_axis_defaults(self):
        raw = synthetic_gcd_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ch2_directory = root / "Batch_CH2"
            ch2_directory.mkdir()
            gcd_path = ch2_directory / "ambiguous.gcd"
            gcd_path.write_bytes(raw)
            modified_epoch = 1_786_332_306
            os.utime(str(gcd_path), (modified_epoch, modified_epoch))
            dataset = load_chromatogram_file(str(gcd_path))

        self.assertEqual(
            dataset.measurement.acquisition_datetime,
            datetime.fromtimestamp(modified_epoch).isoformat(timespec="seconds"),
        )
        self.assertEqual(
            dataset.source_metadata[ACQUISITION_TIMESTAMP_SOURCE_KEY],
            TIMESTAMP_SOURCE_FILE_MTIME,
        )
        self.assertEqual(dataset.y_axis, 2)
        self.assertEqual(dataset.measurement.group, "")
        self.assertEqual(dataset.raw_bytes, raw)
        project = Project(datasets=[dataset])
        self.assertEqual(
            dataset.run_id,
            "%s_1_ambiguous"
            % dataset.measurement.acquisition_datetime.replace("-", "")
            .replace("T", "_")
            .replace(":", ""),
        )
        self.assertEqual(project.run_for(dataset).timestamp,
                         dataset.measurement.acquisition_datetime)
        self.assertEqual(
            infer_y_axis("C:/runs/ch1/sample.gcd"), 1
        )
        self.assertEqual(
            infer_y_axis("C:/runs/BATCH_ch2/sample.gcd"), 2
        )
        self.assertEqual(
            infer_y_axis("C:/runs/ch1/ch2/sample.gcd"), 1
        )
        self.assertEqual(
            infer_y_axis("C:/runs/neutral/ch2.gcd"), 1
        )
        self.assertEqual(
            infer_y_axis("C:/research1/neutral/sample.gcd"), 1
        )

    def test_ascii_import_keeps_label_separate_from_run_timestamp(self):
        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        self.assertEqual(dataset.label, "210601")
        self.assertEqual(
            dataset.measurement.acquisition_datetime, "2026-05-07T19:33:54"
        )
        project = Project(datasets=[dataset])
        run = project.run_for(dataset)
        self.assertEqual(run.timestamp, "2026-05-07T19:33:54")
        self.assertEqual(dataset.label, "210601")


class AnalysisTests(unittest.TestCase):
    def synthetic_dataset(self):
        time = np.linspace(0.0, 10.0, 10001)
        baseline = 100.0 + 5.0 * time
        peak = 1000.0 * np.exp(-0.5 * ((time - 5.0) / 0.3) ** 2)
        dataset = Dataset(label="synthetic", time_min=time, intensity_uv=baseline + peak, raw_bytes=b"test")
        dataset.measurement.aux_range_au_per_v = 2.0
        dataset.measurement.wavelength_nm = 280.0
        dataset.measurement.molar_absorptivity_280 = 10000.0
        dataset.measurement.cell_path_length_cm = 1.0
        dataset.measurement.flow_rate_ml_min = 1.0
        dataset.measurement.molecular_weight_g_mol = 10000.0
        dataset.measurement.gradient = [
            GradientPoint(0.0, 100.0, 0.0, 0.0, 0.0, 1.0),
            GradientPoint(10.0, 0.0, 100.0, 0.0, 0.0, 1.0),
        ]
        return dataset

    def test_uv_to_mau_conversion(self):
        converted = convert_uv(np.asarray([170304.0]), 2.0, "mAU")
        self.assertAlmostEqual(float(converted[0]), 340.608, places=6)

    def test_lightweight_envelope_preserves_narrow_extrema(self):
        time = np.linspace(0.0, 100.0, 100001)
        signal = np.zeros(time.size)
        signal[12345] = 9876.5
        signal[54321] = -4321.25
        reduced_time, reduced_signal = minmax_decimate(time, signal, 2000)
        self.assertLessEqual(reduced_time.size, 2000)
        self.assertEqual(reduced_time[0], time[0])
        self.assertEqual(reduced_time[-1], time[-1])
        self.assertIn(9876.5, reduced_signal)
        self.assertIn(-4321.25, reduced_signal)

    def test_screen_decimation_never_changes_analysis_results(self):
        dataset = self.synthetic_dataset()
        dataset.peaks = [
            integrate_peak(dataset, PeakRegion(start_min=3.5, end_min=6.5))
        ]
        recalculate_dataset_peaks(dataset)
        peak = dataset.peaks[0]
        before = (
            peak.raw_area_uv_sec,
            peak.area_mau_sec,
            peak.retention_time_min,
            peak.fwhm_min,
            peak.area_percent,
        )
        original_time = dataset.time_min.copy()
        original_signal = dataset.intensity_uv.copy()
        screen_time, _screen_signal = screen_series(
            dataset.time_min,
            dataset.intensity_uv,
            LIGHTWEIGHT,
            pixel_width=800,
        )
        self.assertLess(screen_time.size, dataset.time_min.size)
        recalculate_dataset_peaks(dataset)
        after = dataset.peaks[0]
        self.assertTrue(np.array_equal(dataset.time_min, original_time))
        self.assertTrue(np.array_equal(dataset.intensity_uv, original_signal))
        self.assertEqual(
            (
                after.raw_area_uv_sec,
                after.area_mau_sec,
                after.retention_time_min,
                after.fwhm_min,
                after.area_percent,
            ),
            before,
        )

    def test_render_quality_defaults_are_os_specific(self):
        self.assertEqual(
            default_render_quality("win32", (6, 1, 7601)), LIGHTWEIGHT
        )
        self.assertEqual(
            default_render_quality("win32", (10, 0, 22631)), HIGH_QUALITY
        )
        self.assertEqual(default_render_quality("linux", (6, 1)), HIGH_QUALITY)
        values = np.arange(10.0)
        full_x, full_y = screen_series(
            values, values * 2.0, HIGH_QUALITY, pixel_width=10
        )
        self.assertTrue(np.array_equal(full_x, values))
        self.assertTrue(np.array_equal(full_y, values * 2.0))

    def test_screen_renderer_capabilities_are_backend_neutral_and_immutable(self):
        capabilities = ScreenRendererCapabilities(
            backend_id="prototype",
            supports_native_snapshot=True,
        )
        self.assertEqual(capabilities.backend_id, "prototype")
        self.assertTrue(capabilities.supports_native_snapshot)
        self.assertFalse(capabilities.supports_matplotlib_artists)
        with self.assertRaises((AttributeError, TypeError)):
            capabilities.backend_id = "changed"

    def test_screen_pointer_event_normalizes_axis_roles_and_coordinates(self):
        class Transform:
            def __init__(self, offset):
                self.offset = offset

            def inverted(self):
                return self

            def transform(self, values):
                return values[0] + self.offset, values[1] - self.offset

        y1 = SimpleNamespace(transData=Transform(1.0))
        y2 = SimpleNamespace(transData=Transform(10.0))
        raw = SimpleNamespace(
            button="up",
            inaxes=y2,
            x=20.0,
            y=30.0,
            dblclick=False,
            key="ctrl",
        )
        event = normalize_pointer_event(
            raw,
            {"y1": y1, "y2": y2, "gradient": None},
            hit_region="plot_y2",
        )
        self.assertIsInstance(event, ScreenPointerEvent)
        self.assertEqual(event.axis_role, "y2")
        self.assertEqual(event.hit_region, "plot_y2")
        self.assertEqual(event.data_for("y1"), (21.0, 29.0))
        self.assertEqual(event.data_for("y2"), (30.0, 20.0))
        self.assertEqual(event.data_for("outside"), (None, None))
        targeted = event.with_hit_target("annotation", "note-1")
        self.assertEqual(
            (targeted.hit_kind, targeted.hit_id), ("annotation", "note-1")
        )
        self.assertEqual((event.hit_kind, event.hit_id), ("", ""))
        partial = normalize_pointer_event(
            SimpleNamespace(button=1, xdata=8.5, ydata=None),
            {"y1": y1},
        )
        self.assertEqual(partial.data_for("y1"), (8.5, None))
        with self.assertRaises((AttributeError, TypeError)):
            event.axis_role = "changed"
        source = (ROOT / "hplc_app" / "screen_events.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("matplotlib", source.casefold())

    def test_integration_peak_hit_target_uses_shared_overlap_rule(self):
        targets = (
            ("wide", "y1", 2.0, 10.0),
            ("narrow", "y1", 4.0, 8.0),
            ("frontmost-tie", "y1", 8.0, 4.0),
            ("other-axis", "y2", 6.0, 7.0),
        )
        self.assertEqual(
            integration_peak_hit_target(targets, "y1", 6.0),
            ("integration_peak", "frontmost-tie"),
        )
        self.assertEqual(
            integration_peak_hit_target(targets, "y2", 6.5),
            ("integration_peak", "other-axis"),
        )
        self.assertEqual(
            integration_peak_hit_target(targets, "plot", 6.5),
            ("integration_peak", "other-axis"),
        )
        self.assertEqual(integration_peak_hit_target(targets, "y1", 20.0), ("", ""))
        self.assertEqual(integration_peak_hit_target(targets, "outside", 6.0), ("", ""))

    def test_axis_pan_contract_shifts_only_the_targeted_view_limits(self):
        initial = ScreenViewState(
            x=(0.0, 100.0),
            y1=(0.0, 1000.0),
            y2=(-100.0, 100.0),
            gradient=(10.0, 90.0),
        )
        session = begin_axis_pan(
            ScreenPointerEvent(
                button=1,
                hit_region="plot_y2",
                canvas_x=10.0,
                canvas_y=20.0,
            ),
            initial,
        )
        self.assertIsNotNone(session)
        shifted = axis_pan_view(
            session,
            ScreenPointerEvent(canvas_x=34.0, canvas_y=50.0),
            canvas_width=240.0,
            canvas_height=300.0,
        )
        self.assertEqual(shifted.x, (-10.0, 90.0))
        self.assertEqual(shifted.y1, initial.y1)
        self.assertEqual(shifted.y2, (-120.0, 80.0))
        self.assertEqual(shifted.gradient, initial.gradient)
        self.assertIsNone(
            begin_axis_pan(
                ScreenPointerEvent(
                    button=1,
                    hit_region="outside",
                    canvas_x=0.0,
                    canvas_y=0.0,
                ),
                initial,
            )
        )
        source = (ROOT / "hplc_app" / "screen_navigation.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("matplotlib", source.casefold())

    def test_screen_view_history_supports_home_back_forward_and_branching(self):
        home = ScreenViewState(x=(0.0, 100.0), y1=(0.0, 1000.0))
        zoomed = ScreenViewState(x=(20.0, 60.0), y1=(100.0, 500.0))
        history = ScreenViewHistory(max_entries=3)
        history.ensure_home(home)
        self.assertEqual(
            history.capabilities(home), {"back": False, "forward": False}
        )
        self.assertEqual(history.navigate("back", zoomed), home)
        self.assertEqual(
            history.capabilities(home), {"back": False, "forward": True}
        )
        self.assertEqual(history.navigate("forward", home), zoomed)
        self.assertEqual(history.navigate("home", zoomed), home)

        history.record_before_change(home)
        self.assertEqual(
            history.capabilities(home), {"back": False, "forward": False}
        )
        self.assertIsNone(history.navigate("invalid", home))

    def test_overview_state_orders_without_moving_detail_range(self):
        state = compose_overview_state(
            enabled=True,
            full_x=(20.0, 0.0),
            detail_x=(18.0, 25.0),
        )
        self.assertIsInstance(state, ScreenOverviewState)
        self.assertEqual(state.full_x, (0.0, 20.0))
        self.assertEqual(state.detail_x, (18.0, 25.0))
        self.assertTrue(state.enabled)
        with self.assertRaises(ValueError):
            compose_overview_state(True, (0.0, float("nan")), (1.0, 2.0))

    def test_base_screen_scene_preserves_trace_axis_legend_gradient_and_raw_data(self):
        first = self.synthetic_dataset()
        first.line_style = "dashed"
        first.x_shift_min = 0.25
        first.offset = 12.0
        second = self.synthetic_dataset()
        second.id = "second-dataset"
        second.run_id = "second-run"
        second.label = "secondary"
        second.y_axis = 2
        second.measurement.wavelength_nm = 214.0
        invalid = self.synthetic_dataset()
        invalid.id = "invalid-dataset"
        invalid.run_id = "invalid-run"
        invalid.measurement.aux_range_au_per_v = None
        hidden = self.synthetic_dataset()
        hidden.id = "hidden-dataset"
        hidden.run_id = "hidden-run"
        hidden.visible = False
        project = Project(datasets=[first, second, invalid, hidden])
        project.method.display_unit = "mAU"
        project.method.show_gradient_b = True
        project.method.gradient_legend_include_dataset_name = True
        raw_before = (
            first.time_min.copy(),
            first.intensity_uv.copy(),
            second.time_min.copy(),
            second.intensity_uv.copy(),
        )
        scene = compose_base_screen_scene(
            project,
            selected_dataset_id=second.id,
            color_resolver=lambda dataset, index: "#%06d" % (index + 1),
        )
        self.assertEqual([trace.axis_id for trace in scene.traces], ["y1", "y2"])
        self.assertEqual(
            [trace.dataset_id for trace in scene.traces], [first.id, second.id]
        )
        self.assertEqual([trace.color for trace in scene.traces], ["#000001", "#000002"])
        self.assertEqual([trace.line_style for trace in scene.traces], ["dashed", "solid"])
        self.assertEqual(scene.traces[0].x_values[0], 0.25)
        self.assertIn("secondary", scene.traces[1].label)
        self.assertIsNotNone(scene.gradient)
        self.assertEqual(scene.gradient.dataset_id, second.id)
        self.assertEqual(scene.gradient.y_values.tolist(), [0.0, 100.0])
        self.assertIn("%B (", scene.gradient.label)
        self.assertFalse(scene.traces[0].x_values.flags.writeable)
        for current, original in zip(
            (first.time_min, first.intensity_uv, second.time_min, second.intensity_uv),
            raw_before,
        ):
            self.assertTrue(np.array_equal(current, original))

    def test_screen_scene_composes_selected_peak_baseline_fit_and_label(self):
        dataset = self.synthetic_dataset()
        peak = integrate_peak(dataset, PeakRegion(start_min=3.5, end_min=6.5))
        fitted = fit_peak(dataset, peak, "gaussian")
        peak.fit_model = fitted.model
        peak.fit_parameters = dict(fitted.parameters)
        peak.fit_retention_time_min = fitted.retention_time_min
        peak.fit_rmse_uv = fitted.rmse_uv
        peak.fit_r_squared = fitted.r_squared
        peak.fit_aic = fitted.aic
        dataset.peaks = [peak]
        dataset.x_shift_min = 0.4
        dataset.offset = 7.0
        project = Project(datasets=[dataset])
        project.method.show_integration_areas = True
        project.method.show_retention_labels = True
        scene = compose_base_screen_scene(
            project,
            selected_dataset_id=dataset.id,
            selected_dataset_ids=[dataset.id],
            selected_peak_ids=[peak.id],
            color_resolver=lambda _dataset, _index: "#123456",
        )
        self.assertEqual(len(scene.peak_overlays), 1)
        overlay = scene.peak_overlays[0]
        self.assertTrue(overlay.is_selected)
        self.assertEqual(overlay.color, "#f59e0b")
        self.assertAlmostEqual(overlay.start_x, 3.9)
        self.assertAlmostEqual(overlay.end_x, 6.9)
        self.assertIsNotNone(overlay.baseline_x)
        self.assertIsNotNone(overlay.fit_x)
        self.assertEqual(overlay.baseline_x.size, overlay.baseline_y.size)
        self.assertEqual(overlay.fit_x.size, overlay.fit_y.size)
        self.assertFalse(overlay.fit_y.flags.writeable)
        self.assertEqual(overlay.label_text, "5.40")
        self.assertAlmostEqual(overlay.label_x, 5.4, places=2)

    def test_screen_scene_composes_markers_fractions_and_visible_annotations(self):
        visible = self.synthetic_dataset()
        hidden = self.synthetic_dataset()
        hidden.id = "hidden-scene-dataset"
        hidden.run_id = "hidden-scene-run"
        hidden.visible = False
        selected_marker = VerticalMarker(x_min=4.5, y_axis=2, color="#123456")
        other_marker = VerticalMarker(x_min=7.0, y_axis=1, color="#654321")
        region = FractionRegion(start_min=2.1, end_min=1.0, interval_min=0.5)
        shown_annotation = TextAnnotation(
            text="Shown",
            x_min=3.0,
            y_value=20.0,
            dataset_id=visible.id,
            y_axis=2,
        )
        hidden_annotation = TextAnnotation(
            text="Hidden",
            dataset_id=hidden.id,
        )
        blank_annotation = TextAnnotation(text="   ")
        project = Project(
            datasets=[visible, hidden],
            vertical_markers=[selected_marker, other_marker],
            fraction_regions=[region],
            annotations=[shown_annotation, hidden_annotation, blank_annotation],
        )
        scene = compose_base_screen_scene(
            project,
            selected_vertical_marker_id=selected_marker.id,
            color_resolver=lambda _dataset, _index: "#000000",
        )
        self.assertEqual(len(scene.vertical_markers), 2)
        self.assertTrue(scene.vertical_markers[0].selected)
        self.assertEqual(scene.vertical_markers[0].axis_id, "y2")
        self.assertEqual(scene.vertical_markers[0].color, "#f59e0b")
        self.assertEqual(scene.vertical_markers[0].line_width, 2.0)
        self.assertEqual(len(scene.fraction_regions), 1)
        self.assertEqual(scene.fraction_regions[0].start_x, 1.0)
        self.assertEqual(scene.fraction_regions[0].end_x, 2.1)
        self.assertEqual(
            scene.fraction_regions[0].boundary_values,
            (1.0, 1.5, 2.0),
        )
        self.assertEqual(len(scene.text_annotations), 1)
        self.assertEqual(scene.text_annotations[0].annotation_id, shown_annotation.id)
        self.assertEqual(scene.text_annotations[0].axis_id, "y2")

        solo_scene = compose_base_screen_scene(
            project,
            solo_dataset_id=hidden.id,
            color_resolver=lambda _dataset, _index: "#000000",
        )
        self.assertEqual(
            [trace.dataset_id for trace in solo_scene.traces], [hidden.id]
        )
        self.assertEqual(
            [item.annotation_id for item in solo_scene.text_annotations],
            [hidden_annotation.id],
        )

    def test_renderer_benchmark_is_deterministic_and_non_mutating(self):
        workload = RendererWorkload(trace_count=2, point_count=200, repeats=1)
        first_x, first_traces = synthetic_chromatograms(workload)
        second_x, second_traces = synthetic_chromatograms(workload)
        digest = arrays_digest(first_x, first_traces)
        self.assertEqual(digest, arrays_digest(second_x, second_traces))
        result = benchmark_backend("matplotlib_agg", workload)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["raw_data_sha256"], digest)
        self.assertEqual(len(result["durations_seconds"]), 1)

    def test_renderer_benchmark_skips_missing_optional_backend(self):
        workload = RendererWorkload(trace_count=1, point_count=20, repeats=1)
        with patch(
            "hplc_app.renderer_benchmark.importlib.import_module",
            side_effect=ImportError("not installed"),
        ):
            result = benchmark_backend("pyqtgraph", workload)
        self.assertEqual(result["status"], "skipped")
        self.assertIn("optional dependency unavailable", result["reason"])
        self.assertEqual(result["durations_seconds"], [])

    def test_pyqtgraph_scene_consumer_is_optional_and_separately_pinned(self):
        with patch(
            "hplc_app.pyqtgraph_scene.importlib.import_module",
            side_effect=ImportError("missing optional renderer"),
        ):
            self.assertFalse(pyqtgraph_scene_available())
            with self.assertRaises(OptionalRendererUnavailable):
                PyQtGraphSceneConsumer()
        with patch(
            "hplc_app.pyqtgraph_scene.importlib.import_module",
            side_effect=RuntimeError("incompatible optional renderer"),
        ):
            self.assertFalse(pyqtgraph_scene_available())
        optional = (ROOT / "requirements-win11-pyqtgraph.txt").read_text(
            encoding="utf-8"
        )
        normal = (ROOT / "requirements-win11.txt").read_text(encoding="utf-8")
        legacy = (ROOT / "requirements-win7.txt").read_text(encoding="utf-8")
        self.assertIn("pyqtgraph==0.13.7", optional)
        self.assertNotIn("pyqtgraph", normal.casefold())
        self.assertIn("pyqtgraph==0.13.3", legacy)
        self.assertEqual(default_screen_renderer(6), "pyqtgraph")
        self.assertEqual(default_screen_renderer(5), "matplotlib")
        spec = (ROOT / "HPLC_Analyzer.spec").read_text(encoding="utf-8")
        self.assertIn('"pyqtgraph.Qt.QtWidgets"', spec)

    def test_renderer_benchmark_suite_schema_and_validation(self):
        with self.assertRaises(ValueError):
            RendererWorkload(trace_count=0)
        workload = RendererWorkload(trace_count=1, point_count=20, repeats=1)
        with patch(
            "hplc_app.renderer_benchmark.importlib.import_module",
            side_effect=ImportError("not installed"),
        ):
            suite = benchmark_suite(workload)
        self.assertEqual(suite["schema_version"], 1)
        self.assertEqual(
            [result["backend_id"] for result in suite["results"]],
            ["matplotlib_agg", "pyqtgraph"],
        )

    def test_renderer_parity_unavailable_report_has_complete_schema(self):
        report = unavailable_parity_report("not installed")
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["status"], "skipped")
        self.assertEqual(set(report["features"]), set(FEATURES))
        self.assertTrue(
            all(
                value["status"] == "not_tested"
                for value in report["features"].values()
            )
        )

    def test_renderer_parity_verifies_shared_x_and_independent_gradient_scale(self):
        self.assertTrue(
            three_axis_ranges_are_independent(
                ((4.0, 12.0), (-0.1, 1.5)),
                ((4.0, 12.0), (-0.5, 2.5)),
                ((4.0, 12.0), (0.0, 100.0)),
            )
        )
        self.assertFalse(
            three_axis_ranges_are_independent(
                ((4.0, 12.0), (-0.1, 1.5)),
                ((4.0, 12.0), (-0.5, 2.5)),
                ((5.0, 12.0), (0.0, 100.0)),
            )
        )

    def test_update_check_selects_latest_stable_release(self):
        payload = json.dumps(
            [
                {
                    "tag_name": "v1.4.0-rc.1",
                    "html_url": "https://github.com/mshibagaki/HPLC_Analyzer/releases/tag/v1.4.0-rc.1",
                    "draft": False,
                    "prerelease": True,
                },
                {
                    "tag_name": "v1.3.0",
                    "html_url": "https://github.com/mshibagaki/HPLC_Analyzer/releases/tag/v1.3.0",
                    "published_at": "2026-08-28T00:00:00Z",
                    "draft": False,
                    "prerelease": False,
                },
            ]
        ).encode("utf-8")
        result = check_for_updates("1.2.4", fetch=lambda _url, _timeout: payload)
        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["latest_version"], "1.3.0")
        self.assertTrue(result["release_url"].endswith("/tag/v1.3.0"))

    def test_update_check_reports_current_no_release_and_nonfatal_errors(self):
        stable = json.dumps(
            [{
                "tag_name": "1.2.4",
                "html_url": "https://github.com/mshibagaki/HPLC_Analyzer/releases/tag/v1.2.4",
                "draft": False,
                "prerelease": False,
            }]
        ).encode("utf-8")
        self.assertEqual(
            check_for_updates("1.2.4", fetch=lambda _url, _timeout: stable)["status"],
            "current",
        )
        self.assertEqual(
            check_for_updates("1.2.4", fetch=lambda _url, _timeout: b"[]")["status"],
            "no_release",
        )
        result = check_for_updates(
            "1.2.4", fetch=lambda _url, _timeout: (_ for _ in ()).throw(OSError("offline"))
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("offline", result["reason"])

    def test_update_check_rejects_prerelease_semver_and_untrusted_release_url(self):
        self.assertEqual(parse_stable_version("v1.2.3"), (1, 2, 3))
        self.assertIsNone(parse_stable_version("1.3.0-rc.1"))
        self.assertIsNone(parse_stable_version("01.2.3"))
        payload = json.dumps(
            [{
                "tag_name": "9.0.0",
                "html_url": "https://example.invalid/releases/tag/9.0.0",
                "draft": False,
                "prerelease": False,
            }]
        ).encode("utf-8")
        self.assertEqual(
            check_for_updates("1.2.4", fetch=lambda _url, _timeout: payload)["status"],
            "no_release",
        )

    def test_update_check_compares_development_and_rc_versions_to_stable(self):
        stable_124 = json.dumps(
            [{
                "tag_name": "v1.2.4",
                "html_url": "https://github.com/mshibagaki/HPLC_Analyzer/releases/tag/v1.2.4",
                "draft": False,
                "prerelease": False,
            }]
        ).encode("utf-8")
        stable_130 = stable_124.replace(b"1.2.4", b"1.3.0")
        self.assertEqual(
            check_for_updates("1.3.0-dev.1", fetch=lambda *_args: stable_124)["status"],
            "current",
        )
        self.assertEqual(
            check_for_updates("1.3.0-rc.2", fetch=lambda *_args: stable_130)["status"],
            "update_available",
        )
        self.assertLess(compare_semver("1.3.0-dev.1", "1.3.0-rc.1"), 0)
        self.assertLess(compare_semver("1.3.0-rc.2", "1.3.0"), 0)
        self.assertEqual(parse_semver("1.3.0+build.5")[0], (1, 3, 0))

    def test_updater_stages_hash_and_signature_verified_installer_without_launch(self):
        installer = b"signed-installer-fixture"
        digest = hashlib.sha256(installer).hexdigest()
        filename = "HPLC-Analyzer-1.3.0.exe"
        base = "https://github.com/mshibagaki/HPLC_Analyzer/releases/download/v1.3.0/"

        def fetch(url, _limit, _timeout):
            if url.endswith("SHA256SUMS.txt"):
                return (digest + " *" + filename + "\n").encode("utf-8")
            return installer

        with tempfile.TemporaryDirectory() as directory:
            result = stage_verified_installer(
                base + filename,
                base + "SHA256SUMS.txt",
                filename,
                directory,
                fetch=fetch,
                authenticode_probe=lambda _path: {
                    "status": "valid",
                    "thumbprint": "ABC123",
                    "reason": "Valid",
                },
            )
            self.assertEqual(result["status"], "verified")
            self.assertEqual(Path(result["path"]).read_bytes(), installer)
            self.assertFalse(result["launch_allowed"])

    def test_updater_bounded_stream_reports_monotonic_progress_and_cancels(self):
        class Response:
            def __init__(self, payload, declared=None):
                self.payload = payload
                self.offset = 0
                self.headers = {}
                if declared is not None:
                    self.headers["Content-Length"] = str(declared)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, size):
                chunk = self.payload[self.offset : self.offset + size]
                self.offset += len(chunk)
                return chunk

        events = []
        payload = b"abcdefghij"
        result = fetch_bounded(
            "https://example.invalid/file",
            10,
            1.0,
            progress=lambda received, total: events.append((received, total)),
            opener=lambda *_args, **_kwargs: Response(payload, len(payload)),
            chunk_size=3,
        )
        self.assertEqual(result, payload)
        self.assertEqual(events, [(0, 10), (3, 10), (6, 10), (9, 10), (10, 10)])

        cancel_events = []
        with self.assertRaises(DownloadCancelled):
            fetch_bounded(
                "https://example.invalid/file",
                10,
                1.0,
                progress=lambda received, total: cancel_events.append((received, total)),
                cancelled=lambda: bool(cancel_events and cancel_events[-1][0] >= 3),
                opener=lambda *_args, **_kwargs: Response(payload, len(payload)),
                chunk_size=3,
            )
        self.assertEqual(cancel_events[-1], (3, 10))

    def test_updater_stage_cancellation_is_explicit_and_non_mutating(self):
        filename = "safe.exe"
        installer = b"installer"
        digest = hashlib.sha256(installer).hexdigest()
        base = "https://github.com/mshibagaki/HPLC_Analyzer/releases/download/v1/"
        events = []

        def fetch(url, _limit, _timeout):
            if url.endswith("txt"):
                return (digest + " *" + filename + "\n").encode("utf-8")
            return installer

        with tempfile.TemporaryDirectory() as directory:
            result = stage_verified_installer(
                base + filename,
                base + "SHA256SUMS.txt",
                filename,
                directory,
                fetch=fetch,
                progress=lambda asset, received, total: events.append(
                    (asset, received, total)
                ),
                cancelled=lambda: bool(events and events[-1][0] == "manifest" and events[-1][1] > 0),
            )
            self.assertEqual(result["status"], "canceled")
            self.assertFalse(result["launch_allowed"])
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertEqual(events[0], ("manifest", 0, None))

    def test_updater_rejects_untrusted_urls_bad_paths_and_hash_mismatch(self):
        self.assertTrue(
            official_release_asset_url(
                "https://github.com/mshibagaki/HPLC_Analyzer/releases/download/v1/x.exe"
            )
        )
        self.assertFalse(
            official_release_asset_url(
                "https://example.invalid/mshibagaki/HPLC_Analyzer/releases/download/v1/x.exe"
            )
        )
        with self.assertRaisesRegex(ValueError, "exactly once"):
            parse_sha256_manifest(b"", "safe.exe")
        base = "https://github.com/mshibagaki/HPLC_Analyzer/releases/download/v1/"
        with tempfile.TemporaryDirectory() as directory:
            result = stage_verified_installer(
                base + "safe.exe",
                base + "SHA256SUMS.txt",
                "safe.exe",
                directory,
                fetch=lambda url, _limit, _timeout: (
                    ("0" * 64 + " *safe.exe\n").encode("utf-8")
                    if url.endswith("txt")
                    else b"different"
                ),
            )
            self.assertEqual(result["status"], "error")
            self.assertIn("does not match", result["reason"])
            self.assertEqual(list(Path(directory).iterdir()), [])
            unsafe = stage_verified_installer(
                base + "safe.exe",
                base + "SHA256SUMS.txt",
                "../safe.exe",
                directory,
                fetch=lambda *_args: b"",
            )
            self.assertIn("Unsafe", unsafe["reason"])
            existing = Path(directory) / "safe.exe"
            existing.write_bytes(b"user-file")
            collision = stage_verified_installer(
                base + "safe.exe",
                base + "SHA256SUMS.txt",
                "safe.exe",
                directory,
                fetch=lambda *_args: b"",
            )
            self.assertIn("already exists", collision["reason"])
            self.assertEqual(existing.read_bytes(), b"user-file")

    def test_authenticode_probe_uses_argument_list_and_reports_status(self):
        calls = []

        def runner(arguments, **kwargs):
            calls.append((arguments, kwargs))
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {"Status": "Valid", "StatusMessage": "Valid", "Thumbprint": "ab12"}
                ),
                stderr="",
            )

        result = probe_authenticode("C:/Temp/update installer.exe", runner=runner)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["thumbprint"], "AB12")
        self.assertIsInstance(calls[0][0], list)
        self.assertEqual(Path(calls[0][0][-1]), Path("C:/Temp/update installer.exe"))
        self.assertFalse(calls[0][1].get("shell", False))

    def test_updater_selects_exact_canonical_assets_and_rejects_ambiguity(self):
        base = "https://github.com/mshibagaki/HPLC_Analyzer/releases/download/v1.3.0/"
        installer = "HPLC_Analyzer_Setup_1.3.0_Windows11_x64.exe"
        release = {
            "assets": [
                {"name": installer, "browser_download_url": base + installer, "size": 12000},
                {"name": "SHA256SUMS.txt", "browser_download_url": base + "SHA256SUMS.txt", "size": 200},
                {"name": "notes.txt", "browser_download_url": base + "notes.txt", "size": 20},
            ]
        }
        selected = select_canonical_release_assets(release, "1.3.0")
        self.assertEqual(selected["installer"]["name"], installer)
        duplicate = deepcopy(release)
        duplicate["assets"].append(deepcopy(release["assets"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            select_canonical_release_assets(duplicate, "1.3.0")
        release["assets"][0]["browser_download_url"] += "?token=unsafe"
        with self.assertRaisesRegex(ValueError, "not trusted"):
            select_canonical_release_assets(release, "1.3.0")

    def test_signer_policy_is_disabled_until_explicit_identity_matches(self):
        thumbprint = "A1" * 20
        valid = {"status": "valid", "thumbprint": thumbprint.lower()}
        self.assertFalse(signer_policy_result(valid)["launch_allowed"])
        self.assertFalse(
            signer_policy_result(valid, ["B2" * 20])["launch_allowed"]
        )
        self.assertTrue(signer_policy_result(valid, [thumbprint])["launch_allowed"])
        self.assertEqual(normalize_signer_thumbprints([thumbprint, thumbprint]), (thumbprint,))
        with self.assertRaisesRegex(ValueError, "40 hexadecimal"):
            normalize_signer_thumbprints(["not-a-certificate"])

    def test_default_trace_colors_follow_channel_families(self):
        def contrast_on_white(color):
            channels = [int(color[index:index + 2], 16) / 255.0
                        for index in (1, 3, 5)]
            linear = [
                value / 12.92 if value <= 0.04045
                else ((value + 0.055) / 1.055) ** 2.4
                for value in channels
            ]
            luminance = (
                0.2126 * linear[0]
                + 0.7152 * linear[1]
                + 0.0722 * linear[2]
            )
            return 1.05 / (luminance + 0.05)

        self.assertEqual(default_trace_color(1, 0), "#1d4ed8")
        self.assertEqual(default_trace_color(1, 1), "#2563eb")
        self.assertEqual(default_trace_color(2, 0), "#a16207")
        self.assertEqual(default_trace_color(2, 1), "#b45309")
        self.assertEqual(len(CHANNEL_COLOR_PALETTES[1]), 8)
        self.assertEqual(len(CHANNEL_COLOR_PALETTES[2]), 8)
        self.assertEqual(len(set(CHANNEL_COLOR_PALETTES[1])), 8)
        self.assertEqual(len(set(CHANNEL_COLOR_PALETTES[2])), 8)
        self.assertTrue(all(
            contrast_on_white(color) >= 3.0
            for color in CHANNEL_COLOR_PALETTES[2]
        ))
        self.assertEqual(
            default_trace_color(1, 8), default_trace_color(1, 0)
        )
        self.assertEqual(
            default_trace_color(2, 8), default_trace_color(2, 0)
        )
        self.assertIsNone(default_trace_color(3, 0))

    def test_new_projects_default_to_overview_detail_without_migrating_saved_mode(self):
        project = Project()
        self.assertEqual(project.method.view_mode, "overview_detail")
        project.method.view_mode = "single"
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "single-view.hplcproj")
            save_project(path, project)
            restored = load_project(path)
        self.assertEqual(restored.method.view_mode, "single")

    def test_manual_integration_and_quantitation(self):
        dataset = self.synthetic_dataset()
        peak = integrate_peak(dataset, PeakRegion(start_min=3.5, end_min=6.5))
        expected_area_min = 1000.0 * 0.3 * math.sqrt(2.0 * math.pi) * 2.0e-3
        expected_area_sec = expected_area_min * 60.0
        self.assertAlmostEqual(peak.retention_time_min, 5.0, places=3)
        self.assertAlmostEqual(peak.area_mau_sec, peak.area_mau_min * 60.0, places=9)
        # Minute fields are compatibility values for earlier v1.x readers.
        self.assertAlmostEqual(peak.area_mau_min, expected_area_min, places=3)
        self.assertAlmostEqual(peak.fwhm_min, 2.35482 * 0.3, places=3)
        self.assertAlmostEqual(peak.gradient_b_pct, 50.0, places=2)
        self.assertAlmostEqual(
            peak.amount_nmol, expected_area_sec * 1000.0 / (60.0 * 10000.0), places=4
        )
        self.assertAlmostEqual(peak.amount_ug, peak.amount_nmol * 10.0, places=4)

    def test_peak_fitting_selects_gaussian_and_tailing_models_non_destructively(self):
        x = np.linspace(0.0, 10.0, 401)
        gaussian_dataset = Dataset(
            time_min=x.copy(),
            intensity_uv=2500.0 * gaussian_profile(x, 5.0, 0.42),
        )
        gaussian_region = PeakRegion(start_min=2.0, end_min=8.0)
        original_signal = gaussian_dataset.intensity_uv.copy()
        gaussian = fit_peak(gaussian_dataset, gaussian_region, "auto")
        self.assertEqual(gaussian.model, "gaussian")
        self.assertAlmostEqual(gaussian.retention_time_min, 5.0, delta=0.08)
        self.assertGreater(gaussian.r_squared, 0.995)
        self.assertTrue(np.array_equal(gaussian_dataset.intensity_uv, original_signal))
        self.assertIsNone(gaussian_region.retention_time_min)

        tailed_dataset = Dataset(
            time_min=x.copy(),
            intensity_uv=1800.0 * emg_profile(x, 4.4, 0.28, 0.85),
        )
        tailed = fit_peak(
            tailed_dataset,
            PeakRegion(start_min=2.0, end_min=9.5),
            "auto",
        )
        self.assertEqual(tailed.model, "emg")
        self.assertGreater(tailed.parameters["tau_min"], 0.2)
        self.assertGreater(tailed.r_squared, 0.98)

    def test_fitted_area_matches_the_documented_formula_for_both_models(self):
        # Gaussian: the closed form is the reference the code claims to use.
        gaussian = PeakFitResult(
            "gaussian",
            {"amplitude_uv": 12345.0, "center_min": 5.0, "sigma_min": 0.08},
            5.0, 0.0, 1.0, 0.0, 100,
        )
        expected = 12345.0 * 0.08 * math.sqrt(2.0 * math.pi)
        self.assertAlmostEqual(fitted_area_uv_min(gaussian), expected, places=9)
        self.assertAlmostEqual(fitted_area_uv_min(gaussian), 2475.546084, places=6)
        bounded_expected = expected * math.erf(0.5 / math.sqrt(2.0))
        self.assertAlmostEqual(
            fitted_area_uv_min(gaussian, 4.96, 5.04), bounded_expected, places=9
        )
        self.assertLess(fitted_area_uv_min(gaussian, 4.96, 5.04), expected)
        self.assertAlmostEqual(
            fitted_fwhm_min(gaussian),
            2.0 * math.sqrt(2.0 * math.log(2.0)) * 0.08,
            places=9,
        )
        self.assertAlmostEqual(fitted_apex_min("gaussian", gaussian.parameters), 5.0)

        # The same closed form must also equal a dense numerical integration,
        # which is how the EMG area is obtained.
        grid = np.linspace(5.0 - 1.6, 5.0 + 1.6, 200001)
        numeric = float(_trapz(evaluate_fit_profile(grid, gaussian), grid))
        self.assertAlmostEqual(numeric / expected, 1.0, places=6)

        emg = PeakFitResult(
            "emg",
            {
                "amplitude_uv": 9000.0,
                "center_min": 5.0,
                "sigma_min": 0.05,
                "tau_min": 0.12,
            },
            5.0, 0.0, 1.0, 0.0, 100,
        )
        fine = np.linspace(5.0 - 2.0, 5.0 + 6.0, 400001)
        reference = float(_trapz(evaluate_fit_profile(fine, emg), fine))
        self.assertAlmostEqual(fitted_area_uv_min(emg) / reference, 1.0, places=4)
        self.assertAlmostEqual(fitted_area_uv_min(emg), 2080.1754, places=3)

    def test_saturated_peak_correction_recovers_the_clipped_area(self):
        time = np.linspace(4.0, 6.0, 1201)
        amplitude, center, sigma = 20000.0, 5.0, 0.06
        clean = amplitude * gaussian_profile(time, center, sigma)
        true_area = amplitude * sigma * math.sqrt(2.0 * math.pi)
        clipped = np.minimum(clean, 12000.0)
        dataset = Dataset(time_min=time.copy(), intensity_uv=clipped.copy())
        raw_signal = dataset.intensity_uv.copy()
        region = PeakRegion(start_min=4.0, end_min=6.0)
        dataset.peaks = [region]
        recalculate_dataset_peaks(dataset)
        parent = dataset.peaks[0]
        measured_area = parent.raw_area_uv_min
        # The clipped trace really is missing a fifth of the peak.
        self.assertLess(measured_area, true_area * 0.85)

        span = detect_saturated_span(dataset, parent)
        self.assertIsNotNone(span)
        self.assertGreaterEqual(span.point_count, 3)
        self.assertLess(span.start_min, center)
        self.assertGreater(span.end_min, center)

        result, used = fit_saturated_peak(dataset, parent, "gaussian")
        self.assertEqual(used, span)
        model_values = evaluate_fit_profile(time, result)
        hybrid_values = clipped.copy()
        hybrid_values[(time >= used.start_min) & (time <= used.end_min)] = (
            model_values[(time >= used.start_min) & (time <= used.end_min)]
        )
        corrected_area = float(_trapz(hybrid_values, time))
        self.assertAlmostEqual(corrected_area / true_area, 1.0, delta=0.02)
        self.assertAlmostEqual(
            result.parameters["amplitude_uv"] / amplitude, 1.0, delta=0.02
        )
        self.assertAlmostEqual(result.retention_time_min, center, delta=0.01)

        fitted = fitted_peak_from_result(parent, result, dataset, used)
        self.assertTrue(is_saturation_corrected(fitted))
        self.assertAlmostEqual(fitted.raw_area_uv_min, corrected_area, places=9)
        self.assertAlmostEqual(
            fitted.raw_area_uv_sec, corrected_area * 60.0, places=6
        )
        self.assertAlmostEqual(
            fitted.raw_height_uv, result.parameters["amplitude_uv"], places=9
        )
        self.assertEqual(
            fitted.fit_parameters["saturated_point_count"],
            float(span.point_count),
        )
        self.assertEqual(
            fitted.fit_parameters["saturated_start_min"], span.start_min
        )
        self.assertEqual(fitted.fit_parameters["saturated_end_min"], span.end_min)

        # The measurement itself is untouched by the estimate.
        np.testing.assert_array_equal(dataset.intensity_uv, raw_signal)
        self.assertEqual(dataset.peaks[0].raw_area_uv_min, measured_area)
        self.assertEqual(dataset.peaks[0].area_percent, 100.0)

        dataset.fitted_peaks = [fitted]
        recalculate_dataset_peaks(dataset)
        self.assertIsNone(dataset.peaks[0].area_percent)
        self.assertEqual(fitted.area_percent, 100.0)

    def test_emg_amplitude_correction_is_exact_given_the_true_shape(self):
        """Isolate the amplitude fix from the grid search's own shape noise.

        ``_emg_true_amplitude`` rescales a least-squares amplitude fit on a
        range that excludes the model's true apex back to that apex's real
        height. Given the exact (center, sigma, tau) -- as opposed to values
        the coarse grid search only approximates -- this rescale is an exact
        algebraic identity, not an approximation, and runs in well under a
        second because it makes one profile evaluation, not a search. Any
        residual error in the end-to-end saturated-fit tests below therefore
        comes from shape recovery, not from this correction.
        """

        center, sigma, tau = 4.0, 0.25, 0.35
        true_amplitude = 100000.0
        # A range that skips a neighborhood of the true apex, the same way
        # excluding the clipped samples does during the real correction.
        x = np.concatenate(
            [
                np.linspace(2.0, center - 0.3, 400),
                np.linspace(center + 0.3, 8.0, 400),
            ]
        )
        # The physically true signal, normalized over a range that reaches
        # the real apex -- built with the same trick the fix itself uses, so
        # "true_amplitude" means the model's real peak height.
        combined = np.concatenate([x, _model_grid(center, sigma, tau)])
        g_true_at_x = emg_profile(combined, center, sigma, tau)[: len(x)]
        y = true_amplitude * g_true_at_x

        # What _score_profile actually fits against: emg_profile(x, ...),
        # self-normalized to x's own (apex-missing) maximum.
        profile_on_x = emg_profile(x, center, sigma, tau)
        naive_amplitude = float(
            np.dot(y, profile_on_x) / np.dot(profile_on_x, profile_on_x)
        )
        # Confirms the bug this fixes: the naive fit meaningfully
        # underestimates once the apex is excluded from x.
        self.assertLess(naive_amplitude, true_amplitude * 0.98)

        started = time.time()
        corrected = _emg_true_amplitude(x, naive_amplitude, center, sigma, tau)
        self.assertLess(time.time() - started, 1.0)
        self.assertAlmostEqual(corrected, true_amplitude, delta=1.0e-6)

    def test_saturated_emg_correction_recovers_the_true_apex_height(self):
        """Issue #238: emg_profile normalizes by the max within whatever ``x``
        it is given, so a least-squares amplitude fit on the samples the
        saturated-peak correction deliberately excludes near the apex used to
        describe the height at the edge of that exclusion, not the model's
        true peak. The reconstructed height collapsed onto the clipping
        ceiling itself. This pins the fix (a one-time post-fit amplitude
        rescale onto a range that contains the true apex) with a realistic,
        densely sampled EMG peak at several clipping depths.
        """

        clock = time.time  # captured before "time" below shadows the module
        time_axis = np.linspace(0.0, 12.0, 2401)
        true_amplitude, center, sigma, tau = 100000.0, 4.0, 0.25, 0.35
        clean = true_amplitude * emg_profile(time_axis, center, sigma, tau)
        reference = PeakFitResult(
            "emg",
            {
                "amplitude_uv": true_amplitude,
                "center_min": center,
                "sigma_min": sigma,
                "tau_min": tau,
            },
            center, 0.0, 1.0, 0.0, 100,
        )
        true_area = fitted_area_uv_min(reference)
        # The EMG's right tail shifts its true apex off the "center_min" shape
        # parameter; retention_time_min tracks that true apex, not center_min.
        true_apex = fitted_apex_min("emg", reference.parameters)
        region = PeakRegion(start_min=0.0, end_min=12.0, baseline_mode="linear")

        # Height and area recovery, at four clipping depths as required. The
        # amplitude correction is algebraically exact given the fitted shape;
        # the residual error below this margin comes from sigma/tau being
        # harder to disentangle once the most informative apex samples are
        # excluded, not from the correction itself -- so this is a generous
        # margin around measured behavior, not the correction's own precision.
        expected_hybrid_area_ratios = {
            0.90: 0.9907,
            0.70: 0.9743,
            0.50: 0.9954,
            0.35: 0.9986,
        }
        started = clock()
        for clip_ratio in (0.90, 0.70, 0.50, 0.35):
            with self.subTest(clip_ratio=clip_ratio):
                ceiling = true_amplitude * clip_ratio
                dataset = Dataset(
                    time_min=time_axis.copy(),
                    intensity_uv=np.minimum(clean, ceiling).copy(),
                )
                raw_signal = dataset.intensity_uv.copy()
                dataset.peaks = [PeakRegion(start_min=0.0, end_min=12.0)]
                recalculate_dataset_peaks(dataset)
                parent = dataset.peaks[0]
                measured_area = parent.raw_area_uv_min

                result, span = fit_saturated_peak(dataset, region, "emg")
                self.assertEqual(result.model, "emg")
                self.assertGreater(result.r_squared, 0.98)
                self.assertAlmostEqual(
                    result.retention_time_min, true_apex, delta=0.05
                )

                height = result.parameters["amplitude_uv"]
                self.assertAlmostEqual(height / true_amplitude, 1.0, delta=0.07)
                area = fitted_area_uv_min(result)
                self.assertAlmostEqual(area / true_area, 1.0, delta=0.04)

                # amplitude_uv means the model curve's own height: evaluating
                # the fitted curve at its own apex reproduces it, on the same
                # kind of range (containing the true apex) the display paths
                # and fitted_apex_min/fitted_fwhm_min already use.
                apex_time = fitted_apex_min(result.model, result.parameters)
                apex_value = float(
                    evaluate_fit_profile(np.array([apex_time]), result)[0]
                )
                self.assertAlmostEqual(apex_value / height, 1.0, delta=1.0e-6)

                fitted = fitted_peak_from_result(parent, result, dataset, span)
                self.assertTrue(is_saturation_corrected(fitted))
                self.assertAlmostEqual(fitted.raw_height_uv, height, places=9)
                true_window_area = float(_trapz(clean, time_axis))
                self.assertAlmostEqual(
                    fitted.raw_area_uv_min / true_window_area,
                    expected_hybrid_area_ratios[clip_ratio],
                    delta=0.015,
                )

                # The measurement and the original integration are untouched.
                np.testing.assert_array_equal(dataset.intensity_uv, raw_signal)
                self.assertEqual(dataset.peaks[0].raw_area_uv_min, measured_area)
                self.assertEqual(dataset.peaks[0].area_percent, 100.0)

        # An ordinary (unsaturated) EMG fit already reaches the true apex
        # through its samples, so the same correction should leave it close
        # to unchanged -- report-quality accuracy, not the saturated case's
        # reconstruction problem.
        normal_dataset = Dataset(time_min=time_axis.copy(), intensity_uv=clean.copy())
        normal_result = fit_peak(normal_dataset, region, "emg")
        self.assertAlmostEqual(
            normal_result.parameters["amplitude_uv"] / true_amplitude,
            1.0,
            delta=0.02,
        )
        self.assertAlmostEqual(
            fitted_area_uv_min(normal_result) / true_area, 1.0, delta=0.01
        )
        elapsed = clock() - started
        # A generous ceiling: five EMG fits over a 2,401-point trace. This
        # exists to catch a return of the "2 minutes to complete" regression
        # the workflow document warns against (re-normalizing on a dense grid
        # inside the search loop instead of once after it converges), not to
        # assert this is fast in any absolute sense.
        self.assertLess(elapsed, 90.0)

    def test_saturation_detection_ignores_a_merely_rounded_apex(self):
        time = np.linspace(4.0, 6.0, 1201)
        clean = 20000.0 * gaussian_profile(time, 5.0, 0.06)
        dataset = Dataset(time_min=time.copy(), intensity_uv=clean.copy())
        region = PeakRegion(start_min=4.0, end_min=6.0)

        # A smooth apex is flat to within the tolerance over a few samples, but
        # it never covers a real part of the peak width.
        self.assertIsNone(detect_saturated_span(dataset, region))
        with self.assertRaises(ValueError) as raised:
            fit_saturated_peak(dataset, region, "gaussian")
        self.assertEqual(str(raised.exception), "not_saturated")

        # A range given by hand is still honoured, and is validated.
        result, span = fit_saturated_peak(
            dataset, region, "gaussian", saturated_range=(4.95, 5.05)
        )
        self.assertEqual((span.start_min, span.end_min), (4.95, 5.05))
        self.assertGreater(span.point_count, 2)
        self.assertAlmostEqual(result.retention_time_min, 5.0, delta=0.01)
        for bad in ((3.0, 5.0), (5.0, 7.0), (5.0, 5.0)):
            with self.assertRaises(ValueError):
                fit_saturated_peak(dataset, region, "gaussian", saturated_range=bad)

    def test_fitted_rows_carry_estimated_values_and_no_area_share(self):
        dataset = self.synthetic_dataset()
        dataset.measurement.aux_range_au_per_v = 2.0
        dataset.peaks = [
            PeakRegion(start_min=3.5, end_min=6.5),
            PeakRegion(start_min=6.6, end_min=8.0),
        ]
        recalculate_dataset_peaks(dataset)
        parent = dataset.peaks[0]
        shares_before = [peak.area_percent for peak in dataset.peaks]

        result = fit_peak(dataset, parent, "gaussian")
        fitted = fitted_peak_from_result(parent, result, dataset)
        dataset.fitted_peaks = [fitted]
        recalculate_dataset_peaks(dataset)

        # The estimated curve is integrated only over the copied parent window,
        # while its height still comes from the fitted model.
        self.assertIsNotNone(fitted.raw_area_uv_min)
        self.assertAlmostEqual(
            fitted.raw_area_uv_min,
            fitted_area_uv_min(result, parent.start_min, parent.end_min),
            places=9,
        )
        self.assertLess(fitted.raw_area_uv_min, fitted_area_uv_min(result))
        self.assertAlmostEqual(
            fitted.area_mau_min, fitted.raw_area_uv_min * 2.0 * 1.0e-3, places=12
        )
        self.assertAlmostEqual(
            fitted.height_mau, fitted.raw_height_uv * 2.0 * 1.0e-3, places=12
        )
        self.assertIsNotNone(fitted.fwhm_min)
        # %Area stays a property of the measured integrations, and quantitation
        # is not derived from an estimated area.
        self.assertIsNone(fitted.area_percent)
        self.assertIsNone(fitted.amount_nmol)
        self.assertIsNone(fitted.amount_ug)
        self.assertEqual([peak.area_percent for peak in dataset.peaks], shares_before)
        self.assertAlmostEqual(sum(shares_before), 100.0, places=9)
        self.assertFalse(is_saturation_corrected(fitted))
        self.assertEqual(fitted.baseline_mode, parent.baseline_mode)
        self.assertEqual(fitted.baseline_start_uv, parent.baseline_start_uv)
        self.assertEqual(fitted.baseline_end_uv, parent.baseline_end_uv)
        self.assertEqual(
            fitted.calculated_baseline_start_uv,
            parent.calculated_baseline_start_uv,
        )
        self.assertEqual(
            fitted.calculated_baseline_end_uv,
            parent.calculated_baseline_end_uv,
        )

    def test_explicit_fitted_rows_do_not_enter_integration_denominator(self):
        dataset = self.synthetic_dataset()
        dataset.peaks = [
            PeakRegion(start_min=3.5, end_min=6.5),
            PeakRegion(start_min=6.6, end_min=8.0),
        ]
        recalculate_dataset_peaks(dataset)
        parent = dataset.peaks[0]
        original = (
            parent.retention_time_min,
            parent.raw_area_uv_sec,
            parent.area_percent,
            parent.fwhm_min,
        )
        fitted = fitted_peak_from_result(
            parent, fit_peak(dataset, parent, "gaussian")
        )
        dataset.fitted_peaks = [fitted]
        recalculate_dataset_peaks(dataset)
        self.assertEqual(
            (
                parent.retention_time_min,
                parent.raw_area_uv_sec,
                parent.area_percent,
                parent.fwhm_min,
            ),
            original,
        )
        self.assertAlmostEqual(
            sum(peak.area_percent for peak in dataset.peaks), 100.0, places=6
        )
        self.assertIsNone(fitted.area_percent)
        self.assertEqual(
            [peak.id for peak in dataset.display_peaks()],
            [dataset.peaks[0].id, fitted.id, dataset.peaks[1].id],
        )

    def test_saturation_correction_replaces_parent_share_and_is_quantitated(self):
        time = np.linspace(0.0, 10.0, 5001)
        first_clean = 20000.0 * gaussian_profile(time, 3.0, 0.12)
        second = 8000.0 * gaussian_profile(time, 7.0, 0.10)
        clipped = np.minimum(first_clean, 10000.0) + second
        dataset = Dataset(time_min=time.copy(), intensity_uv=clipped.copy())
        dataset.measurement.wavelength_nm = 214.0
        dataset.measurement.aux_range_au_per_v = 2.0
        dataset.measurement.flow_rate_ml_min = 0.5
        dataset.measurement.cell_path_length_cm = 1.0
        dataset.measurement.molar_absorptivity_214 = 10000.0
        dataset.measurement.molecular_weight_g_mol = 50000.0
        dataset.peaks = [
            PeakRegion(start_min=2.0, end_min=4.0),
            PeakRegion(start_min=6.0, end_min=8.0),
        ]
        recalculate_dataset_peaks(dataset)
        parent = dataset.peaks[0]
        parent_values = (
            parent.raw_area_uv_min,
            parent.raw_area_uv_sec,
            parent.raw_height_uv,
            parent.retention_time_min,
        )
        result, span = fit_saturated_peak(dataset, parent, "gaussian")
        fitted = fitted_peak_from_result(parent, result, dataset, span)
        dataset.fitted_peaks = [fitted]

        recalculate_dataset_peaks(dataset)
        recalculated_parent = dataset.peaks[0]

        self.assertEqual(
            (
                recalculated_parent.raw_area_uv_min,
                recalculated_parent.raw_area_uv_sec,
                recalculated_parent.raw_height_uv,
                recalculated_parent.retention_time_min,
            ),
            parent_values,
        )
        self.assertIsNone(recalculated_parent.area_percent)
        self.assertIsNotNone(fitted.area_percent)
        self.assertAlmostEqual(
            fitted.area_percent + dataset.peaks[1].area_percent, 100.0, places=9
        )
        self.assertIsNotNone(fitted.amount_nmol)
        self.assertIsNotNone(fitted.amount_ug)

        # A correction saved by an older build is recalculated from its stored
        # model/span the next time the application recalculates the dataset.
        corrected_area = fitted.raw_area_uv_min
        fitted.raw_area_uv_min = -1.0
        fitted.raw_area_uv_sec = -60.0
        fitted.area_percent = 12.0
        recalculate_dataset_peaks(dataset)
        self.assertAlmostEqual(fitted.raw_area_uv_min, corrected_area, places=9)

        required_cases = (
            ("molar_absorptivity_214", None),
            ("aux_range_au_per_v", None),
            ("flow_rate_ml_min", None),
            ("cell_path_length_cm", None),
        )
        for field_name, missing in required_cases:
            with self.subTest(missing=field_name):
                original = getattr(dataset.measurement, field_name)
                setattr(dataset.measurement, field_name, missing)
                recalculate_dataset_peaks(dataset)
                self.assertIsNone(fitted.amount_nmol)
                self.assertIsNone(fitted.amount_ug)
                setattr(dataset.measurement, field_name, original)
        dataset.measurement.molecular_weight_g_mol = None
        recalculate_dataset_peaks(dataset)
        self.assertIsNotNone(fitted.amount_nmol)
        self.assertIsNone(fitted.amount_ug)

    def test_manual_baseline_is_saved_and_used(self):
        dataset = self.synthetic_dataset()
        peak = integrate_peak(
            dataset,
            PeakRegion(
                start_min=3.5,
                end_min=6.5,
                baseline_mode="manual",
                baseline_start_uv=117.5,
                baseline_end_uv=132.5,
            ),
        )
        self.assertEqual(peak.baseline_mode, "manual")
        self.assertAlmostEqual(peak.calculated_baseline_start_uv, 117.5, places=5)
        self.assertAlmostEqual(peak.calculated_baseline_end_uv, 132.5, places=5)
        self.assertGreater(peak.raw_area_uv_sec, 0.0)
        self.assertAlmostEqual(peak.raw_area_uv_sec, peak.raw_area_uv_min * 60.0)

    def test_area_percent(self):
        dataset = self.synthetic_dataset()
        dataset.peaks = [PeakRegion(start_min=3.5, end_min=6.5), PeakRegion(start_min=3.5, end_min=6.5)]
        recalculate_dataset_peaks(dataset)
        self.assertAlmostEqual(sum(peak.area_percent for peak in dataset.peaks), 100.0, places=6)

    def test_split_peak_preserves_parent_baseline_and_total_area(self):
        dataset = self.synthetic_dataset()
        parent = integrate_peak(
            dataset,
            PeakRegion(start_min=3.5, end_min=6.5, notes="parent note"),
        )
        left, right = split_peak_region(dataset, parent, 5.0)
        self.assertEqual(left.baseline_mode, "manual")
        self.assertEqual(left.split_group_id, right.split_group_id)
        self.assertAlmostEqual(left.end_min, right.start_min, places=8)
        self.assertAlmostEqual(
            (left.raw_area_uv_sec or 0.0) + (right.raw_area_uv_sec or 0.0),
            parent.raw_area_uv_sec,
            places=5,
        )
        self.assertEqual(left.notes, "parent note")
        self.assertEqual(right.notes, "parent note")

    def test_gradient_validation(self):
        valid, message = validate_gradient([
            GradientPoint(0, 90, 10, 0, 0),
            GradientPoint(20, 20, 80, 0, 0),
        ])
        self.assertTrue(valid, message)
        invalid, _message = validate_gradient([GradientPoint(0, 90, 20, 0, 0)])
        self.assertFalse(invalid)

    def test_peaks_are_sorted_and_shifted_gradient_is_recalculated(self):
        dataset = self.synthetic_dataset()
        dataset.peaks = [
            PeakRegion(start_min=6.5, end_min=7.5),
            PeakRegion(start_min=4.0, end_min=6.0),
        ]
        dataset.x_shift_min = 2.0
        recalculate_dataset_peaks(dataset)
        retention = [peak.retention_time_min for peak in dataset.peaks]
        self.assertEqual(retention, sorted(retention))
        centered = min(dataset.peaks, key=lambda peak: abs(peak.retention_time_min - 5.0))
        self.assertAlmostEqual(centered.gradient_b_pct, 70.0, places=2)

    def test_automatic_peak_detection_returns_editable_candidates(self):
        rng = np.random.default_rng(123)
        time = np.linspace(0.0, 10.0, 10001)
        values = (
            100.0
            + 2.0 * time
            + 1000.0 * np.exp(-0.5 * ((time - 3.0) / 0.15) ** 2)
            + 600.0 * np.exp(-0.5 * ((time - 7.0) / 0.25) ** 2)
            + rng.normal(0.0, 2.0, time.size)
        )
        dataset = Dataset(time_min=time, intensity_uv=values, raw_bytes=b"synthetic")
        method = AnalysisMethod(
            auto_peak_snr_threshold=5.0,
            auto_peak_min_prominence_uv=20.0,
            auto_peak_smoothing_min=0.01,
            auto_peak_min_width_min=0.02,
            auto_peak_max_width_min=1.0,
            auto_peak_min_distance_min=0.2,
        )
        peaks = detect_peaks(dataset, method)
        self.assertEqual(len(peaks), 2)
        self.assertTrue(all(peak.integration_source == "auto" for peak in peaks))
        self.assertAlmostEqual(peaks[0].retention_time_min, 3.0, delta=0.03)
        self.assertAlmostEqual(peaks[1].retention_time_min, 7.0, delta=0.03)

    def test_automatic_peak_detection_range_preserves_raw_arrays_and_full_result(self):
        rng = np.random.default_rng(456)
        time = np.linspace(0.0, 10.0, 10001)
        values = (
            100.0
            + 900.0 * np.exp(-0.5 * ((time - 3.0) / 0.15) ** 2)
            + 700.0 * np.exp(-0.5 * ((time - 7.0) / 0.22) ** 2)
            + rng.normal(0.0, 1.0, time.size)
        )
        dataset = Dataset(time_min=time, intensity_uv=values, raw_bytes=b"synthetic")
        method = AnalysisMethod(
            auto_peak_snr_threshold=5.0,
            auto_peak_min_prominence_uv=20.0,
            auto_peak_smoothing_min=0.01,
            auto_peak_min_width_min=0.02,
            auto_peak_max_width_min=1.0,
            auto_peak_min_distance_min=0.2,
        )
        time_before = dataset.time_min.copy()
        values_before = dataset.intensity_uv.copy()
        whole = detect_peaks(dataset, method)
        explicit_whole = detect_peaks(dataset, method, (time[0], time[-1]))
        ranged = detect_peaks(dataset, method, (4.5, 1.5))

        def numeric_result(peaks):
            return [
                (peak.start_min, peak.end_min, peak.retention_time_min, peak.raw_area_uv_sec)
                for peak in peaks
            ]

        self.assertEqual(numeric_result(whole), numeric_result(explicit_whole))
        self.assertEqual(len(ranged), 1)
        self.assertAlmostEqual(ranged[0].retention_time_min, 3.0, delta=0.03)
        self.assertGreaterEqual(ranged[0].start_min, 1.5)
        self.assertLessEqual(ranged[0].end_min, 4.5)
        np.testing.assert_array_equal(dataset.time_min, time_before)
        np.testing.assert_array_equal(dataset.intensity_uv, values_before)
        with self.assertRaisesRegex(ValueError, "must be finite"):
            detect_peaks(dataset, method, (float("nan"), 4.0))

    def test_default_sensitivity_presets_produce_ordered_candidate_counts(self):
        time = np.linspace(0.0, 10.0, 5001)
        values = np.full_like(time, 100.0)
        for center, height in ((2.0, 35.0), (5.0, 70.0), (8.0, 140.0)):
            values += height * np.exp(-0.5 * ((time - center) / 0.08) ** 2)
        dataset = Dataset(time_min=time, intensity_uv=values, raw_bytes=b"synthetic")
        presets = default_auto_peak_sensitivity_presets()
        counts = {}
        for sensitivity in ("low", "medium", "high"):
            method = AnalysisMethod()
            apply_auto_peak_thresholds(method, presets[sensitivity])
            counts[sensitivity] = len(detect_peaks(dataset, method))
        self.assertEqual(counts, {"low": 1, "medium": 2, "high": 3})
        self.assertEqual(
            presets["medium"]["auto_peak_snr_threshold"],
            AnalysisMethod().auto_peak_snr_threshold,
        )


class ProjectTests(unittest.TestCase):
    def test_manifest_migration_pipeline_supports_all_existing_schemas(self):
        base = {
            "format": "hplc-analyzer-project",
            "format_major": 1,
            "schema_version": 102,
            "method": {},
            "datasets": [
                {
                    "measurement": {},
                    "peaks": [
                        {
                            "raw_area_uv_min": 2.0,
                            "area_mau_min": 0.5,
                            "retention_time_min": 3.25,
                            "fwhm_min": 0.12,
                            "area_percent": 42.0,
                            "amount_nmol": 1.5,
                        }
                    ],
                }
            ],
        }
        for schema_version in list(range(8)) + [100, 101, 102]:
            manifest = deepcopy(base)
            manifest["schema_version"] = schema_version
            if schema_version <= 7:
                manifest.pop("format_major")
            migrated = migrate_project_manifest(manifest)
            self.assertEqual(migrated["format_major"], PROJECT_FORMAT_MAJOR)
            self.assertEqual(migrated["schema_version"], PROJECT_SCHEMA_VERSION)
            peak = migrated["datasets"][0]["peaks"][0]
            self.assertEqual(peak["retention_time_min"], 3.25)
            self.assertEqual(peak["fwhm_min"], 0.12)
            self.assertEqual(peak["area_percent"], 42.0)
            self.assertEqual(peak["amount_nmol"], 1.5)
            if schema_version <= 101:
                self.assertEqual(peak["raw_area_uv_sec"], 120.0)
                self.assertEqual(peak["area_mau_sec"], 30.0)
            else:
                self.assertNotIn("raw_area_uv_sec", peak)
                self.assertNotIn("area_mau_sec", peak)
            if schema_version <= 6:
                self.assertEqual(migrated["method"]["zoom_axis"], "auto")

    def test_manifest_migration_is_pure_and_idempotent(self):
        original = {
            "format_major": 1,
            "schema_version": 101,
            "method": {"legend_font_family": ""},
            "datasets": [
                {
                    "peaks": [
                        {
                            "raw_area_uv_min": 1.25,
                            "area_mau_min": 0.25,
                            "future_peak_field": {"kept_during_migration": True},
                        }
                    ],
                    "future_dataset_field": [1, 2, 3],
                }
            ],
            "future_project_field": {"value": 7},
        }
        untouched = deepcopy(original)
        migrated = migrate_project_manifest(original)
        migrated_twice = migrate_project_manifest(migrated)
        self.assertEqual(original, untouched)
        self.assertEqual(migrated_twice, migrated)
        self.assertEqual(migrated["future_project_field"], {"value": 7})
        self.assertEqual(migrated["datasets"][0]["future_dataset_field"], [1, 2, 3])
        self.assertTrue(
            migrated["datasets"][0]["peaks"][0]["future_peak_field"]
            ["kept_during_migration"]
        )

    def test_schema_106_migrates_legacy_fit_to_parent_linked_child(self):
        manifest = {
            "format_major": 1,
            "schema_version": 106,
            "datasets": [{
                "peaks": [{
                    "id": "peak-parent",
                    "start_min": 1.0,
                    "end_min": 2.0,
                    "retention_time_min": 1.4,
                    "raw_area_uv_sec": 123.5,
                    "area_percent": 61.25,
                    "fwhm_min": 0.15,
                    "fit_model": "gaussian",
                    "fit_parameters": {
                        "amplitude_uv": 800.0,
                        "center_min": 1.45,
                        "sigma_min": 0.12,
                    },
                    "fit_retention_time_min": 1.45,
                    "fit_r_squared": 0.998,
                }],
            }],
        }
        untouched = deepcopy(manifest)
        migrated = migrate_project_manifest(manifest)
        self.assertEqual(manifest, untouched)
        self.assertEqual(migrated["schema_version"], PROJECT_SCHEMA_VERSION)
        parent = migrated["datasets"][0]["peaks"][0]
        child = migrated["datasets"][0]["fitted_peaks"][0]
        self.assertEqual(parent["raw_area_uv_sec"], 123.5)
        self.assertEqual(parent["area_percent"], 61.25)
        self.assertEqual(parent["fwhm_min"], 0.15)
        self.assertEqual(child["peak_kind"], "fitted")
        self.assertEqual(child["parent_peak_id"], "peak-parent")
        self.assertEqual(child["retention_time_min"], 1.45)
        self.assertIsNone(child["raw_area_uv_sec"])
        self.assertIsNone(child["area_percent"])
        self.assertEqual(migrate_project_manifest(migrated), migrated)

    def test_schema_107_adds_solid_trace_style_without_mutating_input(self):
        manifest = {
            "format_major": 1,
            "schema_version": 107,
            "datasets": [{"id": "legacy-trace", "peaks": []}],
        }
        untouched = deepcopy(manifest)

        migrated = migrate_project_manifest(manifest)

        self.assertEqual(manifest, untouched)
        self.assertEqual(migrated["schema_version"], PROJECT_SCHEMA_VERSION)
        self.assertEqual(migrated["datasets"][0]["line_style"], "solid")
        self.assertEqual(migrate_project_manifest(migrated), migrated)

    def test_trace_line_style_identifiers_have_stable_backend_mappings(self):
        self.assertEqual(
            LINE_STYLE_IDS, ("solid", "dashed", "dotted", "dash_dot")
        )
        self.assertEqual(
            [matplotlib_line_style(value) for value in LINE_STYLE_IDS],
            ["-", "--", ":", "-."],
        )
        self.assertEqual(normalize_line_style("unsupported"), "solid")
        self.assertEqual(Dataset(line_style="unsupported").line_style, "solid")

    def test_schema_102_creates_one_stable_run_per_legacy_dataset(self):
        manifest = {
            "format_major": 1,
            "schema_version": 102,
            "method": {},
            "datasets": [
                {
                    "id": "same-source",
                    "label": "same label",
                    "original_filename": "20260507_120000.TXT",
                    "measurement": {
                        "sample_name": "sample A",
                        "group": "legacy group",
                        "acquisition_datetime": "2026-05-07T12:00:00",
                        "wavelength_nm": 214.0,
                        "column_name": "C4",
                    },
                    "y_axis": 2,
                    "peaks": [{"retention_time_min": 3.2}],
                    "source_metadata": {"kept": "unchanged"},
                },
                {
                    "id": "same-source",
                    "label": "same label",
                    "original_filename": "20260507_120000.TXT",
                    "measurement": {
                        "sample_name": "sample A",
                        "acquisition_datetime": "2026-05-07T12:00:00",
                        "wavelength_nm": 280.0,
                        "column_name": "C4",
                    },
                    "peaks": [{"retention_time_min": 3.2}],
                    "source_metadata": {"kept": "unchanged"},
                },
            ],
        }
        untouched = deepcopy(manifest)
        migrated = migrate_project_manifest(manifest)
        self.assertEqual(manifest, untouched)
        self.assertEqual(migrated["schema_version"], PROJECT_SCHEMA_VERSION)
        self.assertEqual(len(migrated["runs"]), 2)
        self.assertEqual(
            [item["id"] for item in migrated["runs"]],
            ["run-same-source", "run-same-source-2"],
        )
        self.assertEqual(
            [item["run_id"] for item in migrated["datasets"]],
            ["run-same-source", "run-same-source-2"],
        )
        self.assertEqual(
            [item["label"] for item in migrated["runs"]],
            ["same label", "same label"],
        )
        self.assertEqual(
            migrated["datasets"][0]["measurement"],
            untouched["datasets"][0]["measurement"],
        )
        self.assertEqual(migrated["datasets"][0]["y_axis"], 2)
        self.assertEqual(migrated["runs"][0]["group"], "legacy group")
        self.assertEqual(
            migrated["datasets"][1]["peaks"],
            untouched["datasets"][1]["peaks"],
        )
        self.assertEqual(
            migrated["datasets"][0]["source_metadata"],
            untouched["datasets"][0]["source_metadata"],
        )
        self.assertEqual(migrate_project_manifest(migrated), migrated)

    def test_run_migration_uses_strict_timestamp_fallback_without_relabeling(self):
        manifest = {
            "format_major": 1,
            "schema_version": 102,
            "method": {},
            "datasets": [
                {
                    "id": "known-timestamp",
                    "label": "Keep this user label",
                    "original_filename": "20260507_193354.TXT",
                    "measurement": {"acquisition_datetime": ""},
                    "source_metadata": {
                        "Header.Output Date": "2099/12/31",
                        "Header.Output Time": "23:59:59",
                    },
                },
                {
                    "id": "ambiguous-number",
                    "label": "Also keep this label",
                    "original_filename": "210601.TXT",
                    "measurement": {"acquisition_datetime": ""},
                },
            ],
        }
        migrated = migrate_project_manifest(manifest)
        self.assertEqual(
            [run["timestamp"] for run in migrated["runs"]],
            ["2026-05-07T19:33:54", ""],
        )
        self.assertEqual(
            [dataset["label"] for dataset in migrated["datasets"]],
            ["Keep this user label", "Also keep this label"],
        )
        self.assertEqual(manifest["datasets"][0]["label"], "Keep this user label")
    def test_schema_103_promotes_first_dataset_label_to_shared_run(self):
        manifest = {
            "format_major": 1,
            "schema_version": 103,
            "runs": [{"id": "run-shared", "sample_name": "sample A"}],
            "datasets": [
                {
                    "id": "channel-214",
                    "run_id": "run-shared",
                    "label": "sample A",
                    "short_label": "A",
                    "measurement": {"wavelength_nm": 214.0},
                },
                {
                    "id": "channel-280",
                    "run_id": "run-shared",
                    "label": "conflicting legacy label",
                    "short_label": "conflict",
                    "measurement": {"wavelength_nm": 280.0},
                },
            ],
        }
        untouched = deepcopy(manifest)
        migrated = migrate_project_manifest(manifest)

        self.assertEqual(manifest, untouched)
        self.assertEqual(migrated["schema_version"], PROJECT_SCHEMA_VERSION)
        self.assertEqual(migrated["runs"][0]["label"], "sample A")
        self.assertEqual(migrated["runs"][0]["short_label"], "A")
        self.assertEqual(
            [dataset["label"] for dataset in migrated["datasets"]],
            ["sample A", "sample A"],
        )
        self.assertEqual(
            [dataset["short_label"] for dataset in migrated["datasets"]],
            ["A", "A"],
        )
        self.assertEqual(
            [dataset["measurement"]["wavelength_nm"] for dataset in migrated["datasets"]],
            [214.0, 280.0],
        )

    def test_schema_104_adds_empty_work_directories_and_rejects_invalid_value(self):
        manifest = {
            "format_major": 1,
            "schema_version": 104,
            "runs": [],
            "datasets": [],
        }
        untouched = deepcopy(manifest)
        migrated = migrate_project_manifest(manifest)
        self.assertEqual(manifest, untouched)
        self.assertEqual(migrated["schema_version"], PROJECT_SCHEMA_VERSION)
        self.assertEqual(migrated["work_directories"], [])
        invalid = dict(manifest, work_directories={"path": "C:/HPLC"})
        with self.assertRaisesRegex(
            ProjectMigrationError, "work directories must be an array"
        ):
            migrate_project_manifest(invalid)

    def test_manifest_migration_rejects_invalid_structures_clearly(self):
        with self.assertRaisesRegex(ProjectMigrationError, "datasets must be an array"):
            migrate_project_manifest(
                {
                    "format_major": 1,
                    "schema_version": 101,
                    "method": {},
                    "datasets": {},
                }
            )
        with self.assertRaisesRegex(
            ProjectMigrationError, "newer application version"
        ):
            migrate_project_manifest(
                {
                    "format_major": PROJECT_FORMAT_MAJOR + 1,
                    "schema_version": 1,
                }
            )

    def test_version_independent_preset_store_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "presets.json"
            saved_path = save_preset_store(
                {
                    "280 nm C4": {
                        "wavelength_nm": 280.0,
                        "column_name": "C4",
                        "label": "must not migrate",
                    }
                },
                {
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
                },
                path,
            )
            self.assertEqual(saved_path, path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["format_version"], 3)
            self.assertEqual(payload["written_by"], APP_VERSION)
            conditions, gradients = load_preset_store(path)
            self.assertEqual(conditions["280 nm C4"]["wavelength_nm"], 280.0)
            self.assertNotIn("label", conditions["280 nm C4"])
            self.assertIn("10-90 B", gradients)
            metadata = payload["preset_metadata"]
            condition_metadata = metadata["conditions"]["280 nm C4"]
            self.assertTrue(condition_metadata["id"])
            self.assertTrue(condition_metadata["created_at"])
            self.assertEqual(condition_metadata["last_used_at"], "")

    def test_analyte_presets_round_trip_without_changing_legacy_load_api(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "presets.json"
            analytes = {
                "LL-37": {
                    "molar_absorptivity_214": 12500.0,
                    "molar_absorptivity_280": None,
                    "molecular_weight_g_mol": 4493.3,
                }
            }
            save_preset_store({}, {}, path, analyte_presets=analytes)
            conditions, gradients = load_preset_store(path)
            self.assertEqual((conditions, gradients), ({}, {}))
            _conditions, _gradients, loaded, metadata = (
                load_complete_preset_store(path)
            )
            self.assertEqual(loaded, analytes)
            self.assertTrue(metadata["analytes"]["LL-37"]["id"])

            # Older callers that save only condition/gradient presets must not
            # erase analytes written by a newer build.
            save_preset_store({}, {}, path)
            self.assertEqual(load_complete_preset_store(path)[2], analytes)

    def test_analyte_package_merge_rejects_invalid_values_atomically(self):
        conditions = {"C18": {"column_name": "C18"}}
        analytes = {
            "LL-37": {
                "molar_absorptivity_214": 12500.0,
                "molar_absorptivity_280": None,
                "molecular_weight_g_mol": 4493.3,
            }
        }
        metadata = normalize_preset_metadata(conditions, {}, None, analytes)
        package = build_preset_package(
            conditions, {}, metadata, analytes=analytes
        )
        package["presets"]["analytes"]["Broken"] = {
            "molar_absorptivity_214": -1.0,
            "molar_absorptivity_280": None,
            "molecular_weight_g_mol": None,
        }

        with self.assertRaises(ValueError):
            merge_preset_package(
                conditions, {}, metadata, package, analytes=analytes
            )

        # The rejection leaves every existing preset and record untouched.
        self.assertEqual(conditions, {"C18": {"column_name": "C18"}})
        self.assertEqual(sorted(analytes), ["LL-37"])
        self.assertEqual(
            metadata, normalize_preset_metadata(conditions, {}, None, analytes)
        )

    def test_preset_manager_operations_are_atomic_and_preserve_identity(self):
        presets = {"original": {"column_name": "C18"}}
        metadata = {
            "conditions": {
                "original": {
                    "id": "stable-id",
                    "created_at": "2026-01-01T00:00:00",
                    "updated_at": "2026-01-01T00:00:00",
                    "last_used_at": "",
                }
            },
            "gradients": {},
        }
        renamed, renamed_metadata = apply_preset_operation(
            presets, metadata, "conditions", "rename", "original", "renamed"
        )
        self.assertEqual(renamed["renamed"], presets["original"])
        self.assertEqual(
            renamed_metadata["conditions"]["renamed"]["id"], "stable-id"
        )
        duplicated, duplicate_metadata = apply_preset_operation(
            renamed, renamed_metadata, "conditions", "duplicate", "renamed", "copy"
        )
        self.assertNotEqual(
            duplicate_metadata["conditions"]["copy"]["id"], "stable-id"
        )
        deleted, deleted_metadata = apply_preset_operation(
            duplicated, duplicate_metadata, "conditions", "delete", "copy"
        )
        self.assertNotIn("copy", deleted)
        self.assertNotIn("copy", deleted_metadata["conditions"])
        with self.assertRaisesRegex(ValueError, "already exists"):
            apply_preset_operation(
                duplicated, duplicate_metadata, "conditions", "rename", "copy", "renamed"
            )
        with self.assertRaisesRegex(ValueError, "already exists"):
            apply_preset_operation(
                renamed, renamed_metadata, "conditions", "duplicate", "renamed", "renamed"
            )
        self.assertEqual(presets, {"original": {"column_name": "C18"}})

    def test_preset_package_export_and_explicit_conflict_policies(self):
        conditions = {"shared": {"column_name": "old"}}
        gradients = {"fast": {"gradient": []}}
        metadata = {
            "conditions": {"shared": {"id": "old-id", "created_at": "", "updated_at": "", "last_used_at": ""}},
            "gradients": {"fast": {"id": "gradient-id", "created_at": "", "updated_at": "", "last_used_at": ""}},
        }
        package = build_preset_package(conditions, gradients, metadata)
        package["presets"]["conditions"]["shared"]["column_name"] = "imported"
        package["presets"]["conditions"]["new"] = {"column_name": "new"}
        package["metadata"]["conditions"]["new"] = {"id": "new-id"}
        with self.assertRaisesRegex(ValueError, "requires a policy"):
            merge_preset_package(conditions, gradients, metadata, package)
        replaced, merged_gradients, merged_metadata, imported = merge_preset_package(
            conditions,
            gradients,
            metadata,
            package,
            {("conditions", "shared"): "replace", ("gradients", "fast"): "skip"},
        )
        self.assertEqual(replaced["shared"]["column_name"], "imported")
        self.assertEqual(replaced["new"]["column_name"], "new")
        self.assertEqual(merged_metadata["conditions"]["new"]["id"], "new-id")
        self.assertEqual(merged_gradients, gradients)
        self.assertEqual(imported["gradients"], [])
        kept, _gradients, kept_metadata, imported = merge_preset_package(
            conditions,
            gradients,
            metadata,
            package,
            {("conditions", "shared"): "keep_both", ("gradients", "fast"): "skip"},
        )
        self.assertIn("shared (imported)", kept)
        self.assertNotEqual(
            kept_metadata["conditions"]["shared (imported)"]["id"], "old-id"
        )
        self.assertIn("shared (imported)", imported["conditions"])

    def test_preset_package_rejects_malformed_input_without_mutation(self):
        conditions = {"safe": {"column_name": "C18"}}
        metadata = {"conditions": {}, "gradients": {}}
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            merge_preset_package(conditions, {}, metadata, {"format": 99})
        self.assertEqual(conditions, {"safe": {"column_name": "C18"}})

    def test_format1_preset_metadata_migration_rename_and_usage_are_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "presets.json"
            legacy = {
                "format_version": 1,
                "written_by": "1.2.4",
                "condition_presets": {"Legacy B": {"wavelength_nm": 280.0}},
                "gradient_presets": {"Legacy A": {"gradient": [], "solvents": {}}},
            }
            path.write_text(json.dumps(legacy), encoding="utf-8")
            conditions, gradients, metadata = load_preset_store_with_metadata(path)
            condition_record = metadata["conditions"]["Legacy B"]
            original_id = condition_record["id"]
            self.assertTrue(original_id)
            self.assertEqual(condition_record["created_at"], "")
            self.assertEqual(condition_record["updated_at"], "")
            self.assertEqual(condition_record["last_used_at"], "")
            again = load_preset_store_with_metadata(path)[2]
            self.assertEqual(
                again["conditions"]["Legacy B"]["id"], original_id
            )

            conditions["Renamed B"] = conditions.pop("Legacy B")
            record_preset_saved(
                metadata,
                "conditions",
                "Legacy B",
                "Renamed B",
                now="2026-08-26T12:00:00+09:00",
            )
            record_preset_used(
                metadata,
                "conditions",
                "Renamed B",
                now="2026-08-26T12:30:00+09:00",
            )
            save_preset_store(conditions, gradients, path, metadata=metadata)
            loaded_conditions, _loaded_gradients, loaded_metadata = (
                load_preset_store_with_metadata(path)
            )
            self.assertNotIn("Legacy B", loaded_conditions)
            renamed = loaded_metadata["conditions"]["Renamed B"]
            self.assertEqual(renamed["id"], original_id)
            self.assertEqual(renamed["created_at"], "")
            self.assertEqual(
                renamed["updated_at"], "2026-08-26T12:00:00+09:00"
            )
            self.assertEqual(
                renamed["last_used_at"], "2026-08-26T12:30:00+09:00"
            )
            self.assertEqual(
                stable_preset_names(
                    ["Legacy A", "Renamed B"],
                    loaded_metadata,
                    "conditions",
                ),
                ["Legacy A", "Renamed B"],
            )

    def test_preset_names_support_created_used_updated_name_sort_and_filter(self):
        names = ["Legacy z", "beta", "Alpha"]
        metadata = {
            "conditions": {
                "Alpha": {
                    "created_at": "2026-08-25T09:00:00+09:00",
                    "updated_at": "2026-08-27T09:00:00+09:00",
                    "last_used_at": "",
                },
                "beta": {
                    "created_at": "2026-08-26T09:00:00+09:00",
                    "updated_at": "2026-08-26T10:00:00+09:00",
                    "last_used_at": "2026-08-27T10:00:00+09:00",
                },
                "Legacy z": {
                    "created_at": "",
                    "updated_at": "",
                    "last_used_at": "",
                },
            }
        }
        self.assertEqual(
            stable_preset_names(names, metadata, "conditions", "created"),
            ["beta", "Alpha", "Legacy z"],
        )
        self.assertEqual(
            stable_preset_names(names, metadata, "conditions", "used"),
            ["beta", "Alpha", "Legacy z"],
        )
        self.assertEqual(
            stable_preset_names(names, metadata, "conditions", "updated"),
            ["Alpha", "beta", "Legacy z"],
        )
        self.assertEqual(
            stable_preset_names(names, metadata, "conditions", "name"),
            ["Alpha", "beta", "Legacy z"],
        )
        self.assertEqual(
            filter_preset_names(["Alpha", "beta", "Legacy z"], "A"),
            ["Alpha", "beta", "Legacy z"],
        )
        self.assertEqual(
            filter_preset_names(["Alpha", "beta", "Legacy z"], "LEGACY"),
            ["Legacy z"],
        )

    def test_windows7_build_runs_every_test_module_its_process_can_hold(self):
        """The Windows 7 build runs the non-GUI modules, and says why.

        Qt keeps every window a GUI test leaves behind, so running that module
        in the build's single 32-bit process exhausts its address space before
        PyInstaller (Issue #244). The remaining modules do run there, and a new
        one has to be added to the batch rather than quietly left out.
        """

        batch = (ROOT / "build_windows7_offline.bat").read_text(encoding="utf-8")
        modules = sorted(path.stem for path in (ROOT / "tests").glob("test_*.py"))
        self.assertIn("test_gui", modules)
        for module in modules:
            with self.subTest(module=module):
                if module == "test_gui":
                    self.assertNotIn("tests.test_gui", batch)
                    continue
                self.assertIn("tests." + module, batch)
        # Whole-suite discovery would pull the GUI module back in.
        self.assertNotIn("discover -s tests", batch)
        # The exclusion is deliberate and explained where it is made.
        self.assertIn("Issue #244", batch)
        # What only this machine can prove still runs: the isolated import
        # preflight and the startup smoke test of both frozen executables.
        self.assertIn("windows7_import_preflight.py", batch)
        self.assertIn("HPLC_Analyzer.exe --startup-smoke-test", batch)
        self.assertIn("HPLC_Analyzer_Debug.exe --startup-smoke-test", batch)

    def test_offline_bundle_ships_every_contract_file_the_build_tests_read(self):
        """The Windows 7 offline build runs this suite before PyInstaller.

        Every repository file those tests read as a source contract has to
        travel inside the kit, or the offline build fails its own gate on a
        machine that has no other copy of the repository.
        """

        # The declared lists are the contract and are readable anywhere. The
        # packager itself cannot run without the untracked win7_offline payload,
        # so it is only exercised when that payload happens to be present.
        declared_files = set(ROOT_FILES)
        declared_directories = set(ROOT_DIRECTORIES)

        def is_shipped(relative):
            if relative in declared_files:
                return True
            return any(
                relative.startswith(directory + "/")
                for directory in declared_directories
            )

        for relative in (
            "REQUIREMENTS_STATUS.md",
            "requirements-win11.txt",
            "requirements-win11-pyqtgraph.txt",
            "requirements-win7.txt",
            "requirements-win7-bootstrap.txt",
            ".github/INSTALLER_UPGRADE_EVIDENCE.md",
            ".github/INSTALLER_UPGRADE_TEST.md",
            ".github/RELEASE_CHECKLIST.md",
            ".github/RELEASE_PROCESS.md",
            ".github/RELEASE_TEMPLATE.md",
            ".github/workflows/validation.yml",
        ):
            with self.subTest(relative=relative):
                self.assertTrue((ROOT / relative).is_file(), relative)
                self.assertTrue(is_shipped(relative), relative)

        # A release document added later must travel with the kit as well.
        for path in sorted((ROOT / ".github").rglob("*")):
            if path.is_file() and path.suffix.lower() in (".md", ".yml", ".yaml"):
                with self.subTest(document=path.name):
                    self.assertTrue(
                        is_shipped(path.relative_to(ROOT).as_posix()), path.name
                    )

        # The payload directory stays part of the kit either way.
        self.assertIn("win7_offline", declared_directories)
        if not (ROOT / "win7_offline").is_dir():
            return
        emitted = {
            path.relative_to(ROOT).as_posix() for path in iter_source_files(ROOT)
        }
        self.assertIn("REQUIREMENTS_STATUS.md", emitted)
        self.assertIn("requirements-win11-pyqtgraph.txt", emitted)
        self.assertIn(".github/RELEASE_PROCESS.md", emitted)
        self.assertIn("win7_offline/MANIFEST.sha256", emitted)
        self.assertTrue(
            any(name.endswith(".whl") for name in emitted), "wheels must ship"
        )

    def test_application_version_is_single_valid_source_for_runtime_and_builds(self):
        version_file = ROOT / "hplc_app" / "version.py"
        self.assertEqual(read_version(version_file), APP_VERSION)
        self.assertEqual(
            windows_numeric_version(APP_VERSION),
            ".".join(APP_VERSION.split("-")[0].split("+")[0].split(".") + ["0"]),
        )
        literal_definitions = []
        version_assignment = re.compile(r"^APP_VERSION\s*=\s*['\"]", re.MULTILINE)
        for directory in (ROOT / "hplc_app", ROOT / "scripts"):
            for path in directory.glob("*.py"):
                if version_assignment.search(path.read_text(encoding="utf-8")):
                    literal_definitions.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(literal_definitions, ["hplc_app/version.py"])

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory) / "version.py"
            temporary.write_text('APP_VERSION = "1.3.0-rc.2+build.5"\n', encoding="utf-8")
            self.assertEqual(read_version(temporary), "1.3.0-rc.2+build.5")
            self.assertEqual(
                windows_numeric_version(read_version(temporary)), "1.3.0.0"
            )
            for invalid_source in (
                'APP_VERSION = "01.3.0"\n',
                'APP_VERSION = "1.3"\n',
                'APP_VERSION = "1.3.0-01"\n',
                'APP_VERSION = make_version()\n',
                'OTHER_VERSION = "1.3.0"\n',
                'APP_VERSION = "1.3.0"\nAPP_VERSION = "1.3.1"\n',
            ):
                temporary.write_text(invalid_source, encoding="utf-8")
                with self.assertRaises(VersionError):
                    read_version(temporary)
            with self.assertRaises(VersionError):
                windows_numeric_version("65536.0.0")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "read_version.py"),
                    "--version-file",
                    str(temporary),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn("[ERROR]", completed.stderr)

        reader = ROOT / "scripts" / "read_version.py"
        completed = subprocess.run(
            [sys.executable, str(reader)],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), APP_VERSION)
        reader_source = reader.read_text(encoding="utf-8")
        self.assertNotIn("import hplc_app", reader_source)
        self.assertNotIn("PySide", reader_source)
        self.assertNotIn("numpy", reader_source.lower())
        self.assertNotIn("matplotlib", reader_source.lower())

        self.assertEqual(
            archive_root_name(APP_VERSION), "HPLC_Analyzer_MVP_" + APP_VERSION
        )
        self.assertEqual(
            default_archive_path(Path("C:/build"), APP_VERSION).name,
            "HPLC_Analyzer_{0}_Windows7_Offline_Build.zip".format(APP_VERSION),
        )
        for relative in (
            "build_windows11.bat",
            "build_windows7_offline.bat",
            "build_all_windows.bat",
            "package_windows7_offline_bundle.bat",
            "scripts/build_installer.bat",
        ):
            batch = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("load_version.bat", batch)
        installer_helper = (ROOT / "scripts" / "build_installer.bat").read_text(
            encoding="utf-8"
        )
        self.assertIn("/DAppVersion=%APP_VERSION%", installer_helper)
        self.assertIn(
            "/DAppVersionNumeric=%APP_VERSION_NUMERIC%", installer_helper
        )
        self.assertIn(
            "/DArtifactBaseName=%ARTIFACT_BASE_NAME%", installer_helper
        )

        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        project = Project(title="version propagation", datasets=[dataset])
        with tempfile.TemporaryDirectory() as directory:
            project_path = Path(directory) / "version.hplcproj"
            save_project(str(project_path), project)
            with zipfile.ZipFile(project_path) as archive:
                manifest = json.loads(archive.read("project.json").decode("utf-8"))
            self.assertEqual(manifest["application_version"], APP_VERSION)
            database_path = Path(directory) / "version.sqlite3"
            sync_project_to_database(str(database_path), project)
            with closing(sqlite3.connect(str(database_path))) as connection:
                saved_version = connection.execute(
                    "SELECT saved_with_version FROM projects"
                ).fetchone()[0]
            self.assertEqual(saved_version, APP_VERSION)

    def test_release_artifact_names_and_windows_metadata_are_canonical(self):
        stable = "1.3.0"
        prerelease = "1.3.0-rc.2+build.5"
        expected = {
            "windows11-installer": "HPLC_Analyzer_Setup_1.3.0_Windows11_x64.exe",
            "windows7-installer": "HPLC_Analyzer_Setup_1.3.0_Windows7_x86.exe",
            "windows7-offline": "HPLC_Analyzer_1.3.0_Windows7_Offline_Build.zip",
        }
        for target, filename in expected.items():
            self.assertEqual(artifact_filename(target, stable), filename)
        self.assertEqual(
            artifact_filename("windows11-installer", prerelease),
            "HPLC_Analyzer_Setup_1.3.0-rc.2+build.5_Windows11_x64.exe",
        )
        self.assertEqual(
            installer_basename("windows7-installer", stable),
            "HPLC_Analyzer_Setup_1.3.0_Windows7_x86",
        )
        with self.assertRaises(ValueError):
            installer_basename("windows7-offline", stable)

        release_resource = render_version_info(
            prerelease, "windows11-x64"
        )
        debug_resource = render_version_info(
            prerelease, "windows7-x86", debug=True
        )
        ast.parse(release_resource)
        ast.parse(debug_resource)
        self.assertIn("filevers=(1, 3, 0, 0)", release_resource)
        self.assertIn("'ProductVersion', '1.3.0-rc.2+build.5'", release_resource)
        self.assertIn("flags=0", release_resource)
        self.assertIn("flags=1", debug_resource)
        self.assertIn("HPLC Analyzer diagnostic console", debug_resource)
        self.assertEqual(
            expected_version_strings("windows7-x86", debug=True, version=stable)[
                "OriginalFilename"
            ],
            "HPLC_Analyzer_Debug.exe",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "version-info.txt"
            self.assertEqual(
                write_version_info(path, stable, "windows11-x64"), path
            )
            self.assertEqual(path.read_text(encoding="utf-8"), render_version_info(stable, "windows11-x64"))

        loader = (ROOT / "scripts" / "load_version.bat").read_text(
            encoding="utf-8"
        )
        spec = (ROOT / "HPLC_Analyzer.spec").read_text(encoding="utf-8")
        self.assertIn("artifact_names.py", loader)
        self.assertIn('version=version_file', spec)
        self.assertIn('version=debug_version_file', spec)
        self.assertIn("HPLC_VERSION_FILE", spec)
        self.assertIn("HPLC_DEBUG_VERSION_FILE", spec)
        for filename in expected.values():
            self.assertNotIn(filename.replace("1.3.0", "%APP_VERSION%"), loader)

    def test_release_checksums_cover_exactly_the_three_final_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            release_dir = Path(directory)
            targets = (
                "windows11-installer",
                "windows7-installer",
                "windows7-offline",
            )
            for index, target in enumerate(targets):
                (release_dir / artifact_filename(target, APP_VERSION)).write_bytes(
                    ("asset-{0}".format(index)).encode("ascii")
                )
            checksums = write_sha256sums(release_dir, APP_VERSION)
            self.assertEqual(checksums.name, "SHA256SUMS.txt")
            self.assertEqual(verify_sha256sums(release_dir, APP_VERSION), [])
            entries = read_sha256sums(checksums)
            self.assertEqual(
                set(entries),
                {
                    artifact_filename(target, APP_VERSION)
                    for target in targets
                },
            )
            with self.assertRaises(FileExistsError):
                write_sha256sums(release_dir, APP_VERSION)
            changed = release_dir / artifact_filename(
                "windows11-installer", APP_VERSION
            )
            changed.write_bytes(b"changed after checksum")
            self.assertEqual(
                verify_sha256sums(release_dir, APP_VERSION),
                ["SHA-256 mismatch: {0}".format(changed.name)],
            )
            checksums.write_text(
                "0" * 64 + "  ../outside.exe\n", encoding="ascii"
            )
            with self.assertRaises(ValueError):
                read_sha256sums(checksums)

    def test_release_consistency_checks_identity_schema_pins_and_assets(self):
        # The Windows 7 offline bundle and wheelhouse are large untracked local
        # assets, so they are only verified where they are actually present.
        offline_assets = (ROOT / "win7_offline").is_dir()
        self.assertEqual(
            verify_source_consistency(
                ROOT,
                "v" + APP_VERSION,
                PROJECT_SCHEMA_VERSION,
                offline_assets=offline_assets,
            ),
            [],
        )
        self.assertTrue(
            any(
                "does not match APP_VERSION" in error
                for error in verify_source_consistency(
                    ROOT, "9.9.9", PROJECT_SCHEMA_VERSION
                )
            )
        )
        self.assertTrue(
            any(
                "does not match source schema" in error
                for error in verify_source_consistency(
                    ROOT, APP_VERSION, PROJECT_SCHEMA_VERSION + 1
                )
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            unpinned = Path(directory) / "requirements.txt"
            unpinned.write_text("numpy>=1.20\nnumpy==1.20.3\n", encoding="utf-8")
            _pins, errors = read_pinned_requirements(unpinned)
            self.assertTrue(any("not an exact == pin" in error for error in errors))

            release_dir = Path(directory) / "release"
            release_dir.mkdir()
            for target in ("windows11-installer", "windows7-installer"):
                (release_dir / artifact_filename(target, APP_VERSION)).write_bytes(
                    b"synthetic installer"
                )
            offline = release_dir / artifact_filename(
                "windows7-offline", APP_VERSION
            )
            archive_root = "HPLC_Analyzer_MVP_" + APP_VERSION
            with zipfile.ZipFile(offline, "w") as archive:
                archive.writestr(
                    archive_root + "/hplc_app/version.py",
                    'APP_VERSION = "{0}"\n'.format(APP_VERSION),
                )
                archive.writestr(
                    archive_root + "/hplc_app/__init__.py",
                    "PROJECT_FORMAT_MAJOR = 1\nPROJECT_SCHEMA_VERSION = {0}\n".format(
                        PROJECT_SCHEMA_VERSION
                    ),
                )
            self.assertEqual(
                verify_offline_archive(offline, APP_VERSION, PROJECT_SCHEMA_VERSION),
                [],
            )
            write_sha256sums(release_dir, APP_VERSION)
            metadata = {
                "CompanyName": "Research Tools",
                "ProductName": "HPLC Analyzer",
                "ProductVersion": APP_VERSION,
            }
            numeric = tuple(
                int(part)
                for part in windows_numeric_version(APP_VERSION).split(".")
            )

            def fake_metadata(path):
                values = dict(metadata)
                platform = (
                    "Windows 11 64-bit"
                    if "Windows11" in Path(path).name
                    else "Windows 7 32-bit"
                )
                values["FileDescription"] = (
                    "HPLC Analyzer {0} installer for {1}".format(
                        APP_VERSION, platform
                    )
                )
                return values, numeric

            with mock.patch(
                "scripts.release_consistency.verify_source_consistency",
                return_value=[],
            ), mock.patch(
                "scripts.release_consistency._read_pe_metadata",
                side_effect=fake_metadata,
            ):
                self.assertEqual(
                    verify_release_assets(
                        ROOT,
                        release_dir,
                        APP_VERSION,
                        PROJECT_SCHEMA_VERSION,
                    ),
                    [],
                )
                (release_dir / "unapproved-debug.exe").write_bytes(b"extra")
                self.assertTrue(
                    any(
                        "unexpected file in Release directory" in error
                        for error in verify_release_assets(
                            ROOT,
                            release_dir,
                            APP_VERSION,
                            PROJECT_SCHEMA_VERSION,
                        )
                    )
                )

    def test_project_round_trip_embeds_raw_ascii_and_origin(self):
        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        dataset.label = "CaM-LL37 cleavage 16 h"
        dataset.measurement.aux_range_au_per_v = 1.0
        dataset.measurement.gradient = [GradientPoint(0, 90, 10, 0, 0, 1.0)]
        dataset.measurement.group = "preserved group"
        dataset.y_axis = 2
        dataset.line_style = "dash_dot"
        project = Project(
            title="roundtrip",
            analysis_date="20260809",
            column_name="COSMOSIL C4",
            condition_name="RP-C4",
            author="MShiba",
            work_directories=[
                WorkDirectory(
                    path="C:/HPLC/pac1", label="pac1", recursive=True
                ),
                WorkDirectory(
                    path="D:/HPLC/pac2", label="pac2", enabled=False
                ),
            ],
            datasets=[dataset],
            condition_presets={"280 nm": {"wavelength_nm": 280.0, "aux_range_au_per_v": 1.0}},
            gradient_presets={
                "RP-C4": {
                    "gradient": [{"time_min": 0.0, "a_pct": 90.0, "b_pct": 10.0, "c_pct": 0.0, "d_pct": 0.0, "flow_ml_min": 1.0}],
                    "solvents": {},
                }
            },
        )
        dataset.x_shift_min = 0.25
        dataset.gradient_preset_name = "RP-C4"
        project.method.show_gradient_b = True
        project.method.show_major_grid = True
        project.method.gradient_legend_include_dataset_name = True
        project.method.legend_location = "upper left"
        project.method.legend_components = [
            "run_id",
            "label",
            "timestamp",
            "wavelength",
            "column",
        ]
        project.method.legend_separator = " | "
        project.method.gradient_axis_label = "ACN (%)"
        project.method.show_retention_labels = True
        project.method.zoom_axis = "x"
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "roundtrip.hplcproj")
            save_project(path, project)
            loaded = load_project(path)
            self.assertEqual(len(loaded.datasets), 1)
            restored = loaded.datasets[0]
            self.assertEqual(restored.label, dataset.label)
            self.assertEqual(restored.original_path, dataset.original_path)
            self.assertEqual(restored.raw_bytes, dataset.raw_bytes)
            self.assertEqual(restored.time_min.size, 54001)
            self.assertEqual(restored.measurement.gradient[0].b_pct, 10)
            self.assertEqual(restored.measurement.group, "preserved group")
            self.assertEqual(restored.run_id, dataset.run_id)
            self.assertEqual(restored.y_axis, 2)
            self.assertEqual(restored.line_style, "dash_dot")
            self.assertEqual(restored.x_shift_min, 0.25)
            self.assertEqual(restored.gradient_preset_name, "RP-C4")
            self.assertEqual(loaded.condition_presets["280 nm"]["wavelength_nm"], 280.0)
            self.assertIn("RP-C4", loaded.gradient_presets)
            self.assertTrue(loaded.method.show_gradient_b)
            self.assertTrue(loaded.method.show_major_grid)
            self.assertTrue(loaded.method.gradient_legend_include_dataset_name)
            self.assertEqual(loaded.method.legend_location, "upper left")
            self.assertEqual(
                loaded.method.legend_components,
                ["run_id", "label", "timestamp", "wavelength", "column"],
            )
            self.assertEqual(loaded.method.legend_separator, " | ")
            self.assertEqual(loaded.method.gradient_axis_label, "ACN (%)")
            self.assertTrue(loaded.method.show_retention_labels)
            self.assertEqual(loaded.method.zoom_axis, "x")
            self.assertEqual(loaded.project_id, project.project_id)
            self.assertEqual(loaded.analysis_date, "20260809")
            self.assertEqual(loaded.column_name, "COSMOSIL C4")
            self.assertEqual(loaded.condition_name, "RP-C4")
            self.assertEqual(loaded.author, "MShiba")
            self.assertEqual(loaded.work_directories, project.work_directories)
            with zipfile.ZipFile(path, "r") as archive:
                manifest = json.loads(archive.read("project.json").decode("utf-8"))
            self.assertEqual(manifest["format_major"], PROJECT_FORMAT_MAJOR)
            self.assertEqual(manifest["schema_version"], PROJECT_SCHEMA_VERSION)
            self.assertEqual(manifest["datasets"][0]["line_style"], "dash_dot")
            self.assertNotIn("preset_metadata", manifest)

    def test_readable_run_ids_are_allocated_once_in_project_order(self):
        first = Dataset(label="試料_A", measurement=MeasurementMetadata(
            acquisition_datetime="2026-08-29T14:30:52"))
        second = Dataset(label="試料_A", measurement=deepcopy(first.measurement))
        project = Project(datasets=[first, second])
        self.assertEqual(first.run_id, "20260829_143052_1_試料_A")
        self.assertEqual(second.run_id, "20260829_143052_2_試料_A")
        original_ids = [first.run_id, second.run_id]
        first.label = "changed"
        first.measurement.acquisition_datetime = "2020-01-01T00:00:00"
        project.datasets.reverse()
        project.rebuild_run_index()
        self.assertEqual([first.run_id, second.run_id], original_ids)
        project.remove_dataset_at(0)
        third = Dataset(label="third", measurement=MeasurementMetadata(
            acquisition_datetime="2026/08/28 10:20:30"))
        project.add_dataset(third)
        self.assertEqual(third.run_id, "20260828_102030_3_third")

    def test_unknown_run_datetime_never_uses_import_time(self):
        for timestamp in ("", "not a date", "2026-08-29", "2026-02-30T10:00:00"):
            with self.subTest(timestamp=timestamp):
                dataset = Dataset(label="A", imported_at="2099-12-31T23:59:59",
                                  measurement=MeasurementMetadata(
                                      acquisition_datetime=timestamp))
                Project(datasets=[dataset])
                self.assertEqual(dataset.run_id, "unknown-datetime_1_A")
                self.assertEqual(dataset.measurement.acquisition_datetime, timestamp)

    def test_run_rename_is_atomic_local_and_never_groups(self):
        first, second, other = Dataset(label="A"), Dataset(label="B"), Dataset(label="C")
        project = Project(datasets=[first, other])
        run = project.run_for(first)
        project.add_dataset(second, run=run)
        elsewhere = Project(datasets=[Dataset(label="A")])
        self.assertEqual(elsewhere.runs[0].id, run.id)
        old_id = run.id
        counter = project.next_run_number
        self.assertTrue(project.rename_run(run, "任意の ID_ / 280"))
        self.assertEqual(first.run_id, second.run_id)
        self.assertIs(project.run_for(second), run)
        self.assertNotIn(old_id, project._run_index)
        self.assertEqual(elsewhere.runs[0].id, old_id)
        self.assertEqual([first.label, second.label], ["A", "A"])
        for candidate, message in (("  ", "run_id_empty"), (other.run_id, "run_id_duplicate")):
            with self.assertRaisesRegex(ValueError, message):
                project.rename_run(run, candidate)
            self.assertEqual(run.id, "任意の ID_ / 280")
            self.assertEqual(len(project.runs), 2)
            self.assertIs(project.run_for(first), project.run_for(second))
        self.assertFalse(project.rename_run(run, " 任意の ID_ / 280 "))
        self.assertEqual(project.next_run_number, counter)
        with self.assertRaisesRegex(ValueError, "belong to the Project"):
            project.rename_run(elsewhere.runs[0], "foreign")

    def test_run_number_survives_group_split_delete_rename_and_round_trip(self):
        first = load_ascii_file(str(SAMPLES / "210601.TXT"))
        second = load_ascii_file(str(SAMPLES / "225120.TXT"))
        project = Project(datasets=[first, second])
        run = project.run_for(first)
        original_id = run.id
        project.group_datasets_into_run([first, second], run)
        self.assertEqual(run.id, original_id)
        project.ungroup_datasets([second])
        self.assertIn("_3_", second.run_id)
        project.rename_run(run, "自由編集したID")
        project.remove_dataset_at(1)
        first.peaks = [PeakRegion(start_min=1.0, end_min=2.0)]
        recalculate_dataset_peaks(first)
        peak = deepcopy(first.peaks[0])
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "ids.hplcproj")
            save_project(path, project)
            restored = load_project(path)
            self.assertEqual(restored.next_run_number, 4)
            loaded = restored.datasets[0]
            self.assertEqual(loaded.run_id, "自由編集したID")
            self.assertEqual(loaded.raw_bytes, first.raw_bytes)
            np.testing.assert_array_equal(loaded.time_min, first.time_min)
            np.testing.assert_array_equal(loaded.intensity_uv, first.intensity_uv)
            self.assertEqual(loaded.peaks[0], peak)
            self.assertEqual(loaded.measurement, first.measurement)
            restored.remove_dataset_at(0)
            save_project(path, restored)
            empty = load_project(path)
            new = Dataset(label="new")
            empty.add_dataset(new)
            self.assertEqual(new.run_id, "unknown-datetime_4_new")

    def test_run_number_skips_user_reserved_ids_without_implicit_merge(self):
        project = Project(datasets=[Dataset(label="A")])
        project.rename_run(project.runs[0], "unknown-datetime_2_A")
        new = Dataset(label="A")
        project.add_dataset(new)
        self.assertEqual(new.run_id, "unknown-datetime_3_A")
        self.assertEqual(len(project.runs), 2)

    def test_schema_105_preserves_ids_and_initializes_run_number(self):
        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        dataset.run_id = "legacy-uuid"
        project = Project(datasets=[dataset])
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "legacy.hplcproj")
            save_project(path, project)
            with zipfile.ZipFile(path) as archive:
                contents = {name: archive.read(name) for name in archive.namelist()}
            manifest = json.loads(contents["project.json"])
            manifest["schema_version"] = 105
            del manifest["next_run_number"]
            untouched = deepcopy(manifest)
            migrated = migrate_project_manifest(manifest)
            self.assertEqual(manifest, untouched)
            self.assertEqual(migrated["runs"], manifest["runs"])
            self.assertEqual(migrated["datasets"], manifest["datasets"])
            self.assertEqual(migrated["next_run_number"], 2)
            self.assertEqual(migrate_project_manifest(migrated), migrated)
            contents["project.json"] = json.dumps(manifest).encode("utf-8")
            with zipfile.ZipFile(path, "w") as archive:
                for name, payload in contents.items():
                    archive.writestr(name, payload)
            restored = load_project(path)
            self.assertEqual(restored.datasets[0].run_id, "legacy-uuid")
            new = Dataset(label="new")
            restored.add_dataset(new)
            self.assertEqual(new.run_id, "unknown-datetime_2_new")
            for invalid in (0, -1, True, "3", 1.5, None):
                manifest["next_run_number"] = invalid
                contents["project.json"] = json.dumps(manifest).encode("utf-8")
                with zipfile.ZipFile(path, "w") as archive:
                    for name, payload in contents.items():
                        archive.writestr(name, payload)
                with self.assertRaisesRegex(ProjectError, "positive integer"):
                    load_project(path)

    def test_shared_run_is_authoritative_and_dataset_channels_stay_independent(self):
        run = Run(
            id="run-shared",
            timestamp="2026-05-07T12:00:00",
            sample_name="sample A",
            column_name="C4",
            cell_path_length_cm=0.2,
            molar_absorptivity_214=12500.0,
            molar_absorptivity_280=8500.0,
        )
        first = Dataset(
            run_id=run.id,
            label="214 channel",
            measurement=MeasurementMetadata(
                wavelength_nm=214.0, aux_range_au_per_v=1.0
            ),
        )
        second = Dataset(
            run_id=run.id,
            label="280 channel",
            measurement=MeasurementMetadata(
                wavelength_nm=280.0, aux_range_au_per_v=2.0
            ),
        )
        project = Project(runs=[run], datasets=[first, second])

        self.assertIs(project.run_for(first), run)
        self.assertIs(project._run_index[run.id], run)
        self.assertIs(first.bound_run(), second.bound_run())
        first.measurement.sample_name = "renamed sample"
        first.gradient_preset_name = "10-90 B"
        self.assertEqual(second.measurement.sample_name, "renamed sample")
        self.assertEqual(second.gradient_preset_name, "10-90 B")
        self.assertEqual(first.measurement.wavelength_nm, 214.0)
        self.assertEqual(second.measurement.wavelength_nm, 280.0)
        self.assertEqual(first.measurement.aux_range_au_per_v, 1.0)
        self.assertEqual(second.measurement.aux_range_au_per_v, 2.0)
        self.assertEqual(first.label, "214 channel")
        self.assertEqual(second.label, "214 channel")
        second.label = "shared display label"
        second.short_label = "shared"
        self.assertEqual(first.label, "shared display label")
        self.assertEqual(first.short_label, "shared")
        first.measurement = MeasurementMetadata(
            sample_name="replacement metadata",
            column_name="C18",
            wavelength_nm=220.0,
            aux_range_au_per_v=4.0,
        )
        self.assertEqual(run.sample_name, "replacement metadata")
        self.assertEqual(second.measurement.column_name, "C18")
        self.assertEqual(first.measurement.wavelength_nm, 220.0)
        self.assertEqual(second.measurement.wavelength_nm, 280.0)

    def test_legend_composer_uses_order_skips_empty_values_and_preserves_authority(self):
        run = Run(
            id="run-42",
            label="Sample A",
            timestamp="2026-08-27T12:00:00",
            column_name="C4",
        )
        dataset = Dataset(
            run_id=run.id,
            label="Sample A",
            measurement=MeasurementMetadata(wavelength_nm=280.0),
        )
        project = Project(runs=[run], datasets=[dataset])
        project.method.legend_components = [
            "run_id",
            "label",
            "analyte_name",
            "timestamp",
            "wavelength",
            "column",
        ]
        project.method.legend_separator = "*"
        before = deepcopy(run)

        self.assertEqual(
            project.legend_label_for(dataset),
            "run-42*Sample A*2026-08-27T12:00:00*280 nm*C4",
        )
        self.assertEqual(run, before)
        dataset.label = "Sample A 280 nm"
        dataset.short_label = "Sample A 280 nm"
        self.assertEqual(
            project.legend_label_for(dataset),
            "run-42*Sample A 280 nm*2026-08-27T12:00:00*C4",
        )

        project.method.legend_components = ["run_id", "column"]
        project.method.legend_separator = ""
        self.assertEqual(project.legend_label_for(dataset), "run-42C4")

    def test_shared_run_round_trip_preserves_sources_peaks_and_channel_values(self):
        first = load_ascii_file(str(SAMPLES / "210601.TXT"))
        second = load_ascii_file(str(SAMPLES / "225120.TXT"))
        first.measurement.wavelength_nm = 214.0
        first.measurement.aux_range_au_per_v = 1.0
        second.measurement.wavelength_nm = 280.0
        second.measurement.aux_range_au_per_v = 2.0
        first.peaks = [PeakRegion(start_min=1.0, end_min=2.0)]
        recalculate_dataset_peaks(first)
        run = Run.from_measurement(first.measurement, run_id="run-two-channel")
        run.sample_name = "shared sample"
        first.run_id = run.id
        second.run_id = run.id
        project = Project(runs=[run], datasets=[first, second])
        original_time = [dataset.time_min.copy() for dataset in project.datasets]
        original_signal = [dataset.intensity_uv.copy() for dataset in project.datasets]
        original_raw = [dataset.raw_bytes for dataset in project.datasets]
        original_sources = [deepcopy(dataset.source_metadata) for dataset in project.datasets]
        original_peak = deepcopy(first.peaks[0])

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "shared-run.hplcproj")
            save_project(path, project)
            loaded = load_project(path)

        self.assertEqual(len(loaded.runs), 1)
        self.assertEqual(loaded.runs[0].timestamp, "2026-05-07T19:33:54")
        self.assertEqual(
            [dataset.run_id for dataset in loaded.datasets],
            ["run-two-channel", "run-two-channel"],
        )
        self.assertIs(loaded.datasets[0].bound_run(), loaded.datasets[1].bound_run())
        self.assertEqual(loaded.datasets[0].label, loaded.datasets[1].label)
        self.assertEqual(loaded.datasets[0].short_label, loaded.datasets[1].short_label)
        self.assertEqual(loaded.datasets[0].measurement.sample_name, "shared sample")
        self.assertEqual(
            [dataset.measurement.wavelength_nm for dataset in loaded.datasets],
            [214.0, 280.0],
        )
        self.assertEqual(
            [dataset.measurement.aux_range_au_per_v for dataset in loaded.datasets],
            [1.0, 2.0],
        )
        for index, dataset in enumerate(loaded.datasets):
            np.testing.assert_array_equal(dataset.time_min, original_time[index])
            np.testing.assert_array_equal(dataset.intensity_uv, original_signal[index])
            self.assertEqual(dataset.raw_bytes, original_raw[index])
            self.assertEqual(dataset.source_metadata, original_sources[index])
        restored_peak = loaded.datasets[0].peaks[0]
        self.assertEqual(restored_peak.start_min, original_peak.start_min)
        self.assertEqual(restored_peak.end_min, original_peak.end_min)
        self.assertEqual(restored_peak.retention_time_min, original_peak.retention_time_min)
        self.assertEqual(restored_peak.raw_area_uv_sec, original_peak.raw_area_uv_sec)
        self.assertEqual(restored_peak.area_mau_sec, original_peak.area_mau_sec)

    def test_run_values_win_conflicts_and_are_projected_for_old_readers(self):
        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        dataset.measurement.sample_name = "run authority"
        dataset.measurement.column_name = "Run C4"
        dataset.measurement.wavelength_nm = 280.0
        project = Project(datasets=[dataset])
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "run-authority.hplcproj")
            save_project(path, project)
            with zipfile.ZipFile(path, "r") as source:
                contents = {name: source.read(name) for name in source.namelist()}
            manifest = json.loads(contents["project.json"].decode("utf-8"))
            run_label = manifest["runs"][0]["label"]
            manifest["datasets"][0]["label"] = "legacy label conflict"
            manifest["datasets"][0]["short_label"] = "legacy short conflict"
            manifest["datasets"][0]["measurement"]["sample_name"] = "legacy conflict"
            manifest["datasets"][0]["measurement"]["column_name"] = "Legacy C18"
            manifest["datasets"][0]["measurement"]["wavelength_nm"] = 214.0
            contents["project.json"] = json.dumps(
                manifest, ensure_ascii=False
            ).encode("utf-8")
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as destination:
                for name, payload in contents.items():
                    destination.writestr(name, payload)

            loaded = load_project(path)
            restored = loaded.datasets[0]
            self.assertEqual(restored.label, run_label)
            self.assertEqual(restored.short_label, manifest["runs"][0]["short_label"])
            self.assertEqual(restored.measurement.sample_name, "run authority")
            self.assertEqual(restored.measurement.column_name, "Run C4")
            self.assertEqual(restored.measurement.wavelength_nm, 214.0)
            save_project(path, loaded)
            with zipfile.ZipFile(path, "r") as archive:
                resaved = json.loads(archive.read("project.json").decode("utf-8"))
            compatibility = resaved["datasets"][0]["measurement"]
            self.assertEqual(resaved["datasets"][0]["label"], run_label)
            self.assertEqual(
                resaved["datasets"][0]["short_label"],
                resaved["runs"][0]["short_label"],
            )
            self.assertEqual(compatibility["sample_name"], "run authority")
            self.assertEqual(compatibility["column_name"], "Run C4")
            self.assertEqual(compatibility["wavelength_nm"], 214.0)

    def test_project_load_rejects_missing_and_duplicate_run_references(self):
        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        project = Project(datasets=[dataset])
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "invalid-runs.hplcproj")
            save_project(path, project)
            with zipfile.ZipFile(path, "r") as source:
                original = {name: source.read(name) for name in source.namelist()}

            def write_manifest(manifest):
                contents = dict(original)
                contents["project.json"] = json.dumps(manifest).encode("utf-8")
                with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as destination:
                    for name, payload in contents.items():
                        destination.writestr(name, payload)

            missing = json.loads(original["project.json"].decode("utf-8"))
            missing["datasets"][0]["run_id"] = "missing-run"
            write_manifest(missing)
            with self.assertRaisesRegex(ProjectError, "Run reference is invalid"):
                load_project(path)

            duplicate = json.loads(original["project.json"].decode("utf-8"))
            duplicate["runs"].append(deepcopy(duplicate["runs"][0]))
            write_manifest(duplicate)
            with self.assertRaisesRegex(ProjectError, "Run collection is invalid"):
                load_project(path)

    def test_screen_render_quality_is_not_written_to_project_files(self):
        project = Project(datasets=[load_ascii_file(str(SAMPLES / "210601.TXT"))])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screen-setting.hplcproj"
            save_project(str(path), project)
            with zipfile.ZipFile(path) as archive:
                manifest_text = archive.read("project.json").decode("utf-8")
        self.assertNotIn("render_quality", manifest_text)
        self.assertNotIn("lightweight", manifest_text)

    def test_condition_presets_strip_display_labels_on_save_and_load(self):
        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        project = Project(
            datasets=[dataset],
            condition_presets={
                "C4 condition": {
                    "label": "Must not be saved",
                    "short_label": "Must not be saved either",
                    "wavelength_nm": 280.0,
                    "column_name": "C4",
                }
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "condition-preset.hplcproj")
            save_project(path, project)
            with zipfile.ZipFile(path, "r") as archive:
                manifest = json.loads(archive.read("project.json").decode("utf-8"))
            saved = manifest["condition_presets"]["C4 condition"]
            self.assertNotIn("label", saved)
            self.assertNotIn("short_label", saved)
            self.assertEqual(saved["wavelength_nm"], 280.0)
            restored = load_project(path)
            self.assertEqual(
                restored.condition_presets["C4 condition"],
                {"wavelength_nm": 280.0, "column_name": "C4"},
            )

    def test_ui_uses_platform_font_while_plot_defaults_remain_arial(self):
        launcher = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("app.setFont(", launcher)
        method = AnalysisMethod()
        self.assertEqual(method.axis_label_font_family, "Arial")
        self.assertEqual(method.tick_label_font_family, "Arial")
        self.assertEqual(method.legend_font_family, "Arial")
        self.assertEqual(method.retention_label_font_family, "Arial")
        self.assertEqual(TextAnnotation().font_family, "Arial")

    def test_new_method_and_peak_source_round_trip(self):
        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        dataset.peaks = [
            PeakRegion(
                start_min=1.0,
                end_min=2.0,
                integration_source="auto",
                notes="identified as LL-37",
            )
        ]
        recalculate_dataset_peaks(dataset)
        dataset.peaks[0].fit_model = "gaussian"
        dataset.peaks[0].fit_parameters = {
            "amplitude_uv": 1200.0,
            "center_min": 1.5,
            "sigma_min": 0.2,
        }
        dataset.peaks[0].fit_retention_time_min = 1.5
        dataset.peaks[0].fit_rmse_uv = 3.0
        dataset.peaks[0].fit_r_squared = 0.998
        dataset.peaks[0].fit_aic = 42.0
        fitted_peak = fitted_peak_from_result(
            dataset.peaks[0],
            PeakFitResult(
                "gaussian",
                dict(dataset.peaks[0].fit_parameters),
                1.5,
                3.0,
                0.998,
                42.0,
                25,
            ),
        )
        dataset.fitted_peaks = [fitted_peak]
        mirror_fitted_peak_for_legacy(dataset.peaks[0], fitted_peak)
        project = Project(datasets=[dataset])
        project.method.view_mode = "overview_detail"
        project.method.x_tick_mode = "manual"
        project.method.x_major_tick_min = 2.0
        project.method.x_minor_tick_min = 0.5
        project.method.line_width = 2.4
        project.method.axis_label_font_family = "Arial"
        project.method.axis_label_font_size = 12.0
        project.method.axis_label_color = "#123456"
        project.method.retention_label_font_size = 11.5
        project.method.retention_label_color = "#000000"
        project.method.auto_peak_snr_threshold = 8.0
        dataset.measurement.analyte_name = "LL-37"
        dataset.measurement.analyte_id = "analyte-ll37"
        dataset.measurement.analyte_aliases = ["CAP18", "hCAP-18"]
        dataset.measurement.analyte_source = "UniProt P49913"
        dataset.measurement.extinction_coefficient_unit = "M^-1 cm^-1"
        project.annotations = [
            TextAnnotation(
                text="LL-37",
                x_min=1.5,
                y_value=1200.0,
                dataset_id=dataset.id,
                font_family="Arial",
                font_size=12.0,
                color="#123456",
            )
        ]
        project.vertical_markers = [
            VerticalMarker(x_min=7.25, y_axis=2, color="#123456")
        ]
        project.fraction_regions = [
            FractionRegion(start_min=2.0, end_min=8.0, interval_min=1.5)
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "new_fields.hplcproj")
            save_project(path, project)
            loaded = load_project(path)
            self.assertEqual(loaded.method.view_mode, "overview_detail")
            self.assertEqual(loaded.method.x_tick_mode, "manual")
            self.assertEqual(loaded.method.x_major_tick_min, 2.0)
            self.assertEqual(loaded.method.x_minor_tick_min, 0.5)
            self.assertEqual(loaded.method.line_width, 2.4)
            self.assertEqual(loaded.method.axis_label_font_family, "Arial")
            self.assertEqual(loaded.method.axis_label_color, "#123456")
            self.assertEqual(loaded.method.retention_label_font_size, 11.5)
            self.assertEqual(loaded.method.retention_label_color, "#000000")
            loaded.method.x_major_tick_min = 0.05
            loaded.method.x_minor_tick_min = 0.01
            save_project(path, loaded)
            legacy_spacing = load_project(path)
            self.assertEqual(legacy_spacing.method.x_major_tick_min, 0.05)
            self.assertEqual(legacy_spacing.method.x_minor_tick_min, 0.01)
            self.assertEqual(loaded.method.auto_peak_snr_threshold, 8.0)
            loaded_meta = loaded.datasets[0].measurement
            self.assertEqual(loaded_meta.analyte_id, "analyte-ll37")
            self.assertEqual(loaded_meta.analyte_aliases, ["CAP18", "hCAP-18"])
            self.assertEqual(loaded_meta.analyte_source, "UniProt P49913")
            self.assertEqual(
                loaded_meta.extinction_coefficient_unit, "M^-1 cm^-1"
            )
            self.assertEqual(loaded.datasets[0].peaks[0].integration_source, "auto")
            self.assertEqual(loaded.datasets[0].peaks[0].notes, "identified as LL-37")
            self.assertEqual(loaded.datasets[0].peaks[0].fit_model, "gaussian")
            self.assertEqual(
                loaded.datasets[0].peaks[0].fit_parameters["sigma_min"], 0.2
            )
            self.assertEqual(loaded.datasets[0].peaks[0].fit_r_squared, 0.998)
            self.assertEqual(len(loaded.datasets[0].fitted_peaks), 1)
            loaded_fit = loaded.datasets[0].fitted_peaks[0]
            self.assertTrue(loaded_fit.is_fitted)
            self.assertEqual(
                loaded_fit.parent_peak_id, loaded.datasets[0].peaks[0].id
            )
            self.assertEqual(loaded_fit.fit_model, "gaussian")
            self.assertIsNone(loaded_fit.area_percent)
            self.assertEqual(len(loaded.annotations), 1)
            self.assertEqual(loaded.annotations[0].text, "LL-37")
            self.assertEqual(loaded.annotations[0].dataset_id, dataset.id)
            self.assertEqual(loaded.annotations[0].font_family, "Arial")
            self.assertEqual(len(loaded.vertical_markers), 1)
            self.assertAlmostEqual(loaded.vertical_markers[0].x_min, 7.25)
            self.assertEqual(loaded.vertical_markers[0].y_axis, 2)
            self.assertEqual(loaded.vertical_markers[0].color, "#123456")
            self.assertEqual(len(loaded.fraction_regions), 1)
            self.assertEqual(loaded.fraction_regions[0].start_min, 2.0)
            self.assertEqual(loaded.fraction_regions[0].end_min, 8.0)
            self.assertEqual(loaded.fraction_regions[0].interval_min, 1.5)

    def test_peak_area_seconds_round_trip_and_v113_migration(self):
        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        dataset.measurement.aux_range_au_per_v = 2.0
        dataset.peaks = [PeakRegion(start_min=1.0, end_min=2.0)]
        recalculate_dataset_peaks(dataset)
        project = Project(datasets=[dataset])
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "area-units.hplcproj")
            save_project(path, project)
            with zipfile.ZipFile(path, "r") as source:
                contents = {name: source.read(name) for name in source.namelist()}
            manifest = json.loads(contents["project.json"].decode("utf-8"))
            saved_peak = manifest["datasets"][0]["peaks"][0]
            self.assertAlmostEqual(
                saved_peak["raw_area_uv_sec"], saved_peak["raw_area_uv_min"] * 60.0
            )
            self.assertAlmostEqual(
                saved_peak["area_mau_sec"], saved_peak["area_mau_min"] * 60.0
            )

            # Simulate a v1.1.3 project that has only minute-based area fields.
            manifest["schema_version"] = 101
            manifest["application_version"] = "1.1.3"
            saved_peak.pop("raw_area_uv_sec")
            saved_peak.pop("area_mau_sec")
            contents["project.json"] = json.dumps(
                manifest, ensure_ascii=False
            ).encode("utf-8")
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as destination:
                for name, payload in contents.items():
                    destination.writestr(name, payload)
            loaded = load_project(path)
            migrated = loaded.datasets[0].peaks[0]
            self.assertAlmostEqual(
                migrated.raw_area_uv_sec, migrated.raw_area_uv_min * 60.0
            )
            self.assertAlmostEqual(
                migrated.area_mau_sec, migrated.area_mau_min * 60.0
            )

    def test_v100_project_migrates_new_optional_fields_and_arial_defaults(self):
        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        dataset.peaks = [PeakRegion(start_min=1.0, end_min=2.0)]
        recalculate_dataset_peaks(dataset)
        project = Project(datasets=[dataset])
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "v100.hplcproj")
            save_project(path, project)
            with zipfile.ZipFile(path, "r") as source:
                contents = {name: source.read(name) for name in source.namelist()}
            manifest = json.loads(contents["project.json"].decode("utf-8"))
            manifest["schema_version"] = 100
            manifest["application_version"] = "1.0.0"
            manifest.pop("annotations", None)
            for field in (
                "axis_label_font_family",
                "tick_label_font_family",
                "legend_font_family",
                "retention_label_font_family",
            ):
                manifest["method"][field] = ""
            manifest["datasets"][0]["peaks"][0].pop("notes", None)
            contents["project.json"] = json.dumps(
                manifest, ensure_ascii=False
            ).encode("utf-8")
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as destination:
                for name, payload in contents.items():
                    destination.writestr(name, payload)
            loaded = load_project(path)
            self.assertEqual(loaded.annotations, [])
            self.assertEqual(loaded.datasets[0].peaks[0].notes, "")
            self.assertEqual(loaded.method.axis_label_font_family, "Arial")
            self.assertEqual(loaded.method.tick_label_font_family, "Arial")
            self.assertEqual(loaded.method.legend_font_family, "Arial")
            self.assertEqual(loaded.method.retention_label_font_family, "Arial")

    def test_v06_projects_migrate_to_cursor_sensitive_wheel_zoom(self):
        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        project = Project(datasets=[dataset])
        project.method.zoom_axis = "y"
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "legacy.hplcproj")
            save_project(path, project)
            with zipfile.ZipFile(path, "r") as source:
                contents = {
                    name: source.read(name) for name in source.namelist()
                }
            manifest = json.loads(contents["project.json"].decode("utf-8"))
            manifest["schema_version"] = 6
            manifest["application_version"] = "0.6.0"
            manifest.pop("format_major", None)
            manifest.pop("project_id", None)
            manifest["method"]["zoom_axis"] = "y"
            contents["project.json"] = json.dumps(
                manifest, ensure_ascii=False, indent=2
            ).encode("utf-8")
            with zipfile.ZipFile(
                path, "w", compression=zipfile.ZIP_DEFLATED
            ) as destination:
                for name, payload in contents.items():
                    destination.writestr(name, payload)
            loaded = load_project(path)
            self.assertEqual(loaded.method.zoom_axis, "auto")
            self.assertTrue(loaded.project_id)

    def test_v1_reader_accepts_later_v1_schema_with_known_fields(self):
        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        project = Project(title="v1 compatibility", datasets=[dataset])
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "future-v1.hplcproj")
            save_project(path, project)
            with zipfile.ZipFile(path, "r") as source:
                contents = {name: source.read(name) for name in source.namelist()}
            manifest = json.loads(contents["project.json"].decode("utf-8"))
            manifest["schema_version"] = PROJECT_SCHEMA_VERSION + 1
            manifest["application_version"] = "1.1.0"
            manifest["future_optional_field"] = {"preserved_by_future_reader": True}
            contents["project.json"] = json.dumps(manifest).encode("utf-8")
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as destination:
                for name, payload in contents.items():
                    destination.writestr(name, payload)
            loaded = load_project(path)
            self.assertEqual(loaded.project_id, project.project_id)
            self.assertEqual(loaded.title, "v1 compatibility")
            save_project(path, loaded)
            with zipfile.ZipFile(path, "r") as archive:
                resaved = json.loads(
                    archive.read("project.json").decode("utf-8")
                )
            self.assertNotIn("future_optional_field", resaved)

    def test_standardized_project_filename_suggestion(self):
        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        dataset.label = "LL-37 cleavage"
        dataset.measurement.sample_name = "LL-37"
        dataset.measurement.column_name = "5C4-AR-300"
        dataset.measurement.method_name = "10-90% B"
        project = Project(datasets=[dataset])
        parts = suggest_project_name_parts(
            project, default_author="M Shiba", today="20260809"
        )
        self.assertEqual(parts["date"], "20260507")
        self.assertEqual(
            build_project_filename(parts),
            "20260507_LL-37_5C4-AR-300_10-90%-B_M-Shiba.hplcproj",
        )
        self.assertEqual(
            timestamped_filename(
                "analysis_report_Sample.pdf", datetime(2026, 9, 9, 14, 25, 30)
            ),
            "analysis_report_Sample_20260909_142530.pdf",
        )

    def test_lab_database_rewrites_same_project_without_peak_results(self):
        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        dataset.label = dataset.short_label = "LL-37"
        dataset.measurement.sample_name = "LL-37"
        dataset.measurement.wavelength_nm = 280.0
        dataset.measurement.column_name = "COSMOSIL C4"
        dataset.measurement.method_name = "RP-C4"
        dataset.measurement.gradient = [
            GradientPoint(0.0, 90.0, 10.0, 0.0, 0.0, 1.0),
            GradientPoint(20.0, 10.0, 90.0, 0.0, 0.0, 1.0),
        ]
        dataset.peaks = [PeakRegion(start_min=3.0, end_min=5.0)]
        recalculate_dataset_peaks(dataset)
        project = Project(
            title="Cleavage",
            analysis_date="20260809",
            column_name="COSMOSIL C4",
            condition_name="RP-C4",
            author="MShiba",
            datasets=[dataset],
        )
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, "HPLC_Lab_Database.sqlite3")
            project.project_path = os.path.join(directory, "cleavage.hplcproj")
            sync_project_to_database(database_path, project)
            sections = database_sections(database_path)
            self.assertEqual({key: len(value[1]) for key, value in sections.items()}, {
                "projects": 1, "datasets": 1, "gradients": 1
            })
            self.assertEqual(sections["datasets"][1][0][5], 280.0)
            self.assertEqual(sections["datasets"][1][0][6], "LL-37")
            self.assertIn("0 min: A 90%", sections["gradients"][1][0][2])
            self.assertIn("20 min: A 10%", sections["gradients"][1][0][2])
            # sqlite3.Connection.__exit__ commits or rolls back but does not close.
            # Keep the explicit closing wrapper so Windows can delete the temporary
            # database as soon as this assertion finishes.
            with closing(sqlite3.connect(database_path)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM peaks").fetchone()[0], 0)
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

            project.title = "Cleavage updated"
            project.datasets[0].measurement.sample_name = "LL-37 updated"
            project.datasets[0].measurement.gradient = [GradientPoint(0.0, 100.0, 0.0)]
            project.datasets[0].peaks = []
            sync_project_to_database(database_path, project)
            updated = database_sections(database_path)
            self.assertEqual(len(updated["projects"][1]), 1)
            self.assertEqual(updated["projects"][1][0][1], "Cleavage updated")
            self.assertEqual(updated["datasets"][1][0][6], "LL-37 updated")
            self.assertEqual(len(updated["gradients"][1]), 1)
            exported = export_database_csvs(database_path, directory)
            self.assertEqual(len(exported), 3)
            self.assertTrue(all(Path(path).read_bytes().startswith(b"\xef\xbb\xbf") for path in exported))

    def test_lab_database_deduplicates_gradient_programs_and_sorts_newest_first(self):
        first = load_ascii_file(str(SAMPLES / "210601.TXT"))
        second = load_ascii_file(str(SAMPLES / "225120.TXT"))
        for dataset, name in ((first, "RP-C4"), (second, "Same program alias")):
            dataset.gradient_preset_name = name
            dataset.measurement.column_name = "C4"
            dataset.measurement.gradient = [
                GradientPoint(0.0, 90.0, 10.0, 0.0, 0.0, 1.0),
                GradientPoint(20.0, 10.0, 90.0, 0.0, 0.0, 1.0),
            ]
        first.measurement.solvents["A"] = Solvent("Water", "0.1% TFA")
        first.measurement.solvents["B"] = Solvent("ACN", "0.1% TFA")
        older = Project(
            title="Older",
            analysis_date="20260808",
            author="A",
            datasets=[first],
        )
        newer = Project(
            title="Newer",
            analysis_date="20260809",
            author="B",
            datasets=[second],
        )
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, "lab.sqlite3")
            sync_project_to_database(database_path, older)
            sync_project_to_database(database_path, newer)
            sections = database_sections(database_path)
            self.assertEqual(
                [row[1] for row in sections["projects"][1]], ["Newer", "Older"]
            )
            self.assertEqual(len(sections["gradients"][1]), 1)
            gradient = sections["gradients"][1][0]
            self.assertEqual(gradient[0], "20260809")
            self.assertIn("RP-C4", gradient[1])
            self.assertIn("Same program alias", gradient[1])
            self.assertEqual(gradient[-1], 2)

    def test_ch2_path_is_assigned_to_second_axis(self):
        raw = (SAMPLES / "210601.TXT").read_bytes()
        dataset = dataset_from_bytes(raw, source_path=r"C:\\Data1\\Ch2\\2026_05_07\\210601.TXT")
        self.assertEqual(dataset.y_axis, 2)


    def test_chromatogram_csv_has_two_columns_and_batch_export(self):
        first = load_ascii_file(str(SAMPLES / "210601.TXT"))
        second = load_ascii_file(str(SAMPLES / "225120.TXT"))
        first.short_label = "sample"
        second.short_label = "sample"
        with tempfile.TemporaryDirectory() as directory:
            single = os.path.join(directory, "single.csv")
            export_chromatogram_csv(single, first, "uV")
            header = Path(single).read_text(encoding="utf-8-sig").splitlines()[0]
            self.assertEqual(header, "Time_min,Intensity_uV")
            paths = export_chromatograms_csv(directory, [first, second], "uV")
            self.assertEqual(len(paths), 2)
            self.assertNotEqual(Path(paths[0]).name, Path(paths[1]).name)

    def test_peak_csv_exports_second_based_area_columns(self):
        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        dataset.measurement.aux_range_au_per_v = 2.0
        dataset.peaks = [PeakRegion(start_min=1.0, end_min=2.0)]
        recalculate_dataset_peaks(dataset)
        fitted_peak = fitted_peak_from_result(
            dataset.peaks[0],
            PeakFitResult(
                "gaussian",
                {"amplitude_uv": 100.0, "center_min": 1.5, "sigma_min": 0.1},
                1.5,
                2.0,
                0.99,
                10.0,
                20,
            ),
        )
        dataset.fitted_peaks = [fitted_peak]
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "peaks.csv")
            export_peak_csv(path, [dataset])
            with open(path, "r", encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            self.assertIn("raw_area_uV_sec", rows[0])
            self.assertIn("area_mAU_sec", rows[0])
            self.assertNotIn("raw_area_uV_min", rows[0])
            self.assertNotIn("area_mAU_min", rows[0])
            self.assertIn("peak_type", rows[0])
            self.assertIn("parent_peak_id", rows[0])
            self.assertEqual(len(rows), 3)
            raw_index = rows[0].index("raw_area_uV_sec")
            mau_index = rows[0].index("area_mAU_sec")
            self.assertAlmostEqual(float(rows[1][raw_index]), dataset.peaks[0].raw_area_uv_sec)
            self.assertAlmostEqual(float(rows[1][mau_index]), dataset.peaks[0].area_mau_sec)
            type_index = rows[0].index("peak_type")
            parent_index = rows[0].index("parent_peak_id")
            model_index = rows[0].index("fit_model")
            self.assertEqual(rows[1][type_index], "integrated")
            self.assertEqual(rows[2][0], dataset.label)
            self.assertEqual(rows[2][1], "F1")
            self.assertEqual(rows[2][type_index], "fitted")
            self.assertEqual(rows[2][parent_index], dataset.peaks[0].id)
            self.assertEqual(rows[2][model_index], "gaussian")

    def test_metadata_table_csv_contains_sample_conditions_and_source(self):
        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        dataset.short_label = "CaM"
        dataset.measurement.sample_name = "CaM-LL37"
        dataset.measurement.column_name = "COSMOSIL 5C4-AR-300"
        dataset.measurement.wavelength_nm = 280.0
        dataset.measurement.analyte_name = "LL-37"
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "conditions.csv")
            export_metadata_csv(path, [dataset], "ja")
            text = Path(path).read_text(encoding="utf-8-sig")
            self.assertIn("表示ラベル,短縮ラベル,サンプル名", text)
            self.assertIn("COSMOSIL 5C4-AR-300", text)
            self.assertIn("LL-37", text)
            self.assertIn(dataset.original_path, text)

    def test_distribution_includes_icon_and_vector_backends(self):
        spec = (ROOT / "HPLC_Analyzer.spec").read_text(encoding="utf-8")
        self.assertIn("assets/app_icon.ico", spec)
        self.assertIn("matplotlib.backends.backend_svg", spec)
        self.assertIn("matplotlib.backends.backend_pdf", spec)
        self.assertTrue((ROOT / "assets" / "app_icon.png").exists())
        self.assertTrue((ROOT / "assets" / "app_icon.ico").exists())
        self.assertEqual(
            __import__("hashlib").sha256((ROOT / "assets" / "app_icon.png").read_bytes()).hexdigest(),
            "300706c2862844d792dd2a6aaabd3658c525ef7e1f6ab8125d7604a0fde36078",
        )
        self.assertEqual(
            __import__("hashlib").sha256((ROOT / "assets" / "app_icon.ico").read_bytes()).hexdigest(),
            "8bf0a146065f87040cd7b535b8f4ead20e6dda4739cc470c02c92c4ce5006f3a",
        )

    def test_windows7_build_is_local_offline_on_windows7_sp1_x86(self):
        wrapper = (ROOT / "build_windows7.bat").read_text(encoding="utf-8")
        batch = (ROOT / "build_windows7_offline.bat").read_text(encoding="utf-8")
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        packager = (ROOT / "scripts" / "package_windows7_offline_bundle.py").read_text(
            encoding="utf-8"
        )
        spec = (ROOT / "HPLC_Analyzer.spec").read_text(encoding="utf-8")
        requirements = (ROOT / "requirements-win7.txt").read_text(encoding="utf-8")
        verifier = (ROOT / "scripts" / "verify_windows7_x86.py").read_text(encoding="utf-8")
        self.assertIn("build_windows7_offline.bat", wrapper)
        self.assertIn('ver | findstr /c:"6.1.7601"', batch)
        self.assertIn('if /i not "%PROCESSOR_ARCHITECTURE%"=="x86"', batch)
        self.assertIn("python-3.8.10.exe", batch)
        self.assertIn("innosetup-6.7.3.exe", batch)
        self.assertIn("PIP_NO_INDEX=1", batch)
        self.assertIn("--no-index", batch)
        self.assertIn("\\win7_offline", batch)
        self.assertIn("\\wheels", batch)
        self.assertNotIn('set "PYTHON_EXE=py -3.8-32"', batch)
        self.assertNotIn("pip download", batch.lower())
        self.assertNotIn("Invoke-WebRequest", batch)
        self.assertNotIn("http://", batch.lower())
        self.assertNotIn("https://", batch.lower())
        self.assertIn(".venv-win7-x86", batch)
        self.assertIn("--only-binary=:all:", batch)
        self.assertIn("--distpath dist\\windows7-x86", batch)
        self.assertIn("--exe dist\\windows7-x86\\HPLC_Analyzer.exe", batch)
        self.assertIn("HPLC_ANALYZER_BUILD_DEBUG=1", batch)
        self.assertIn("--exe dist\\windows7-x86\\HPLC_Analyzer_Debug.exe", batch)
        self.assertGreaterEqual(batch.count("--startup-smoke-test"), 2)
        self.assertIn("build_installer.bat windows7-x86", batch)
        self.assertIn("%WINDOWS7_INSTALLER_NAME%", batch)
        self.assertIn("write_windows_version_info.py --target windows7-x86", batch)
        self.assertIn('name="HPLC_Analyzer_Debug"', spec)
        self.assertIn("console=True", spec)
        self.assertIn("debug=True", spec)
        self.assertNotIn("upx=True", spec)
        self.assertGreaterEqual(spec.count("upx=False"), 2)
        pinned = [
            line.strip()
            for line in requirements.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertTrue(all("==" in line for line in pinned))
        self.assertIn("PySide2==5.15.2.1", pinned)
        self.assertIn("numpy==1.20.3", pinned)
        self.assertIn("Pillow==9.5.0", pinned)
        self.assertIn("PyInstaller==5.13.2", pinned)
        self.assertIn("pyqtgraph==0.13.3", pinned)
        self.assertIn("importlib-resources==6.4.5", pinned)
        self.assertIn("zipp==3.20.2", pinned)
        self.assertIn('EXPECTED_PYTHON = (3, 8, 10)', verifier)
        self.assertIn("def verify_host():", verifier)
        self.assertIn('b"AddDllDirectory"', verifier)
        self.assertIn("PE_MACHINE_I386 = 0x014C", verifier)
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "test-x86.exe"
            image = bytearray(0x88)
            image[0:2] = b"MZ"
            image[0x3C:0x40] = (0x80).to_bytes(4, "little")
            image[0x80:0x84] = b"PE\0\0"
            image[0x84:0x86] = PE_MACHINE_I386.to_bytes(2, "little")
            executable.write_bytes(image)
            self.assertEqual(read_pe_machine(executable), PE_MACHINE_I386)

    def test_windows7_import_preflight_is_isolated_ordered_and_blocking(self):
        names = [probe[0] for probe in PROBES]
        self.assertEqual(
            names,
            [
                "NumPy",
                "Pillow",
                "shiboken2",
                "PySide2",
                "QtCore",
                "QtGui",
                "QtWidgets",
                "PyQtGraph",
                "Matplotlib",
                "HPLC GUI import",
                "HPLC Analyzer GUI smoke test",
                "PyInstaller",
            ],
        )
        source = (ROOT / "scripts" / "windows7_import_preflight.py").read_text(
            encoding="utf-8"
        )
        batch = (ROOT / "build_windows7_offline.bat").read_text(encoding="utf-8")
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        packager = (ROOT / "scripts" / "package_windows7_offline_bundle.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("subprocess.call", source)
        self.assertIn("[sys.executable, \"-c\", source]", source)
        self.assertIn("Test name", source)
        self.assertIn("Exit code", source)
        self.assertIn("Package/version", source)
        self.assertIn("EventID=1000", EVENT_LOG_COMMAND)
        self.assertLess(
            batch.index("-r requirements-win7.txt"),
            batch.index("scripts\\windows7_import_preflight.py"),
        )
        self.assertLess(
            batch.index("scripts\\windows7_import_preflight.py"),
            batch.index("-m PyInstaller"),
        )
        self.assertLess(
            batch.rindex("--startup-smoke-test"),
            batch.index("build_installer.bat windows7-x86"),
        )
        self.assertIn("HPLC_ANALYZER_TEST_SETTINGS_DIR", batch)
        self.assertIn("HPLC_ANALYZER_CONFIG_DIR", batch)
        self.assertIn("configure_isolated_test_settings", app_source)
        self.assertIn('".build-test-settings"', packager)
        self.assertLess(
            batch.rindex("--startup-smoke-test"),
            batch.index("copy /y dist\\windows7-x86\\HPLC_Analyzer.exe"),
        )
        self.assertLess(
            batch.index("copy /y dist\\windows7-x86\\HPLC_Analyzer_Debug.exe"),
            batch.index("build_installer.bat windows7-x86"),
        )

    def test_windows7_python_bootstrap_uses_absolute_paths_and_diagnostics(self):
        batch = (ROOT / "build_windows7_offline.bat").read_text(encoding="utf-8")
        self.assertIn('for %%I in ("%~dp0.") do set "PROJECT_ROOT=%%~fI"', batch)
        self.assertIn(
            'for %%I in ("%TOOLS_ROOT%\\Python38-32") do set "PYTHON_DIR=%%~fI"',
            batch,
        )
        self.assertNotIn('set "PROJECT_ROOT=%CD%"', batch)
        self.assertIn('if not exist "%PYTHON_DIR%" mkdir "%PYTHON_DIR%"', batch)
        self.assertIn('TargetDir="%PYTHON_DIR%"', batch)
        self.assertIn('DefaultJustForMeTargetDir="%PYTHON_DIR%"', batch)
        self.assertIn('/log "%PYTHON_LOG%"', batch)
        self.assertIn('set "PYTHON_RESULT=%ERRORLEVEL%"', batch)
        self.assertIn("Python installer exit code: %PYTHON_RESULT%", batch)
        self.assertGreaterEqual(
            batch.count('Checked path: "%LOCAL_PYTHON_EXE%"'),
            3,
        )
        self.assertIn(":install_local_python", batch)
        self.assertIn(":find_existing_python", batch)
        self.assertIn("Using compatible existing Python", batch)
        self.assertIn("scripts\\verify_windows7_x86.py --interpreter", batch)

    def test_windows7_offline_payload_is_complete_hashed_and_x86_only(self):
        if not (ROOT / "win7_offline").is_dir():
            self.skipTest("win7_offline assets are not present")
        self.assertEqual(verify_windows7_offline_bundle(ROOT), [])
        manifest = read_windows7_offline_manifest(
            ROOT / "win7_offline" / "MANIFEST.sha256"
        )
        self.assertEqual(set(manifest), set(WINDOWS7_OFFLINE_FILES))
        self.assertGreater(
            sum((ROOT / relative).stat().st_size for relative in WINDOWS7_OFFLINE_FILES),
            150_000_000,
        )
        self.assertTrue(
            all(
                "win_amd64" not in relative.lower()
                and "manylinux" not in relative.lower()
                and "macosx" not in relative.lower()
                for relative in WINDOWS7_OFFLINE_FILES
            )
        )

    def test_windows7_wheelhouse_is_complete_before_python_installation(self):
        if not (ROOT / "win7_offline").is_dir():
            self.skipTest("win7_offline assets are not present")
        batch = (ROOT / "build_windows7_offline.bat").read_text(encoding="utf-8")
        preparer = (ROOT / "prepare_windows7_offline_wheels.bat").read_text(
            encoding="utf-8"
        )
        required = (ROOT / "win7_offline" / "REQUIRED_WHEELS.txt").read_text(
            encoding="ascii"
        )
        self.assertEqual(verify_dependency_closure(ROOT), [])
        self.assertIn("importlib_resources-6.4.5-py3-none-any.whl", required)
        self.assertIn("zipp-3.20.2-py3-none-any.whl", required)
        self.assertIn("numpy-1.20.3-cp38-cp38-win32.whl", required)
        self.assertIn("Pillow-9.5.0-cp38-cp38-win32.whl", required)
        self.assertIn("pyqtgraph-0.13.3-py3-none-any.whl", required)
        self.assertNotIn("numpy-1.24.4", required)
        self.assertNotIn("pillow-10.4.0", required.lower())
        self.assertLess(
            batch.index("call :verify_required_wheels"),
            batch.index("Installing or confirming the Windows 7-compatible VC++"),
        )
        self.assertLess(
            batch.index("scripts\\verify_windows7_wheelhouse.py"),
            batch.index("-m venv .venv-win7-x86"),
        )
        self.assertIn("--platform win32", preparer)
        self.assertIn("--python-version 3.8", preparer)
        self.assertIn("--implementation cp", preparer)
        self.assertIn("--abi cp38", preparer)
        self.assertIn("--only-binary=:all:", preparer)
        self.assertEqual(
            __import__("hashlib").sha256(
                (
                    ROOT
                    / "win7_offline"
                    / "wheels"
                    / "importlib_resources-6.4.5-py3-none-any.whl"
                ).read_bytes()
            ).hexdigest(),
            "ac29d5f956f01d5e4bb63102a5a19957f1b9175e45649977264a1416783bb717",
        )
        self.assertEqual(
            __import__("hashlib").sha256(
                (
                    ROOT
                    / "win7_offline"
                    / "wheels"
                    / "zipp-3.20.2-py3-none-any.whl"
                ).read_bytes()
            ).hexdigest(),
            "a817ac80d6cf4b23bf7f2828b7cabf326f15a001bea8b1f9b49631780ba28350",
        )
        self.assertEqual(
            __import__("hashlib").sha256(
                (
                    ROOT
                    / "win7_offline"
                    / "wheels"
                    / "numpy-1.20.3-cp38-cp38-win32.whl"
                ).read_bytes()
            ).hexdigest(),
            "f39a995e47cb8649673cfa0579fbdd1cdd33ea497d1728a6cb194d6252268e48",
        )
        self.assertEqual(
            __import__("hashlib").sha256(
                (
                    ROOT
                    / "win7_offline"
                    / "wheels"
                    / "Pillow-9.5.0-cp38-cp38-win32.whl"
                ).read_bytes()
            ).hexdigest(),
            "6608ff3bf781eee0cd14d0901a2b9cc3d3834516532e3bd673a0a204dc8615fc",
        )
        self.assertEqual(
            __import__("hashlib").sha256(
                (
                    ROOT
                    / "win7_offline"
                    / "wheels"
                    / "pyqtgraph-0.13.3-py3-none-any.whl"
                ).read_bytes()
            ).hexdigest(),
            "fdcc04ac4b32a7bedf1bf3cf74cbb93ab3ba5687791712bbfa8d0712377d2f2b",
        )
    def test_frozen_startup_smoke_test_exercises_the_main_window(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('"--startup-smoke-test" in sys.argv', app_source)
        self.assertIn("window = MainWindow()", app_source)
        self.assertIn("app.processEvents()", app_source)
        self.assertIn("window.close()", app_source)

    def test_windows11_build_targets_python311_x64(self):
        batch = (ROOT / "build_windows11.bat").read_text(encoding="utf-8")
        requirements = (ROOT / "requirements-win11.txt").read_text(encoding="utf-8")
        verifier = (ROOT / "scripts" / "verify_windows11_x64.py").read_text(encoding="utf-8")
        self.assertIn("py -3.11-64", batch)
        self.assertIn(".venv-win11-x64", batch)
        self.assertIn("--distpath dist\\windows11-x64", batch)
        self.assertIn("--exe dist\\windows11-x64\\HPLC_Analyzer.exe", batch)
        self.assertIn("build_installer.bat windows11-x64", batch)
        self.assertIn("HPLC_ANALYZER_TEST_SETTINGS_DIR", batch)
        self.assertIn("%WINDOWS11_INSTALLER_NAME%", batch)
        self.assertIn("write_windows_version_info.py --target windows11-x64", batch)
        self.assertIn("Python 3.11 x64", requirements)
        self.assertIn("EXPECTED_PYTHON = (3, 11)", verifier)
        self.assertIn("PE_MACHINE_AMD64 = 0x8664", verifier)
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "test-x64.exe"
            image = bytearray(0x88)
            image[0:2] = b"MZ"
            image[0x3C:0x40] = (0x80).to_bytes(4, "little")
            image[0x80:0x84] = b"PE\0\0"
            image[0x84:0x86] = PE_MACHINE_AMD64.to_bytes(2, "little")
            executable.write_bytes(image)
            self.assertEqual(read_pe_machine_x64(executable), PE_MACHINE_AMD64)

    def test_installers_target_windows11_x64_and_windows7_x86(self):
        scripts = {
            "windows11-x64": ROOT / "installer" / "windows11_x64.iss",
            "windows7-x86": ROOT / "installer" / "windows7_x86.iss",
        }
        app_ids = []
        for target, path in scripts.items():
            self.assertEqual(verify_installer_script(target, path), [])
            text = path.read_text(encoding="utf-8")
            match = re.search(r"^AppId=(.+)$", text, re.MULTILINE)
            self.assertIsNotNone(match)
            app_ids.append(match.group(1).strip())
            self.assertNotIn("[UninstallDelete]", text)
            self.assertIn('Name: "desktopicon"', text)
            self.assertIn("UninstallDisplayIcon={app}\\HPLC_Analyzer.exe", text)
        self.assertEqual(app_ids, [INSTALLER_APP_ID, INSTALLER_APP_ID])
        self.assertIn(
            "HPLC_Analyzer_Debug.exe",
            scripts["windows7-x86"].read_text(encoding="utf-8"),
        )

    def test_installer_upgrade_fixture_and_guard_cover_user_owned_data(self):
        qsettings = {
            "hive": "HKEY_CURRENT_USER",
            "key": r"Software\Research Tools\HPLC Analyzer",
            "tree": {
                "values": {
                    "ui/language": {"type": 1, "value": "en"},
                    "rendering/quality": {"type": 1, "value": "lightweight"},
                },
                "subkeys": {},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            fixture_root = Path(directory) / "fixture"
            paths, manifest = create_fixture(fixture_root, ROOT)
            self.assertTrue(manifest.is_file())
            snapshot = build_installer_data_snapshot(paths, qsettings)
            baseline_path = Path(directory) / "evidence" / "before.json"
            write_installer_data_snapshot(baseline_path, snapshot)
            baseline = read_installer_data_snapshot(baseline_path)
            self.assertEqual(
                compare_installer_data_snapshots(
                    baseline, build_installer_data_snapshot(paths, qsettings)
                ),
                [],
            )

            project = load_project(
                str(Path(paths["projects"]) / "upgrade-preservation.hplcproj")
            )
            raw = Path(paths["raw"]) / "upgrade-canary.TXT"
            self.assertEqual(project.datasets[0].raw_bytes, raw.read_bytes())
            conditions, gradients = load_preset_store(Path(paths["presets"]))
            self.assertIn("Canary 280 nm", conditions)
            self.assertEqual(
                gradients["Canary gradient"]["metadata_note"],
                "must survive installer operations",
            )
            self.assertTrue(database_sections(paths["database"])["projects"])

            raw.write_bytes(raw.read_bytes() + b"changed")
            errors = compare_installer_data_snapshots(
                baseline, build_installer_data_snapshot(paths, qsettings)
            )
            self.assertTrue(
                any("protected file changed: raw/" in error for error in errors)
            )
            changed_settings = deepcopy(qsettings)
            changed_settings["tree"]["values"]["ui/language"]["value"] = "ja"
            errors = compare_installer_data_snapshots(
                baseline, build_installer_data_snapshot(paths, changed_settings)
            )
            self.assertIn("QSettings registry changed", errors)

    def test_installer_policy_and_upgrade_evidence_forbid_user_data_management(self):
        guide = (ROOT / ".github" / "INSTALLER_UPGRADE_TEST.md").read_text(
            encoding="utf-8"
        )
        evidence = (
            ROOT / ".github" / "INSTALLER_UPGRADE_EVIDENCE.md"
        ).read_text(encoding="utf-8")
        for required in (
            "Clean install",
            "v1.2.4 → v1.3.0",
            "Uninstall",
            "Reinstall",
            "QSettings",
            "presets.json",
            "SQLite",
            ".hplcproj",
            "raw ASCII",
            "user export",
            "physical Windows 7 SP1 32-bit/Core 2",
        ):
            self.assertIn(required, guide)
        self.assertIn("Data guard result", evidence)
        self.assertIn("Previous Stable installer filename / SHA-256", evidence)
        with tempfile.TemporaryDirectory() as directory:
            unsafe = Path(directory) / "unsafe.iss"
            original = (ROOT / "installer" / "windows11_x64.iss").read_text(
                encoding="utf-8"
            )
            unsafe.write_text(
                original
                + "\n[UninstallDelete]\n"
                + 'Type: filesandordirs; Name: "{userappdata}\\presets.json"\n',
                encoding="utf-8",
            )
            errors = verify_installer_script("windows11-x64", unsafe)
            self.assertTrue(
                any("must not manage protected user data" in error for error in errors)
            )

    def test_installer_build_is_automatic_and_has_combined_entry_point(self):
        helper = (ROOT / "scripts" / "build_installer.bat").read_text(encoding="utf-8")
        combined = (ROOT / "build_all_windows.bat").read_text(encoding="utf-8")
        packager = (ROOT / "scripts" / "package_windows7_offline_bundle.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("Inno Setup 6\\ISCC.exe", helper)
        self.assertIn("HPLC_ISCC_EXE", helper)
        self.assertIn("ISCC.exe", helper)
        self.assertIn("verify_installer.py", helper)
        self.assertIn("build_windows11.bat --no-pause", combined)
        self.assertIn("package_windows7_offline_bundle.bat --no-pause", combined)
        self.assertNotIn("build_windows7.bat --no-pause", combined)
        self.assertIn("%WINDOWS11_INSTALLER_NAME%", combined)
        self.assertIn("%WINDOWS7_OFFLINE_ARCHIVE_NAME%", combined)
        self.assertNotIn('VERSION = "', packager)
        self.assertIn("read_version", packager)
        self.assertIn("verify_bundle(root)", packager)
        self.assertIn("verify_dependency_closure(root)", packager)
        self.assertIn('"win7_offline"', packager)

    def test_windows7_installer_bundles_verified_microsoft_vc_runtime(self):
        helper = (ROOT / "scripts" / "build_installer.bat").read_text(encoding="utf-8")
        offline_batch = (ROOT / "build_windows7_offline.bat").read_text(
            encoding="utf-8"
        )
        installer = (ROOT / "installer" / "windows7_x86.iss").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("prepare_windows7_redist.ps1", helper)
        self.assertNotIn("Invoke-WebRequest", helper)
        self.assertIn("copy /y", offline_batch)
        self.assertIn("VC_redist.x86.exe", offline_batch)
        self.assertIn(
            "1acd8d5ea1cdc3eb2eb4c87be3ab28722d0825c15449e5c9ceef95d897de52fa",
            offline_batch,
        )
        self.assertIn("installer\\redist\\VC_redist.x86.exe", installer)
        self.assertIn("VCRedistNeedsInstall", installer)
        self.assertIn('Parameters: "/install /quiet /norestart"', installer)

    def test_generated_installer_container_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            setup = Path(directory) / "HPLC_Analyzer_Setup.exe"
            image = bytearray(5000)
            image[0:2] = b"MZ"
            image[0x3C:0x40] = (0x80).to_bytes(4, "little")
            image[0x80:0x84] = b"PE\0\0"
            setup.write_bytes(image)
            self.assertEqual(verify_setup_executable(setup), [])

    def test_a4_report_pdf_and_print_pages(self):
        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        dataset.label = "Report sample"
        dataset.line_style = "dotted"
        dataset.peaks = [PeakRegion(start_min=1.0, end_min=2.0)]
        recalculate_dataset_peaks(dataset)
        fitted_peak = fitted_peak_from_result(
            dataset.peaks[0],
            PeakFitResult(
                "gaussian",
                {"amplitude_uv": 1000.0, "center_min": 1.5, "sigma_min": 0.12},
                1.5,
                3.0,
                0.995,
                15.0,
                30,
            ),
        )
        dataset.fitted_peaks = [fitted_peak]
        dataset.peaks[0].raw_area_uv_sec = 123456.4
        dataset.peaks[0].area_percent = 62.5
        dataset.peaks[0].amount_nmol = 12.5
        dataset.peaks[0].amount_ug = 3.75
        fitted_peak.raw_area_uv_sec = 98765.6
        fitted_peak.amount_nmol = 4.25
        fitted_peak.amount_ug = 1.5
        project = Project(title="Report test", datasets=[dataset])
        project.method.line_width = 2.4
        figures = analysis_report_figures(project, [dataset], "en")
        plot_axis = next(
            axis
            for axis in figures[0].axes
            if axis.get_title(loc="left") == "Chromatogram"
        )
        retention_label = "%.2f" % dataset.peaks[0].retention_time_min
        trace_line = next(
            line
            for line in plot_axis.lines
            if line.get_label() == project.legend_label_for(dataset)
        )
        self.assertEqual(trace_line.get_linestyle(), ":")
        self.assertAlmostEqual(trace_line.get_linewidth(), 2.4)
        self.assertIn(retention_label, [text.get_text() for text in plot_axis.texts])
        boundary_lines = [
            line
            for line in plot_axis.lines
            if line.get_color() == "#9ca3af"
        ]
        self.assertEqual(len(boundary_lines), 2)
        report_cells = [
            cell.get_text().get_text()
            for axis in figures[0].axes
            for table in axis.tables
            for cell in table.get_celld().values()
        ]
        self.assertIn("Area (µV·sec)", report_cells)
        self.assertNotIn("Area (mAU·sec)", report_cells)
        self.assertNotIn("Type / parent", report_cells)
        self.assertNotIn("Method", report_cells)
        self.assertIn("F1 estimated→#1", report_cells)
        self.assertIn("123,456", report_cells)
        self.assertNotIn("1.235e+05", report_cells)
        self.assertIn("Amount (nmol)", report_cells)
        self.assertIn("Amount (µg)", report_cells)
        self.assertIn("12.5", report_cells)
        self.assertIn("3.75", report_cells)
        self.assertEqual(dataset.peaks[0].area_percent, 62.5)
        self.assertTrue(
            any(line.get_color() == "#c026d3" for line in plot_axis.lines)
        )
        for figure in figures:
            figure.clear()

        compact_options = ReportOptions(
            integration_range=False,
            baseline=False,
            retention_time=False,
            gradient_b=False,
            gradient_conditions=False,
            quantitation=False,
        )
        compact = analysis_report_figures(
            project, [dataset], "en", compact_options
        )
        compact_plot = next(
            axis
            for axis in compact[0].axes
            if axis.get_title(loc="left") == "Chromatogram"
        )
        self.assertNotIn(
            retention_label, [text.get_text() for text in compact_plot.texts]
        )
        self.assertFalse(
            any(line.get_color() == "#9ca3af" for line in compact_plot.lines)
        )
        compact_cells = [
            cell.get_text().get_text()
            for axis in compact[0].axes
            for table in axis.tables
            for cell in table.get_celld().values()
        ]
        for omitted_header in (
            "RT (min)",
            "Range (min)",
            "%B",
            "Amount (nmol)",
            "Amount (µg)",
        ):
            self.assertNotIn(omitted_header, compact_cells)
        for figure in compact:
            figure.clear()
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = export_analysis_report_pdf(
                os.path.join(directory, "report.pdf"), project, [dataset], "en"
            )
            self.assertTrue(Path(pdf_path).read_bytes().startswith(b"%PDF"))
            pages = render_analysis_report_pages(directory, project, [dataset], "en")
            self.assertEqual(len(pages), 1)
            from matplotlib import image as matplotlib_image

            image = matplotlib_image.imread(pages[0])
            self.assertGreater(image.shape[0], image.shape[1])
            self.assertAlmostEqual(image.shape[0] / image.shape[1], 297.0 / 210.0, delta=0.02)

    def test_report_peak_tables_are_top_aligned_without_stretching_short_lists(self):
        from matplotlib.backends.backend_agg import FigureCanvasAgg

        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        dataset.label = "Natural table height"

        def peak(index):
            return PeakRegion(
                start_min=1.0 + index,
                end_min=1.5 + index,
                retention_time_min=1.25 + index,
                raw_area_uv_sec=1000.0 + index,
                area_mau_sec=1.0 + index / 100.0,
                area_percent=1.0,
                fwhm_min=0.2,
            )

        project = Project(title="Natural table layout", datasets=[dataset])
        dataset.peaks = [peak(0)]
        short_figure = analysis_report_figures(project, [dataset], "en")[0]
        short_canvas = FigureCanvasAgg(short_figure)
        short_canvas.draw()
        short_axis = next(axis for axis in short_figure.axes if axis.tables)
        short_box = short_axis.tables[0].get_window_extent(short_canvas.get_renderer())
        short_axis_box = short_axis.get_window_extent(short_canvas.get_renderer())
        self.assertAlmostEqual(short_box.y1, short_axis_box.y1, delta=1.0)
        self.assertLess(short_box.height, short_axis_box.height * 0.25)
        short_figure.clear()

        dataset.peaks = [peak(index) for index in range(21)]
        figures = analysis_report_figures(project, [dataset], "en")
        self.assertEqual(len(figures), 2)
        continuation = figures[1]
        continuation_canvas = FigureCanvasAgg(continuation)
        continuation_canvas.draw()
        continuation_axis = continuation.axes[0]
        continuation_box = continuation_axis.tables[0].get_window_extent(
            continuation_canvas.get_renderer()
        )
        continuation_axis_box = continuation_axis.get_window_extent(
            continuation_canvas.get_renderer()
        )
        self.assertAlmostEqual(continuation_box.y1, continuation_axis_box.y1, delta=1.0)
        self.assertLess(continuation_box.height, continuation_axis_box.height * 0.25)
        for figure in figures:
            figure.clear()

    def test_a4_report_keeps_full_first_page_peak_table_below_x_label(self):
        from matplotlib.backends.backend_agg import FigureCanvasAgg

        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        dataset.label = "Crowded report sample"
        dataset.peaks = [
            PeakRegion(
                start_min=1.0 + index * 4.0,
                end_min=2.0 + index * 4.0,
                retention_time_min=1.5 + index * 4.0,
                raw_area_uv_sec=1000.0 + index,
                area_mau_sec=1.0 + index / 100.0,
                area_percent=5.0,
                fwhm_min=0.2,
            )
            for index in range(20)
        ]
        project = Project(title="Crowded report", datasets=[dataset])
        figure = analysis_report_figures(project, [dataset], "en")[0]
        canvas = FigureCanvasAgg(figure)
        canvas.draw()
        renderer = canvas.get_renderer()
        plot_axis = next(
            axis
            for axis in figure.axes
            if axis.get_title(loc="left") == "Chromatogram"
        )
        table_axis = next(axis for axis in figure.axes if axis.tables)
        x_label_box = plot_axis.xaxis.label.get_window_extent(renderer)
        table_box = table_axis.tables[0].get_window_extent(renderer)
        figure_box = figure.get_window_extent(renderer)
        y_label_box = plot_axis.yaxis.label.get_window_extent(renderer)
        self.assertAlmostEqual(figure.subplotpars.left, 0.105)
        self.assertAlmostEqual(figure.subplotpars.right, 0.895)
        self.assertGreater(plot_axis.get_position().width, 0.75)
        self.assertGreaterEqual(y_label_box.x0, figure_box.x0)
        self.assertGreater(x_label_box.y0 - table_box.y1, 4.0)
        self.assertGreaterEqual(table_box.y0, figure_box.y0)
        self.assertIn("Crowded report", [text.get_text() for text in figure.axes[0].texts])
        self.assertTrue(figure.axes[1].texts)
        figure.clear()

        dataset.peaks.extend(
            PeakRegion(
                start_min=1.0 + index * 4.0,
                end_min=2.0 + index * 4.0,
                retention_time_min=1.5 + index * 4.0,
                raw_area_uv_sec=1000.0 + index,
                area_mau_sec=1.0 + index / 100.0,
                area_percent=4.0,
                fwhm_min=0.2,
            )
            for index in range(20, 25)
        )
        continued = analysis_report_figures(project, [dataset], "en")
        self.assertEqual(len(continued), 2)
        self.assertIn(
            "Peak table (continued)",
            continued[1].axes[0].get_title(loc="left"),
        )
        for page in continued:
            page.clear()

    def test_a4_report_continuation_fits_48_rows_and_keeps_footer_clear(self):
        from matplotlib.backends.backend_agg import FigureCanvasAgg

        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        dataset.label = "Long peak table"
        dataset.peaks = [
            PeakRegion(
                start_min=1.0 + index,
                end_min=1.5 + index,
                retention_time_min=1.25 + index,
                raw_area_uv_sec=1000.0 + index,
                area_mau_sec=1.0 + index / 100.0,
                area_percent=1.0,
                fwhm_min=0.2,
            )
            for index in range(116)
        ]
        project = Project(title="Continuation layout", datasets=[dataset])
        figures = analysis_report_figures(project, [dataset], "en")

        # The first page keeps the layout established by Issues #182 and #219.
        self.assertEqual(len(figures), 3)
        self.assertAlmostEqual(figures[0].subplotpars.top, 0.95)
        self.assertAlmostEqual(figures[0].subplotpars.bottom, 0.055)
        first_table = next(
            axis.tables[0] for axis in figures[0].axes if axis.tables
        )
        header_columns = sorted(
            column for row, column in first_table.get_celld() if row == 0
        )
        expected_headers = [
            first_table[(0, column)].get_text().get_text()
            for column in header_columns
        ]
        self.assertNotIn("Type / parent", expected_headers)
        self.assertNotIn("Method", expected_headers)
        self.assertIn("Area (µV·sec)", expected_headers)
        self.assertIn("Amount (nmol)", expected_headers)
        self.assertAlmostEqual(
            sum(
                first_table[(0, column)].get_width()
                for column in range(len(expected_headers))
            ),
            1.0,
        )
        continuation_numbers = []
        for figure in figures[1:]:
            self.assertAlmostEqual(figure.subplotpars.top, 0.975)
            self.assertAlmostEqual(figure.subplotpars.bottom, 0.035)
            canvas = FigureCanvasAgg(figure)
            canvas.draw()
            renderer = canvas.get_renderer()
            axis = figure.axes[0]
            table = axis.tables[0]
            headers = [
                table[(0, column)].get_text().get_text()
                for column in range(len(expected_headers))
            ]
            self.assertEqual(headers, expected_headers)
            table_box = table.get_window_extent(renderer)
            axis_box = axis.get_window_extent(renderer)
            footer_box = figure.texts[0].get_window_extent(renderer)
            self.assertGreaterEqual(table_box.y0, axis_box.y0 - 0.5)
            self.assertLessEqual(table_box.y1, axis_box.y1 + 0.5)
            self.assertGreater(table_box.y0 - footer_box.y1, 2.0)
            rows = sorted({row for row, _column in table.get_celld() if row > 0})
            self.assertEqual(len(rows), 48)
            row_boxes = [table[(row, 0)].get_window_extent(renderer) for row in rows]
            for upper, lower in zip(row_boxes, row_boxes[1:]):
                self.assertGreaterEqual(upper.y0, lower.y1 - 0.5)
            continuation_numbers.extend(
                int(table[(row, 0)].get_text().get_text()) for row in rows
            )
        self.assertEqual(continuation_numbers, list(range(21, 117)))
        for figure in figures:
            figure.clear()

        # One item past an exact 48-row boundary creates one non-empty page.
        dataset.peaks.append(
            PeakRegion(
                start_min=117.0,
                end_min=117.5,
                retention_time_min=117.25,
                raw_area_uv_sec=1117.0,
                area_mau_sec=2.17,
                area_percent=1.0,
                fwhm_min=0.2,
            )
        )
        overflow = analysis_report_figures(project, [dataset], "en")
        self.assertEqual(len(overflow), 4)
        last_table = overflow[-1].axes[0].tables[0]
        last_rows = sorted({row for row, _column in last_table.get_celld() if row > 0})
        self.assertEqual(len(last_rows), 1)
        self.assertEqual(last_table[(1, 0)].get_text().get_text(), "117")
        for figure in overflow:
            figure.clear()

    def test_3d_chromatogram_uses_selected_order_existing_styles_and_full_raw_data(self):
        from hplc_app.plot3d import ThreeDPlotOptions, build_3d_chromatogram_figure

        first = load_ascii_file(str(SAMPLES / "210601.TXT"))
        second = load_ascii_file(str(SAMPLES / "225120.TXT"))
        first.label, second.label = "Second in table", "First in table"
        first.color, second.color = "#123456", "#abcdef"
        first.x_shift_min, second.x_shift_min = 0.5, -0.25
        first.offset, second.offset = 1000000.0, -1000000.0
        raw = [
            (dataset.time_min.copy(), dataset.intensity_uv.copy())
            for dataset in (second, first)
        ]
        method = Project().method
        method.x_axis_label = "Existing time title"
        method.y_axis_1_label = "Existing intensity title"
        method.axis_label_font_family = "DejaVu Sans"
        method.axis_label_font_size = 13.0
        method.tick_label_font_family = "DejaVu Sans"
        method.tick_label_font_size = 11.0
        method.line_width = 2.5
        options = ThreeDPlotOptions(
            y_axis_title="Sample order",
            z_min=-500.0,
            z_max=5000.0,
            elevation_deg=32.0,
            azimuth_deg=-48.0,
            aspect_x=2.0,
            aspect_y=1.0,
            aspect_z=3.0,
            x_tick_interval=2.5,
            z_tick_interval=250.0,
            axis_line_width=3.0,
            axis_label_font_size=8.0,
            tick_label_font_size=7.0,
        )
        figure = build_3d_chromatogram_figure(
            [second, first], method, (4.0, 12.0), options
        )
        axis = figure.axes[0]
        self.assertEqual(axis.get_xlabel(), "Existing time title")
        self.assertEqual(axis.get_ylabel(), "Sample order")
        self.assertEqual(axis.get_zlabel(), "Existing intensity title")
        self.assertEqual(axis.xaxis.label.get_fontsize(), 8.0)
        self.assertEqual(axis.get_xticklabels()[0].get_fontsize(), 7.0)
        self.assertEqual([item.get_text() for item in axis.get_yticklabels()],
                         ["First in table", "Second in table"])
        self.assertEqual(tuple(round(value, 6) for value in axis.get_xlim()), (4.0, 12.0))
        self.assertEqual(tuple(round(value, 6) for value in axis.get_zlim()), (-500.0, 5000.0))
        self.assertEqual((axis.elev, axis.azim), (32.0, -48.0))
        aspect = axis.get_box_aspect()
        self.assertAlmostEqual(aspect[0] / aspect[1], 2.0)
        self.assertAlmostEqual(aspect[2] / aspect[1], 3.0)
        self.assertAlmostEqual(
            np.diff(axis.xaxis.get_major_locator().tick_values(0.0, 10.0))[0], 2.5
        )
        self.assertAlmostEqual(
            np.diff(axis.zaxis.get_major_locator().tick_values(0.0, 1000.0))[0], 250.0
        )
        self.assertEqual(len(axis.lines), 2)
        for item in (axis.xaxis, axis.yaxis, axis.zaxis):
            self.assertFalse(item.pane.get_visible())
            self.assertEqual(item.line.get_color(), "#000000")
            self.assertEqual(item.line.get_linewidth(), 3.0)
        for index, (line, dataset) in enumerate(zip(axis.lines, (second, first))):
            x_values, y_values, z_values = line.get_data_3d()
            mask = (
                (dataset.time_min + dataset.x_shift_min >= 4.0)
                & (dataset.time_min + dataset.x_shift_min <= 12.0)
            )
            np.testing.assert_array_equal(
                x_values, dataset.time_min[mask] + dataset.x_shift_min
            )
            np.testing.assert_array_equal(z_values, dataset.intensity_uv[mask])
            np.testing.assert_array_equal(y_values, np.full(np.count_nonzero(mask), index))
            self.assertEqual(line.get_color().lower(), dataset.color)
            self.assertEqual(line.get_linewidth(), 2.5)
            self.assertEqual(len(x_values), int(np.count_nonzero(mask)))
        for dataset, (time, intensity) in zip((second, first), raw):
            np.testing.assert_array_equal(dataset.time_min, time)
            np.testing.assert_array_equal(dataset.intensity_uv, intensity)
        with tempfile.TemporaryDirectory() as directory:
            for suffix in ("png", "svg", "pdf"):
                path = Path(directory) / ("three-d." + suffix)
                figure.savefig(path, dpi=120)
                self.assertGreater(path.stat().st_size, 500)
        figure.clear()

    def test_3d_axis_display_options_hide_only_requested_labels_and_ticks(self):
        from hplc_app.plot3d import ThreeDPlotOptions, build_3d_chromatogram_figure

        first = load_ascii_file(str(SAMPLES / "210601.TXT"))
        second = load_ascii_file(str(SAMPLES / "225120.TXT"))
        method = Project().method
        defaults = ThreeDPlotOptions()
        self.assertTrue(defaults.show_x_label)
        self.assertTrue(defaults.show_y_label)
        self.assertTrue(defaults.show_z_label)
        self.assertTrue(defaults.show_x_tick_labels)
        self.assertTrue(defaults.show_y_tick_labels)
        self.assertTrue(defaults.show_z_tick_labels)
        figure = build_3d_chromatogram_figure(
            [first, second],
            method,
            (4.0, 12.0),
            ThreeDPlotOptions(
                show_x_label=False,
                show_y_label=True,
                show_z_label=False,
                show_x_tick_labels=False,
                show_y_tick_labels=True,
                show_z_tick_labels=False,
            ),
        )
        axis = figure.axes[0]
        self.assertFalse(axis.xaxis.label.get_visible())
        self.assertTrue(axis.yaxis.label.get_visible())
        self.assertFalse(axis.zaxis.label.get_visible())
        self.assertTrue(axis.get_yticklabels()[0].get_visible())
        self.assertTrue(all(not label.get_visible() for label in axis.get_xticklabels()))
        self.assertTrue(all(not label.get_visible() for label in axis.get_zticklabels()))
        with tempfile.TemporaryDirectory() as directory:
            for suffix in ("png", "svg", "pdf"):
                path = Path(directory) / ("hidden-axis-labels." + suffix)
                figure.savefig(path, dpi=120)
                self.assertGreater(path.stat().st_size, 500)
        figure.clear()

    def test_3d_grid_planes_reach_the_figure_and_its_image_output(self):
        from hplc_app.plot3d import ThreeDPlotOptions, build_3d_chromatogram_figure

        first = load_ascii_file(str(SAMPLES / "210601.TXT"))
        second = load_ascii_file(str(SAMPLES / "225120.TXT"))
        method = Project().method
        raw = [
            (dataset.time_min.copy(), dataset.intensity_uv.copy())
            for dataset in (first, second)
        ]

        defaults = ThreeDPlotOptions()
        self.assertTrue(defaults.grid_xy)
        self.assertFalse(defaults.grid_xz)
        self.assertFalse(defaults.grid_yz)
        self.assertEqual(ThreeDPlotOptions().elevation_deg, 20.0)
        self.assertEqual(ThreeDPlotOptions().azimuth_deg, -65.0)

        rendered = {}
        choices = {
            "none": (False, False, False, set()),
            "xy": (True, False, False, {"hplc-grid-xy"}),
            "xz": (False, True, False, {"hplc-grid-xz"}),
            "yz": (False, False, True, {"hplc-grid-yz"}),
            "all": (
                True,
                True,
                True,
                {"hplc-grid-xy", "hplc-grid-xz", "hplc-grid-yz"},
            ),
        }
        for name, (grid_xy, grid_xz, grid_yz, expected_gids) in choices.items():
            options = ThreeDPlotOptions(
                z_min=-500.0,
                z_max=5000.0,
                aspect_x=2.0,
                aspect_z=3.0,
                x_tick_interval=2.5,
                z_tick_interval=250.0,
                grid_xy=grid_xy,
                grid_xz=grid_xz,
                grid_yz=grid_yz,
            )
            figure = build_3d_chromatogram_figure(
                [first, second], method, (4.0, 12.0), options
            )
            axis = figure.axes[0]
            self.assertEqual(
                {collection.get_gid() for collection in axis.collections},
                expected_gids,
            )
            # The panes stay hidden for every plane selection.
            for item in (axis.xaxis, axis.yaxis, axis.zaxis):
                self.assertFalse(item.pane.get_visible())
            # Existing settings must survive the new option untouched.
            self.assertEqual(
                tuple(round(value, 6) for value in axis.get_zlim()), (-500.0, 5000.0)
            )
            aspect = axis.get_box_aspect()
            self.assertAlmostEqual(aspect[0] / aspect[1], 2.0)
            self.assertAlmostEqual(aspect[2] / aspect[1], 3.0)
            self.assertAlmostEqual(
                np.diff(axis.xaxis.get_major_locator().tick_values(0.0, 10.0))[0], 2.5
            )
            self.assertAlmostEqual(
                np.diff(axis.zaxis.get_major_locator().tick_values(0.0, 1000.0))[0],
                250.0,
            )
            self.assertEqual((axis.elev, axis.azim), (20.0, -65.0))
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / ("grid-%s.png" % name)
                figure.savefig(path, dpi=100)
                rendered[name] = path.read_bytes()
                self.assertGreater(len(rendered[name]), 500)
            figure.clear()

        # Exported images, not only the live preview, follow the plane choices.
        self.assertEqual(len(set(rendered.values())), len(choices))
        source = (ROOT / "hplc_app" / "plot3d.py").read_text(encoding="utf-8")
        self.assertNotIn("_axinfo", source)
        for dataset, (time, intensity) in zip((first, second), raw):
            np.testing.assert_array_equal(dataset.time_min, time)
            np.testing.assert_array_equal(dataset.intensity_uv, intensity)

    def test_3d_gradient_density_uses_dense_scale_segment_and_declared_aliases(self):
        from matplotlib import colormaps
        from hplc_app.plot3d import (
            COLORMAP_ALIASES,
            ThreeDPlotOptions,
            gradient_colors,
            suggest_z_tick_interval,
        )

        self.assertEqual(COLORMAP_ALIASES["Portland"], "coolwarm")
        self.assertEqual(COLORMAP_ALIASES["Picnic"], "Spectral")
        self.assertEqual(COLORMAP_ALIASES["Electric"], "inferno")
        self.assertEqual(ThreeDPlotOptions().axis_line_width, 4.0)
        for name in COLORMAP_ALIASES:
            self.assertEqual(len(gradient_colors(name, 100, 2)), 2)
        colors = gradient_colors("Blues", 40, 4)
        expected = [colormaps["Blues"](value) for value in (0.6, 0.7, 0.8, 0.9)]
        np.testing.assert_allclose(colors, expected)
        dataset = load_ascii_file(str(SAMPLES / "210601.TXT"))
        interval = suggest_z_tick_interval(
            [dataset], Project().method, (5.0, 10.0)
        )
        mask = (dataset.time_min >= 5.0) & (dataset.time_min <= 10.0)
        self.assertLessEqual(
            (np.max(dataset.intensity_uv[mask]) - np.min(dataset.intensity_uv[mask]))
            / interval,
            20.0,
        )


class _FakeSettingsBackend:
    def __init__(
        self,
        values=None,
        status=0,
        fail_value=False,
        fail_set=False,
        fail_sync=False,
    ):
        self.values = dict(values or {})
        self.status_value = status
        self.fail_value = fail_value
        self.fail_set = fail_set
        self.fail_sync = fail_sync

    def value(self, key, default=None):
        if self.fail_value:
            raise OSError("read failed")
        return self.values.get(key, default)

    def setValue(self, key, value):
        if self.fail_set:
            raise OSError("write failed")
        self.values[key] = value

    def sync(self):
        if self.fail_sync:
            raise OSError("sync failed")

    def status(self):
        return self.status_value


class ApplicationSettingsTests(unittest.TestCase):
    def test_preset_json_is_authoritative_over_legacy_qsettings_fallback(self):
        conditions, gradients = merge_preset_sources(
            {
                "shared": {"column_name": "legacy C4"},
                "legacy only": {"column_name": "C8"},
            },
            {
                "shared": {"gradient": [{"time_min": 1.0}]},
                "legacy only": {"gradient": []},
            },
            {
                "shared": {"column_name": "stored C18"},
                "stored only": {"column_name": "C30"},
            },
            {
                "shared": {"gradient": [{"time_min": 2.0}]},
                "stored only": {"gradient": []},
            },
        )
        self.assertEqual(conditions["shared"]["column_name"], "stored C18")
        self.assertIn("legacy only", conditions)
        self.assertIn("stored only", conditions)
        self.assertEqual(gradients["shared"]["gradient"][0]["time_min"], 2.0)
        self.assertIn("legacy only", gradients)
        self.assertIn("stored only", gradients)

    def test_all_application_keys_round_trip_without_renaming_legacy_keys(self):
        expected_keys = {
            "ui/language",
            "ui/dataset_column_order",
            "updates/automatic_check",
            "paths/import_directory",
            "paths/save_directory",
            "paths/last_import_directory",
            "paths/last_save_directory",
            "paths/last_project_directory",
            "database/path",
            "rendering/quality",
            "rendering/screen_renderer",
            "export/figure_format",
            "naming/author",
            "presets/conditions",
            "presets/gradients",
            "analysis/auto_peak_sensitivity_presets",
        }
        self.assertEqual(set(SETTING_SPECS), expected_keys)
        backend = _FakeSettingsBackend()
        store = ApplicationSettings(backend)
        values = {
            UI_LANGUAGE: "en",
            DATASET_COLUMN_ORDER: list(reversed(DEFAULT_DATASET_COLUMN_ORDER)),
            AUTOMATIC_UPDATE_CHECK: False,
            IMPORT_DIRECTORY: "C:/HPLC/import",
            SAVE_DIRECTORY: "C:/HPLC/save",
            LAST_IMPORT_DIRECTORY: "C:/HPLC/last-import",
            LAST_SAVE_DIRECTORY: "C:/HPLC/last-save",
            LAST_PROJECT_DIRECTORY: "C:/HPLC/projects",
            DATABASE_PATH: "C:/HPLC/lab.sqlite3",
            RENDERING_QUALITY: "lightweight",
            SCREEN_RENDERER: "matplotlib",
            FIGURE_FORMAT: "SVG",
            NAMING_AUTHOR: "M Shiba",
            LEGACY_CONDITION_PRESETS: {"C4": {"wavelength_nm": 280.0}},
            LEGACY_GRADIENT_PRESETS: {"10-90 B": {"gradient": []}},
            AUTO_PEAK_SENSITIVITY_PRESETS: {
                "low": {"auto_peak_snr_threshold": 15.0},
                "medium": {"auto_peak_snr_threshold": 7.0},
                "high": {"auto_peak_snr_threshold": 3.0},
            },
        }
        self.assertTrue(store.set_many(values))
        self.assertEqual(store.get(UI_LANGUAGE), "en")
        self.assertEqual(
            store.get(DATASET_COLUMN_ORDER),
            list(reversed(DEFAULT_DATASET_COLUMN_ORDER)),
        )
        self.assertFalse(store.get(AUTOMATIC_UPDATE_CHECK))
        self.assertEqual(store.get(IMPORT_DIRECTORY), "C:/HPLC/import")
        self.assertEqual(store.get(SAVE_DIRECTORY), "C:/HPLC/save")
        self.assertEqual(store.get(LAST_IMPORT_DIRECTORY), "C:/HPLC/last-import")
        self.assertEqual(store.get(LAST_SAVE_DIRECTORY), "C:/HPLC/last-save")
        self.assertEqual(store.get(LAST_PROJECT_DIRECTORY), "C:/HPLC/projects")
        self.assertEqual(store.get(DATABASE_PATH), "C:/HPLC/lab.sqlite3")
        self.assertEqual(store.get(RENDERING_QUALITY), "lightweight")
        self.assertEqual(store.get(SCREEN_RENDERER), "matplotlib")
        self.assertEqual(store.get(FIGURE_FORMAT), "svg")
        self.assertEqual(store.get(NAMING_AUTHOR), "M Shiba")
        self.assertEqual(
            store.get(LEGACY_CONDITION_PRESETS),
            {"C4": {"wavelength_nm": 280.0}},
        )
        self.assertEqual(
            store.get(LEGACY_GRADIENT_PRESETS),
            {"10-90 B": {"gradient": []}},
        )
        self.assertEqual(
            store.get(AUTO_PEAK_SENSITIVITY_PRESETS)["medium"][
                "auto_peak_snr_threshold"
            ],
            7.0,
        )
        self.assertEqual(
            store.get(AUTO_PEAK_SENSITIVITY_PRESETS)["medium"][
                "auto_peak_max_count"
            ],
            200,
        )
        self.assertIsInstance(backend.values[LEGACY_CONDITION_PRESETS], str)
        self.assertIsInstance(backend.values[AUTO_PEAK_SENSITIVITY_PRESETS], str)

    def test_missing_corrupt_and_failed_settings_use_safe_fallbacks(self):
        backend = _FakeSettingsBackend(
            {
                UI_LANGUAGE: "de",
                DATASET_COLUMN_ORDER: '["selected", "missing"]',
                IMPORT_DIRECTORY: 123,
                RENDERING_QUALITY: "unsupported",
                SCREEN_RENDERER: "unsupported",
                FIGURE_FORMAT: "bmp",
                LEGACY_CONDITION_PRESETS: "not-json",
                LEGACY_GRADIENT_PRESETS: "[]",
                AUTO_PEAK_SENSITIVITY_PRESETS: '{"medium":{"auto_peak_snr_threshold":"bad"}}',
            }
        )
        store = ApplicationSettings(backend)
        self.assertEqual(store.get(UI_LANGUAGE), "ja")
        self.assertEqual(
            store.get(DATASET_COLUMN_ORDER), list(DEFAULT_DATASET_COLUMN_ORDER)
        )
        self.assertEqual(store.get(IMPORT_DIRECTORY), "")
        self.assertEqual(store.get(RENDERING_QUALITY), default_render_quality())
        self.assertEqual(store.get(SCREEN_RENDERER), default_screen_renderer())
        self.assertEqual(store.get(FIGURE_FORMAT), "png")
        self.assertEqual(store.get(LEGACY_CONDITION_PRESETS), {})
        self.assertEqual(store.get(LEGACY_GRADIENT_PRESETS), {})
        self.assertEqual(
            store.get(AUTO_PEAK_SENSITIVITY_PRESETS),
            default_auto_peak_sensitivity_presets(),
        )
        self.assertEqual(
            ApplicationSettings(_FakeSettingsBackend(fail_value=True)).get(
                NAMING_AUTHOR
            ),
            "",
        )
        with self.assertRaisesRegex(KeyError, "Unknown application setting"):
            store.get("unknown/key")

    def test_legacy_dataset_column_order_drops_source_without_reordering(self):
        legacy_order = [
            "source",
            "visible",
            "selected",
            "label",
            "color",
            "run_id",
            "timestamp",
            "wavelength",
            "y_axis",
            "auv",
            "column",
            "x_shift",
            "offset",
        ]
        backend = _FakeSettingsBackend(
            {DATASET_COLUMN_ORDER: json.dumps(legacy_order)}
        )

        self.assertEqual(
            ApplicationSettings(backend).get(DATASET_COLUMN_ORDER),
            [column for column in legacy_order if column != "source"] + ["solo"],
        )

    def test_write_and_sync_failures_are_non_fatal_and_reported(self):
        self.assertFalse(
            ApplicationSettings(_FakeSettingsBackend(fail_set=True)).set(
                NAMING_AUTHOR, "M Shiba"
            )
        )
        self.assertFalse(
            ApplicationSettings(_FakeSettingsBackend(fail_sync=True)).set(
                NAMING_AUTHOR, "M Shiba", sync=True
            )
        )
        self.assertFalse(ApplicationSettings(_FakeSettingsBackend(status=1)).sync())

    def test_gui_has_no_direct_qsettings_key_access(self):
        gui_source = (ROOT / "hplc_app" / "gui.py").read_text(encoding="utf-8")
        self.assertNotIn("QSettings(", gui_source)
        self.assertNotIn("._settings.value(", gui_source)
        self.assertNotIn("._settings.setValue(", gui_source)
        for key in SETTING_SPECS:
            self.assertNotIn('"%s"' % key, gui_source)


if __name__ == "__main__":
    unittest.main()
