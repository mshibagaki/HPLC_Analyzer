"""Optional PyQtGraph consumer for the backend-neutral production scene."""

from __future__ import annotations

import importlib

from .screen_events import ScreenPointerEvent
from .screen_navigation import ScreenOverviewState, ScreenViewState


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
        self.overview = self.widget.addPlot(row=0, col=0)
        self.overview.setMaximumHeight(140)
        self.overview.setMouseEnabled(x=False, y=False)
        self.overview.hideAxis("left")
        self.overview.hideAxis("bottom")
        self.overview.setTitle("Overview", color="#4b5563", size="8pt")
        self.overview_region = self.pg.LinearRegionItem(
            values=(0.0, 1.0),
            movable=False,
            brush=self._brush("#2563eb", 0.14),
            pen=self.pg.mkPen("#1d4ed8", width=0.8),
        )
        self.overview.addItem(self.overview_region)
        self.overview.setVisible(False)
        self.primary = self.widget.addPlot(row=1, col=0)
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
        self.overview_items = []
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

    @staticmethod
    def _button_value(button):
        return getattr(button, "value", button)

    @staticmethod
    def _point_in_rect(scene_position, rectangle, padding=0.0):
        x_value = float(scene_position.x())
        y_value = float(scene_position.y())
        return (
            float(rectangle.left()) - padding
            <= x_value
            <= float(rectangle.right()) + padding
            and float(rectangle.top()) - padding
            <= y_value
            <= float(rectangle.bottom()) + padding
        )

    def _pointer_region(self, scene_position):
        if self.overview.isVisible() and self._point_in_rect(
            scene_position, self.overview.vb.sceneBoundingRect()
        ):
            return "overview_y1", "x"

        axis_regions = (
            (self.primary.getAxis("bottom"), "y1", "x"),
            (self.primary.getAxis("left"), "y1", "y1"),
            (self.primary.getAxis("right"), "y2", "y2"),
            (self.gradient_axis, "gradient", "gradient"),
        )
        for axis, role, region in axis_regions:
            if self._point_in_rect(scene_position, axis.sceneBoundingRect(), 2.0):
                return role, region
        if self._point_in_rect(
            scene_position, self.primary.vb.sceneBoundingRect()
        ):
            return "y1", "plot"
        return "outside", ""

    def pointer_event(
        self,
        scene_position,
        button=None,
        double_click=False,
        key="",
    ):
        """Map a Qt scene position into the shared pointer-event contract."""
        axis_role, hit_region = self._pointer_region(scene_position)
        widget_position = self.widget.mapFromScene(scene_position)
        coordinates = []
        views = (
            ("y1", self.primary.vb),
            ("y2", self.secondary),
            ("gradient", self.gradient),
        )
        if self.overview.isVisible():
            views += (("overview_y1", self.overview.vb),)
        for role, view in views:
            point = view.mapSceneToView(scene_position)
            coordinates.append((role, float(point.x()), float(point.y())))
        return ScreenPointerEvent(
            button=self._button_value(button),
            axis_role=axis_role,
            hit_region=hit_region,
            canvas_x=float(widget_position.x()),
            canvas_y=float(widget_position.y()),
            data_coordinates=tuple(coordinates),
            double_click=bool(double_click),
            key=str(key or ""),
        )

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
            overview_item = self.pg.PlotCurveItem(
                trace.x_values,
                trace.y_values,
                pen=self.pg.mkPen(trace.color, width=trace.line_width),
            )
            self.overview.addItem(overview_item)
            self.overview_items.append(overview_item)
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
        self.overview.enableAutoRange()
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

    @staticmethod
    def _range_tuple(view_range):
        return tuple(float(value) for value in view_range)

    def apply_view_state(
        self,
        view_state: ScreenViewState,
        overview_state: ScreenOverviewState,
    ):
        """Apply backend-neutral navigation state to the optional renderer."""
        self.primary.setXRange(*view_state.x, padding=0.0)
        self.primary.setYRange(*view_state.y1, padding=0.0)
        if view_state.y2 is not None:
            self.secondary.setYRange(*view_state.y2, padding=0.0)
        if view_state.gradient is not None:
            self.gradient.setYRange(*view_state.gradient, padding=0.0)

        self.overview.setVisible(overview_state.enabled)
        self.overview_region.setVisible(overview_state.enabled)
        if overview_state.enabled:
            self.overview.setXRange(*overview_state.full_x, padding=0.0)
            self.overview_region.setRegion(overview_state.detail_x)

        self._sync_auxiliary_views()
        self.application.processEvents()
        primary_range = self.primary.viewRange()
        secondary_range = self.secondary.viewRange()
        gradient_range = self.gradient.viewRange()
        overview_range = self.overview.viewRange()
        evidence = {
            "x": self._range_tuple(primary_range[0]),
            "y1": self._range_tuple(primary_range[1]),
            "y2": self._range_tuple(secondary_range[1]),
            "gradient": self._range_tuple(gradient_range[1]),
            "overview_enabled": self.overview.isVisible(),
            "overview_full_x": self._range_tuple(overview_range[0]),
            "overview_detail_x": self._range_tuple(
                self.overview_region.getRegion()
            ),
        }
        self.last_evidence["view_state"] = evidence
        return dict(evidence)

    def snapshot(self):
        self.widget.show()
        self.application.processEvents()
        return self.widget.grab()

    def close(self):
        self.widget.close()
