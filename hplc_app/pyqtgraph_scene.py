"""Optional PyQtGraph consumer for the backend-neutral production scene."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
import importlib
import weakref

import numpy as np

from .models import normalize_line_style
from .rendering import minmax_decimate, screen_point_budget
from .screen_events import ScreenPointerEvent
from .screen_navigation import (
    ScreenOverviewState, ScreenViewState, compose_overview_state,
)


class OptionalRendererUnavailable(RuntimeError):
    pass


def pyqtgraph_scene_available() -> bool:
    try:
        importlib.import_module("pyqtgraph")
        importlib.import_module("pyqtgraph.Qt.QtWidgets")
    except Exception:
        return False
    return True


class PyQtGraphSceneConsumer:
    """Translate a production scene into optional PyQtGraph graphics items."""

    POINTER_EVENTS = frozenset((
        "button_press_event", "motion_notify_event",
        "button_release_event", "scroll_event",
    ))
    # Reserved outer margin (px) so the navigation scrollbars have somewhere
    # to sit without overlapping the chromatogram (Issue #307/27.2).
    SCROLLBAR_MARGIN = 20
    # PyQtGraph's own default GraphicsLayout inter-row spacing, restored
    # around the overview/handle rows whenever they are visible again
    # (Issue #307/27.4).
    _ROW_SPACING = 6.0

    def __init__(self, size=(1000, 700), split_y_axes=False):
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
        self.qt_widgets = qt_widgets
        if self.application is None:
            self.application = qt_widgets.QApplication([])
        self.widget = self.pg.GraphicsLayoutWidget(show=False)
        self.widget.resize(int(size[0]), int(size[1]))
        qt_core = self.qt_core
        qt_gui = self.qt_gui

        class SplitHandle(self.pg.GraphicsWidget):
            def __init__(self):
                super().__init__()
                self.setMinimumHeight(8.0)
                self.setMaximumHeight(8.0)
                cursor = getattr(
                    getattr(qt_core.Qt, "CursorShape", qt_core.Qt),
                    "SplitVCursor",
                )
                self.setCursor(cursor)

            def paint(self, painter, *_args):
                painter.fillRect(self.boundingRect(), qt_gui.QColor("#d1d5db"))

        self.overview = self.widget.addPlot(row=0, col=0)
        self.overview.setMouseEnabled(x=False, y=False)
        self.overview.hideAxis("left")
        # Issue #307/27.3: show the X ticks/numbers (helps orient the overview
        # window) but not an axis label/title text.
        self.overview.showAxis("bottom")
        self.overview.getAxis("bottom").setLabel(None)
        self.overview.setTitle("Overview", color="#4b5563", size="8pt")
        self.overview.vb.setBorder(self.pg.mkPen("#6b7280", width=0.8))
        self.overview_region = qt_widgets.QGraphicsRectItem()
        self.overview_region.setPen(self.pg.mkPen("#1d4ed8", width=0.8))
        self.overview_region.setBrush(self._brush("#2563eb", 0.14))
        self.overview_region.setZValue(10.0)
        self.overview.vb.addItem(self.overview_region, ignoreBounds=True)
        self.overview_secondary_region = qt_widgets.QGraphicsRectItem()
        self.overview_secondary_region.setPen(
            self.pg.mkPen("#ca8a04", width=0.8)
        )
        self.overview_secondary_region.setBrush(self._brush("#eab308", 0.12))
        self.overview_secondary_region.setZValue(11.0)
        self.overview.setVisible(False)
        self.overview_secondary = self.pg.ViewBox()
        self.overview.scene().addItem(self.overview_secondary)
        self.overview_secondary.setXLink(self.overview)
        self.overview_secondary.setMouseEnabled(x=False, y=False)
        self.overview_secondary.addItem(
            self.overview_secondary_region, ignoreBounds=True
        )
        self.overview_secondary.setVisible(False)
        self.overview.vb.sigResized.connect(self._sync_overview_view)
        self.overview_handle = SplitHandle()
        self.widget.ci.addItem(self.overview_handle, row=1, col=0)
        self.primary = self.widget.addPlot(row=2, col=0)
        self.split_y_axes = bool(split_y_axes)
        self.secondary_plot = None
        self.split_handle = None
        self.split_ratio = 0.5
        self.overview_ratio = 0.25
        self.overview_ratio_changed = None
        self.overview_action_handler = None
        self._split_drag_active = False
        self._overview_drag_active = False
        if self.split_y_axes:
            self.split_handle = SplitHandle()
            self.widget.ci.addItem(self.split_handle, row=3, col=0)
            self.secondary_plot = self.widget.addPlot(row=4, col=0)
            self.secondary = self.secondary_plot.vb
            self.primary.getAxis("bottom").setStyle(showValues=False)
            self.set_split_ratio(self.split_ratio)
        else:
            self.secondary = self.pg.ViewBox()
            self.primary.showAxis("right")
            self.primary.scene().addItem(self.secondary)
            self.primary.getAxis("right").linkToView(self.secondary)
        self.gradient = self.pg.ViewBox()
        self.secondary.setXLink(self.primary)
        self.gradient_axis = self.pg.AxisItem("right")
        self.primary.layout.addItem(self.gradient_axis, 2, 3)
        self._separate_gradient_axis_column(self.primary)
        self.primary.scene().addItem(self.gradient)
        self.gradient_axis.linkToView(self.gradient)
        self.gradient.setXLink(self.primary)
        self.gradient.setYRange(0.0, 100.0, padding=0.0)
        self.gradient_layers = [(self.gradient, self.gradient_axis, self.primary)]
        if self.split_y_axes:
            gradient_secondary = self.pg.ViewBox()
            gradient_axis_secondary = self.pg.AxisItem("right")
            self.secondary_plot.layout.addItem(gradient_axis_secondary, 2, 3)
            self._separate_gradient_axis_column(self.secondary_plot)
            self.secondary_plot.scene().addItem(gradient_secondary)
            gradient_axis_secondary.linkToView(gradient_secondary)
            gradient_secondary.setXLink(self.primary)
            gradient_secondary.setYLink(self.gradient)
            self.gradient_layers.append((gradient_secondary, gradient_axis_secondary, self.secondary_plot))
        self.primary.vb.sigResized.connect(self._sync_auxiliary_views)
        if self.split_y_axes:
            self.secondary.sigResized.connect(self._sync_auxiliary_views)
        self._sync_auxiliary_views()
        self.items = []
        self.overview_items = []
        self.trace_items = {}
        self.overview_trace_items = {}
        self.fit_items = {}
        self.gradient_items = []
        self.peak_overlay_items = {}
        self.marker_items = {}
        self._marker_specs = ()
        self.annotation_items = {}
        self._annotation_specs = ()
        self.pointer_cursor = self.pg.InfiniteLine(
            angle=90, movable=False,
            pen=self.pg.mkPen("#2563eb", width=1.0, style=getattr(
                getattr(self.qt_core.Qt, "PenStyle", self.qt_core.Qt), "DashLine"
            )),
        )
        self.pointer_cursor.setZValue(30)
        self.primary.addItem(self.pointer_cursor, ignoreBounds=True)
        self._cursor_view = self.primary.vb
        self.pointer_cursor.hide()
        self.span_selection = self.pg.LinearRegionItem(
            values=(0.0, 0.0), movable=False,
            brush=self._brush("#06b6d4", 0.25),
            pen=self.pg.mkPen("#0891b2", width=1.0),
        )
        self.span_selection.setZValue(25)
        self.primary.addItem(self.span_selection, ignoreBounds=True)
        self._span_view = self.primary.vb
        self.span_selection.hide()
        self.zoom_rectangle = qt_widgets.QGraphicsRectItem()
        self.zoom_rectangle.setPen(self.pg.mkPen("#2563eb", width=1.0))
        self.zoom_rectangle.setBrush(self._brush("#2563eb", 0.12))
        self.primary.vb.addItem(self.zoom_rectangle, ignoreBounds=True)
        self._zoom_view = self.primary.vb
        self.zoom_rectangle.hide()
        self.last_evidence = {}
        self._rendered_scene = None
        self._connections = {}
        self._next_connection_id = 1
        self._closed = False
        self._pointer_handler = None
        self.overview_state = ScreenOverviewState(False, (0.0, 1.0), (0.0, 1.0))
        self._overview_scroll_sync = False
        self.scroll_bounds_provider = None
        orientations = getattr(self.qt_core.Qt, "Orientation", self.qt_core.Qt)
        horizontal = getattr(orientations, "Horizontal")
        vertical = getattr(orientations, "Vertical")
        self.navigation_scrollbars = {}
        for name, orientation in (
            ("overview_x", horizontal),
            ("overview_y", vertical),
            ("detail_x", horizontal),
            ("detail_y", vertical),
        ):
            scrollbar = qt_widgets.QScrollBar(
                orientation, self.widget.viewport()
            )
            scrollbar.setToolTip("Move visible range")
            if orientation == vertical:
                scrollbar.setInvertedAppearance(True)
            scrollbar.valueChanged.connect(
                lambda value, target=name: self._scrollbar_changed(
                    target, value
                )
            )
            self.navigation_scrollbars[name] = scrollbar
        # Preserve the public name used by the existing overview tests.
        self.overview_scrollbar = self.navigation_scrollbars["overview_x"]
        self.overview_y_scrollbar = self.navigation_scrollbars["overview_y"]
        self.detail_x_scrollbar = self.navigation_scrollbars["detail_x"]
        self.detail_y_scrollbar = self.navigation_scrollbars["detail_y"]
        self.overview_buttons = []
        for text, command, tooltip in (
            ("+", "zoom_in", "Zoom overview in"),
            ("−", "zoom_out", "Zoom overview out"),
            ("⌂", "home", "Reset overview"),
        ):
            button = qt_widgets.QToolButton(self.widget.viewport())
            button.setText(text)
            button.setToolTip(tooltip)
            button.clicked.connect(
                lambda _checked=False, value=command: self._overview_action(value)
            )
            self.overview_buttons.append(button)
        self._set_overview_controls_visible(False)
        self._set_detail_controls_visible(False)
        self._install_pointer_filter()
        if not self.split_y_axes:
            self.set_overview_ratio(self.overview_ratio)

    # A right-oriented AxisItem draws its rotated title 5 px past its own right
    # edge, so with no spacing the Y2 title lands on top of the B% axis line.
    GRADIENT_AXIS_COLUMN_SPACING = 12.0

    def _separate_gradient_axis_column(self, plot):
        """Keep the second Y-axis title clear of the B% axis beside it."""
        plot.layout.setColumnSpacing(2, self.GRADIENT_AXIS_COLUMN_SPACING)

    def set_split_ratio(self, ratio):
        """Resize the two split panels without rebuilding their scene items."""

        if not self.split_y_axes:
            return
        self.split_ratio = min(0.85, max(0.15, float(ratio)))
        self._apply_plot_row_stretches()
        layout = self.widget.ci.layout
        layout.invalidate()
        layout.activate()
        if hasattr(self, "gradient_layers"):
            self._sync_auxiliary_views()
        self.widget.update()

    def set_overview_ratio(self, ratio):
        """Resize overview/detail without persisting the session-only ratio."""

        self.overview_ratio = min(0.85, max(0.15, float(ratio)))
        self._apply_plot_row_stretches()
        layout = self.widget.ci.layout
        layout.invalidate()
        layout.activate()
        self.widget.update()
        if callable(self.overview_ratio_changed):
            self.overview_ratio_changed(self.overview_ratio)

    def _apply_plot_row_stretches(self):
        """Allocate only visible plot rows while retaining session ratios."""

        enabled = bool(
            getattr(self, "overview_state", None)
            and self.overview_state.enabled
        )
        scale = 1000
        overview_share = self.overview_ratio if enabled else 0.0
        detail_share = 1.0 - overview_share
        layout = self.widget.ci.layout
        layout.setRowStretchFactor(0, int(round(overview_share * scale)))
        layout.setRowStretchFactor(1, 0)
        if self.split_y_axes:
            layout.setRowStretchFactor(
                2, int(round(detail_share * self.split_ratio * scale))
            )
            layout.setRowStretchFactor(3, 0)
            layout.setRowStretchFactor(
                4,
                int(round(detail_share * (1.0 - self.split_ratio) * scale)),
            )
        else:
            layout.setRowStretchFactor(2, int(round(detail_share * scale)))

        handle_height = 8.0 if enabled else 0.0
        self.overview_handle.setMinimumHeight(handle_height)
        self.overview_handle.setMaximumHeight(handle_height)

        # Issue #307/27.4: a stretch factor of 0 alone still leaves the
        # overview row 0 and its handle row 1 reserving their own preferred
        # size -- the overview PlotItem itself measures ~100 px even hidden,
        # and the layout keeps its default 6 px inter-row spacing on both
        # sides of a hidden row. Neither shrank with `setVisible(False)`,
        # which is what left single/split modes with a blank strip on top.
        # Clamp the PlotItem's own height and null out that spacing whenever
        # the overview is disabled; restore both when it is re-enabled.
        default_max = 16777215.0
        self.overview.setMinimumHeight(0.0)
        self.overview.setMaximumHeight(default_max if enabled else 0.0)
        layout.setRowSpacing(0, self._ROW_SPACING if enabled else 0.0)
        layout.setRowSpacing(1, self._ROW_SPACING if enabled else 0.0)
        # Reserve room for the scrollbars only where the enabled rows
        # actually need it (Issue #307/27.2): the right/bottom outer margin
        # always fits the detail scrollbars, and the overview's own visible
        # X axis (27.3) means it needs no extra top margin of its own.
        layout.setContentsMargins(0, 0, self.SCROLLBAR_MARGIN, self.SCROLLBAR_MARGIN)

    def _overview_action(self, command, value=None):
        if callable(self.overview_action_handler):
            self.overview_action_handler(command, value)

    def set_overview_tooltips(self, scrollbar, zoom_in, zoom_out, home):
        for control in self.navigation_scrollbars.values():
            control.setToolTip(scrollbar)
        for button, tooltip in zip(
            self.overview_buttons, (zoom_in, zoom_out, home)
        ):
            button.setToolTip(tooltip)

    def _set_overview_controls_visible(self, visible):
        self.overview_scrollbar.setVisible(bool(visible))
        self.overview_y_scrollbar.setVisible(bool(visible))
        for button in self.overview_buttons:
            button.setVisible(bool(visible))

    def _set_detail_controls_visible(self, visible):
        self.detail_x_scrollbar.setVisible(bool(visible))
        self.detail_y_scrollbar.setVisible(bool(visible))

    def _layout_overview_controls(self):
        if not getattr(self, "overview_state", None) or not self.overview_state.enabled:
            self._layout_detail_scrollbars()
            return
        # Buttons stay anchored to the plotted (ViewBox) area, unchanged.
        button_rectangle = self.overview.vb.sceneBoundingRect()
        button_top_left = self.widget.mapFromScene(button_rectangle.topLeft())
        button_bottom_right = self.widget.mapFromScene(button_rectangle.bottomRight())
        left, right = int(button_top_left.x()), int(button_bottom_right.x())
        bottom = int(button_bottom_right.y())
        size, gap, margin = 22, 2, 4
        buttons_width = len(self.overview_buttons) * size + 2 * gap
        button_left = max(left + margin, right - margin - buttons_width)
        for index, button in enumerate(self.overview_buttons):
            button.setGeometry(
                button_left + index * (size + gap), bottom - margin - size,
                size, size,
            )
            button.raise_()
        # Issue #307/27.2: the scrollbars themselves move into the reserved
        # outer margin, anchored to the whole PlotItem (including the now
        # visible bottom axis) so they never sit on top of plotted content.
        plot_rectangle = self.overview.sceneBoundingRect()
        plot_top_left = self.widget.mapFromScene(plot_rectangle.topLeft())
        plot_bottom_right = self.widget.mapFromScene(plot_rectangle.bottomRight())
        vertical_width = 18
        horizontal_height = 18
        self.overview_scrollbar.setGeometry(
            int(plot_top_left.x()),
            int(plot_bottom_right.y()) + 1,
            max(0, int(plot_bottom_right.x() - plot_top_left.x())),
            horizontal_height,
        )
        self.overview_scrollbar.raise_()
        self.overview_y_scrollbar.setGeometry(
            int(plot_bottom_right.x()) + 1,
            int(plot_top_left.y()),
            vertical_width,
            max(0, int(plot_bottom_right.y() - plot_top_left.y())),
        )
        self.overview_y_scrollbar.raise_()

        self._layout_detail_scrollbars()

    def _layout_detail_scrollbars(self):
        if not hasattr(self, "detail_x_scrollbar"):
            return
        # Issue #307/27.2: anchor to the whole PlotItem (ticks, numbers and
        # axis label included) and place the scrollbars in the reserved
        # outer margin just past it, so the bottom one sits outside the X
        # axis label and neither overlaps the chromatogram.
        detail_rectangle = self.primary.sceneBoundingRect()
        detail_top_left = self.widget.mapFromScene(detail_rectangle.topLeft())
        detail_bottom_right = self.widget.mapFromScene(
            detail_rectangle.bottomRight()
        )
        detail_left = int(detail_top_left.x())
        detail_right = int(detail_bottom_right.x())
        detail_top = int(detail_top_left.y())
        detail_bottom = int(detail_bottom_right.y())
        vertical_width = 18
        self.detail_x_scrollbar.setGeometry(
            detail_left,
            detail_bottom + 1,
            max(0, detail_right - detail_left),
            18,
        )
        self.detail_y_scrollbar.setGeometry(
            detail_right + 1,
            detail_top,
            vertical_width,
            max(0, detail_bottom - detail_top),
        )
        self.detail_x_scrollbar.raise_()
        self.detail_y_scrollbar.raise_()

    def _scrollbar_window_and_bounds(self, name):
        windows = {
            "overview_x": self._range_tuple(self.overview.viewRange()[0]),
            "overview_y": self._range_tuple(self.overview.viewRange()[1]),
            "detail_x": self._range_tuple(self.primary.viewRange()[0]),
            "detail_y": self._range_tuple(self.primary.viewRange()[1]),
        }
        window = windows[name]
        bounds = (
            self.scroll_bounds_provider(name)
            if callable(self.scroll_bounds_provider) else window
        )
        return window, bounds

    def _sync_scrollbar(self, name):
        (full_left, full_right), bounds = self._scrollbar_window_and_bounds(name)
        if bounds is None:
            self.navigation_scrollbars[name].setVisible(False)
            return
        data_left, data_right = bounds
        data_left, data_right = sorted((float(data_left), float(data_right)))
        data_span = data_right - data_left
        full_span = min(full_right - full_left, data_span)
        scale = 10000
        page = scale if data_span <= 0 else max(
            1, int(round(scale * full_span / data_span))
        )
        maximum = max(0, scale - page)
        movable = max(data_span - full_span, 0.0)
        value = 0 if movable <= 0 else int(round(
            maximum * (full_left - data_left) / movable
        ))
        self._overview_scroll_sync = True
        try:
            scrollbar = self.navigation_scrollbars[name]
            scrollbar.setRange(0, maximum)
            scrollbar.setPageStep(page)
            scrollbar.setValue(min(max(value, 0), maximum))
        finally:
            self._overview_scroll_sync = False

    def sync_navigation_scrollbars(self):
        for name in self.navigation_scrollbars:
            self._sync_scrollbar(name)

    def _scrollbar_changed(self, name, value):
        if self._overview_scroll_sync:
            return
        if name.startswith("overview_") and not self.overview_state.enabled:
            return
        (full_left, full_right), bounds = self._scrollbar_window_and_bounds(name)
        if bounds is None:
            return
        data_left, data_right = bounds
        data_left, data_right = sorted((float(data_left), float(data_right)))
        span = full_right - full_left
        maximum = self.navigation_scrollbars[name].maximum()
        movable = max((data_right - data_left) - span, 0.0)
        offset = 0.0 if maximum <= 0 else movable * float(value) / maximum
        self._overview_action(
            name, (data_left + offset, data_left + offset + span)
        )

    def _split_drag_event(self, event_name, event, scene_position):
        if event_name == "scroll_event":
            return False
        buttons = getattr(self.qt_core.Qt, "MouseButton", self.qt_core.Qt)
        if event_name == "button_press_event":
            if (
                self.overview.isVisible()
                and event.button() == buttons.LeftButton
                and self._point_in_rect(
                    scene_position, self.overview_handle.sceneBoundingRect(), 3.0
                )
            ):
                self._overview_drag_active = True
                return True
            if (
                self.split_handle is not None
                and event.button() == buttons.LeftButton
                and self._point_in_rect(
                    scene_position,
                    self.split_handle.sceneBoundingRect(),
                    3.0,
                )
            ):
                self._split_drag_active = True
                return True
            return False
        if self._overview_drag_active:
            if event_name == "motion_notify_event":
                top = float(self.overview.vb.sceneBoundingRect().top())
                bottom = float(self.primary.vb.sceneBoundingRect().bottom())
                if bottom > top:
                    self.set_overview_ratio(
                        (float(scene_position.y()) - top) / (bottom - top)
                    )
                return True
            if event_name == "button_release_event":
                self._overview_drag_active = False
                return True
            return False
        if not self._split_drag_active:
            return False
        if event_name == "motion_notify_event":
            top = float(self.primary.vb.sceneBoundingRect().top())
            bottom = float(
                self.secondary_plot.vb.sceneBoundingRect().bottom()
            )
            if bottom > top:
                self.set_split_ratio(
                    (float(scene_position.y()) - top) / (bottom - top)
                )
            return True
        if event_name == "button_release_event":
            self._split_drag_active = False
            return True
        return False

    def _install_pointer_filter(self):
        owner_ref = weakref.ref(self)

        class PointerFilter(self.qt_core.QObject):
            def eventFilter(self, watched, event):
                owner = owner_ref()
                if owner is not None:
                    return owner._dispatch_viewport_event(event)
                return False

        viewport = self.widget.viewport()
        self._pointer_filter = PointerFilter(viewport)
        viewport.setMouseTracking(True)
        viewport.installEventFilter(self._pointer_filter)

    def connect_event(self, event_name, callback):
        if self._closed:
            raise RuntimeError("Renderer is closed")
        if event_name not in self.POINTER_EVENTS:
            raise ValueError("Unsupported pointer event: %s" % event_name)
        if not callable(callback):
            raise TypeError("Pointer callback must be callable")
        connection_id = self._next_connection_id
        self._next_connection_id += 1
        self._connections[connection_id] = (event_name, callback)
        return connection_id

    def disconnect_event(self, connection_id):
        self._connections.pop(connection_id, None)

    def set_pointer_handler(self, handler):
        """Install one explicit owner; observers remain non-consuming."""
        if self._closed:
            raise RuntimeError("Renderer is closed")
        if handler is not None:
            if not callable(handler):
                raise TypeError("Pointer handler must be callable")
            if self._pointer_handler is not None and self._pointer_handler != handler:
                raise RuntimeError("A pointer handler is already installed")
        self._pointer_handler = handler

    def _dispatch_viewport_event(self, event):
        types = getattr(self.qt_core.QEvent, "Type", self.qt_core.QEvent)
        names = {
            types.MouseButtonPress: "button_press_event",
            types.MouseButtonDblClick: "button_press_event",
            types.MouseMove: "motion_notify_event",
            types.MouseButtonRelease: "button_release_event",
            types.Wheel: "scroll_event",
        }
        event_name = names.get(event.type())
        if event_name is None:
            return False
        position = event.position() if hasattr(event, "position") else event.pos()
        if hasattr(position, "toPoint"):
            position = position.toPoint()
        scene_position = self.widget.mapToScene(position)
        if self._split_drag_event(event_name, event, scene_position):
            return True
        listeners = tuple(
            (connection_id, callback)
            for connection_id, (name, callback) in self._connections.items()
            if name == event_name
        )
        if not listeners and self._pointer_handler is None:
            return False
        buttons = getattr(self.qt_core.Qt, "MouseButton", self.qt_core.Qt)
        if event_name == "scroll_event":
            delta = event.angleDelta().y() or event.pixelDelta().y()
            if not delta:
                if self._pointer_handler is None:
                    return False
                listeners = ()
            button = ("up" if delta > 0 else "down") if delta else None
        else:
            raw_button = (
                event.buttons() if event_name == "motion_notify_event"
                else event.button()
            )
            # Qt uses Left=1/Right=2/Middle=4; the shared contract uses 1/3/2.
            button = next((
                value for flag, value in (
                    (buttons.LeftButton, 1), (buttons.MiddleButton, 2),
                    (buttons.RightButton, 3),
                ) if raw_button & flag
            ), None)
        modifiers = getattr(self.qt_core.Qt, "KeyboardModifier", self.qt_core.Qt)
        key = "+".join(
            name for flag, name in (
                (modifiers.ControlModifier, "ctrl"),
                (modifiers.ShiftModifier, "shift"),
                (modifiers.AltModifier, "alt"),
                (modifiers.MetaModifier, "super"),
            ) if event.modifiers() & flag
        )
        normalized = self.pointer_event(
            scene_position, button=button,
            double_click=event.type() == types.MouseButtonDblClick, key=key,
        )
        consumed = (
            bool(self._pointer_handler(event_name, normalized))
            if self._pointer_handler is not None else False
        )
        for connection_id, callback in listeners:
            if connection_id in self._connections:
                callback(normalized)
        return consumed or self._closed

    def _brush(self, color, alpha):
        value = self.pg.mkColor(color)
        value.setAlphaF(float(alpha))
        return self.pg.mkBrush(value)

    def _pen(self, color, width, alpha=1.0):
        value = self.pg.mkColor(color)
        value.setAlphaF(float(alpha))
        return self.pg.mkPen(value, width=float(width))

    def _trace_pen(self, trace):
        style_name = {
            "solid": "SolidLine",
            "dashed": "DashLine",
            "dotted": "DotLine",
            "dash_dot": "DashDotLine",
        }[normalize_line_style(trace.line_style)]
        style_owner = getattr(self.qt_core.Qt, "PenStyle", self.qt_core.Qt)
        return self.pg.mkPen(
            trace.color,
            width=trace.line_width,
            style=getattr(style_owner, style_name),
        )

    def _sync_auxiliary_views(self):
        if not self.split_y_axes:
            self.secondary.setGeometry(self.primary.vb.sceneBoundingRect())
        self.secondary.linkedViewChanged(self.primary.vb, self.secondary.XAxis)
        primary_rectangle = self.primary.vb.sceneBoundingRect()
        for view, _axis, host in self.gradient_layers:
            host_rectangle = host.vb.sceneBoundingRect()
            view.setGeometry(self.qt_core.QRectF(
                primary_rectangle.left(), host_rectangle.top(),
                primary_rectangle.width(), host_rectangle.height(),
            ))
            view.linkedViewChanged(self.primary.vb, view.XAxis)
            self.primary.vb.blockLink(True)
            try:
                view.setXRange(*self.primary.viewRange()[0], padding=0.0)
            finally:
                self.primary.vb.blockLink(False)
        self._layout_detail_scrollbars()

    def pan_rectangle(self, target):
        view = self.secondary if target in ("y2", "plot_y2") else self.primary.vb
        return view.sceneBoundingRect()

    def _sync_overview_view(self):
        self.overview_secondary.setGeometry(self.overview.vb.sceneBoundingRect())
        self.overview_secondary.linkedViewChanged(self.overview.vb, self.overview_secondary.XAxis)
        self._layout_overview_controls()

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

        # The plot area is checked before any axis band. A visible grid makes
        # PyQtGraph's AxisItem.boundingRect() report the union of its own band
        # with the whole ViewBox (it draws the grid lines), so checking axis
        # bands first would resolve every point inside the plot to the
        # bottom (X-only) axis region and break two-direction panning. A point
        # genuinely on an axis band is outside the plot's ViewBox regardless
        # of that inflation, so this reordering does not change single-axis
        # panning from dragging the axis itself.
        if self.split_y_axes:
            for _view, axis, _host in self.gradient_layers:
                if axis.isVisible() and self._point_in_rect(
                    scene_position, axis.sceneBoundingRect(), 2.0
                ):
                    return "gradient", "gradient"
            for plot, role in ((self.primary, "y1"), (self.secondary_plot, "y2")):
                if self._point_in_rect(scene_position, plot.vb.sceneBoundingRect()):
                    return role, "plot_" + role
                for edge, region in (("bottom", "x"), ("left", role)):
                    axis = plot.getAxis(edge)
                    if self._point_in_rect(scene_position, axis.sceneBoundingRect(), 2.0):
                        return role, region
            return "outside", ""

        if self._point_in_rect(
            scene_position, self.primary.vb.sceneBoundingRect()
        ):
            return "y1", "plot"
        axis_regions = (
            (self.primary.getAxis("bottom"), "y1", "x"),
            (self.primary.getAxis("left"), "y1", "y1"),
            (self.primary.getAxis("right"), "y2", "y2"),
            (self.gradient_axis, "gradient", "gradient"),
        )
        for axis, role, region in axis_regions:
            if self._point_in_rect(scene_position, axis.sceneBoundingRect(), 2.0):
                return role, region
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
            # Shared pan calculations use bottom-left, Y-up canvas pixels.
            canvas_y=float(self.widget.viewport().height() - widget_position.y()),
            data_coordinates=tuple(coordinates),
            double_click=bool(double_click),
            key=str(key or ""),
        ).with_hit_target(*self._editing_hit_target(scene_position))

    def _editing_hit_target(self, scene_position):
        """Use viewport pixels, so selection tolerance is independent of zoom."""
        pointer_x = self.widget.mapFromScene(scene_position).x()
        for marker in reversed(self._marker_specs):
            item = self.marker_items.get(marker.marker_id)
            view = self.secondary if marker.axis_id == "y2" else self.primary.vb
            if item is None or not item.isVisible() or not self._point_in_rect(
                scene_position, view.sceneBoundingRect()
            ):
                continue
            marker_scene = view.mapViewToScene(self.qt_core.QPointF(marker.x_value, 0.0))
            marker_x = self.widget.mapFromScene(marker_scene).x()
            if abs(pointer_x - marker_x) <= 6:
                return "vertical_marker", marker.marker_id
        for annotation in reversed(self._annotation_specs):
            item = self.annotation_items.get(annotation.annotation_id)
            if item is not None and item.isVisible() and self._point_in_rect(
                scene_position, item.sceneBoundingRect(), 3.0
            ):
                return "annotation", annotation.annotation_id
        return "", ""

    def set_pointer_cursor(self, x_value=None, axis_id="y1"):
        if x_value is None:
            self.pointer_cursor.hide()
        else:
            view = self.secondary if self.split_y_axes and axis_id == "y2" else self.primary.vb
            if view is not self._cursor_view:
                self._cursor_view.removeItem(self.pointer_cursor)
                view.addItem(self.pointer_cursor, ignoreBounds=True)
                self._cursor_view = view
            self.pointer_cursor.setPos(float(x_value))
            self.pointer_cursor.show()

    def set_span_selection(self, start=None, end=None, axis_id="y1", mode="select"):
        """Transient drag feedback, excluded from the model and autorange."""
        if start is None or end is None:
            self.span_selection.hide()
            return
        color = (
            "#2563eb"
            if mode == "integrate"
            else "#f59e0b"
            if mode == "edit"
            else "#7c3aed"
            if mode == "select"
            else "#06b6d4"
        )
        self.span_selection.setBrush(self._brush(color, 0.25))
        for line in self.span_selection.lines:
            line.setPen(self.pg.mkPen(color, width=1.0))
        view = self.secondary if self.split_y_axes and axis_id == "y2" else self.primary.vb
        if view is not self._span_view:
            self._span_view.removeItem(self.span_selection)
            view.addItem(self.span_selection, ignoreBounds=True)
            self._span_view = view
        self.span_selection.setRegion(sorted((float(start), float(end))))
        self.span_selection.show()

    def set_trace_translation(self, dataset_id, x_delta=0.0, y_delta=0.0):
        """Move one rendered trace transiently without touching scene data."""
        item = self.trace_items.get(dataset_id)
        overview = self.overview_trace_items.get(dataset_id)
        if item is not None:
            item.setPos(float(x_delta), float(y_delta))
        if overview is not None:
            overview.setPos(float(x_delta), float(y_delta))

    def set_annotation_position(self, annotation_id, x_value, y_value):
        item = self.annotation_items.get(annotation_id)
        if item is not None:
            item.setPos(float(x_value), float(y_value))

    def set_vertical_marker_position(self, marker_id, x_value):
        item = self.marker_items.get(marker_id)
        if item is None:
            return
        item.setValue(float(x_value))
        label = getattr(item, "label", None)
        if label is not None:
            label.setText("%g min" % float(x_value))

    def set_peak_selection(self, selected_peak_ids):
        """Restyle existing peak items without rebuilding the scene."""

        selected_ids = set(selected_peak_ids or ())
        for peak_id, overlay in self.peak_overlay_items.items():
            selected = peak_id in selected_ids
            color = "#f59e0b" if selected else overlay["base_color"]
            region = overlay.get("region")
            if region is not None:
                region.setBrush(self._brush(color, 0.24 if selected else 0.08))
                region.update()
                for line in region.lines:
                    line.setPen(
                        self._pen(
                            "#9ca3af", 1.15 if selected else 0.8,
                            0.9 if selected else 0.55,
                        )
                    )
            for boundary_line in overlay.get("boundary_lines", ()):
                boundary_line.setPen(
                    self._pen(
                        "#9ca3af", 1.15 if selected else 0.8,
                        0.9 if selected else 0.55,
                    )
                )
            retention_line = overlay.get("retention_line")
            if retention_line is not None:
                retention_line.setPen(
                    self._pen(
                        color, 1.1 if selected else 0.8,
                        0.75 if selected else 0.35,
                    )
                )
            baseline_line = overlay.get("baseline_line")
            if baseline_line is not None:
                baseline_line.setPen(
                    self._pen(
                        color, 1.4 if selected else 0.9,
                        0.95 if selected else 0.55,
                    )
                )
            fit_line = overlay.get("fit_line")
            if fit_line is not None:
                fit_line.setPen(
                    self.pg.mkPen(
                        "#f59e0b" if selected else "#c026d3",
                        width=1.8 if selected else 1.2,
                    )
                )
        self.widget.update()

    def set_display_options(
        self, *, show_integration, show_retention, show_gradient
    ):
        """Show prepared display layers without rerendering the scene."""

        show_integration = bool(show_integration)
        show_retention = bool(show_retention)
        show_gradient = bool(show_gradient and self.gradient_items)
        for item in self.gradient_items:
            item.setVisible(show_gradient)
        for view, axis, _host in self.gradient_layers:
            view.setVisible(show_gradient)
            axis.setVisible(show_gradient)

        visible_overlays = 0
        for overlay in self.peak_overlay_items.values():
            integration_items = [
                overlay.get("region"),
                overlay.get("retention_line"),
                overlay.get("baseline_line"),
            ]
            integration_items.extend(overlay.get("boundary_lines", ()))
            for item in integration_items:
                if item is not None:
                    item.setVisible(show_integration)
            retention_label = overlay.get("retention_label")
            if retention_label is not None:
                retention_label.setVisible(show_retention)
            if (
                (show_integration and any(
                    item is not None for item in integration_items
                ))
                or (show_retention and retention_label is not None)
                or overlay.get("fit_line") is not None
            ):
                visible_overlays += 1

        if self.last_evidence:
            counts = self.last_evidence.get("counts", {})
            counts["gradients"] = (
                len(self.gradient_items) if show_gradient else 0
            )
            counts["peak_overlays"] = visible_overlays
        self.widget.update()

    def set_zoom_rectangle(self, start=None, end=None, axis_id="y1", mode="both"):
        if start is None or end is None:
            self.zoom_rectangle.hide()
            return
        view = (self.overview.vb if axis_id == "overview_y1" else
                self.secondary if self.split_y_axes and axis_id == "y2"
                else self.primary.vb)
        if view is not self._zoom_view:
            self._zoom_view.removeItem(self.zoom_rectangle)
            view.addItem(self.zoom_rectangle, ignoreBounds=True)
            self._zoom_view = view
        x1, y1 = start
        x2, y2 = end
        current = view.viewRange()
        if mode == "x":
            y1, y2 = current[1]
        elif mode == "y":
            x1, x2 = current[0]
        self.zoom_rectangle.setRect(self.qt_core.QRectF(
            min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)
        ))
        self.zoom_rectangle.show()

    def _add(self, item, axis_id="y1"):
        self._view(axis_id).addItem(item)
        self.items.append(item)
        return item

    def _can_reuse_trace_items(self, scene):
        previous = self._rendered_scene
        if previous is None:
            return False
        previous_axes = {
            trace.dataset_id: trace.axis_id for trace in previous.traces
        }
        current_axes = {trace.dataset_id: trace.axis_id for trace in scene.traces}
        return previous_axes == current_axes and set(current_axes) == set(self.trace_items)

    @classmethod
    def _scene_values_equal(cls, left, right):
        if type(left) is not type(right):
            return False
        if isinstance(left, np.ndarray):
            return np.array_equal(left, right, equal_nan=True)
        if is_dataclass(left):
            return all(
                cls._scene_values_equal(
                    getattr(left, field.name), getattr(right, field.name)
                )
                for field in fields(left)
            )
        if isinstance(left, (tuple, list)):
            return len(left) == len(right) and all(
                cls._scene_values_equal(first, second)
                for first, second in zip(left, right)
            )
        return left == right

    def _can_reuse_static_items(self, scene):
        previous = self._rendered_scene
        if previous is None:
            return False
        return all(
            self._scene_values_equal(old, new)
            for old, new in (
                (previous.gradient, scene.gradient),
                (previous.peak_overlays, scene.peak_overlays),
                (previous.vertical_markers, scene.vertical_markers),
                (previous.fraction_regions, scene.fraction_regions),
                (previous.text_annotations, scene.text_annotations),
            )
        )

    def _trace_screen_data(self, trace, *, overview=False):
        width = max(1, int(self.widget.viewport().width()))
        budget = screen_point_budget(width, overview=overview)
        return minmax_decimate(trace.x_values, trace.y_values, budget)

    def _finish_render(
        self, scene, counts, *, reused_traces=False, reused_static=False,
        view_state=None, overview_state=None,
    ):
        if view_state is None or overview_state is None:
            self.primary.enableAutoRange()
            self.secondary.enableAutoRange()
            self.overview.enableAutoRange()
            self.overview_secondary.enableAutoRange()
        self.gradient.setYRange(0.0, 100.0, padding=0.0)
        self._sync_auxiliary_views()
        if view_state is None or overview_state is None:
            # Standalone consumers still resolve their initial data range before
            # evidence or a snapshot is requested. The on-screen adapter supplies
            # the final range and does not pump an intermediate frame.
            self.application.processEvents()
        else:
            self.apply_view_state(
                view_state, overview_state, process_events=False
            )
        primary_range = self.primary.viewRange()
        secondary_range = self.secondary.viewRange()
        gradient_range = self.gradient.viewRange()
        if view_state is None or overview_state is None:
            self.overview_state = compose_overview_state(
                False, primary_range[0], primary_range[0]
            )
        self._rendered_scene = scene
        self.last_evidence = {
            "counts": counts,
            "shared_x": all(
                abs(current[index] - primary_range[0][index]) < 0.01
                for current in (secondary_range[0], gradient_range[0])
                for index in (0, 1)
            ),
            "gradient_range": tuple(gradient_range[1]),
            "reused_traces": bool(reused_traces),
            "reused_static": bool(reused_static),
            "source_trace_points": sum(
                len(trace.x_values) for trace in scene.traces
            ),
            "rendered_trace_points": sum(
                len(item.getData()[0]) for item in self.trace_items.values()
            ),
        }
        self.set_display_options(
            show_integration=any(
                overlay.show_integration_area
                for overlay in scene.peak_overlays
            ),
            show_retention=any(
                overlay.show_retention_label
                for overlay in scene.peak_overlays
            ),
            show_gradient=(
                scene.gradient is not None and scene.gradient.visible
            ),
        )
        return dict(self.last_evidence)

    def _update_trace_items(self, scene):
        for trace in scene.traces:
            x_values, y_values = self._trace_screen_data(trace)
            item = self.trace_items[trace.dataset_id]
            # Native trace dragging uses item position only for transient
            # feedback. Scene arrays already contain the committed shift and
            # offset, so every scene refresh must clear that translation.
            item.setPos(0.0, 0.0)
            item.setData(
                x_values,
                y_values,
                pen=self._trace_pen(trace),
                name=trace.label,
            )
            overview_x, overview_y = self._trace_screen_data(
                trace, overview=True
            )
            overview = self.overview_trace_items[trace.dataset_id]
            overview.setPos(0.0, 0.0)
            overview.setData(
                overview_x,
                overview_y,
                pen=self._trace_pen(trace),
            )

    def _remove_from_scene_views(self, items):
        views = (
            (self.primary.vb, self.secondary)
            + tuple(layer[0] for layer in self.gradient_layers)
        )
        for item in items:
            for view in views:
                if item in view.addedItems:
                    view.removeItem(item)

    def render(self, scene, *, view_state=None, overview_state=None):
        reuse_traces = self._can_reuse_trace_items(scene)
        reuse_static = reuse_traces and self._can_reuse_static_items(scene)
        if reuse_static:
            self._update_trace_items(scene)
            self._marker_specs = scene.vertical_markers
            self._annotation_specs = scene.text_annotations
            counts = {
                "traces": len(scene.traces),
                "gradients": (
                    len(self.gradient_layers)
                    if scene.gradient is not None and scene.gradient.visible
                    else 0
                ),
                "peak_overlays": len(scene.peak_overlays),
                "vertical_markers": len(scene.vertical_markers),
                "fraction_regions": len(scene.fraction_regions),
                "text_annotations": len(scene.text_annotations),
            }
            return self._finish_render(
                scene,
                counts,
                reused_traces=True,
                reused_static=True,
                view_state=view_state,
                overview_state=overview_state,
            )
        if reuse_traces:
            detail_traces = tuple(self.trace_items.values())
            static_items = [
                item for item in self.items
                if not any(item is trace for trace in detail_traces)
            ]
            self._remove_from_scene_views(static_items)
            self.items = [
                self.trace_items[trace.dataset_id] for trace in scene.traces
            ]
            self._update_trace_items(scene)
        else:
            # Trace topology or axis ownership changed: replace everything.
            self._remove_from_scene_views(self.items)
            for item in self.overview_items:
                view = (
                    self.overview_secondary
                    if item in self.overview_secondary.addedItems
                    else self.overview
                )
                view.removeItem(item)
            self.items.clear()
            self.overview_items.clear()
            self.trace_items.clear()
            self.overview_trace_items.clear()

        self.fit_items.clear()
        self.gradient_items.clear()
        self.peak_overlay_items.clear()
        self.marker_items.clear()
        self._marker_specs = scene.vertical_markers
        self.annotation_items.clear()
        self._annotation_specs = scene.text_annotations
        self.set_pointer_cursor()
        self.set_span_selection()
        self.set_zoom_rectangle()
        for view, axis, _host in self.gradient_layers:
            gradient_visible = (
                scene.gradient is not None and scene.gradient.visible
            )
            view.setVisible(gradient_visible)
            axis.setVisible(gradient_visible)
        counts = {
            "traces": len(scene.traces),
            "gradients": 0,
            "peak_overlays": 0,
            "vertical_markers": 0,
            "fraction_regions": 0,
            "text_annotations": 0,
        }
        if not reuse_traces:
            for trace in scene.traces:
                x_values, y_values = self._trace_screen_data(trace)
                item = self.pg.PlotCurveItem(
                    x_values,
                    y_values,
                    pen=self._trace_pen(trace),
                    name=trace.label,
                )
                self._add(item, trace.axis_id)
                self.trace_items[trace.dataset_id] = item
                overview_x, overview_y = self._trace_screen_data(
                    trace, overview=True
                )
                overview_item = self.pg.PlotCurveItem(
                    overview_x,
                    overview_y,
                    pen=self._trace_pen(trace),
                )
                overview_view = (
                    self.overview_secondary
                    if trace.axis_id == "y2"
                    else self.overview
                )
                overview_view.addItem(overview_item)
                self.overview_items.append(overview_item)
                self.overview_trace_items[trace.dataset_id] = overview_item

        for view, axis, _host in self.gradient_layers:
            if scene.gradient is None:
                continue
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
            view.addItem(item)
            axis.setLabel(scene.gradient.axis_label)
            self.items.append(item)
            self.gradient_items.append(item)
            counts["gradients"] += 1

        trace_colors = {trace.dataset_id: trace.color for trace in scene.traces}
        for overlay in scene.peak_overlays:
            view = self._view(overlay.axis_id)
            overlay_items = {
                "base_color": trace_colors.get(overlay.dataset_id, overlay.color),
                "region": None,
                "boundary_lines": [],
                "retention_line": None,
                "baseline_line": None,
                "fit_line": None,
                "retention_label": None,
            }
            if (
                overlay.show_integration_area
                or overlay.prepare_integration_area
            ):
                region = self.pg.LinearRegionItem(
                    values=(overlay.start_x, overlay.end_x),
                    movable=False,
                    brush=self._brush(
                        overlay.color, 0.24 if overlay.is_selected else 0.08
                    ),
                    pen=self._pen(
                        "#9ca3af", 1.15 if overlay.is_selected else 0.8,
                        0.9 if overlay.is_selected else 0.55,
                    ),
                )
                view.addItem(region)
                self.items.append(region)
                overlay_items["region"] = region
                for value in (overlay.start_x, overlay.end_x):
                    boundary_line = self._add(
                        self.pg.InfiniteLine(
                            pos=value,
                            angle=90,
                            movable=False,
                            pen=self._pen(
                                "#9ca3af",
                                1.15 if overlay.is_selected else 0.8,
                                0.9 if overlay.is_selected else 0.55,
                            ),
                        ),
                        overlay.axis_id,
                    )
                    overlay_items["boundary_lines"].append(boundary_line)
                if overlay.retention_x is not None:
                    overlay_items["retention_line"] = self._add(
                        self.pg.InfiniteLine(
                            pos=overlay.retention_x,
                            angle=90,
                            movable=False,
                            pen=self._pen(
                                overlay.color,
                                1.1 if overlay.is_selected else 0.8,
                                0.75 if overlay.is_selected else 0.35,
                            ),
                        ),
                        overlay.axis_id,
                    )
                if overlay.baseline_x is not None:
                    overlay_items["baseline_line"] = self._add(
                        self.pg.PlotCurveItem(
                            overlay.baseline_x,
                            overlay.baseline_y,
                            pen=self._pen(
                                overlay.color,
                                1.4 if overlay.is_selected else 0.9,
                                0.95 if overlay.is_selected else 0.55,
                            ),
                        ),
                        overlay.axis_id,
                    )
            if overlay.fit_x is not None:
                fit_item = self._add(
                    self.pg.PlotCurveItem(
                        overlay.fit_x,
                        overlay.fit_y,
                        pen=self.pg.mkPen(
                            "#f59e0b" if overlay.is_selected else "#c026d3",
                            width=1.8 if overlay.is_selected else 1.2,
                        ),
                    ),
                    overlay.axis_id,
                )
                self.fit_items[overlay.peak_id] = fit_item
                overlay_items["fit_line"] = fit_item
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
                overlay_items["retention_label"] = text
            self.peak_overlay_items[overlay.peak_id] = overlay_items
            counts["peak_overlays"] += 1

        for marker in scene.vertical_markers:
            item = self._add(
                self.pg.InfiniteLine(
                    pos=marker.x_value,
                    angle=90,
                    movable=False,
                    pen=self.pg.mkPen(marker.color, width=marker.line_width),
                    label=marker.label_text,
                    labelOpts={
                        "position": 0.96,
                        "color": marker.color,
                    },
                ),
                marker.axis_id,
            )
            item.setOpacity(marker.alpha)
            self.marker_items[marker.marker_id] = item
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
            self.annotation_items[annotation.annotation_id] = text
            counts["text_annotations"] += 1

        return self._finish_render(
            scene,
            counts,
            reused_traces=reuse_traces,
            reused_static=False,
            view_state=view_state,
            overview_state=overview_state,
        )

    @staticmethod
    def _range_tuple(view_range):
        return tuple(float(value) for value in view_range)

    def apply_view_state(
        self,
        view_state: ScreenViewState,
        overview_state: ScreenOverviewState,
        *,
        process_events=True,
    ):
        """Apply backend-neutral navigation state to the optional renderer."""
        self.overview_state = overview_state
        self._apply_plot_row_stretches()
        self.widget.ci.layout.invalidate()
        self.widget.ci.layout.activate()
        self._has_y2 = view_state.y2 is not None
        self._has_gradient = view_state.gradient is not None
        self.primary.setXRange(*view_state.x, padding=0.0)
        self.primary.setYRange(*view_state.y1, padding=0.0)
        if view_state.y2 is not None:
            self.secondary.setYRange(*view_state.y2, padding=0.0)
        if view_state.gradient is not None:
            self.gradient.setYRange(*view_state.gradient, padding=0.0)

        self.overview.setVisible(overview_state.enabled)
        self.overview_handle.setVisible(overview_state.enabled)
        self.overview_secondary.setVisible(overview_state.enabled and self._has_y2)
        self.overview_region.setVisible(overview_state.enabled)
        self.overview_secondary_region.setVisible(
            overview_state.enabled and self._has_y2
        )
        self._set_overview_controls_visible(overview_state.enabled)
        self._set_detail_controls_visible(True)
        if overview_state.enabled:
            self.overview.setXRange(*overview_state.full_x, padding=0.0)
            left, right = overview_state.detail_x
            bottom, top = view_state.y1
            self.overview_region.setRect(self.qt_core.QRectF(
                min(left, right), min(bottom, top),
                abs(right - left), abs(top - bottom),
            ))
            if view_state.y2 is not None:
                y2_bottom, y2_top = view_state.y2
                self.overview_secondary_region.setRect(self.qt_core.QRectF(
                    min(left, right), min(y2_bottom, y2_top),
                    abs(right - left), abs(y2_top - y2_bottom),
                ))
        self.sync_navigation_scrollbars()

        self._sync_auxiliary_views()
        self._sync_overview_view()
        if process_events:
            self.application.processEvents()
            # Visibility and dynamic axis-width changes settle during event
            # processing. Re-align linked overlays to the final plot geometry.
            self._sync_auxiliary_views()
            self._sync_overview_view()
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
            "overview_detail_x": overview_state.detail_x,
            "overview_detail_y1": self._range_tuple(view_state.y1),
        }
        self.last_evidence["view_state"] = evidence
        return dict(evidence)

    def capture_view_state(self):
        primary_range = self.primary.viewRange()
        return ScreenViewState(
            x=self._range_tuple(primary_range[0]),
            y1=self._range_tuple(primary_range[1]),
            y2=(self._range_tuple(self.secondary.viewRange()[1])
                if getattr(self, "_has_y2", True) else None),
            gradient=(self._range_tuple(self.gradient.viewRange()[1])
                      if getattr(self, "_has_gradient", True) else None),
        )

    def snapshot(self):
        self.widget.show()
        self.application.processEvents()
        return self.widget.grab()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._pointer_handler = None
        self._connections.clear()
        self.widget.viewport().removeEventFilter(self._pointer_filter)
        for view in (self.primary.vb, self.secondary, self.overview.vb, self.overview_secondary
                     ) + tuple(layer[0] for layer in self.gradient_layers):
            view.close()
        self.widget.close()
