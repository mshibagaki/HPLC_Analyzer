"""Backend-neutral base scene composition for the interactive plot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Tuple

import numpy as np

from .analysis import baseline_trace, display_values, reference_values_for_display
from .models import Dataset, Project
from .peak_fitting import PeakFitResult, evaluate_fit_profile


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
    line_style: str = "solid"


@dataclass(frozen=True)
class ScreenGradientSpec:
    dataset_id: str
    x_values: np.ndarray
    y_values: np.ndarray
    label: str
    axis_label: str
    visible: bool = True


@dataclass(frozen=True)
class ScreenPeakOverlaySpec:
    dataset_id: str
    peak_id: str
    axis_id: str
    is_selected: bool
    color: str
    show_integration_area: bool
    start_x: float
    end_x: float
    retention_x: Optional[float]
    baseline_x: Optional[np.ndarray]
    baseline_y: Optional[np.ndarray]
    fit_x: Optional[np.ndarray]
    fit_y: Optional[np.ndarray]
    label_x: Optional[float]
    label_y: Optional[float]
    label_text: str
    label_font_family: str
    label_font_size: float
    label_color: str
    fit_label: str = ""
    prepare_integration_area: bool = False
    show_retention_label: bool = True


@dataclass(frozen=True)
class ScreenVerticalMarkerSpec:
    marker_id: str
    axis_id: str
    x_value: float
    selected: bool
    color: str
    line_width: float
    alpha: float
    label_text: str = ""


@dataclass(frozen=True)
class ScreenFractionRegionSpec:
    region_id: str
    start_x: float
    end_x: float
    boundary_values: Tuple[float, ...]
    fill_color: str = "#06b6d4"
    fill_alpha: float = 0.08
    line_color: str = "#0891b2"
    line_width: float = 0.8
    line_alpha: float = 0.75


@dataclass(frozen=True)
class ScreenTextAnnotationSpec:
    annotation_id: str
    axis_id: str
    x_value: float
    y_value: float
    text: str
    font_family: str
    font_size: float
    color: str
    background_color: str
    border_color: str


@dataclass(frozen=True)
class BaseScreenScene:
    traces: Tuple[ScreenTraceSpec, ...]
    gradient: Optional[ScreenGradientSpec]
    peak_overlays: Tuple[ScreenPeakOverlaySpec, ...]
    vertical_markers: Tuple[ScreenVerticalMarkerSpec, ...]
    fraction_regions: Tuple[ScreenFractionRegionSpec, ...]
    text_annotations: Tuple[ScreenTextAnnotationSpec, ...]
    time_candidates: Tuple[float, ...]


MAX_FRACTION_BOUNDARY_LINES = 5000


def fraction_boundary_count(start_x, end_x, interval_min) -> int:
    """Return the number of persisted-range divider lines before capping."""

    start = min(float(start_x), float(end_x))
    end = max(float(start_x), float(end_x))
    interval = float(interval_min)
    if not np.isfinite(interval) or interval <= 0.0:
        return MAX_FRACTION_BOUNDARY_LINES + 1
    return int(np.floor((end - start) / interval + 1.0e-9)) + 1


def _readonly(values) -> np.ndarray:
    result = np.asarray(values, dtype=float)
    result.setflags(write=False)
    return result


def _fit_curve(dataset, fitted_peak, baseline_peak, unit):
    if not fitted_peak.fit_model or not fitted_peak.fit_parameters:
        return None, None
    fit_mask = (
        (dataset.time_min >= baseline_peak.start_min)
        & (dataset.time_min <= baseline_peak.end_min)
    )
    fit_time = dataset.time_min[fit_mask]
    if fit_time.size < 3:
        return None, None
    retention_time = fitted_peak.fit_retention_time_min
    if retention_time is None:
        retention_time = fit_time[0]
    fit_result = PeakFitResult(
        model=fitted_peak.fit_model,
        parameters=dict(fitted_peak.fit_parameters),
        retention_time_min=float(retention_time),
        rmse_uv=float(fitted_peak.fit_rmse_uv or 0.0),
        r_squared=float(fitted_peak.fit_r_squared or 0.0),
        aic=float(fitted_peak.fit_aic or 0.0),
        point_count=int(fit_time.size),
    )
    fitted_uv = evaluate_fit_profile(fit_time, fit_result)
    baseline_time, baseline_uv = baseline_trace(dataset, baseline_peak)
    if baseline_time.size == fit_time.size:
        fitted_uv = fitted_uv + baseline_uv
    return (
        _readonly(fit_time + dataset.x_shift_min),
        _readonly(
            reference_values_for_display(dataset, fitted_uv, unit)
            + dataset.offset
        ),
    )


def compose_base_screen_scene(
    project: Project,
    selected_dataset_id: str = "",
    selected_dataset_ids: Sequence[str] = (),
    selected_peak_ids: Sequence[str] = (),
    selected_vertical_marker_id: str = "",
    color_resolver: Optional[Callable[[Dataset, int], str]] = None,
    selected_vertical_marker_ids: Sequence[str] = (),
    include_hidden_display_items: bool = False,
) -> BaseScreenScene:
    """Compose visible base traces and B% data without creating GUI artists."""

    if color_resolver is None:
        raise ValueError("A backend-neutral color resolver is required")
    traces = []
    selected_ids = set(selected_dataset_ids)
    selected_peaks = set(selected_peak_ids)
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
                line_style=dataset.line_style,
            )
        )

    trace_by_id = {trace.dataset_id: trace for trace in traces}
    peak_overlays = []
    for dataset in project.datasets:
        trace = trace_by_id.get(dataset.id)
        if trace is None or dataset.id not in selected_ids:
            continue
        if not (
            include_hidden_display_items
            or project.method.show_integration_areas
            or project.method.show_retention_labels
            or any(peak.fit_model for peak in dataset.peaks)
            or bool(dataset.fitted_peaks)
        ):
            continue
        fitted_parent_ids = {
            peak.parent_peak_id for peak in dataset.fitted_peaks
            if peak.parent_peak_id
        }
        parent_numbers = {
            peak.id: index for index, peak in enumerate(dataset.peaks, start=1)
        }
        for peak in dataset.peaks:
            is_selected = peak.id in selected_peaks
            color = "#f59e0b" if is_selected else trace.color
            baseline_x = baseline_y = None
            if (
                project.method.show_integration_areas
                or include_hidden_display_items
            ):
                baseline_time, baseline_uv = baseline_trace(dataset, peak)
                if baseline_time.size:
                    baseline_x = _readonly(baseline_time + dataset.x_shift_min)
                    baseline_y = _readonly(
                        reference_values_for_display(dataset, baseline_uv, unit)
                        + dataset.offset
                    )

            fit_x = fit_y = None
            fit_label = ""
            if peak.id not in fitted_parent_ids:
                fit_x, fit_y = _fit_curve(dataset, peak, peak, unit)
                if fit_x is not None:
                    fit_label = "%s — Fit %s (#%d)" % (
                        project.legend_label_for(dataset),
                        peak.fit_model.upper(),
                        parent_numbers[peak.id],
                    )

            retention_x = (
                float(peak.retention_time_min + dataset.x_shift_min)
                if peak.retention_time_min is not None
                else None
            )
            label_x = label_y = None
            label_text = ""
            if (
                (
                    project.method.show_retention_labels
                    or include_hidden_display_items
                )
                and retention_x is not None
            ):
                label_x = retention_x
                label_y = float(
                    np.interp(retention_x, trace.x_values, trace.y_values)
                )
                label_text = "%.2f" % retention_x
            peak_overlays.append(
                ScreenPeakOverlaySpec(
                    dataset_id=dataset.id,
                    peak_id=peak.id,
                    axis_id=trace.axis_id,
                    is_selected=is_selected,
                    color=color,
                    show_integration_area=bool(
                        project.method.show_integration_areas
                    ),
                    start_x=float(peak.start_min + dataset.x_shift_min),
                    end_x=float(peak.end_min + dataset.x_shift_min),
                    retention_x=retention_x,
                    baseline_x=baseline_x,
                    baseline_y=baseline_y,
                    fit_x=fit_x,
                    fit_y=fit_y,
                    label_x=label_x,
                    label_y=label_y,
                    label_text=label_text,
                    label_font_family=str(
                        project.method.retention_label_font_family or ""
                    ),
                    label_font_size=float(
                        project.method.retention_label_font_size
                    ),
                    label_color=(
                        project.method.retention_label_color or "#000000"
                    ),
                    fit_label=fit_label,
                    prepare_integration_area=include_hidden_display_items,
                    show_retention_label=bool(
                        project.method.show_retention_labels
                    ),
                )
            )

        fitted_rows = [
            peak for peak in dataset.display_peaks() if peak.is_fitted
        ]
        for fitted_number, fitted_peak in enumerate(fitted_rows, start=1):
            fit_x, fit_y = _fit_curve(
                dataset, fitted_peak, fitted_peak, unit
            )
            if fit_x is None:
                continue
            parent_number = parent_numbers.get(fitted_peak.parent_peak_id)
            parent_label = (
                "#%d" % parent_number
                if parent_number is not None else "orphan"
            )
            peak_overlays.append(
                ScreenPeakOverlaySpec(
                    dataset_id=dataset.id,
                    peak_id=fitted_peak.id,
                    axis_id=trace.axis_id,
                    is_selected=fitted_peak.id in selected_peaks,
                    color=(
                        "#f59e0b"
                        if fitted_peak.id in selected_peaks else trace.color
                    ),
                    show_integration_area=False,
                    start_x=float(fitted_peak.start_min + dataset.x_shift_min),
                    end_x=float(fitted_peak.end_min + dataset.x_shift_min),
                    retention_x=None,
                    baseline_x=None,
                    baseline_y=None,
                    fit_x=fit_x,
                    fit_y=fit_y,
                    label_x=None,
                    label_y=None,
                    label_text="",
                    label_font_family="",
                    label_font_size=float(
                        project.method.retention_label_font_size
                    ),
                    label_color=(
                        project.method.retention_label_color or "#000000"
                    ),
                    fit_label="%s — F%d Fit %s (%s)" % (
                        project.legend_label_for(dataset),
                        fitted_number,
                        fitted_peak.fit_model.upper(),
                        parent_label,
                    ),
                )
            )

    gradient = None
    if (
        (project.method.show_gradient_b or include_hidden_display_items)
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
            visible=bool(project.method.show_gradient_b),
        )

    selected_marker_ids = set(selected_vertical_marker_ids)
    if selected_vertical_marker_id:
        selected_marker_ids.add(selected_vertical_marker_id)
    vertical_markers = tuple(
        ScreenVerticalMarkerSpec(
            marker_id=marker.id,
            axis_id="y2" if marker.y_axis == 2 else "y1",
            x_value=float(marker.x_min),
            selected=marker.id in selected_marker_ids,
            color=(
                "#f59e0b"
                if marker.id in selected_marker_ids
                else (marker.color or "#7c3aed")
            ),
            line_width=2.0 if marker.id in selected_marker_ids else 1.15,
            alpha=0.95 if marker.id in selected_marker_ids else 0.8,
            label_text="%g min" % float(marker.x_min),
        )
        for marker in project.vertical_markers
    )

    fraction_regions = []
    remaining_fraction_boundaries = MAX_FRACTION_BOUNDARY_LINES
    for region in project.fraction_regions:
        start = min(float(region.start_min), float(region.end_min))
        end = max(float(region.start_min), float(region.end_min))
        interval = max(float(region.interval_min), 0.01)
        count = min(
            fraction_boundary_count(start, end, interval),
            remaining_fraction_boundaries,
        )
        boundaries = []
        for index in range(count):
            value = start + index * interval
            boundaries.append(value)
        remaining_fraction_boundaries -= count
        fraction_regions.append(
            ScreenFractionRegionSpec(
                region_id=region.id,
                start_x=start,
                end_x=end,
                boundary_values=tuple(boundaries),
            )
        )

    datasets = {dataset.id: dataset for dataset in project.datasets}
    text_annotations = []
    for annotation in project.annotations:
        if not annotation.text.strip():
            continue
        dataset = datasets.get(annotation.dataset_id)
        if dataset is not None and not dataset.visible:
            continue
        text_annotations.append(
            ScreenTextAnnotationSpec(
                annotation_id=annotation.id,
                axis_id="y2" if annotation.y_axis == 2 else "y1",
                x_value=float(annotation.x_min),
                y_value=float(annotation.y_value),
                text=annotation.text,
                font_family=annotation.font_family or "Arial",
                font_size=float(annotation.font_size),
                color=annotation.color or "#000000",
                background_color=annotation.background_color or "#ffffff",
                border_color=annotation.border_color or "#6b7280",
            )
        )
    return BaseScreenScene(
        traces=tuple(traces),
        gradient=gradient,
        peak_overlays=tuple(peak_overlays),
        vertical_markers=vertical_markers,
        fraction_regions=tuple(fraction_regions),
        text_annotations=tuple(text_annotations),
        time_candidates=tuple(time_candidates),
    )
