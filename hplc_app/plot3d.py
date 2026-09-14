"""Read-only Matplotlib 3D chromatogram figure construction."""

from __future__ import annotations

from dataclasses import dataclass
import itertools
from typing import Optional, Sequence, Tuple

import numpy as np
from matplotlib import colormaps
from matplotlib.backends.backend_agg import RendererAgg
from matplotlib.figure import Figure
from matplotlib.ticker import MultipleLocator
from matplotlib.transforms import Bbox
from mpl_toolkits.mplot3d import Axes3D as _Axes3D
from mpl_toolkits.mplot3d import proj3d
from mpl_toolkits.mplot3d.art3d import Line3DCollection

from .analysis import display_values
from .models import AnalysisMethod, Dataset
from .rendering import default_trace_color


# Below Line3D's default zorder of 2, so every trace draws over the grid.
GRID_ZORDER = 1.0

# Series-axis gap kept between the nearest trace and the retention time axis.
NEAR_SERIES_PADDING = 0.35

# Blank share of the figure kept on each side when the box is fitted.  The
# fit treats label sizes as independent of the zoom, which measured up to
# 1.2 points optimistic, so this sits above the roughly 2 % gap wanted.
FIT_MARGIN = 0.035
# Bounds for the fitted box zoom.
MIN_BOX_ZOOM = 0.3
MAX_BOX_ZOOM = 1.6

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
    axis_label_font_size: float = 10.0
    tick_label_font_size: float = 9.0
    grid_xy: bool = True
    grid_xz: bool = False
    grid_yz: bool = False
    show_x_label: bool = True
    show_y_label: bool = True
    show_z_label: bool = True
    show_x_tick_labels: bool = True
    show_y_tick_labels: bool = True
    show_z_tick_labels: bool = True


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
        or default_trace_color(dataset.y_axis, index)
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


def _ticks_inside(values, limits):
    low, high = sorted((float(limits[0]), float(limits[1])))
    return [
        float(value)
        for value in values
        if np.isfinite(value) and low <= float(value) <= high
    ]


def _far_limits(axis) -> Tuple[float, float, float]:
    """Return the x, y and z limits on the side facing away from the viewer.

    Matplotlib places the eye along (cos e cos a, cos e sin a, sin e) from the
    box centre, so each far side is the limit opposite that direction's sign.
    Grid planes drawn there stay behind the traces, like Matplotlib's panes.
    """

    elevation = np.deg2rad(float(axis.elev))
    azimuth = np.deg2rad(float(axis.azim))
    eye = (
        np.cos(elevation) * np.cos(azimuth),
        np.cos(elevation) * np.sin(azimuth),
        np.sin(elevation),
    )
    limits = (axis.get_xlim(), axis.get_ylim(), axis.get_zlim())
    return tuple(
        float(min(pair)) if component >= 0 else float(max(pair))
        for component, pair in zip(eye, limits)
    )


def _fill_subplot_area(axis) -> None:
    """Let the 3D axes use its whole subplot rectangle instead of a square.

    ``Axes3D.apply_aspect`` shrinks every 3D axes to a square inside its
    subplot area, which in a landscape output figure left about 45 % of the
    width empty beside the box.  Keeping the full rectangle stretches the
    projection horizontally to fill the figure without rewriting the
    X / Y / Z aspect the user chose.  mplot3d re-applies the aspect on every
    draw, so the override is installed on the axes rather than applied once.
    """

    def apply_aspect(position=None):
        if position is None:
            position = axis.get_position(original=True)
        # The same private setter mplot3d's own apply_aspect uses; the public
        # set_position would also drop the axes from layout calculations.
        axis._set_position(position, "active")

    axis.apply_aspect = apply_aspect


