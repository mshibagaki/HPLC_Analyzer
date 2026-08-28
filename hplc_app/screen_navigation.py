"""Backend-neutral screen navigation state and axis-pan calculations."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Optional, Tuple

from .screen_events import ScreenPointerEvent


Limits = Tuple[float, float]
PAN_TARGETS = frozenset(("x", "y1", "y2", "plot", "plot_y1", "plot_y2"))


@dataclass(frozen=True)
class ScreenViewState:
    x: Limits
    y1: Limits
    y2: Optional[Limits] = None
    gradient: Optional[Limits] = None


@dataclass(frozen=True)
class ScreenOverviewState:
    enabled: bool
    full_x: Limits
    detail_x: Limits


def _ordered_finite_limits(limits: Limits) -> Limits:
    try:
        first, second = float(limits[0]), float(limits[1])
    except (IndexError, TypeError, ValueError) as exc:
        raise ValueError("Screen limits require two finite values") from exc
    if not math.isfinite(first) or not math.isfinite(second):
        raise ValueError("Screen limits require two finite values")
    return (first, second) if first <= second else (second, first)


def compose_overview_state(
    enabled: bool,
    full_x: Limits,
    detail_x: Limits,
) -> ScreenOverviewState:
    full_left, full_right = _ordered_finite_limits(full_x)
    detail_left, detail_right = _ordered_finite_limits(detail_x)
    full_span = full_right - full_left
    detail_span = detail_right - detail_left
    if full_span <= 0.0 or detail_span >= full_span:
        detail_left, detail_right = full_left, full_right
    else:
        if detail_left < full_left:
            detail_right += full_left - detail_left
            detail_left = full_left
        if detail_right > full_right:
            detail_left -= detail_right - full_right
            detail_right = full_right
    return ScreenOverviewState(
        enabled=bool(enabled),
        full_x=(full_left, full_right),
        detail_x=(detail_left, detail_right),
    )


class ScreenViewHistory:
    def __init__(self, max_entries: int = 50):
        self.max_entries = max(2, int(max_entries))
        self._entries = []
        self._position = -1

    @property
    def count(self):
        return len(self._entries)

    @property
    def position(self):
        return self._position

    def clear(self):
        self._entries = []
        self._position = -1

    def _trim(self):
        if len(self._entries) <= self.max_entries:
            return
        overflow = len(self._entries) - self.max_entries
        self._entries = self._entries[overflow:]
        self._position -= overflow

    def ensure_home(self, state: ScreenViewState):
        if not self._entries:
            self._entries = [state]
            self._position = 0

    def record_before_change(self, state: ScreenViewState):
        self.ensure_home(state)
        self._entries = self._entries[: self._position + 1]
        if self._entries[self._position] != state:
            self._entries.append(state)
            self._position += 1
        self._trim()

    def capabilities(self, current: ScreenViewState):
        self.ensure_home(current)
        changed = self._entries[self._position] != current
        return {
            "back": changed or self._position > 0,
            "forward": not changed and self._position < len(self._entries) - 1,
        }

    def navigate(self, command: str, current: ScreenViewState):
        if command not in ("home", "back", "forward"):
            return None
        self.ensure_home(current)
        if self._entries[self._position] != current:
            self._entries = self._entries[: self._position + 1]
            self._entries.append(current)
            self._position += 1
            self._trim()
        if command == "home":
            target = 0
        elif command == "back":
            target = max(0, self._position - 1)
        elif command == "forward":
            target = min(len(self._entries) - 1, self._position + 1)
        if target == self._position:
            return None
        self._position = target
        return self._entries[self._position]


@dataclass(frozen=True)
class ScreenPanSession:
    target: str
    start_x: float
    start_y: float
    initial_view: ScreenViewState


def begin_axis_pan(
    event: ScreenPointerEvent,
    view: ScreenViewState,
) -> Optional[ScreenPanSession]:
    if (
        event.button != 1
        or event.hit_region not in PAN_TARGETS
        or event.canvas_x is None
        or event.canvas_y is None
    ):
        return None
    return ScreenPanSession(
        target=event.hit_region,
        start_x=event.canvas_x,
        start_y=event.canvas_y,
        initial_view=view,
    )


def _shifted_limits(limits: Limits, pixel_delta: float, pixel_span: float):
    if not pixel_span:
        return limits
    lower, upper = limits
    data_delta = float(pixel_delta) * (upper - lower) / float(pixel_span)
    return lower - data_delta, upper - data_delta


def axis_pan_view(
    session: ScreenPanSession,
    event: ScreenPointerEvent,
    canvas_width: float,
    canvas_height: float,
) -> ScreenViewState:
    if event.canvas_x is None or event.canvas_y is None:
        return session.initial_view
    target = session.target
    delta_x = event.canvas_x - session.start_x
    delta_y = event.canvas_y - session.start_y
    initial = session.initial_view
    x_limits = initial.x
    y1_limits = initial.y1
    y2_limits = initial.y2
    if target in ("x", "plot", "plot_y1", "plot_y2"):
        x_limits = _shifted_limits(initial.x, delta_x, canvas_width)
    if target in ("y1", "plot", "plot_y1"):
        y1_limits = _shifted_limits(initial.y1, delta_y, canvas_height)
    if target in ("y2", "plot", "plot_y2") and initial.y2 is not None:
        y2_limits = _shifted_limits(initial.y2, delta_y, canvas_height)
    return ScreenViewState(
        x=x_limits, y1=y1_limits, y2=y2_limits, gradient=initial.gradient
    )
