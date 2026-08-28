"""Transitional MainWindow adapter; Matplotlib remains the export/edit model.

Imported only on explicit opt-in. No project data or persistent settings change.
"""

from html import escape

from .pyqtgraph_navigation import PyQtGraphNavigationController
from .pyqtgraph_scene import PyQtGraphSceneConsumer
from .screen_navigation import compose_overview_state


class ExperimentalScreenPreview:
    def __init__(self, owner):
        self.owner = owner
        self.consumer = PyQtGraphSceneConsumer()
        self.navigation = None
        self._scene = None
        self._busy = False
        try:
            self.consumer.widget.setBackground("w")
            self.consumer.primary.setMenuEnabled(False)
            for view in (self.consumer.primary.vb, self.consumer.secondary,
                         self.consumer.gradient, self.consumer.overview.vb,
                         self.consumer.overview_secondary):
                view.setMenuEnabled(False)
            owner.plot_stack.addWidget(self.consumer.widget)
            self.refresh()
            self.navigation = PyQtGraphNavigationController(self.consumer)
            # One history across backend changes and existing toolbar actions.
            self.navigation.history = owner._view_history
            self.consumer.set_pointer_handler(None)
            self.consumer.set_pointer_handler(self.handle_event)
        except Exception:
            self.close()
            raise

    def refresh(self):
        if self._busy:
            return
        self._busy = True
        try:
            owner = self.owner
            scene = owner._screen_scene
            if scene is not self._scene:
                self.consumer.render(scene)
                self._scene = scene
                if self.navigation is not None:
                    self.navigation.set_pan_enabled(False)
                self._update_legend(scene)
            primary = self.consumer.primary
            primary.setLabel("bottom", escape(owner.axes.get_xlabel()))
            primary.setLabel("left", escape(owner.axes.get_ylabel()))
            primary.showAxis("right", owner.axes_right is not None)
            if owner.axes_right is not None:
                primary.setLabel("right", escape(owner.axes_right.get_ylabel()))
            self.consumer.gradient_axis.setVisible(owner.axes_gradient is not None)
            primary.showGrid(
                x=owner.project.method.show_major_grid,
                y=owner.project.method.show_major_grid, alpha=0.2,
            )
            for axis in (primary.getAxis("bottom"), primary.getAxis("left"),
                         primary.getAxis("right"), self.consumer.gradient_axis):
                axis.setPen("#111827")
                axis.setTextPen(owner.project.method.tick_label_color or "#000000")
                font = self.consumer.qt_gui.QFont(self.consumer.application.font())
                if owner.project.method.tick_label_font_family:
                    font.setFamily(owner.project.method.tick_label_font_family)
                font.setPointSizeF(owner.project.method.tick_label_font_size)
                axis.setStyle(tickFont=font)
                if axis.orientation in ("left", "right"):
                    metrics = self.consumer.qt_gui.QFontMetricsF(font)
                    axis.setWidth(max(80, metrics.horizontalAdvance("-12345.6789")
                                      + metrics.height() + 16))
            state = owner._screen_view_state()
            self.consumer.apply_view_state(state, compose_overview_state(
                owner.project.method.view_mode == "overview_detail",
                owner._full_x_bounds(), state.x,
            ))
        finally:
            self._busy = False

    def _update_legend(self, scene):
        method = self.owner.project.method
        legend = self.consumer.primary.addLegend(
            labelTextColor=method.legend_font_color or "#000000",
            labelTextSize="%gpt" % method.legend_font_size,
        )
        legend.clear()
        for item, trace in zip(self.consumer.items, scene.traces):
            legend.addItem(item, escape(trace.label))
        if scene.gradient is not None:
            legend.addItem(self.consumer.items[len(scene.traces)], escape(scene.gradient.label))
        location = method.legend_location
        right = "left" not in location
        bottom = "lower" in location
        corner = (int(right), int(bottom))
        legend.anchor(corner, corner, offset=(-10 if right else 10, -10 if bottom else 10))

    def handle_event(self, name, event):
        if self._busy:
            return True
        try:
            owner = self.owner
            if not owner._view_initialized:
                return True
            if name == "scroll_event":
                if self.navigation._pan is None:
                    owner._on_scroll(event)
                return True
            pan = bool(owner.toolbar._actions["pan"].isChecked())
            if pan != self.navigation.pan_enabled:
                self.navigation.set_pan_enabled(pan)
            consumed = self.navigation.handle_event(name, event)
            state = self.consumer.capture_view_state()
            if state != owner._screen_view_state():
                owner._apply_view_state(state)
            owner.toolbar.set_history_buttons()
            return consumed
        except Exception:
            # Do not let an optional renderer exception escape a Qt callback.
            self.owner._stop_screen_preview(failed=True)
            return True

    def close(self):
        if self.navigation is not None:
            self.navigation.close()
        self.owner.plot_stack.removeWidget(self.consumer.widget)
        self.consumer.close()
        self.consumer.widget.deleteLater()
