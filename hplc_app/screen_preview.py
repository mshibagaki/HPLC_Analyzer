"""Qt5/Qt6 MainWindow screen adapter; Matplotlib remains output and fallback.

Model edits use the owner's undo-aware methods. The application-level renderer
preference is owned by MainWindow and does not enter project files.
"""

from copy import deepcopy
from dataclasses import replace
from html import escape
from math import isfinite

from .pyqtgraph_navigation import PyQtGraphNavigationController
from .pyqtgraph_scene import PyQtGraphSceneConsumer
from .qt_compat import (
    EVENT_FOCUS_OUT,
    EVENT_KEY_PRESS,
    EVENT_LEAVE,
    EVENT_RESIZE,
    KEY_DELETE,
    KEY_ESCAPE,
    MOUSE_FOCUS_REASON,
    USER_ROLE,
)
from .screen_events import ScreenPointerEvent
from .screen_navigation import (
    ScreenViewState, axis_pan_view, begin_axis_pan, compose_overview_state,
)
from .rendering import safe_manual_x_tick_spacing


class ExperimentalScreenPreview:
    def __init__(self, owner):
        self.owner = owner
        self.consumer = PyQtGraphSceneConsumer(
            split_y_axes=owner.project.method.view_mode == "split_y_axes"
        )
        self.navigation = None
        self._scene = None
        self._busy = False
        self._key_filter = None
        self._span_drag = None
        self._move_target = None
        self._annotation_target = None
        self._zoom_drag = None
        self._overview_pan = None
        self._legend = None
        self._legend_host = None
        self._legend_outside = False
        try:
            self.consumer.widget.setBackground("w")
            self.consumer.primary.setMenuEnabled(False)
            if self.consumer.secondary_plot is not None:
                self.consumer.secondary_plot.setMenuEnabled(False)
            for view in (self.consumer.primary.vb, self.consumer.secondary,
                         self.consumer.overview.vb, self.consumer.overview_secondary
                         ) + tuple(layer[0] for layer in self.consumer.gradient_layers):
                view.setMenuEnabled(False)
            owner.plot_stack.addWidget(self.consumer.widget)
            self.consumer.overview_ratio_changed = (
                lambda ratio: setattr(owner, "_overview_split_ratio", ratio)
            )
            self.consumer.overview_action_handler = self._handle_overview_action
            self.consumer.scroll_bounds_provider = owner._screen_scroll_bounds
            self.consumer.set_overview_tooltips(
                owner.translator("navigation_scrollbar_tooltip"),
                owner.translator("overview_zoom_in_tooltip"),
                owner.translator("overview_zoom_out_tooltip"),
                owner.translator("overview_home_tooltip"),
            )
            self.consumer.set_overview_ratio(owner._overview_split_ratio)
            self.refresh()
            self.navigation = PyQtGraphNavigationController(self.consumer)
            # One history across backend changes and existing toolbar actions.
            self.navigation.history = owner._view_history
            self.consumer.set_pointer_handler(None)
            self.consumer.set_pointer_handler(self.handle_event)
            self._install_key_filter()
        except Exception:
            self.close()
            raise

    def _install_key_filter(self):
        preview = self
        core = self.consumer.qt_core

        class PlotKeyFilter(core.QObject):
            def eventFilter(self, watched, event):
                try:
                    if event.type() in (
                        EVENT_LEAVE, EVENT_FOCUS_OUT, EVENT_RESIZE,
                    ):
                        preview.cancel_span_drag()
                        preview.cancel_move_drag()
                        preview.cancel_annotation_drag()
                        preview.cancel_zoom_drag()
                        preview.cancel_overview_pan()
                    if event.type() == EVENT_LEAVE:
                        preview.consumer.set_pointer_cursor()
                        preview.owner._update_pointer_coordinates(
                            ScreenPointerEvent()
                        )
                    if (event.type() == EVENT_KEY_PRESS
                            and event.key() == KEY_ESCAPE
                            and (preview._span_drag is not None
                                 or preview._move_target is not None
                                 or preview._annotation_target is not None
                                 or preview._zoom_drag is not None
                                 or preview.owner.selected_time_range is not None
                                 or preview.owner.edit_peak_button.isChecked())):
                        preview.cancel_span_drag()
                        preview.cancel_move_drag()
                        preview.cancel_annotation_drag()
                        preview.cancel_zoom_drag()
                        preview.owner._clear_selected_time_range()
                        if preview.owner.edit_peak_button.isChecked():
                            preview.owner.edit_peak_button.setChecked(False)
                        event.accept()
                        return True
                    if (event.type() == EVENT_KEY_PRESS
                            and event.key() == KEY_DELETE
                            and preview.owner.delete_selected_plot_items()):
                        event.accept()
                        return True
                except Exception:
                    preview.owner._stop_screen_preview(failed=True)
                    return True
                return False

        self._key_filter = PlotKeyFilter(self.consumer.widget)
        self.consumer.widget.installEventFilter(self._key_filter)
        self.consumer.widget.viewport().installEventFilter(self._key_filter)

    def refresh(self):
        if self._busy:
            return
        self._busy = True
        try:
            owner = self.owner
            scene = owner._screen_scene
            method = owner.project.method
            state = owner._screen_view_state()
            overview_state = compose_overview_state(
                method.view_mode == "overview_detail",
                owner._current_overview_x(), state.x,
            )
            if (scene is not self._scene or (self._span_drag is not None
                    and self._span_drag["view"] != owner._screen_view_state())):
                self.cancel_span_drag()
            if scene is not self._scene:
                self.consumer.render(
                    scene,
                    view_state=state,
                    overview_state=overview_state,
                )
                self._scene = scene
                if self.navigation is not None:
                    self.navigation.set_pan_enabled(False)
            self.consumer.set_display_options(
                show_integration=method.show_integration_areas,
                show_retention=method.show_retention_labels,
                show_gradient=method.show_gradient_b,
            )
            if not self.consumer._overview_drag_active:
                self.consumer.set_overview_ratio(owner._overview_split_ratio)
            self._update_legend(scene)
            primary = self.consumer.primary
            lower = self.consumer.secondary_plot
            if lower is None:
                primary.showAxis("right", owner.axes_right is not None)
            # Only the X axis contributes grid lines; the requested grid is the
            # vertical set alone. The existing show_major_grid setting keeps its
            # name and its on/off meaning.
            primary.showGrid(
                x=owner.project.method.show_major_grid, y=False, alpha=0.2,
            )
            if lower is not None:
                lower.showGrid(
                    x=owner.project.method.show_major_grid, y=False, alpha=0.2,
                )
            x_axes = [primary.getAxis("bottom")]
            if lower is not None:
                x_axes.append(lower.getAxis("bottom"))
            if owner.project.method.x_tick_mode == "manual":
                spacing = safe_manual_x_tick_spacing(
                    abs(state.x[1] - state.x[0]),
                    owner.project.method.x_major_tick_min,
                    owner.project.method.x_minor_tick_min,
                )
            else:
                spacing = None
            if spacing is None:
                for axis in x_axes:
                    axis.setTickSpacing()
            else:
                for axis in x_axes:
                    axis.setTickSpacing(*spacing)
            axes = [primary.getAxis("bottom"), primary.getAxis("left")]
            axes.extend(layer[1] for layer in self.consumer.gradient_layers)
            if lower is None:
                axes.append(primary.getAxis("right"))
            else:
                axes.extend((lower.getAxis("bottom"), lower.getAxis("left")))
            for axis in axes:
                axis.setPen("#111827")
                axis.setTextPen(owner.project.method.tick_label_color or "#000000")
                font = self.consumer.qt_gui.QFont(self.consumer.application.font())
                if owner.project.method.tick_label_font_family:
                    font.setFamily(owner.project.method.tick_label_font_family)
                font.setPointSizeF(owner.project.method.tick_label_font_size)
                axis.setStyle(tickFont=font)
            # AxisItem.setTextPen also changes its title color. Reapply title
            # styles after setting the independent tick-label pen.
            self._apply_axis_labels(primary, lower, scene)
            self._apply_vertical_axis_widths(primary, lower, font)
            if lower is not None:
                # Both panels have equally sized B% axes; no placeholder margin.
                for plot in (primary, lower):
                    plot.showAxis("right")
                    right = plot.getAxis("right")
                    right.setLabel("")
                    right.setStyle(showValues=False)
                    right.setPen(None)
                    right.setWidth(0)
            if self._span_drag is None:
                selected = owner._selected_dataset()
                if owner._mouse_mode == "select" and owner.selected_time_range:
                    role = (
                        "y2" if selected is not None and selected.y_axis == 2
                        else "y1"
                    )
                    self.consumer.set_span_selection(
                        *owner.selected_time_range, role, "select"
                    )
                else:
                    self.consumer.set_span_selection()
            self.consumer.apply_view_state(state, overview_state)
            owner._apply_overview_y_ranges()
        finally:
            self._busy = False

    def set_peak_selection(self, selected_peak_ids):
        self.consumer.set_peak_selection(selected_peak_ids)

    def _handle_overview_action(self, command, value=None):
        owner = self.owner
        if command == "zoom_in":
            owner._zoom_overview_y(0.8)
        elif command == "zoom_out":
            owner._zoom_overview_y(1.25)
        elif command == "home":
            owner._overview_full_x = None
            owner._overview_full_y = None
            owner._set_overview_x(owner._full_x_bounds())
            owner._set_overview_y(owner._overview_y_bounds())
        elif command == "detail":
            state = self.consumer.capture_view_state()
            owner._push_view_history()
            owner._apply_view_state(replace(state, x=tuple(value)))
            owner.toolbar.set_history_buttons()
        elif command == "overview_x":
            owner._set_overview_x(value)
        elif command == "overview_y":
            owner._set_overview_y(value)
        elif command == "detail_x":
            owner._apply_view_state(replace(
                self.consumer.capture_view_state(), x=tuple(value)
            ))
        elif command == "detail_y":
            owner._set_detail_y_from_primary(value)

    def _handle_overview_pan_event(self, name, event):
        """Pan the overview in X without changing the detail view or history."""
        owner = self.owner
        pan = self._overview_pan
        if pan is not None:
            if name == "motion_notify_event" and event.button is None:
                self._overview_pan = None
                return True
            if name not in ("motion_notify_event", "button_release_event"):
                return True
            try:
                rectangle = self.consumer.pan_rectangle("x")
                view = axis_pan_view(pan, event, rectangle.width(), rectangle.height())
                owner._set_overview_x(view.x)
            finally:
                if name == "button_release_event":
                    self._overview_pan = None
            return True
        if (
            name == "button_press_event" and event.button == 1
            and event.axis_role == "overview_y1"
            and bool(owner.toolbar._actions["pan"].isChecked())
            and not event.double_click
        ):
            state = owner._screen_view_state()
            overview_view = ScreenViewState(
                x=owner._current_overview_x(), y1=state.y1,
                y2=state.y2, gradient=state.gradient,
            )
            self._overview_pan = begin_axis_pan(event, overview_view)
            return self._overview_pan is not None
        return False

    def _vertical_axis_width(self, axis, view, font):
        metrics = self.consumer.qt_gui.QFontMetricsF(font)
        lower, upper = view.viewRange()[1]
        pixels = max(100, int(view.height()))
        strings = []
        try:
            for spacing, values in axis.tickValues(lower, upper, pixels):
                strings.extend(axis.tickStrings(values, axis.scale, spacing))
        except (AttributeError, TypeError, ValueError, OverflowError):
            strings = []
        tick_width = max(
            [metrics.horizontalAdvance(str(value)) for value in strings] or [0.0]
        )
        title_space = metrics.height() + 8 if axis.labelText else 4
        return int(max(36.0, tick_width + title_space + 8.0))

    def _apply_vertical_axis_widths(self, primary, lower, font):
        left_pairs = [(primary.getAxis("left"), primary.vb)]
        if lower is not None:
            left_pairs.append((lower.getAxis("left"), lower.vb))
        left_width = max(
            self._vertical_axis_width(axis, view, font)
            for axis, view in left_pairs
        )
        for axis, _view in left_pairs:
            axis.setWidth(left_width)
        if lower is None and self.owner.axes_right is not None:
            right = primary.getAxis("right")
            right.setWidth(self._vertical_axis_width(
                right, self.consumer.secondary, font
            ))
        gradient_width = max(
            self._vertical_axis_width(axis, view, font)
            for view, axis, _host in self.consumer.gradient_layers
        )
        for _view, axis, _host in self.consumer.gradient_layers:
            axis.setWidth(gradient_width)

    def _apply_axis_labels(self, primary, lower, scene):
        owner = self.owner
        self._set_axis_label(
            primary.getAxis("bottom"),
            "" if lower is not None else owner.axes.get_xlabel(),
        )
        self._set_axis_label(primary.getAxis("left"), owner.axes.get_ylabel())
        if lower is not None:
            self._set_axis_label(lower.getAxis("bottom"), owner.axes_right.get_xlabel())
            self._set_axis_label(lower.getAxis("left"), owner.axes_right.get_ylabel())
        elif owner.axes_right is not None:
            self._set_axis_label(primary.getAxis("right"), owner.axes_right.get_ylabel())
        gradient_text = scene.gradient.axis_label if scene.gradient is not None else ""
        for _view, axis, _host in self.consumer.gradient_layers:
            self._set_axis_label(axis, gradient_text)

    def _set_axis_label(self, axis, text):
        method = self.owner.project.method
        axis.setLabel(
            escape(text or ""),
            **{
                "color": method.axis_label_color or "#000000",
                "font-family": (
                    method.axis_label_font_family
                    or self.consumer.application.font().family()
                ),
                "font-size": "%gpt" % method.axis_label_font_size,
            },
        )

    def _make_legend_host(self, legend):
        """Reserve grid space for an outside legend without stretching it.

        Placing a ``LegendItem`` directly into ``GraphicsLayout.addItem``
        makes the outer ``QGraphicsGridLayout`` call ``setGeometry()`` on it
        with the whole cell rect; the legend's own internal
        ``QGraphicsGridLayout`` then stretches every entry to fill that
        rect, which is the "legend spreads down the plot" symptom (Issue
        #308/27.6). This host reparents the legend as an ordinary child
        instead (the pattern the LegendItem docstring itself recommends) and
        only repositions -- never resizes -- it on ``setGeometry``, so the
        legend keeps its own natural, top-anchored size while the grid
        layout still reserves the cell's width/height for it.
        """

        pg = self.consumer.pg

        class _LegendHost(pg.GraphicsWidget):
            def setGeometry(self, rect):
                super().setGeometry(rect)
                # ``rect`` is this host's own new geometry in its PARENT's
                # coordinates; ``super().setGeometry`` already moves/sizes
                # the host to it. The legend is a CHILD of the host, so its
                # position must stay in the host's *local* coordinates
                # (top-left corner, 0, 0) rather than being offset by
                # ``rect`` again -- doing that double-applied the offset and
                # walked the legend away from the reserved cell (measured
                # offscreen: host at (9, 9) left the legend at (18, 18)
                # instead of (9, 9)).
                legend.setPos(0.0, 0.0)

        return _LegendHost()

    def _update_legend(self, scene):
        method = self.owner.project.method
        if self._legend is None:
            self._legend = self.consumer.pg.LegendItem(frame=False)
            self._legend.setParentItem(self.consumer.primary.vb)
            # Preserve PlotItem.addLegend's public access pattern for existing
            # preview consumers while allowing outside-layout reparenting.
            self.consumer.primary.legend = self._legend
            self._legend_host = self._make_legend_host(self._legend)
        legend = self._legend
        legend.clear()
        for trace in scene.traces:
            item = self.consumer.trace_items.get(trace.dataset_id)
            if item is not None:
                legend.addItem(item, escape(trace.label))
        if (
            method.show_gradient_b
            and scene.gradient is not None
            and self.consumer.gradient_items
        ):
            legend.addItem(
                self.consumer.gradient_items[0], escape(scene.gradient.label)
            )
        for overlay in scene.peak_overlays:
            fit_item = self.consumer.fit_items.get(overlay.peak_id)
            if fit_item is not None and overlay.fit_label:
                legend.addItem(fit_item, escape(overlay.fit_label))
        color = method.legend_font_color or "#000000"
        size = "%gpt" % method.legend_font_size
        family = method.legend_font_family or self.consumer.application.font().family()
        legend.setLabelTextColor(color)
        legend.setLabelTextSize(size)
        for _sample, label in legend.items:
            label.setText(label.text, color=color, size=size, family=family)
        # Issue #308/27.7: an explicit frame/fill color makes the legend
        # visible with that border/background; "" (the existing
        # Dataset.color "none" convention) keeps it unframed and unfilled,
        # matching every project saved before these fields existed.
        frame_color = method.legend_frame_color or ""
        fill_color = method.legend_fill_color or ""
        legend.setPen(frame_color or None)
        legend.setBrush(fill_color or None)
        legend.frame = bool(frame_color or fill_color)
        legend.update()
        location = method.legend_location
        outside = location == "outside right"
        host = self._legend_host
        if outside and not self._legend_outside:
            legend.setParentItem(host)
            legend.setPos(0.0, 0.0)
            self.consumer.widget.ci.addItem(
                host, row=2, col=1,
                rowspan=3 if self.consumer.split_y_axes else 1,
            )
        elif not outside and self._legend_outside:
            self.consumer.widget.ci.removeItem(host)
            legend.setParentItem(self.consumer.primary.vb)
        self._legend_outside = outside
        layout = self.consumer.widget.ci.layout
        if outside:
            legend.updateSize()
            host_width = legend.boundingRect().width() + 8.0
            layout.setColumnFixedWidth(1, host_width)
            # Commit the new column/row geometry immediately rather than
            # waiting for the next paint cycle, so callers reading
            # ``host.geometry()`` right after this call (screenshots,
            # pixmap capture, tests) see the reserved cell already sized.
            layout.invalidate()
            layout.activate()
            return
        layout.setColumnFixedWidth(1, 0.0)
        layout.invalidate()
        layout.activate()
        anchors = {
            "upper left": ((0, 0), (0, 0), (10, 10)),
            "lower left": ((0, 1), (0, 1), (10, -10)),
            "lower right": ((1, 1), (1, 1), (-10, -10)),
        }
        item_pos, parent_pos, offset = anchors.get(
            location, ((1, 0), (1, 0), (-10, 10))
        )
        legend.anchor(item_pos, parent_pos, offset=offset)

    def cancel_span_drag(self):
        self._span_drag = None
        self.consumer.set_span_selection()

    def cancel_zoom_drag(self):
        self._zoom_drag = None
        try:
            self.consumer.set_zoom_rectangle()
        except Exception:
            # The scene may already be partially torn down after a preview
            # renderer failure. Clearing the pending interaction is enough.
            pass

    def cancel_overview_pan(self):
        self._overview_pan = None

    def _handle_zoom_event(self, name, event):
        owner = self.owner
        drag = self._zoom_drag
        if drag is None:
            zoom_checked = owner.toolbar._actions["zoom"].isChecked()
            if not zoom_checked:
                return False
            in_plot = event.hit_region in ("plot", "plot_y1", "plot_y2")
            in_overview = event.axis_role == "overview_y1"
            on_axis = event.hit_region in ("x", "y1", "y2")
            managed = in_plot or in_overview or on_axis
            role = (event.axis_role if event.axis_role in
                    ("y1", "y2", "overview_y1") else "y1")
            if (
                name == "button_press_event"
                and event.button == 1
                and event.double_click
                and managed
            ):
                self.navigation.navigate("back")
                owner._apply_view_state(self.consumer.capture_view_state())
                owner.toolbar.set_history_buttons()
                return True
            values = event.data_for(role)
            valid = (managed
                     and all(value is not None and isfinite(value) for value in values)
                     and event.canvas_x is not None and event.canvas_y is not None)
            if (name == "button_press_event" and event.button == 1
                    and valid and not event.double_click):
                configured = owner.project.method.zoom_axis
                mode = (
                    "x" if in_overview or event.hit_region == "x"
                    else "y" if on_axis
                    else "both" if configured == "auto"
                    else configured
                )
                self._zoom_drag = {
                    "start": values, "role": role, "mode": mode,
                    "pixel": (event.canvas_x, event.canvas_y),
                }
                self.consumer.set_zoom_rectangle(values, values, role, mode)
                return True
            return name == "button_press_event" and managed
        role = drag["role"]
        values = event.data_for(role)
        if name == "scroll_event":
            return True
        if name not in ("motion_notify_event", "button_release_event"):
            return True
        valid = (all(value is not None and isfinite(value) for value in values)
                 and event.canvas_x is not None and event.canvas_y is not None)
        if not valid:
            if name == "button_release_event":
                self.cancel_zoom_drag()
            return True
        state = self.consumer.capture_view_state()
        x_limits = self.consumer.overview_state.full_x if role == "overview_y1" else state.x
        y_limits = state.y1 if role == "overview_y1" else getattr(state, role)
        values = (
            min(max(values[0], min(x_limits)), max(x_limits)),
            min(max(values[1], min(y_limits)), max(y_limits)),
        )
        if name == "motion_notify_event":
            self.consumer.set_zoom_rectangle(drag["start"], values, role, drag["mode"])
        elif name == "button_release_event":
            self.cancel_zoom_drag()
            dx = abs(event.canvas_x - drag["pixel"][0])
            dy = abs(event.canvas_y - drag["pixel"][1])
            mode = drag["mode"]
            click_only = (
                (mode == "x" and dx < 3)
                or (mode == "y" and dy < 3)
                or (mode == "both" and (dx < 3 or dy < 3))
            )
            if click_only:
                if role == "overview_y1":
                    return True
                return True
            if role == "overview_y1":
                self.navigation._record_and_apply(replace(
                    state, x=tuple(sorted((drag["start"][0], values[0])))
                ))
                owner._apply_view_state(self.consumer.capture_view_state())
                owner.toolbar.set_history_buttons()
                return True
            changes = {}
            if mode in ("x", "both"):
                changes["x"] = tuple(sorted((drag["start"][0], values[0])))
            if mode in ("y", "both"):
                changes[role] = tuple(sorted((drag["start"][1], values[1])))
            self.navigation._record_and_apply(replace(state, **changes))
            owner._apply_view_state(self.consumer.capture_view_state())
            owner.toolbar.set_history_buttons()
        return True

    def cancel_move_drag(self):
        target = self._move_target
        self._move_target = None
        if target is None:
            return
        drag = self.owner._move_drag
        if drag is not None and drag.get("undo_state") is not None:
            self.owner._restore_analysis_state(drag["undo_state"])
        self.owner._move_drag = None
        self.owner.project.dirty = target["dirty"]
        self.consumer.set_trace_translation(target["dataset_id"])

    def cancel_annotation_drag(self):
        target = self._annotation_target
        self._annotation_target = None
        if target is None:
            return
        drag = self.owner._annotation_drag
        if drag is not None:
            self.owner._restore_analysis_state(drag["undo_state"])
        self.owner._annotation_drag = None
        self.owner.project.dirty = target["dirty"]
        try:
            self.consumer.set_annotation_position(
                target["annotation_id"], target["initial_x"], target["initial_y"]
            )
        except Exception:
            # Model restoration is authoritative during renderer teardown.
            pass

    def _handle_annotation_event(self, name, event):
        owner = self.owner
        editing = owner.annotation_action.isChecked() or event.hit_kind == "annotation"
        target = self._annotation_target
        if target is not None:
            annotation = next((item for item in owner.project.annotations
                               if item.id == target["annotation_id"]), None)
            if annotation is None:
                self.cancel_annotation_drag()
                return True
            coordinates = event.data_for(target["role"])
            valid = (event.hit_region in ("plot", "plot_y1", "plot_y2")
                     and (not self.consumer.split_y_axes or event.axis_role == target["role"])
                     and all(value is not None and isfinite(value) for value in coordinates))
            if name == "scroll_event":
                return True
            if (not valid or event.button != 1):
                self.cancel_annotation_drag()
                return True
            if name == "motion_notify_event":
                owner._on_canvas_motion(event)
                self.consumer.set_annotation_position(
                    annotation.id, annotation.x_min, annotation.y_value
                )
            elif name == "button_release_event":
                self._annotation_target = None
                owner._on_canvas_release(event)
            return True
        if not editing:
            return False
        # Issue #236/9.1: "normal" mode now defaults to pan being active, so
        # owner.toolbar.mode is no longer empty while idle in it (it used to
        # be, which is what this gate originally relied on to mean "no other
        # tool owns this click"). A click that lands squarely on an existing
        # annotation (hit_kind == "annotation") must still open it for
        # editing regardless -- only bail out to let a real pan/zoom drag
        # start when the click is not on an annotation.
        if str(owner.toolbar.mode) and event.hit_kind != "annotation":
            return False
        in_plot = event.hit_region in ("plot", "plot_y1", "plot_y2")
        if (name == "button_press_event" and event.button == 1 and in_plot):
            owner._on_canvas_press(event)
            drag = owner._annotation_drag
            if drag is not None:
                annotation = drag["annotation"]
                self.consumer.widget.setFocus(
                    MOUSE_FOCUS_REASON
                )
                self._annotation_target = {
                    "annotation_id": annotation.id, "role": drag["axis_role"],
                    "initial_x": drag["initial_x"], "initial_y": drag["initial_y"],
                    "dirty": owner.project.dirty,
                }
            return True
        return name == "button_press_event" and in_plot

    def _handle_move_event(self, name, event):
        owner = self.owner
        if not owner.move_trace_button.isChecked() or str(owner.toolbar.mode):
            if self._move_target is not None:
                self.cancel_move_drag()
            return False
        selected = owner._selected_dataset()
        role = "y2" if selected is not None and selected.y_axis == 2 else "y1"
        in_plot = event.hit_region in ("plot", "plot_y1", "plot_y2")
        coordinates = event.data_for(role)
        valid = (selected is not None and selected.visible
                 and any(trace.dataset_id == selected.id for trace in self._scene.traces)
                 and in_plot and (not self.consumer.split_y_axes or event.axis_role == role)
                 and all(value is not None and isfinite(value) for value in coordinates))
        target = self._move_target
        if target is not None:
            current = (selected.id if selected else "", role, owner.move_axis_combo.currentData())
            if current != target["identity"]:
                self.cancel_move_drag()
                return True
            if name == "scroll_event":
                return True
            # Only the split layout puts the Y2 data on its own panel. In the
            # single panel the pointer always reports "y1" while a Y2 trace keeps
            # its own coordinate system, so the roles must not be compared there.
            if (not valid or event.button != 1
                    or (self.consumer.split_y_axes
                        and event.axis_role != target["role"])):
                self.cancel_move_drag()
                return True
            if name == "motion_notify_event":
                owner._on_canvas_motion(event)
                drag = owner._move_drag
                self.consumer.set_trace_translation(
                    target["dataset_id"],
                    selected.x_shift_min - drag["initial_x_shift"],
                    selected.offset - drag["initial_offset"],
                )
            elif name == "button_release_event":
                self._move_target = None
                owner._on_canvas_release(event)
            return True
        if (name == "button_press_event" and event.button == 1 and valid
                and not event.double_click and not event.hit_kind):
            owner._on_canvas_press(event)
            if owner._move_drag is not None:
                self.consumer.widget.setFocus(
                    MOUSE_FOCUS_REASON
                )
                self._move_target = {
                    "dataset_id": selected.id, "role": role,
                    "identity": (selected.id, role, owner.move_axis_combo.currentData()),
                    "dirty": owner.project.dirty,
                }
            return True
        return name == "button_press_event" and in_plot

    def _handle_span_event(self, name, event):
        owner = self.owner
        mode = ("integrate" if owner.integrate_button.isChecked() else
                "edit" if owner.edit_peak_button.isChecked() else
                "select" if owner._mouse_mode == "select" else "")
        if not mode or str(owner.toolbar.mode):
            if self._span_drag is not None:
                self.cancel_span_drag()
            return False
        selected = owner._selected_dataset()
        target = (None if selected is None else
                  (selected.id, selected.y_axis, selected.visible, selected.x_shift_min,
                   owner.project.method.baseline_mode))
        if mode == "edit":
            # Capture identity and content, never a row index or mutable reference.
            # A selection/recalculation change must not redirect a pending edit.
            peak = next((item for item in selected.peaks
                         if item.id == owner._edit_range_peak_id
                         and item.id in owner._selected_peak_ids()), None) if selected else None
            target = (target, deepcopy(peak))
        in_plot = event.hit_region in ("plot", "plot_y1", "plot_y2")
        x_value = event.data_for(event.axis_role)[0]
        valid = (in_plot and x_value is not None and isfinite(x_value)
                 and event.canvas_x is not None and isfinite(event.canvas_x))
        if mode in ("integrate", "edit"):
            valid = (valid and selected is not None and selected.visible
                     and any(trace.dataset_id == selected.id for trace in self._scene.traces)
                     and (not self.consumer.split_y_axes
                          or event.axis_role == ("y2" if selected.y_axis == 2 else "y1")))
            if mode == "edit":
                valid = valid and peak is not None
        drag = self._span_drag
        if drag is not None:
            if drag["mode"] != mode or drag["target"] != target:
                self.cancel_span_drag()
                return True
            # Do not change the mapping from pixels to time during selection.
            if name == "scroll_event":
                return True
            if (not valid or event.axis_role != drag["role"] or event.button != 1):
                self.cancel_span_drag()
                return True
            if name == "motion_notify_event":
                self.consumer.set_span_selection(drag["start"], x_value, drag["role"], mode)
                self.consumer.set_pointer_cursor(x_value, drag["role"])
            elif name == "button_release_event":
                if mode == "select":
                    self._span_drag = None
                else:
                    self.cancel_span_drag()
                if abs(event.canvas_x - drag["pixel"]) >= 3 and x_value != drag["start"]:
                    callback = (owner._on_span_selected if mode == "integrate"
                                else owner._on_edit_span_selected if mode == "edit"
                                else owner._on_selection_span_selected)
                    callback(drag["start"], x_value)
                elif mode == "select":
                    self.consumer.set_span_selection()
            return True
        if (name == "button_press_event" and event.button == 1 and valid
                and not event.double_click and owner._selected_dataset() is not None):
            self.consumer.widget.setFocus(MOUSE_FOCUS_REASON)
            if mode == "select":
                owner._clear_selected_time_range()
            self._span_drag = {
                "start": x_value, "pixel": event.canvas_x, "role": event.axis_role,
                "view": owner._screen_view_state(),
                "mode": mode, "target": target,
            }
            self.consumer.set_span_selection(x_value, x_value, event.axis_role, mode)
            return True
        # Plot presses belong to the editing tool, including rejected/double
        # clicks; do not reinterpret them as marker selection or history back.
        return name == "button_press_event" and in_plot

    def _handle_split_event(self, name, event):
        owner = self.owner
        if not owner.split_peak_button.isChecked() or str(owner.toolbar.mode):
            return False
        if name not in ("button_press_event", "button_release_event", "motion_notify_event"):
            return False
        selected = owner._selected_dataset()
        row = owner.peak_table.currentRow()
        item = owner.peak_table.item(row, 0) if row >= 0 else None
        peak = owner._peak_at_table_row(row, selected)
        # Check the table's stable ID too: stale/reordered rows must not edit a
        # different peak. Splitting is immediate, with no pending drag target.
        valid = (selected is not None and selected.visible
                 and peak is not None and not peak.is_fitted and item is not None
                 and item.isSelected() and item.data(USER_ROLE) == peak.id
                 and any(trace.dataset_id == selected.id for trace in self._scene.traces)
                 and event.hit_region in ("plot", "plot_y1", "plot_y2")
                 and (not self.consumer.split_y_axes
                      or event.axis_role == ("y2" if selected.y_axis == 2 else "y1")))
        time = event.data_for(event.axis_role)[0]
        valid = valid and time is not None and isfinite(time)
        self.consumer.set_pointer_cursor(time if valid else None, event.axis_role)
        if name == "button_press_event" and valid and event.button == 1 and not event.double_click:
            self.consumer.widget.setFocus(MOUSE_FOCUS_REASON)
            owner._split_selected_peak_at(time)
        # Split-tool clicks must not select markers/annotations or navigate back.
        return True

    def handle_event(self, name, event):
        if self._busy:
            return True
        try:
            owner = self.owner
            if name == "motion_notify_event":
                owner._update_pointer_coordinates(event)
            if not owner._view_initialized:
                return True
            if owner._vertical_marker_drag is not None:
                if name == "motion_notify_event":
                    owner._on_canvas_motion(event)
                    return True
                if name == "button_release_event":
                    owner._on_canvas_release(event)
                    return True
            if (
                owner._mouse_mode == "select"
                and name == "button_press_event"
                and event.button == 1
                and event.hit_region in ("plot", "plot_y1", "plot_y2")
                and not str(owner.toolbar.mode)
            ):
                integration_hit = owner._integration_peak_hit_target(event)
                if event.hit_kind == "vertical_marker" or integration_hit[0]:
                    if event.hit_kind != "vertical_marker":
                        event = event.with_hit_target(*integration_hit)
                    self.consumer.widget.setFocus(
                        MOUSE_FOCUS_REASON
                    )
                    owner._on_canvas_press(event)
                    return True
            if self._handle_span_event(name, event):
                return True
            if self._handle_zoom_event(name, event):
                return True
            if self._handle_overview_pan_event(name, event):
                return True
            if self._handle_split_event(name, event):
                return True
            if self._handle_move_event(name, event):
                return True
            if self._handle_annotation_event(name, event):
                return True
            if name == "motion_notify_event":
                x_value = (event.data_for("y1")[0]
                           if (owner.pointer_action.isChecked()
                               or owner.integrate_button.isChecked() or owner.edit_peak_button.isChecked()
                               or owner._mouse_mode == "select")
                           and event.hit_region in ("plot", "plot_y1", "plot_y2")
                           else None)
                self.consumer.set_pointer_cursor(x_value, event.axis_role)
            if name == "button_press_event" and event.button == 1:
                # Issue #236/9.1: "normal" mode now defaults to pan being
                # active, so owner.toolbar.mode is no longer empty while idle
                # in it. A click on an existing marker or annotation must
                # still select/edit it regardless -- only a click elsewhere
                # (which starts a real pan drag) still needs mode to be
                # empty, exactly as before.
                on_item = event.hit_region in ("plot", "plot_y1", "plot_y2") and (
                    event.hit_kind in ("vertical_marker", "annotation")
                )
                if not (str(owner.toolbar.mode) and not on_item):
                    if event.hit_region in ("plot", "plot_y1", "plot_y2") and (
                        owner.pointer_action.isChecked() or on_item
                    ):
                        self.consumer.widget.setFocus(MOUSE_FOCUS_REASON)
                        owner._on_canvas_press(event)
                        return True
                    if owner._selected_vertical_marker_id:
                        owner._select_vertical_marker(None)
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
        self._span_drag = None
        self.cancel_overview_pan()
        self.cancel_move_drag()
        self.cancel_annotation_drag()
        self.cancel_zoom_drag()
        if self._key_filter is not None:
            self.consumer.widget.removeEventFilter(self._key_filter)
            self.consumer.widget.viewport().removeEventFilter(self._key_filter)
        if self.navigation is not None:
            self.navigation.close()
        self.owner.plot_stack.removeWidget(self.consumer.widget)
        self.consumer.close()
        self.consumer.widget.deleteLater()
