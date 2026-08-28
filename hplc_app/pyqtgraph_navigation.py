"""Opt-in standalone consumer navigation; no Qt imports or scientific edits."""

from dataclasses import replace

from .screen_navigation import (
    ScreenViewHistory, axis_pan_view, begin_axis_pan, compose_overview_state,
)


class PyQtGraphNavigationController:
    """Own native pointer navigation after render/apply_view_state.

    Call reset_history after replacing the scene or loading a new project.
    Application editing tools and MainWindow toolbar integration are separate.
    """

    def __init__(self, consumer):
        self.consumer = consumer
        self.pan_enabled = False
        self._pan = None
        self._pan_recorded = False
        self.history = ScreenViewHistory()
        consumer.set_pointer_handler(self.handle_event)
        self.reset_history()

    def reset_history(self):
        self._pan = None
        self.history.clear()
        self.history.ensure_home(self.consumer.capture_view_state())

    def set_pan_enabled(self, enabled):
        self.pan_enabled = bool(enabled)
        self._pan = None

    def close(self):
        if self.consumer._pointer_handler == self.handle_event:
            self.consumer.set_pointer_handler(None)
        self._pan = None

    def capabilities(self):
        return self.history.capabilities(self.consumer.capture_view_state())

    def navigate(self, command):
        self._pan = None
        state = self.history.navigate(command, self.consumer.capture_view_state())
        if state is not None:
            self._apply(state)

    def _bounded_state(self, state):
        previous = self.consumer.overview_state
        overview = compose_overview_state(previous.enabled, previous.full_x, state.x)
        if overview.enabled:
            state = replace(state, x=overview.detail_x)
        return state, overview

    def _apply(self, state):
        state, overview = self._bounded_state(state)
        self.consumer.apply_view_state(state, overview)

    def _record_and_apply(self, state):
        state, _overview = self._bounded_state(state)
        current = self.consumer.capture_view_state()
        if state != current:
            self.history.record_before_change(current)
            self._apply(state)

    @staticmethod
    def _scaled(limits, center, factor):
        if center is None:
            center = (limits[0] + limits[1]) / 2.0
        return tuple(center + (value - center) * factor for value in limits)

    def _scroll(self, event):
        if event.button not in ("up", "down"):
            return
        factor = 0.8 if event.button == "up" else 1.25
        target = event.hit_region
        state = self.consumer.capture_view_state()
        changes = {}
        if target in ("x", "plot", "plot_y1", "plot_y2"):
            role = "overview_y1" if event.axis_role == "overview_y1" else "y1"
            changes["x"] = self._scaled(state.x, event.data_for(role)[0], factor)
        for role, targets in (
            ("y1", ("y1", "plot", "plot_y1")),
            ("y2", ("y2", "plot", "plot_y2")),
        ):
            limits = getattr(state, role)
            if target in targets and limits is not None:
                changes[role] = self._scaled(limits, event.data_for(role)[1], factor)
        if changes:
            self._record_and_apply(replace(state, **changes))

    def handle_event(self, name, event):
        managed = event.axis_role != "outside" or bool(event.hit_region)
        # A wheel sample during a drag must not replace its initial view/history.
        if self._pan is not None and name == "scroll_event":
            return True
        # Keep ownership until release even when the pointer leaves the plot.
        if self._pan is not None and name in (
            "motion_notify_event", "button_release_event",
        ):
            if name == "button_release_event" and event.button != 1:
                return True
            rectangle = self.consumer.pan_rectangle(self._pan.target)
            state = axis_pan_view(self._pan, event, rectangle.width(), rectangle.height())
            state, _overview = self._bounded_state(state)
            if state != self._pan.initial_view and not self._pan_recorded:
                self.history.record_before_change(self._pan.initial_view)
                self._pan_recorded = True
            self._apply(state)
            if name == "button_release_event":
                self._pan = None
            return True
        if not managed:
            return False
        if name == "scroll_event":
            self._scroll(event)
        elif name == "button_press_event" and event.button == 1:
            if event.axis_role == "overview_y1":
                center = event.data_for("overview_y1")[0]
                if center is not None:
                    state = self.consumer.capture_view_state()
                    half_span = (state.x[1] - state.x[0]) / 2.0
                    overview = compose_overview_state(
                        True, self.consumer.overview_state.full_x,
                        (center - half_span, center + half_span),
                    )
                    self._record_and_apply(replace(state, x=overview.detail_x))
            elif event.double_click:
                self.navigate("back")
            elif self.pan_enabled:
                self._pan = begin_axis_pan(event, self.consumer.capture_view_state())
                self._pan_recorded = False
        # Native right-drag/axis/wheel handling must not apply a second change.
        return True