def _fit_box_zoom(figure: Figure, axis, aspect) -> float:
    """Scale the box so the plot fills the figure without a label leaving it.

    One off-screen draw measures the projected box and how far the tick and
    axis labels stand out beyond it.  The box grows with the zoom about the
    axes centre while the labels keep their size, so each figure edge gives
    the zoom at which the content would just reach it; the smallest wins.
    A fixed zoom filled the default view but pushed labels off the figure
    once the X aspect was stretched, and mplot3d's own layout already cut
    off views from below and tall Z aspects.
    """

    axis.set_box_aspect(aspect)
    width, height = (max(1, int(round(value))) for value in figure.bbox.size)
    renderer = RendererAgg(width, height, figure.dpi)
    # The projection matrix and the tick label positions only exist after a
    # draw; measuring before one returns meaningless extents.
    figure.draw(renderer)
    corners = itertools.product(axis.get_xlim(), axis.get_ylim(), axis.get_zlim())
    xs, ys, zs = (np.array(values, dtype=float) for values in zip(*corners))
    projected_x, projected_y, _ = proj3d.proj_transform(xs, ys, zs, axis.M)
    points = axis.transData.transform(np.column_stack([projected_x, projected_y]))
    box = Bbox.from_extents(
        points[:, 0].min(), points[:, 1].min(), points[:, 0].max(), points[:, 1].max()
    )
    # The axes' own tight box includes its empty rectangle, so the labels are
    # taken from the three axis artists instead.
    extents = [box] + [
        extent
        for extent in (
            item.get_tightbbox(renderer)
            for item in (axis.xaxis, axis.yaxis, axis.zaxis)
        )
        if extent is not None
    ]
    content = Bbox.union(extents)
    frame = figure.bbox
    centre_x = axis.bbox.x0 + axis.bbox.width / 2.0
    centre_y = axis.bbox.y0 + axis.bbox.height / 2.0
    gap_x = frame.width * FIT_MARGIN
    gap_y = frame.height * FIT_MARGIN
    scales = []
    for box_edge, content_edge, limit, centre in (
        (box.x1, content.x1, frame.x1 - gap_x, centre_x),
        (box.x0, content.x0, frame.x0 + gap_x, centre_x),
        (box.y1, content.y1, frame.y1 - gap_y, centre_y),
        (box.y0, content.y0, frame.y0 + gap_y, centre_y),
    ):
        reach = box_edge - centre
        if abs(reach) > 1.0e-6:
            scales.append((limit - (content_edge - box_edge) - centre) / reach)
    zoom = float(np.clip(min(scales), MIN_BOX_ZOOM, MAX_BOX_ZOOM)) if scales else 1.0
    axis.set_box_aspect(aspect, zoom=zoom)
    return zoom


def _pad_near_series(axis) -> None:
    """Hold the series range away from the viewer's side of the box.

    The far side stays flush with the rearmost trace so the grid and the
    intensity axis share its plane; the near side gains padding so the
    closest trace does not lie along the retention time axis.
    """

    low, high = axis.get_ylim()
    _, far_y, _ = _far_limits(axis)
    if far_y >= high:
        axis.set_ylim(low - NEAR_SERIES_PADDING, high)
    else:
        axis.set_ylim(low, high + NEAR_SERIES_PADDING)


