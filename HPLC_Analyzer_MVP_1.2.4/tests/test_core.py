from __future__ import annotations

from copy import deepcopy
from contextlib import closing
import csv
import ast
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
import unittest
from unittest import mock
import zipfile

import numpy as np

from hplc_app import APP_VERSION, PROJECT_FORMAT_MAJOR, PROJECT_SCHEMA_VERSION
from hplc_app.analysis import (
    convert_uv,
    detect_peaks,
    gradient_at,
    integrate_peak,
    recalculate_dataset_peaks,
    split_peak_region,
    validate_gradient,
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
    GradientPoint,
    MeasurementMetadata,
    PeakRegion,
    Project,
    Run,
    Solvent,
    TextAnnotation,
)
from hplc_app.naming import build_project_filename, suggest_project_name_parts
from hplc_app.gcd_parser import GcdParseError, parse_gcd_bytes, parse_gcd_streams
from hplc_app.parser import dataset_from_bytes, load_ascii_file, load_chromatogram_file
from hplc_app.preset_store import (
    filter_preset_names,
    load_preset_store,
    load_preset_store_with_metadata,
    merge_preset_sources,
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
    HIGH_QUALITY,
    LIGHTWEIGHT,
    default_render_quality,
    minmax_decimate,
    screen_series,
)
from hplc_app.timestamps import acquisition_timestamp, timestamp_from_filename
from hplc_app.settings_store import (
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
    SETTING_SPECS,
    UI_LANGUAGE,
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
    archive_root_name,
    default_archive_path,
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
        rawdata = ROOT.parent / "rawdata"
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
                        "acquisition_datetime": "2026-05-07T12:00:00",
                        "wavelength_nm": 214.0,
                        "column_name": "C4",
                    },
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
        self.assertEqual(migrated["schema_version"], 104)
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
        self.assertEqual(migrated["schema_version"], 104)
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
            self.assertEqual(payload["format_version"], 2)
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
        self.assertIn("--define=AppVersion=%APP_VERSION%", installer_helper)
        self.assertIn(
            "--define=AppVersionNumeric=%APP_VERSION_NUMERIC%", installer_helper
        )
        self.assertIn(
            "--define=ArtifactBaseName=%ARTIFACT_BASE_NAME%", installer_helper
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
        self.assertEqual(
            verify_source_consistency(ROOT, "v" + APP_VERSION, PROJECT_SCHEMA_VERSION),
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
        dataset.y_axis = 2
        project = Project(
            title="roundtrip",
            analysis_date="20260809",
            column_name="COSMOSIL C4",
            condition_name="RP-C4",
            author="MShiba",
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
        project.method.legend_location = "upper left"
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
            self.assertEqual(restored.y_axis, 2)
            self.assertEqual(restored.x_shift_min, 0.25)
            self.assertEqual(restored.gradient_preset_name, "RP-C4")
            self.assertEqual(loaded.condition_presets["280 nm"]["wavelength_nm"], 280.0)
            self.assertIn("RP-C4", loaded.gradient_presets)
            self.assertTrue(loaded.method.show_gradient_b)
            self.assertEqual(loaded.method.legend_location, "upper left")
            self.assertEqual(loaded.method.gradient_axis_label, "ACN (%)")
            self.assertTrue(loaded.method.show_retention_labels)
            self.assertEqual(loaded.method.zoom_axis, "x")
            self.assertEqual(loaded.project_id, project.project_id)
            self.assertEqual(loaded.analysis_date, "20260809")
            self.assertEqual(loaded.column_name, "COSMOSIL C4")
            self.assertEqual(loaded.condition_name, "RP-C4")
            self.assertEqual(loaded.author, "MShiba")
            with zipfile.ZipFile(path, "r") as archive:
                manifest = json.loads(archive.read("project.json").decode("utf-8"))
            self.assertEqual(manifest["format_major"], PROJECT_FORMAT_MAJOR)
            self.assertEqual(manifest["schema_version"], PROJECT_SCHEMA_VERSION)
            self.assertNotIn("preset_metadata", manifest)

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
        project = Project(datasets=[dataset])
        project.method.view_mode = "overview_detail"
        project.method.x_tick_mode = "manual"
        project.method.x_major_tick_min = 2.0
        project.method.x_minor_tick_min = 0.5
        project.method.axis_label_font_family = "Arial"
        project.method.axis_label_font_size = 12.0
        project.method.axis_label_color = "#123456"
        project.method.retention_label_font_size = 11.5
        project.method.retention_label_color = "#000000"
        project.method.auto_peak_snr_threshold = 8.0
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
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "new_fields.hplcproj")
            save_project(path, project)
            loaded = load_project(path)
            self.assertEqual(loaded.method.view_mode, "overview_detail")
            self.assertEqual(loaded.method.x_tick_mode, "manual")
            self.assertEqual(loaded.method.x_major_tick_min, 2.0)
            self.assertEqual(loaded.method.x_minor_tick_min, 0.5)
            self.assertEqual(loaded.method.axis_label_font_family, "Arial")
            self.assertEqual(loaded.method.axis_label_color, "#123456")
            self.assertEqual(loaded.method.retention_label_font_size, 11.5)
            self.assertEqual(loaded.method.retention_label_color, "#000000")
            self.assertEqual(loaded.method.auto_peak_snr_threshold, 8.0)
            self.assertEqual(loaded.datasets[0].peaks[0].integration_source, "auto")
            self.assertEqual(loaded.datasets[0].peaks[0].notes, "identified as LL-37")
            self.assertEqual(len(loaded.annotations), 1)
            self.assertEqual(loaded.annotations[0].text, "LL-37")
            self.assertEqual(loaded.annotations[0].dataset_id, dataset.id)
            self.assertEqual(loaded.annotations[0].font_family, "Arial")

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
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "peaks.csv")
            export_peak_csv(path, [dataset])
            with open(path, "r", encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            self.assertIn("raw_area_uV_sec", rows[0])
            self.assertIn("area_mAU_sec", rows[0])
            self.assertNotIn("raw_area_uV_min", rows[0])
            self.assertNotIn("area_mAU_min", rows[0])
            raw_index = rows[0].index("raw_area_uV_sec")
            mau_index = rows[0].index("area_mAU_sec")
            self.assertAlmostEqual(float(rows[1][raw_index]), dataset.peaks[0].raw_area_uv_sec)
            self.assertAlmostEqual(float(rows[1][mau_index]), dataset.peaks[0].area_mau_sec)

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
        guide = (ROOT.parent / ".github" / "INSTALLER_UPGRADE_TEST.md").read_text(
            encoding="utf-8"
        )
        evidence = (
            ROOT.parent / ".github" / "INSTALLER_UPGRADE_EVIDENCE.md"
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
        dataset.peaks = [PeakRegion(start_min=1.0, end_min=2.0)]
        recalculate_dataset_peaks(dataset)
        project = Project(title="Report test", datasets=[dataset])
        figures = analysis_report_figures(project, [dataset], "en")
        plot_axis = next(
            axis
            for axis in figures[0].axes
            if axis.get_title(loc="left") == "Chromatogram"
        )
        retention_label = "%.2f" % dataset.peaks[0].retention_time_min
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
        self.assertIn("Area (mAU·sec)", report_cells)
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
            "Amount (µg)",
            "Method",
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
            "paths/import_directory",
            "paths/save_directory",
            "paths/last_import_directory",
            "paths/last_save_directory",
            "paths/last_project_directory",
            "database/path",
            "rendering/quality",
            "export/figure_format",
            "naming/author",
            "presets/conditions",
            "presets/gradients",
        }
        self.assertEqual(set(SETTING_SPECS), expected_keys)
        backend = _FakeSettingsBackend()
        store = ApplicationSettings(backend)
        values = {
            UI_LANGUAGE: "en",
            IMPORT_DIRECTORY: "C:/HPLC/import",
            SAVE_DIRECTORY: "C:/HPLC/save",
            LAST_IMPORT_DIRECTORY: "C:/HPLC/last-import",
            LAST_SAVE_DIRECTORY: "C:/HPLC/last-save",
            LAST_PROJECT_DIRECTORY: "C:/HPLC/projects",
            DATABASE_PATH: "C:/HPLC/lab.sqlite3",
            RENDERING_QUALITY: "lightweight",
            FIGURE_FORMAT: "SVG",
            NAMING_AUTHOR: "M Shiba",
            LEGACY_CONDITION_PRESETS: {"C4": {"wavelength_nm": 280.0}},
            LEGACY_GRADIENT_PRESETS: {"10-90 B": {"gradient": []}},
        }
        self.assertTrue(store.set_many(values))
        self.assertEqual(store.get(UI_LANGUAGE), "en")
        self.assertEqual(store.get(IMPORT_DIRECTORY), "C:/HPLC/import")
        self.assertEqual(store.get(SAVE_DIRECTORY), "C:/HPLC/save")
        self.assertEqual(store.get(LAST_IMPORT_DIRECTORY), "C:/HPLC/last-import")
        self.assertEqual(store.get(LAST_SAVE_DIRECTORY), "C:/HPLC/last-save")
        self.assertEqual(store.get(LAST_PROJECT_DIRECTORY), "C:/HPLC/projects")
        self.assertEqual(store.get(DATABASE_PATH), "C:/HPLC/lab.sqlite3")
        self.assertEqual(store.get(RENDERING_QUALITY), "lightweight")
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
        self.assertIsInstance(backend.values[LEGACY_CONDITION_PRESETS], str)

    def test_missing_corrupt_and_failed_settings_use_safe_fallbacks(self):
        backend = _FakeSettingsBackend(
            {
                UI_LANGUAGE: "de",
                IMPORT_DIRECTORY: 123,
                RENDERING_QUALITY: "unsupported",
                FIGURE_FORMAT: "bmp",
                LEGACY_CONDITION_PRESETS: "not-json",
                LEGACY_GRADIENT_PRESETS: "[]",
            }
        )
        store = ApplicationSettings(backend)
        self.assertEqual(store.get(UI_LANGUAGE), "ja")
        self.assertEqual(store.get(IMPORT_DIRECTORY), "")
        self.assertEqual(store.get(RENDERING_QUALITY), default_render_quality())
        self.assertEqual(store.get(FIGURE_FORMAT), "png")
        self.assertEqual(store.get(LEGACY_CONDITION_PRESETS), {})
        self.assertEqual(store.get(LEGACY_GRADIENT_PRESETS), {})
        self.assertEqual(
            ApplicationSettings(_FakeSettingsBackend(fail_value=True)).get(
                NAMING_AUTHOR
            ),
            "",
        )
        with self.assertRaisesRegex(KeyError, "Unknown application setting"):
            store.get("unknown/key")

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
