"""Optional PyQtGraph consumer for the backend-neutral production scene."""

from __future__ import annotations

import importlib


class OptionalRendererUnavailable(RuntimeError):
    pass


def pyqtgraph_scene_available() -> bool:
    try:
        importlib.import_module("pyqtgraph")
        importlib.import_module("pyqtgraph.Qt.QtWidgets")
    except ImportError:
        return False
    return True


class PyQtGraphSceneConsumer:
    """Translate a production scene into optional PyQtGraph graphics items."""

    def __init__(self, size=(1000, 700)):
        try:
            self.pg = importlib.import_module("pyqtgraph")
            qt_widgets = importlib.import_module("pyqtgraph.Qt.QtWidgets")
            self.qt_gui = importlib.import_module("pyqtgraph.Qt.QtGui")
            self.qt_core = importlib.import_module("pyqtgraph.Qt.QtCore")
        except ImportError as exc:
            raise OptionalRendererUnavailable(
                "PyQtGraph is not installed in this environment"
            ) from exc
        self.application = qt_widgets.QApplication.instance()
        if self.application is None:
            self.application = qt_widgets.QApplication([])
        self.widget = self.pg.GraphicsLayoutWidget(show=False)
        self.widget.resize(int(size[0]), int(size[1]))
        self.primary = self.widget.addPlot(row=0, col=0)
        self.secondary = self.pg.ViewBox()
        self.gradient = self.pg.ViewBox()
        self.primary.showAxis("right")
        self.primary.scene().addItem(self.secondary)
        self.primary.getAxis("right").linkToView(self.secondary)
        self.secondary.setXLink(self.primary)
        self.gradient_axis = self.pg.AxisItem("right")
        self.primary.layout.addItem(self.gradient_axis, 2, 3)
        self.primary.scene().addItem(self.gradient)
        self.gradient_axis.linkToView(self.gradient)
        self.gradient.setXLink(self.primary)
        self.gradient.setYRange(0.0, 100.0, padding=0.0)
        self.primary.vb.sigResized.connect(self._sync_auxiliary_views)
        self._sync_auxiliary_views()
        self.items = []
        self.last_evidence = {}

    def _brush(self, color, alpha):
        value = self.pg.mkColor(color)
        value.setAlphaF(float(alpha))
        return self.pg.mkBrush(value)

    def _sync_auxiliary_views(self):
        geometry = self.primary.vb.sceneBoundingRect()
        for view in (self.secondary, self.gradient):
            view.setGeometry(geometry)
            view.linkedViewChanged(self.primary.vb, view.XAxis)

    def _view(self, axis_id):
        return self.secondary if axis_id == "y2" else self.primary

    def _add(self, item, axis_id="y1"):
        self._view(axis_id).addItem(item)
        self.items.append(item)
        return item

    def render(self, scene):
        counts = {
            "traces": 0,
            "gradients": 0,
            "peak_overlays": 0,
            "vertical_markers": 0,
            "fraction_regions": 0,
            "text_annotations": 0,
        }
        for trace in scene.traces:
            item = self.pg.PlotCurveItem(
                trace.x_values,
                trace.y_values,
                pen=self.pg.mkPen(trace.color, width=trace.line_width),
                name=trace.label,
            )
            self._add(item, trace.axis_id)
            counts["traces"] += 1

        if scene.gradient is not None:
            item = self.pg.PlotCurveItem(
                scene.gradient.x_values,
                scene.gradient.y_values,
                pen=self.pg.mkPen(
                    "#111827",
                    width=1.3,
                    style=getattr(
                        getattr(self.qt_core.Qt, "PenStyle", self.qt_core.Qt),
                        "DotLine",
                    ),
                ),
            )
            self.gradient.addItem(item)
            self.gradient_axis.setLabel(scene.gradient.axis_label)
            self.items.append(item)
            counts["gradients"] += 1

        for overlay in scene.peak_overlays:
            view = self._view(overlay.axis_id)
            if overlay.show_integration_area:
                region = self.pg.LinearRegionItem(
                    values=(overlay.start_x, overlay.end_x),
                    movable=False,
                    brush=self._brush(overlay.color, 0.12),
                    pen=self.pg.mkPen(overlay.color, width=0.8),
                )
                view.addItem(region)
                self.items.append(region)
                for value in (overlay.start_x, overlay.end_x):
                    self._add(
                        self.pg.InfiniteLine(
                            pos=value,
                            angle=90,
                            movable=False,
                            pen=self.pg.mkPen("#9ca3af", width=0.8),
                        ),
                        overlay.axis_id,
                    )
                if overlay.retention_x is not None:
                    self._add(
                        self.pg.InfiniteLine(
                            pos=overlay.retention_x,
                            angle=90,
                            movable=False,
                            pen=self.pg.mkPen(overlay.color, width=0.8),
                        ),
                        overlay.axis_id,
                    )
                if overlay.baseline_x is not None:
                    self._add(
                        self.pg.PlotCurveItem(
                            overlay.baseline_x,
                            overlay.baseline_y,
                            pen=self.pg.mkPen(overlay.color, width=0.9),
                        ),
                        overlay.axis_id,
                    )
            if overlay.fit_x is not None:
                self._add(
                    self.pg.PlotCurveItem(
                        overlay.fit_x,
                        overlay.fit_y,
                        pen=self.pg.mkPen("#c026d3", width=1.2),
                    ),
                    overlay.axis_id,
                )
            if overlay.label_x is not None:
                text = self.pg.TextItem(
                    text=overlay.label_text,
                    color=overlay.label_color,
                    anchor=(0.5, 1.0),
                )
                font = self.qt_gui.QFont(overlay.label_font_family)
                font.setPointSizeF(overlay.label_font_size)
                text.setFont(font)
                text.setPos(overlay.label_x, overlay.label_y)
                self._add(text, overlay.axis_id)
            counts["peak_overlays"] += 1

        for marker in scene.vertical_markers:
            self._add(
                self.pg.InfiniteLine(
                    pos=marker.x_value,
                    angle=90,
                    movable=False,
                    pen=self.pg.mkPen(marker.color, width=marker.line_width),
                ),
                marker.axis_id,
            )
            counts["vertical_markers"] += 1

        for region in scene.fraction_regions:
            item = self.pg.LinearRegionItem(
                values=(region.start_x, region.end_x),
                movable=False,
                brush=self._brush(region.fill_color, region.fill_alpha),
                pen=self.pg.mkPen(region.line_color, width=region.line_width),
            )
            self.primary.addItem(item)
            self.items.append(item)
            for value in region.boundary_values + (region.end_x,):
                self._add(
                    self.pg.InfiniteLine(
                        pos=value,
                        angle=90,
                        movable=False,
                        pen=self.pg.mkPen(region.line_color, width=region.line_width),
                    )
                )
            counts["fraction_regions"] += 1

        for annotation in scene.text_annotations:
            text = self.pg.TextItem(
                text=annotation.text,
                color=annotation.color,
                fill=self.pg.mkBrush(annotation.background_color),
                border=self.pg.mkPen(annotation.border_color, width=0.8),
                anchor=(0.0, 1.0),
            )
            font = self.qt_gui.QFont(annotation.font_family)
            font.setPointSizeF(annotation.font_size)
            text.setFont(font)
            text.setPos(annotation.x_value, annotation.y_value)
            self._add(text, annotation.axis_id)
            counts["text_annotations"] += 1

        self.primary.enableAutoRange()
        self.secondary.enableAutoRange()
        self.gradient.setYRange(0.0, 100.0, padding=0.0)
        self._sync_auxiliary_views()
        self.application.processEvents()
        primary_range = self.primary.viewRange()
        secondary_range = self.secondary.viewRange()
        gradient_range = self.gradient.viewRange()
        self.last_evidence = {
            "counts": counts,
            "shared_x": all(
                abs(current[index] - primary_range[0][index]) < 0.01
                for current in (secondary_range[0], gradient_range[0])
                for index in (0, 1)
            ),
            "gradient_range": tuple(gradient_range[1]),
        }
        return dict(self.last_evidence)

    def snapshot(self):
        self.widget.show()
        self.application.processEvents()
        return self.widget.grab()

    def close(self):
        self.widget.close()
