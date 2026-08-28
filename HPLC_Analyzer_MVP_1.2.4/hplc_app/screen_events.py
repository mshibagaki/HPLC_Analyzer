"""Backend-neutral pointer event contract and current-canvas adapter."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Optional, Tuple


@dataclass(frozen=True)
class ScreenPointerEvent:
    button: object = None
    axis_role: str = "outside"
    hit_region: str = ""
    canvas_x: Optional[float] = None
    canvas_y: Optional[float] = None
    data_coordinates: Tuple[
        Tuple[str, Optional[float], Optional[float]], ...
    ] = ()
    double_click: bool = False
    key: str = ""

    def data_for(self, axis_role: str):
        for role, x_value, y_value in self.data_coordinates:
            if role == axis_role:
                return x_value, y_value
        return None, None


def _finite_value(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _finite_pair(values):
    try:
        x_value = _finite_value(values[0])
        y_value = _finite_value(values[1])
    except (IndexError, TypeError):
        return None
    if x_value is None and y_value is None:
        return None
    return x_value, y_value


def normalize_pointer_event(
    event,
    axes_by_role: Mapping[str, object],
    hit_region: str = "",
) -> ScreenPointerEvent:
    """Adapt a canvas event without importing a concrete plotting backend."""

    source_axis = getattr(event, "inaxes", None)
    axis_role = next(
        (
            role
            for role, axis in axes_by_role.items()
            if axis is not None and axis is source_axis
        ),
        "outside",
    )
    raw_x = getattr(event, "x", None)
    raw_y = getattr(event, "y", None)
    canvas_x = canvas_y = None
    try:
        if raw_x is not None and raw_y is not None:
            canvas_x, canvas_y = float(raw_x), float(raw_y)
    except (TypeError, ValueError):
        canvas_x = canvas_y = None

    coordinates = []
    for role, axis in axes_by_role.items():
        if axis is None:
            continue
        pair = None
        if canvas_x is not None and canvas_y is not None:
            try:
                pair = _finite_pair(
                    axis.transData.inverted().transform((canvas_x, canvas_y))
                )
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pair = None
        if pair is None:
            pair = _finite_pair(
                (getattr(event, "xdata", None), getattr(event, "ydata", None))
            )
        if pair is not None:
            coordinates.append((str(role), pair[0], pair[1]))

    button = getattr(event, "button", None)
    if hasattr(button, "value"):
        button = button.value
    return ScreenPointerEvent(
        button=button,
        axis_role=axis_role,
        hit_region=str(hit_region or ""),
        canvas_x=canvas_x,
        canvas_y=canvas_y,
        data_coordinates=tuple(coordinates),
        double_click=bool(getattr(event, "dblclick", False)),
        key=str(getattr(event, "key", "") or ""),
    )
