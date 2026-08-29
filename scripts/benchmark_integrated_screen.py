"""Benchmark complete MainWindow refreshes for legacy and native screen paths."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hplc_app.gui import MainWindow
from hplc_app.models import Dataset, Project
from hplc_app.qt_compat import QT_API, QtCore, QtWidgets
from hplc_app.renderer_benchmark import (
    RendererWorkload,
    arrays_digest,
    synthetic_chromatograms,
)
from hplc_app.pyqtgraph_scene import pyqtgraph_scene_available


def _project_for(workload: RendererWorkload):
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
    return Project(datasets=datasets), x_values, traces


def _measure(window, application, repeats: int):
    durations = []
    for _ in range(repeats):
        started = time.perf_counter()
        window._plot()
        application.processEvents()
        durations.append(time.perf_counter() - started)
    return durations


def benchmark(workload: RendererWorkload):
    if QT_API != 6 or not pyqtgraph_scene_available():
        raise RuntimeError("The integrated benchmark requires Qt 6 and PyQtGraph")

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    original_format = QtCore.QSettings.defaultFormat()
    ini_format = QtCore.QSettings.Format.IniFormat
    user_scope = QtCore.QSettings.Scope.UserScope
    project, x_values, traces = _project_for(workload)
    digest_before = arrays_digest(x_values, traces)

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

    digest_after = arrays_digest(
        project.datasets[0].time_min,
        [dataset.intensity_uv for dataset in project.datasets],
    )
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
            "traces": workload.trace_count,
            "points_per_trace": workload.point_count,
            "repeats": workload.repeats,
            "seed": workload.seed,
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
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    payload = benchmark(RendererWorkload(
        trace_count=arguments.traces,
        point_count=arguments.points,
        repeats=arguments.repeats,
        seed=arguments.seed,
    ))
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if arguments.output is None:
        print(rendered)
    else:
        arguments.output.write_text(rendered + "\n", encoding="utf-8")
        print(str(arguments.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
