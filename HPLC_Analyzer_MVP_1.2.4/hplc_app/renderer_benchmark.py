"""Isolated screen-renderer benchmark helpers.

This module is not imported by the application GUI.  PyQtGraph is deliberately
optional and loaded only when its benchmark is requested.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import importlib
import platform
import sys
import time
from typing import Callable, Dict, List, Tuple

import numpy as np


@dataclass(frozen=True)
class RendererWorkload:
    trace_count: int = 8
    point_count: int = 100000
    repeats: int = 3
    seed: int = 1729

    def __post_init__(self):
        if int(self.trace_count) <= 0:
            raise ValueError("trace_count must be positive")
        if int(self.point_count) < 2:
            raise ValueError("point_count must be at least 2")
        if int(self.repeats) <= 0:
            raise ValueError("repeats must be positive")


def synthetic_chromatograms(
    workload: RendererWorkload,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return deterministic chromatography-like arrays without external files."""

    rng = np.random.RandomState(int(workload.seed))
    x_values = np.linspace(0.0, 30.0, int(workload.point_count), dtype=float)
    traces = []
    for ordinal in range(int(workload.trace_count)):
        center = 4.0 + (ordinal % 6) * 4.2
        width = 0.08 + (ordinal % 4) * 0.07
        height = 1.0 + ordinal * 0.12
        peak = height * np.exp(-0.5 * ((x_values - center) / width) ** 2)
        baseline = 0.015 * np.sin(x_values * (0.7 + ordinal * 0.03))
        noise = rng.normal(0.0, 0.0015, x_values.size)
        traces.append(peak + baseline + noise + ordinal * 0.04)
    return x_values, np.asarray(traces, dtype=float)


def arrays_digest(x_values: np.ndarray, traces: np.ndarray) -> str:
    digest = hashlib.sha256()
    for array in (np.asarray(x_values), np.asarray(traces)):
        contiguous = np.ascontiguousarray(array)
        digest.update(str(contiguous.dtype).encode("ascii"))
        digest.update(str(contiguous.shape).encode("ascii"))
        digest.update(contiguous.tobytes())
    return digest.hexdigest()


def _matplotlib_draw(x_values: np.ndarray, traces: np.ndarray) -> None:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(10.0, 6.0), dpi=100)
    canvas = FigureCanvasAgg(figure)
    axes = figure.add_subplot(111)
    for trace in traces:
        axes.plot(x_values, trace, linewidth=1.0, antialiased=False)
    axes.set_xlim(float(x_values[0]), float(x_values[-1]))
    canvas.draw()
    figure.clear()


def _pyqtgraph_draw(x_values: np.ndarray, traces: np.ndarray) -> None:
    pg = importlib.import_module("pyqtgraph")
    qt_widgets = importlib.import_module("pyqtgraph.Qt.QtWidgets")
    application = qt_widgets.QApplication.instance()
    if application is None:
        application = qt_widgets.QApplication([])
    widget = pg.PlotWidget()
    widget.resize(1000, 600)
    plot_item = widget.getPlotItem()
    plot_item.setClipToView(True)
    plot_item.setDownsampling(auto=True, mode="peak")
    for trace in traces:
        plot_item.plot(
            x_values,
            trace,
            pen=pg.mkPen(width=1),
            antialias=False,
            skipFiniteCheck=True,
        )
    widget.show()
    application.processEvents()
    widget.grab()
    widget.close()


BACKEND_RUNNERS: Dict[str, Callable[[np.ndarray, np.ndarray], None]] = {
    "matplotlib_agg": _matplotlib_draw,
    "pyqtgraph": _pyqtgraph_draw,
}


def benchmark_backend(
    backend_id: str,
    workload: RendererWorkload,
) -> Dict[str, object]:
    """Measure one backend and return a JSON-serializable result."""

    normalized = str(backend_id or "").strip().lower()
    if normalized not in BACKEND_RUNNERS:
        raise ValueError("Unsupported benchmark backend: %s" % backend_id)
    x_values, traces = synthetic_chromatograms(workload)
    digest_before = arrays_digest(x_values, traces)
    if normalized == "pyqtgraph":
        try:
            importlib.import_module("pyqtgraph")
        except ImportError as exc:
            return {
                "backend_id": normalized,
                "status": "skipped",
                "reason": "optional dependency unavailable: %s" % exc,
                "workload": asdict(workload),
                "environment": environment_snapshot(),
                "raw_data_sha256": digest_before,
                "durations_seconds": [],
            }
    durations: List[float] = []
    runner = BACKEND_RUNNERS[normalized]
    for _repeat in range(int(workload.repeats)):
        started = time.perf_counter()
        runner(x_values, traces)
        durations.append(time.perf_counter() - started)
    digest_after = arrays_digest(x_values, traces)
    if digest_after != digest_before:
        raise RuntimeError("Renderer benchmark mutated source arrays")
    ordered = sorted(durations)
    return {
        "backend_id": normalized,
        "status": "completed",
        "reason": "",
        "workload": asdict(workload),
        "environment": environment_snapshot(),
        "raw_data_sha256": digest_before,
        "durations_seconds": durations,
        "median_seconds": ordered[len(ordered) // 2],
        "minimum_seconds": min(durations),
    }


def environment_snapshot() -> Dict[str, str]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "numpy": np.__version__,
    }


def benchmark_suite(workload: RendererWorkload) -> Dict[str, object]:
    return {
        "schema_version": 1,
        "results": [
            benchmark_backend("matplotlib_agg", workload),
            benchmark_backend("pyqtgraph", workload),
        ],
    }
