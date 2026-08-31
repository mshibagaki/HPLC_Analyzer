"""Read-only Matplotlib 3D chromatogram figure construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import numpy as np
from matplotlib import colormaps
from matplotlib.figure import Figure
from matplotlib.ticker import MultipleLocator
from mpl_toolkits.mplot3d import Axes3D as _Axes3D

from .analysis import display_values
from .models import AnalysisMethod, Dataset
from .rendering import default_trace_color


TRACE_FALLBACK_COLORS = (
    "#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e",
    "#17becf", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22",
)

# Plotly has no exact Matplotlib equivalents for the final three maps.  The
# aliases retain their broad visual character without adding a dependency:
# Portland is diverging blue/red, Picnic is rainbow-like, and Electric is a
# high-contrast dark-to-bright sequential scale.
COLORMAP_ALIASES = {
    "Blues": "Blues",
    "Viridis": "viridis",
    "Cividis": "cividis",
    "Inferno": "inferno",
    "Magma": "magma",
    "Plasma": "plasma",
    "Turbo": "turbo",
    "Jet": "jet",
    "Rainbow": "rainbow",
    "Portland": "coolwarm",
    "Picnic": "Spectral",
    "Electric": "inferno",
}


@dataclass(frozen=True)
class ThreeDPlotOptions:
    y_axis_title: str = "Series"
    z_min: Optional[float] = None
    z_max: Optional[float] = None
    elevation_deg: float = 20.0
    azimuth_deg: float = -65.0
    aspect_x: float = 1.0
    aspect_y: float = 1.0
    aspect_z: float = 1.0
    x_tick_interval: float = 5.0
    z_tick_interval: float = 5.0
    color_mode: str = "trace"
    colormap: str = "Blues"
    density_percent: int = 100
    axis_line_width: float = 4.0
    show_grid: bool = False


def gradient_colors(name: str, density_percent: int, count: int):
    """Return colors from the dense end of a mapped continuous scale."""

    if count <= 0:
        return []
    mapped = COLORMAP_ALIASES.get(name)
    if mapped is None:
        raise ValueError("Unknown 3D colormap: %s" % name)
    density = min(100, max(10, int(density_percent))) / 100.0
    start = 1.0 - density
    positions = start + np.arange(count, dtype=float) * density / count
    cmap = colormaps[mapped]
    return [cmap(float(position)) for position in positions]


def suggest_z_tick_interval(
    datasets: Sequence[Dataset],
    method: AnalysisMethod,
    x_limits: Tuple[float, float],
    preferred: float = 5.0,
) -> float:
    """Keep the reference default unless current units would create excessive ticks."""

    left, right = sorted((float(x_limits[0]), float(x_limits[1])))
    visible = []
    for dataset in datasets:
        time = dataset.time_min + float(dataset.x_shift_min)
        mask = (time >= left) & (time <= right)
        if np.any(mask):
            visible.append(display_values(dataset, method.display_unit)[mask])
    if not visible:
        return float(preferred)
    low = min(float(np.nanmin(values)) for values in visible)
    high = max(float(np.nanmax(values)) for values in visible)
    span = high - low
    if not np.isfinite(span) or span <= float(preferred) * 20.0:
        return float(preferred)
    rough = span / 20.0
    magnitude = 10.0 ** np.floor(np.log10(rough))
    for multiplier in (1.0, 2.0, 5.0, 10.0):
        candidate = multiplier * magnitude
        if candidate >= rough:
            return float(candidate)
    return float(preferred)


def _trace_colors(datasets: Sequence[Dataset]):
    return [
        dataset.color
        or default_trace_color(dataset.measurement.wavelength_nm, index)
        or TRACE_FALLBACK_COLORS[index % len(TRACE_FALLBACK_COLORS)]
        for index, dataset in enumerate(datasets)
    ]


def _axis_labels(method: AnalysisMethod) -> Tuple[str, str]:
    z_defaults = {
        "uV": "Intensity (µV)",
        "mAU": "Absorbance (mAU)",
        "AU": "Absorbance (AU)",
        "normalized": "Normalized intensity",
    }
    return (
        method.x_axis_label.strip() or "Retention time (min)",
        method.y_axis_1_label.strip()
        or z_defaults.get(method.display_unit, "Intensity"),
    )


def build_3d_chromatogram_figure(
    datasets: Sequence[Dataset],
    method: AnalysisMethod,
    x_limits: Tuple[float, float],
    options: ThreeDPlotOptions,
    figure: Optional[Figure] = None,
) -> Figure:
    """Build a full-data 3D figure without mutating datasets or the Method."""

    if not datasets:
        raise ValueError("At least one chromatogram is required")
    left, right = sorted((float(x_limits[0]), float(x_limits[1])))
    if not np.isfinite((left, right)).all() or left == right:
        raise ValueError("The X range must contain two finite values")
    if (options.z_min is None) != (options.z_max is None):
        raise ValueError("Specify both Z limits or leave both automatic")
    if options.z_min is not None and float(options.z_min) >= float(options.z_max):
        raise ValueError("The minimum Z value must be below the maximum")
    aspect = (options.aspect_x, options.aspect_y, options.aspect_z)
    if any(not np.isfinite(value) or value <= 0 for value in aspect):
        raise ValueError("All 3D aspect values must be positive")
    if options.x_tick_interval <= 0 or options.z_tick_interval <= 0:
        raise ValueError("3D tick intervals must be positive")

    target = figure or Figure(figsize=(8.0, 8.0))
    target.clear()
    target.subplots_adjust(left=0.04, right=0.92, bottom=0.08, top=0.97)
    # Referencing Axes3D explicitly also keeps the projection registered and
    # discoverable in both PyInstaller targets.
    axis = target.add_subplot(111, projection=_Axes3D.name)
    if options.color_mode not in ("trace", "gradient"):
        raise ValueError("Unknown 3D color mode: %s" % options.color_mode)
    colors = (
        _trace_colors(datasets)
        if options.color_mode == "trace"
        else gradient_colors(options.colormap, options.density_percent, len(datasets))
    )

    for index, (dataset, color) in enumerate(zip(datasets, colors)):
        time = dataset.time_min + float(dataset.x_shift_min)
        mask = (time >= left) & (time <= right)
        if not np.any(mask):
            continue
        intensity = display_values(dataset, method.display_unit)
        axis.plot(
            time[mask],
            np.full(int(np.count_nonzero(mask)), float(index)),
            intensity[mask],
            color=color,
            linewidth=method.line_width,
        )

    x_label, z_label = _axis_labels(method)
    axis.set_xlabel(x_label)
    axis.set_ylabel(options.y_axis_title)
    axis.set_zlabel(z_label)
    axis.set_xlim(left, right)
    axis.set_ylim(-0.35, max(0.35, len(datasets) - 0.65))
    if options.z_min is not None:
        axis.set_zlim(float(options.z_min), float(options.z_max))
    axis.set_yticks(range(len(datasets)))
    axis.set_yticklabels(
        [dataset.label or dataset.short_label or dataset.run_id for dataset in datasets]
    )
    axis.xaxis.set_major_locator(MultipleLocator(options.x_tick_interval))
    axis.zaxis.set_major_locator(MultipleLocator(options.z_tick_interval))
    axis.view_init(elev=options.elevation_deg, azim=options.azimuth_deg)
    axis.set_box_aspect(aspect)
    # Grid lines only; the panes stay hidden either way.
    axis.grid(bool(options.show_grid))
    for item in (axis.xaxis, axis.yaxis, axis.zaxis):
        item.pane.set_visible(False)
        item.line.set_color("#000000")
        item.line.set_linewidth(options.axis_line_width)

    axis_color = method.axis_label_color or "#000000"
    tick_color = method.tick_label_color or "#000000"
    for label in (axis.xaxis.label, axis.yaxis.label, axis.zaxis.label):
        label.set_fontfamily(method.axis_label_font_family)
        label.set_fontsize(method.axis_label_font_size)
        label.set_color(axis_color)
    axis.tick_params(axis="both", labelsize=method.tick_label_font_size, colors=tick_color)
    for label in axis.get_xticklabels() + axis.get_yticklabels() + axis.get_zticklabels():
        label.set_fontfamily(method.tick_label_font_family)
        label.set_color(tick_color)
    return target
