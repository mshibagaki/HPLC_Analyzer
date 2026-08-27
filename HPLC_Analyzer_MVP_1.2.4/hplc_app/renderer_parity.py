"""Optional PyQtGraph feature-parity probe for the interactive screen.

The application never imports this module during normal startup.  It exists to
collect evidence before any production renderer migration.
"""

from __future__ import annotations

import importlib
import platform
from typing import Dict, Sequence

import numpy as np

from .renderer_benchmark import RendererWorkload, arrays_digest, synthetic_chromatograms


FEATURES = (
    "primary_trace",
    "secondary_y_axis",
    "split_panels",
    "gradient_axis",
    "zoom_pan_range",
    "integration_region",
    "retention_label",
    "fixed_size_text",
    "vertical_marker",
    "curve_picking",
    "snapshot",
)


def _entry(status: str, evidence: str) -> Dict[str, str]:
    return {"status": status, "evidence": evidence}


def unavailable_parity_report(reason: str) -> Dict[str, object]:
    return {
        "schema_version": 1,
        "backend_id": "pyqtgraph",
        "status": "skipped",
        "reason": str(reason),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "numpy": np.__version__,
        },
        "raw_data_sha256": "",
        "features": {
            feature: _entry("not_tested", "optional backend unavailable")
            for feature in FEATURES
        },
    }


def three_axis_ranges_are_independent(
    primary_range: Sequence[Sequence[float]],
    secondary_range: Sequence[Sequence[float]],
    gradient_range: Sequence[Sequence[float]],
    tolerance: float = 0.01,
) -> bool:
    """Confirm three views share X while Y2 and B% keep independent ranges."""

    x_ranges = (primary_range[0], secondary_range[0], gradient_range[0])
    shared_x = all(
        abs(current[index] - x_ranges[0][index]) <= tolerance
        for current in x_ranges[1:]
        for index in (0, 1)
    )
    secondary_y = secondary_range[1]
    gradient_y = gradient_range[1]
    independent_y = any(
        abs(secondary_y[index] - gradient_y[index]) > tolerance
        for index in (0, 1)
    )
    gradient_is_percent = (
        abs(gradient_y[0]) <= tolerance
        and abs(gradient_y[1] - 100.0) <= tolerance
    )
    return shared_x and independent_y and gradient_is_percent