def _add_grid_plane(axis, plane: str) -> None:
    """Draw one selected 3D grid plane using only public Matplotlib APIs."""

    x_min, x_max = axis.get_xlim()
    y_min, y_max = axis.get_ylim()
    z_min, z_max = axis.get_zlim()
    far_x, far_y, far_z = _far_limits(axis)
    x_ticks = _ticks_inside(axis.get_xticks(), (x_min, x_max))
    y_ticks = _ticks_inside(axis.get_yticks(), (y_min, y_max))
    z_ticks = _ticks_inside(axis.get_zticks(), (z_min, z_max))
    if plane == "xy":
        segments = [
            ((value, y_min, far_z), (value, y_max, far_z)) for value in x_ticks
        ] + [
            ((x_min, value, far_z), (x_max, value, far_z)) for value in y_ticks
        ]
    elif plane == "xz":
        segments = [
            ((value, far_y, z_min), (value, far_y, z_max)) for value in x_ticks
        ] + [
            ((x_min, far_y, value), (x_max, far_y, value)) for value in z_ticks
        ]
    elif plane == "yz":
        segments = [
            ((far_x, value, z_min), (far_x, value, z_max)) for value in y_ticks
        ] + [
            ((far_x, y_min, value), (far_x, y_max, value)) for value in z_ticks
        ]
    else:
        raise ValueError("Unknown 3D grid plane: %s" % plane)
    if not segments:
        return
    collection = Line3DCollection(
        segments,
        colors="#cbd5e1",
        linewidths=0.65,
        alpha=0.8,
    )
    collection.set_gid("hplc-grid-%s" % plane)
    collection.set_zorder(GRID_ZORDER)
    axis.add_collection3d(collection)


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
    if options.axis_label_font_size <= 0 or options.tick_label_font_size <= 0:
        raise ValueError("3D font sizes must be positive")

    target = figure or Figure(figsize=(8.0, 8.0))
    target.clear()
    target.subplots_adjust(left=0.04, right=0.92, bottom=0.08, top=0.97)
    # Referencing Axes3D explicitly also keeps the projection registered and
    # discoverable in both PyInstaller targets.
    axis = target.add_subplot(111, projection=_Axes3D.name)
    # With the default computed order, mplot3d lifts every collection above
    # the axis zorder + 1 (2.5), which put the grid planes over the Line3D
    # traces (zorder 2).  Fixed zorders keep the grid at the back instead.
    axis.computed_zorder = False
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
    # Padding on the near side only.  The far wall then lies on the rearmost
    # chromatogram, so the retention time x intensity grid and the intensity
    # axis share that trace's plane, while the nearest trace keeps clear of
    # the retention time axis instead of running along it.  Which side is
    # near depends on the view, so this is applied after view_init below.
    axis.set_ylim(
        (-NEAR_SERIES_PADDING, NEAR_SERIES_PADDING)
        if len(datasets) < 2
        else (0.0, float(len(datasets) - 1))
    )
    if options.z_min is not None:
        axis.set_zlim(float(options.z_min), float(options.z_max))
    axis.set_yticks(range(len(datasets)))
    axis.set_yticklabels(
        [dataset.label or dataset.short_label or dataset.run_id for dataset in datasets]
    )
    axis.xaxis.set_major_locator(MultipleLocator(options.x_tick_interval))
    axis.zaxis.set_major_locator(MultipleLocator(options.z_tick_interval))
    axis.view_init(elev=options.elevation_deg, azim=options.azimuth_deg)
    if len(datasets) > 1:
        _pad_near_series(axis)
    axis.set_box_aspect(aspect)
    _fill_subplot_area(axis)
    # Matplotlib's native 3D grid crosses multiple planes, so selected planes
    # are drawn explicitly while the native all-or-nothing grid stays off.
    axis.grid(False)
    # Matplotlib 3.10's add_collection3d autoscales by default, so each grid
    # plane would widen an automatic Z range after the plane was placed at
    # the old one, leaving the grid inside the box instead of on its walls.
    # Pin the range the traces produced first (3.7 never autoscaled here).
    axis.set_zlim(*axis.get_zlim())
    for enabled, plane in (
        (options.grid_xy, "xy"),
        (options.grid_xz, "xz"),
        (options.grid_yz, "yz"),
    ):
        if enabled:
            _add_grid_plane(axis, plane)
    for item in (axis.xaxis, axis.yaxis, axis.zaxis):
        item.pane.set_visible(False)
        item.line.set_color("#000000")
        item.line.set_linewidth(options.axis_line_width)

    axis_color = method.axis_label_color or "#000000"
    tick_color = method.tick_label_color or "#000000"
    for label in (axis.xaxis.label, axis.yaxis.label, axis.zaxis.label):
        label.set_fontfamily(method.axis_label_font_family)
        label.set_fontsize(options.axis_label_font_size)
        label.set_color(axis_color)
    for label, visible in (
        (axis.xaxis.label, options.show_x_label),
        (axis.yaxis.label, options.show_y_label),
        (axis.zaxis.label, options.show_z_label),
    ):
        label.set_visible(bool(visible))
    axis.tick_params(axis="both", labelsize=options.tick_label_font_size, colors=tick_color)
    for labels, visible in (
        (axis.get_xticklabels(), options.show_x_tick_labels),
        (axis.get_yticklabels(), options.show_y_tick_labels),
        (axis.get_zticklabels(), options.show_z_tick_labels),
    ):
        for label in labels:
            label.set_visible(bool(visible))
    for label in axis.get_xticklabels() + axis.get_yticklabels() + axis.get_zticklabels():
        label.set_fontfamily(method.tick_label_font_family)
        label.set_color(tick_color)
    # Last, once every font, label and visibility choice is in place, since
    # all of them change how far the labels stand out beyond the box.
    _fit_box_zoom(target, axis, aspect)
    return target
