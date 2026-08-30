"""Benchmark complete MainWindow refreshes for legacy and native screen paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hplc_app.gui import MainWindow
from hplc_app.analysis import recalculate_dataset_peaks
from hplc_app.import_batch import (
    SUPPORTED_CHROMATOGRAM_SUFFIXES,
    discover_chromatogram_files,
)
from hplc_app.models import (
    Dataset,
    FractionRegion,
    GradientPoint,
    PeakRegion,
    Project,
    TextAnnotation,
    VerticalMarker,
)
from hplc_app.qt_compat import QT_API, QtCore, QtWidgets
from hplc_app.renderer_benchmark import (
    RendererWorkload,
    synthetic_chromatograms,
)
from hplc_app.pyqtgraph_scene import pyqtgraph_scene_available
from hplc_app.parser import load_chromatogram_file


def _decorate_project(project):
    first = project.datasets[0]
    start = float(first.time_min[0])
    end = float(first.time_min[-1])
    span = max(end - start, 1.0)
    first.measurement.gradient = [
        GradientPoint(start, 90.0, 10.0, 0.0, 0.0),
        GradientPoint(end, 10.0, 90.0, 0.0, 0.0),
    ]
    first.peaks = [PeakRegion(
        start_min=start + span * 0.10,
        end_min=start + span * 0.15,
    )]
    recalculate_dataset_peaks(first)
    project.method.show_gradient_b = True
    project.method.show_integration_areas = True
    project.method.show_retention_labels = True
    target = project.datasets[1] if len(project.datasets) > 1 else first
    project.vertical_markers.append(VerticalMarker(
        x_min=start + span * 0.40, y_axis=target.y_axis
    ))
    project.fraction_regions.append(FractionRegion(
        start_min=start + span * 0.50,
        end_min=start + span * 0.60,
        interval_min=max(span * 0.02, 0.01),
    ))
    project.annotations.append(TextAnnotation(
        text="Benchmark annotation",
        x_min=start + span * 0.70,
        y_value=float(np.nanmedian(target.intensity_uv)),
        dataset_id=target.id,
        y_axis=target.y_axis,
    ))


def _project_for(workload: RendererWorkload, *, decorated=False):
    x_values, traces = synthetic_chromatograms(workload)
    datasets = []
    for index, values in enumerate(traces):
        datasets.append(Dataset(
            label="Benchmark %02d" % (index + 1),
            short_label="B%02d" % (index + 1),
            original_filename="benchmark-%02d.txt" % (index + 1),
            y_axis=1 if index % 2 == 0 else 2,
            time_min=x_values.copy(),
            intensity_uv=values.copy(),
        ))
    project = Project(datasets=datasets)
    if decorated:
        _decorate_project(project)
    return project, x_values, traces


def _discover_inputs(inputs, recursive=False):
    discovered = []
    for entry in inputs:
        path = Path(entry)
        if path.is_dir():
            discovered.extend(discover_chromatogram_files(path, recursive))
        elif path.is_file() and path.suffix.lower() in SUPPORTED_CHROMATOGRAM_SUFFIXES:
            discovered.append(path)
        else:
            raise ValueError("Unsupported or missing benchmark input: %s" % path)
    unique = {}
    for path in discovered:
        resolved = path.resolve()
        unique[str(resolved).casefold()] = resolved
    result = sorted(unique.values(), key=lambda path: (str(path).casefold(), str(path)))
    if not result:
        raise ValueError("No supported .gcd or .txt benchmark inputs were found")
    return result


def _project_from_inputs(inputs, *, recursive=False, decorated=False):
    paths = _discover_inputs(inputs, recursive=recursive)
    datasets = []
    loaded_paths = []
    errors = []
    for path in paths:
        try:
            datasets.append(load_chromatogram_file(str(path)))
            loaded_paths.append(path)
        except (OSError, ValueError) as exc:
            errors.append({"file": path.name, "error": str(exc)})
    if not datasets:
        details = "; ".join(
            "%s: %s" % (item["file"], item["error"])
            for item in errors
        )
        raise ValueError("No benchmark input could be loaded: %s" % details)
    project = Project(datasets=datasets)
    if decorated:
        _decorate_project(project)
    return project, loaded_paths, errors


def _project_digest(project):
    digest = hashlib.sha256()
    for dataset in project.datasets:
        for array in (dataset.time_min, dataset.intensity_uv):
            contiguous = np.ascontiguousarray(array)
            digest.update(str(contiguous.dtype).encode("ascii"))
            digest.update(str(contiguous.shape).encode("ascii"))
            digest.update(contiguous.tobytes())
    return digest.hexdigest()


def _measure(window, application, repeats: int):
    durations = []
    for _ in range(repeats):
        started = time.perf_counter()
        window._plot()
        application.processEvents()
        durations.append(time.perf_counter() - started)
    return durations


def benchmark(
    workload: RendererWorkload,
    *,
    decorated=False,
    inputs=(),
    recursive=False,
):
    if QT_API != 6 or not pyqtgraph_scene_available():
        raise RuntimeError("The integrated benchmark requires Qt 6 and PyQtGraph")

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    original_format = QtCore.QSettings.defaultFormat()
    ini_format = QtCore.QSettings.Format.IniFormat
    user_scope = QtCore.QSettings.Scope.UserScope
    input_paths = []
    input_errors = []
    if inputs:
        project, input_paths, input_errors = _project_from_inputs(
            inputs, recursive=recursive, decorated=decorated
        )
        source = "files"
    else:
        project, _x_values, _traces = _project_for(
            workload, decorated=decorated
        )
        source = "synthetic"
    digest_before = _project_digest(project)

    with tempfile.TemporaryDirectory() as settings_directory:
        QtCore.QSettings.setDefaultFormat(ini_format)
        QtCore.QSettings.setPath(ini_format, user_scope, settings_directory)
        original_config = os.environ.get("HPLC_ANALYZER_CONFIG_DIR")
        os.environ["HPLC_ANALYZER_CONFIG_DIR"] = settings_directory
        window = None
        try:
            window = MainWindow()
            window.project = project
            window._refresh_all(0)
            window.resize(1200, 760)
            window.show()
            application.processEvents()

            window._plot()
            application.processEvents()
            legacy = _measure(window, application, workload.repeats)

            window.screen_preview_checkbox.setChecked(True)
            application.processEvents()
            native = _measure(window, application, workload.repeats)
            preview = window._screen_preview
            evidence = {
                "matplotlib_complete": window._matplotlib_screen_complete,
                "matplotlib_dataset_lines": len(window._dataset_lines),
                "matplotlib_axis_lines": sum(
                    len(axis.lines) for axis in window.figure.axes
                ),
                "native_counts": dict(preview.consumer.last_evidence["counts"]),
                "native_reused_traces": preview.consumer.last_evidence.get(
                    "reused_traces", False
                ),
                "native_reused_static": preview.consumer.last_evidence.get(
                    "reused_static", False
                ),
                "native_source_trace_points": preview.consumer.last_evidence.get(
                    "source_trace_points", 0
                ),
                "native_rendered_trace_points": preview.consumer.last_evidence.get(
                    "rendered_trace_points", 0
                ),
            }
        finally:
            if window is not None:
                window.project.dirty = False
                window.close()
            QtCore.QSettings.setDefaultFormat(original_format)
            if original_config is None:
                os.environ.pop("HPLC_ANALYZER_CONFIG_DIR", None)
            else:
                os.environ["HPLC_ANALYZER_CONFIG_DIR"] = original_config

    digest_after = _project_digest(project)
    legacy_median = statistics.median(legacy)
    native_median = statistics.median(native)
    return {
        "schema": 1,
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "qt_api": QT_API,
        },
        "workload": {
            "source": source,
            "traces": len(project.datasets),
            "total_points": sum(
                int(dataset.time_min.size) for dataset in project.datasets
            ),
            "minimum_points_per_trace": min(
                int(dataset.time_min.size) for dataset in project.datasets
            ),
            "maximum_points_per_trace": max(
                int(dataset.time_min.size) for dataset in project.datasets
            ),
            "points_per_trace": (
                workload.point_count if source == "synthetic" else None
            ),
            "repeats": workload.repeats,
            "seed": workload.seed,
            "decorated": bool(decorated),
            "input_files": [path.name for path in input_paths],
            "suffix_counts": {
                suffix: sum(path.suffix.lower() == suffix for path in input_paths)
                for suffix in sorted({path.suffix.lower() for path in input_paths})
            },
            "skipped_inputs": input_errors,
        },
        "legacy_matplotlib_seconds": legacy,
        "native_preview_seconds": native,
        "legacy_median_seconds": legacy_median,
        "native_median_seconds": native_median,
        "legacy_to_native_ratio": legacy_median / native_median,
        "raw_digest_before": digest_before,
        "raw_digest_after": digest_after,
        "raw_arrays_unchanged": digest_before == digest_after,
        "native_skeleton_evidence": evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces", type=int, default=8)
    parser.add_argument("--points", type=int, default=100000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument(
        "--decorated",
        action="store_true",
        help="Include B%, a peak overlay, marker, fraction, and annotation.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        nargs="+",
        help="One or more .gcd/.txt files or directories; otherwise use synthetic data.",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively discover supported files below input directories.",
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    payload = benchmark(
        RendererWorkload(
            trace_count=arguments.traces,
            point_count=arguments.points,
            repeats=arguments.repeats,
            seed=arguments.seed,
        ),
        decorated=arguments.decorated,
        inputs=arguments.input or (),
        recursive=arguments.recursive,
    )
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if arguments.output is None:
        print(rendered)
    else:
        arguments.output.write_text(rendered + "\n", encoding="utf-8")
        print(str(arguments.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
