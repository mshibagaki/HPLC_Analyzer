"""Qt6 MainWindow screen adapter; Matplotlib remains output and fallback.

Model edits use the owner's undo-aware methods. The application-level renderer
preference is owned by MainWindow and does not enter project files.
"""

from copy import deepcopy
from dataclasses import replace
from html import escape
from math import isfinite

from .pyqtgraph_navigation import PyQtGraphNavigationController
from .pyqtgraph_scene import PyQtGraphSceneConsumer
from .qt_compat import USER_ROLE
from .screen_events import ScreenPointerEvent
from .screen_navigation import compose_overview_state


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
        self._legend = None
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
                    if event.type() in (core.QEvent.Type.Leave, core.QEvent.Type.FocusOut,
                                        core.QEvent.Type.Resize):
                        preview.cancel_span_drag()
                        preview.cancel_move_drag()
                        preview.cancel_annotation_drag()
                        preview.cancel_zoom_drag()
                    if event.type() == core.QEvent.Type.Leave:
                        preview.consumer.set_pointer_cursor()
                        preview.owner._update_pointer_coordinates(
                            ScreenPointerEvent()
                        )
                    if (event.type() == core.QEvent.Type.KeyPress
                            and event.key() == core.Qt.Key.Key_Escape
                            and (preview._span_drag is not None
                                 or preview._move_target is not None
                                 or preview._annotation_target is not None
                                 or preview._zoom_drag is not None)):
                        preview.cancel_span_drag()
                        preview.cancel_move_drag()
                        preview.cancel_annotation_drag()
                        preview.cancel_zoom_drag()
                        event.accept()
                        return True
                    if (event.type() == core.QEvent.Type.KeyPress
                            and event.key() == core.Qt.Key.Key_Delete
                            and preview.owner.delete_selected_vertical_marker()):
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
            if (scene is not self._scene or (self._span_drag is not None
                    and self._span_drag["view"] != owner._screen_view_state())):
                self.cancel_span_drag()
            if scene is not self._scene:
                self.consumer.render(scene)
                self._scene = scene
                if self.navigation is not None:
                    self.navigation.set_pan_enabled(False)
                self._update_legend(scene)
            primary = self.consumer.primary
            lower = self.consumer.secondary_plot
            if lower is None:
                primary.showAxis("right", owner.axes_right is not None)
            for _view, axis, _host in self.consumer.gradient_layers:
                axis.setVisible(owner.axes_gradient is not None)
            primary.showGrid(
                x=owner.project.method.show_major_grid,
                y=owner.project.method.show_major_grid, alpha=0.2,
            )
            if lower is not None:
                lower.showGrid(x=owner.project.method.show_major_grid,
                               y=owner.project.method.show_major_grid, alpha=0.2)
            x_axes = [primary.getAxis("bottom")]
            if lower is not None:
                x_axes.append(lower.getAxis("bottom"))
            if owner.project.method.x_tick_mode == "manual":
                for axis in x_axes:
                    axis.setTickSpacing(
                        owner.project.method.x_major_tick_min,
                        owner.project.method.x_minor_tick_min,
                    )
            else:
                for axis in x_axes:
                    axis.setTickSpacing()
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
                if axis.orientation in ("left", "right"):
                    metrics = self.consumer.qt_gui.QFontMetricsF(font)
                    axis.setWidth(max(80, metrics.horizontalAdvance("-12345.6789")
                                      + metrics.height() + 16))
            # AxisItem.setTextPen also changes its title color. Reapply title
            # styles after setting the independent tick-label pen.
            self._apply_axis_labels(primary, lower, scene)
            if lower is not None:
                # Both panels have equally sized B% axes; no placeholder margin.
                for plot in (primary, lower):
                    plot.showAxis("right")
                    right = plot.getAxis("right")
                    right.setLabel("")
                    right.setStyle(showValues=False)
                    right.setPen(None)
                    right.setWidth(0)
            state = owner._screen_view_state()
            self.consumer.apply_view_state(state, compose_overview_state(
                owner.project.method.view_mode == "overview_detail",
                owner._full_x_bounds(), state.x,
            ))
        finally:
            self._busy = False

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

    def _update_legend(self, scene):
        method = self.owner.project.method
        if self._legend is None:
            self._legend = self.consumer.pg.LegendItem(frame=False)
            self._legend.setParentItem(self.consumer.primary.vb)
            # Preserve PlotItem.addLegend's public access pattern for existing
            # preview consumers while allowing outside-layout reparenting.
            self.consumer.primary.legend = self._legend
        legend = self._legend
        legend.clear()
        for item, trace in zip(self.consumer.items, scene.traces):
            legend.addItem(item, escape(trace.label))
        if scene.gradient is not None:
            legend.addItem(self.consumer.items[len(scene.traces)], escape(scene.gradient.label))
        color = method.legend_font_color or "#000000"
        size = "%gpt" % method.legend_font_size
        family = method.legend_font_family or self.consumer.application.font().family()
        legend.setLabelTextColor(color)
        legend.setLabelTextSize(size)
        for _sample, label in legend.items:
            label.setText(label.text, color=color, size=size, family=family)
        location = method.legend_location
        outside = location == "outside right"
        if outside and not self._legend_outside:
            legend.setParentItem(None)
            self.consumer.widget.ci.addItem(
                legend, row=1, col=1,
                rowspan=2 if self.consumer.split_y_axes else 1,
            )
        elif not outside and self._legend_outside:
            self.consumer.widget.ci.removeItem(legend)
            legend.setParentItem(self.consumer.primary.vb)
        self._legend_outside = outside
        if outside:
            return
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

    def _handle_zoom_event(self, name, event):
        owner = self.owner
        if not owner.toolbar._actions["zoom"].isChecked():
            if self._zoom_drag is not None:
                self.cancel_zoom_drag()
            return False
        in_plot = event.hit_region in ("plot", "plot_y1", "plot_y2")
        role = event.axis_role if event.axis_role in ("y1", "y2") else "y1"
        values = event.data_for(role)
        valid = (in_plot and all(value is not None and isfinite(value) for value in values)
                 and event.canvas_x is not None and event.canvas_y is not None)
        drag = self._zoom_drag
        if drag is not None:
            if name == "scroll_event":
                return True
            if not valid or role != drag["role"] or event.button != 1:
                self.cancel_zoom_drag()
                return True
            if name == "motion_notify_event":
                self.consumer.set_zoom_rectangle(drag["start"], values, role, drag["mode"])
            elif name == "button_release_event":
                self.cancel_zoom_drag()
                dx = abs(event.canvas_x - drag["pixel"][0])
                dy = abs(event.canvas_y - drag["pixel"][1])
                mode = drag["mode"]
                if ((mode == "x" and dx < 3) or (mode == "y" and dy < 3)
                        or (mode == "both" and (dx < 3 or dy < 3))):
                    return True
                state = self.consumer.capture_view_state()
                changes = {}
                if mode in ("x", "both"):
                    changes["x"] = tuple(sorted((drag["start"][0], values[0])))
                if mode in ("y", "both"):
                    changes[role] = tuple(sorted((drag["start"][1], values[1])))
                self.navigation._record_and_apply(replace(state, **changes))
                owner._apply_view_state(self.consumer.capture_view_state())
                owner.toolbar.set_history_buttons()
            return True
        if (name == "button_press_event" and event.button == 1 and valid
                and not event.double_click):
            configured = owner.project.method.zoom_axis
            mode = "both" if configured == "auto" else configured
            self._zoom_drag = {
                "start": values, "role": role, "mode": mode,
                "pixel": (event.canvas_x, event.canvas_y),
            }
            self.consumer.set_zoom_rectangle(values, values, role, mode)
            return True
        return name == "button_press_event" and in_plot

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
        if not editing or str(owner.toolbar.mode):
            return False
        in_plot = event.hit_region in ("plot", "plot_y1", "plot_y2")
        if (name == "button_press_event" and event.button == 1 and in_plot):
            owner._on_canvas_press(event)
            drag = owner._annotation_drag
            if drag is not None:
                annotation = drag["annotation"]
                self.consumer.widget.setFocus(
                    self.consumer.qt_core.Qt.FocusReason.MouseFocusReason
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
            if (not valid or event.axis_role != target["role"] or event.button != 1):
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
                    self.consumer.qt_core.Qt.FocusReason.MouseFocusReason
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
                "fraction" if owner.fraction_button.isChecked() else
                "time_range" if owner._mouse_mode == "time_range" else "")
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
                self.cancel_span_drag()
                if abs(event.canvas_x - drag["pixel"]) >= 3 and x_value != drag["start"]:
                    callback = (owner._on_span_selected if mode == "integrate"
                                else owner._on_edit_span_selected if mode == "edit"
                                else owner._on_fraction_span_selected if mode == "fraction"
                                else owner._on_time_range_selected)
                    callback(drag["start"], x_value)
            return True
        if (name == "button_press_event" and event.button == 1 and valid
                and not event.double_click and owner._selected_dataset() is not None):
            self.consumer.widget.setFocus(self.consumer.qt_core.Qt.FocusReason.MouseFocusReason)
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
        # Check the table's stable ID too: stale/reordered rows must not edit a
        # different peak. Splitting is immediate, with no pending drag target.
        valid = (selected is not None and selected.visible
                 and 0 <= row < len(selected.peaks) and item is not None
                 and item.isSelected() and item.data(USER_ROLE) == selected.peaks[row].id
                 and any(trace.dataset_id == selected.id for trace in self._scene.traces)
                 and event.hit_region in ("plot", "plot_y1", "plot_y2")
                 and (not self.consumer.split_y_axes
                      or event.axis_role == ("y2" if selected.y_axis == 2 else "y1")))
        time = event.data_for(event.axis_role)[0]
        valid = valid and time is not None and isfinite(time)
        self.consumer.set_pointer_cursor(time if valid else None, event.axis_role)
        if name == "button_press_event" and valid and event.button == 1 and not event.double_click:
            self.consumer.widget.setFocus(self.consumer.qt_core.Qt.FocusReason.MouseFocusReason)
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
            if (
                owner._mouse_mode == "peak_select"
                and name == "button_press_event"
                and event.button == 1
                and event.hit_region in ("plot", "plot_y1", "plot_y2")
                and not str(owner.toolbar.mode)
            ):
                event = event.with_hit_target(
                    *owner._integration_peak_hit_target(event)
                )
                self.consumer.widget.setFocus(
                    self.consumer.qt_core.Qt.FocusReason.MouseFocusReason
                )
                owner._on_canvas_press(event)
                return True
            if self._handle_span_event(name, event):
                return True
            if self._handle_zoom_event(name, event):
                return True
            if self._handle_split_event(name, event):
                return True
            if self._handle_move_event(name, event):
                return True
            if self._handle_annotation_event(name, event):
                return True
            if name == "motion_notify_event":
                x_value = (event.data_for("y1")[0]
                           if (owner.pointer_action.isChecked() or owner.fraction_button.isChecked()
                               or owner.integrate_button.isChecked() or owner.edit_peak_button.isChecked()
                               or owner._mouse_mode == "time_range")
                           and event.hit_region in ("plot", "plot_y1", "plot_y2")
                           else None)
                self.consumer.set_pointer_cursor(x_value, event.axis_role)
            if (name == "button_press_event" and event.button == 1
                    and not str(owner.toolbar.mode)):
                if event.hit_region in ("plot", "plot_y1", "plot_y2") and (
                    owner.pointer_action.isChecked()
                    or event.hit_kind in ("vertical_marker", "annotation")
                ):
                    self.consumer.widget.setFocus(self.consumer.qt_core.Qt.FocusReason.MouseFocusReason)
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