def probe_pyqtgraph_parity() -> Dict[str, object]:
    """Create representative screen objects and return machine-readable evidence."""

    try:
        pg = importlib.import_module("pyqtgraph")
        qt_widgets = importlib.import_module("pyqtgraph.Qt.QtWidgets")
    except ImportError as exc:
        return unavailable_parity_report("optional dependency unavailable: %s" % exc)

    workload = RendererWorkload(trace_count=3, point_count=20000, repeats=1)
    x_values, traces = synthetic_chromatograms(workload)
    digest_before = arrays_digest(x_values, traces)
    application = qt_widgets.QApplication.instance()
    if application is None:
        application = qt_widgets.QApplication([])

    features = {}
    widget = pg.GraphicsLayoutWidget(show=False)
    widget.resize(1000, 700)
    primary = widget.addPlot(row=0, col=0, title="Y1 / Y2 and overlays")
    detail = widget.addPlot(row=1, col=0, title="Split detail / gradient")
    detail.setXLink(primary)

    primary_curve = primary.plot(x_values, traces[0], pen=pg.mkPen("#2563eb", width=1))
    features["primary_trace"] = _entry(
        "supported", "PlotDataItem created with the complete source arrays"
    )

    secondary_view = pg.ViewBox()
    primary.showAxis("right")
    primary.scene().addItem(secondary_view)
    primary.getAxis("right").linkToView(secondary_view)
    secondary_view.setXLink(primary)
    secondary_curve = pg.PlotCurveItem(
        x_values, traces[1], pen=pg.mkPen("#dc2626", width=1)
    )
    secondary_view.addItem(secondary_curve)
    secondary_view.setYRange(-0.5, 2.5, padding=0.0)
    features["secondary_y_axis"] = _entry(
        "supported", "linked auxiliary ViewBox and right AxisItem created"
    )

    detail.plot(x_values, traces[2], pen=pg.mkPen("#7c3aed", width=1))
    features["split_panels"] = _entry(
        "supported", "two PlotItems share the primary X range"
    )

    gradient_x = np.asarray([0.0, 5.0, 15.0, 25.0, 30.0])
    gradient_y = np.asarray([5.0, 5.0, 90.0, 90.0, 5.0])
    gradient_curve = pg.PlotCurveItem(
        gradient_x, gradient_y, pen=pg.mkPen("#111827", width=1)
    )
    gradient_axis = pg.AxisItem("right")
    gradient_axis.setLabel("B", units="%")
    primary.layout.addItem(gradient_axis, 2, 3)
    gradient_view = pg.ViewBox()
    primary.scene().addItem(gradient_view)
    gradient_axis.linkToView(gradient_view)
    gradient_view.setXLink(primary)
    gradient_view.addItem(gradient_curve)
    gradient_view.setYRange(0.0, 100.0, padding=0.0)

    def sync_auxiliary_views():
        geometry = primary.vb.sceneBoundingRect()
        for view in (secondary_view, gradient_view):
            view.setGeometry(geometry)
            view.linkedViewChanged(primary.vb, view.XAxis)

    sync_auxiliary_views()
    primary.vb.sigResized.connect(sync_auxiliary_views)

    primary.setXRange(4.0, 12.0, padding=0.0)
    primary.setYRange(-0.1, 1.5, padding=0.0)
    x_range, y_range = primary.viewRange()
    application.processEvents()
    three_axis_ok = three_axis_ranges_are_independent(
        primary.viewRange(), secondary_view.viewRange(), gradient_view.viewRange()
    )
    features["gradient_axis"] = _entry(
        "supported" if three_axis_ok else "blocked",
        "custom right AxisItem and ViewBox share X while Y2 and B% retain independent ranges",
    )
    ranges_ok = abs(x_range[0] - 4.0) < 0.01 and abs(x_range[1] - 12.0) < 0.01
    features["zoom_pan_range"] = _entry(
        "supported" if ranges_ok else "blocked",
        "programmatic ViewBox X/Y ranges applied and read back",
    )

    region = pg.LinearRegionItem(values=(6.0, 7.5), movable=True)
    primary.addItem(region)
    features["integration_region"] = _entry(
        "supported", "movable LinearRegionItem created"
    )

    retention = pg.TextItem(text="7.10", color="#111827", anchor=(0.5, 1.0))
    retention.setPos(7.1, 1.0)
    primary.addItem(retention)
    features["retention_label"] = _entry(
        "supported", "data-positioned TextItem created"
    )
    features["fixed_size_text"] = _entry(
        "supported", "TextItem uses screen-sized text while its position stays in data coordinates"
    )

    marker = pg.InfiniteLine(pos=8.0, angle=90, movable=True, pen=pg.mkPen("#7c3aed"))
    primary.addItem(marker)
    features["vertical_marker"] = _entry(
        "supported", "movable vertical InfiniteLine created"
    )

    clickable = False
    curve_object = getattr(primary_curve, "curve", None)
    if curve_object is not None and hasattr(curve_object, "setClickable"):
        curve_object.setClickable(True, width=8)
        clickable = hasattr(curve_object, "sigClicked")
    features["curve_picking"] = _entry(
        "supported" if clickable else "adaptable",
        "PlotCurveItem click signal is available" if clickable else "custom hit testing required",
    )

    widget.show()
    application.processEvents()
    pixmap = widget.grab()
    snapshot_ok = not pixmap.isNull() and pixmap.width() > 0 and pixmap.height() > 0
    features["snapshot"] = _entry(
        "supported" if snapshot_ok else "blocked",
        "Qt widget snapshot returned %dx%d" % (pixmap.width(), pixmap.height()),
    )
    widget.close()

    digest_after = arrays_digest(x_values, traces)
    if digest_after != digest_before:
        raise RuntimeError("Parity probe mutated source arrays")
    blocked = [
        name for name, value in features.items() if value["status"] == "blocked"
    ]
    return {
        "schema_version": 1,
        "backend_id": "pyqtgraph",
        "backend_version": getattr(pg, "__version__", "unknown"),
        "status": "completed" if not blocked else "blocked",
        "reason": "" if not blocked else "blocked features: %s" % ", ".join(blocked),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "numpy": np.__version__,
        },
        "raw_data_sha256": digest_before,
        "features": features,
    }
