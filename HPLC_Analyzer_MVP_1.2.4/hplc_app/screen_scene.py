"""Backend-neutral base scene composition for the interactive plot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple

import numpy as np

from .analysis import display_values
from .models import Dataset, Project


@dataclass(frozen=True)
class ScreenTraceSpec:
    dataset_id: str
    dataset_index: int
    axis_id: str
    x_values: np.ndarray
    y_values: np.ndarray
    color: str
    label: str
    line_width: float


@dataclass(frozen=True)
class ScreenGradientSpec:
    dataset_id: str
    x_values: np.ndarray
    y_values: np.ndarray
    label: str
    axis_label: str


@dataclass(frozen=True)
class BaseScreenScene:
    traces: Tuple[ScreenTraceSpec, ...]
    gradient: Optional[ScreenGradientSpec]
    time_candidates: Tuple[float, ...]


def _readonly(values) -> np.ndarray:
    result = np.asarray(values, dtype=float)
    result.setflags(write=False)
    return result


def compose_base_screen_scene(
    project: Project,
    selected_dataset_id: str = "",
    color_resolver: Optional[Callable[[Dataset, int], str]] = None,
) -> BaseScreenScene:
    """Compose visible base traces and B% data without creating GUI artists."""

    if color_resolver is None:
        raise ValueError("A backend-neutral color resolver is required")
    traces = []
    time_candidates = []
    selected = None
    unit = project.method.display_unit
    for index, dataset in enumerate(project.datasets):
        if dataset.id == selected_dataset_id:
            selected = dataset
        if not dataset.visible:
            continue
        if dataset.time_min.size:
            time_candidates.append(float(dataset.time_min[-1] + dataset.x_shift_min))
        if dataset.measurement.gradient:
            time_candidates.append(
                float(max(point.time_min for point in dataset.measurement.gradient))
            )
        try:
            values = display_values(dataset, unit)
        except ValueError:
            continue
        traces.append(
            ScreenTraceSpec(
                dataset_id=dataset.id,
                dataset_index=index,
                axis_id="y2" if dataset.y_axis == 2 else "y1",
                x_values=_readonly(dataset.time_min + dataset.x_shift_min),
                y_values=_readonly(values + dataset.offset),
                color=str(color_resolver(dataset, index)),
                label=project.legend_label_for(dataset),
                line_width=float(project.method.line_width),
            )
        )

    gradient = None
    if (
        project.method.show_gradient_b
        and selected is not None
        and selected.visible
        and selected.measurement.gradient
    ):
        points = sorted(selected.measurement.gradient, key=lambda point: point.time_min)
        label = "%B"
        if project.method.gradient_legend_include_dataset_name:
            label = "%B ({})".format(project.legend_label_for(selected))
        gradient = ScreenGradientSpec(
            dataset_id=selected.id,
            x_values=_readonly([point.time_min for point in points]),
            y_values=_readonly([point.b_pct for point in points]),
            label=label,
            axis_label=(
                project.method.gradient_axis_label.strip()
                or "Mobile phase B (%)"
            ),
        )
    return BaseScreenScene(
        traces=tuple(traces),
        gradient=gradient,
        time_candidates=tuple(time_candidates),
    )
