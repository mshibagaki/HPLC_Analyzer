"""Backend-neutral screen navigation state and axis-pan calculations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .screen_events import ScreenPointerEvent


Limits = Tuple[float, float]
PAN_TARGETS = frozenset(("x", "y1", "y2", "plot", "plot_y1", "plot_y2"))


@dataclass(frozen=True)
class ScreenViewState:
    x: Limits
    y1: Limits
    y2: Optional[Limits] = None


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
    return ScreenViewState(x=x_limits, y1=y1_limits, y2=y2_limits)
