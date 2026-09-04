"""Screen-only rendering helpers for high-quality and legacy PCs.

The functions in this module never mutate a :class:`Dataset`.  Analysis,
project storage, CSV export, and publication figure export continue to use the
complete arrays; only the interactive Matplotlib line data may be decimated.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import numpy as np

from .models import normalize_line_style


HIGH_QUALITY = "high_quality"
LIGHTWEIGHT = "lightweight"
RENDER_QUALITIES = (HIGH_QUALITY, LIGHTWEIGHT)
WAVELENGTH_COLOR_PALETTES = {
    214: ("#d62728", "#ef4444", "#b91c1c", "#f87171"),
    280: ("#1f77b4", "#2563eb", "#1d4ed8", "#60a5fa"),
}
MATPLOTLIB_LINE_STYLES = {
    "solid": "-",
    "dashed": "--",
    "dotted": ":",
    "dash_dot": "-.",
}
MIN_MANUAL_X_TICK_SPACING_MIN = 0.1
MAX_MANUAL_X_TICK_COUNT = 5000


def manual_x_tick_count(x_span_min, major_spacing_min, minor_spacing_min) -> int:
    """Return a conservative count for both manual X-axis tick levels."""

    try:
        span = abs(float(x_span_min))
        major = float(major_spacing_min)
        minor = float(minor_spacing_min)
    except (TypeError, ValueError, OverflowError):
        return MAX_MANUAL_X_TICK_COUNT + 1
    if (
        not all(math.isfinite(value) for value in (span, major, minor))
        or major < MIN_MANUAL_X_TICK_SPACING_MIN
        or minor < MIN_MANUAL_X_TICK_SPACING_MIN
        or minor >= major
    ):
        return MAX_MANUAL_X_TICK_COUNT + 1
    major_intervals = span / major
    minor_intervals = span / minor
    if (
        not math.isfinite(major_intervals)
        or not math.isfinite(minor_intervals)
    ):
        return MAX_MANUAL_X_TICK_COUNT + 1
    return (
        int(math.ceil(major_intervals))
        + int(math.ceil(minor_intervals))
        + 4
    )


def safe_manual_x_tick_spacing(
    x_span_min, major_spacing_min, minor_spacing_min
) -> Optional[Tuple[float, float]]:
    """Return validated manual spacing, or None to request automatic ticks."""

    count = manual_x_tick_count(
        x_span_min, major_spacing_min, minor_spacing_min
    )
    if count > MAX_MANUAL_X_TICK_COUNT:
        return None
    return float(major_spacing_min), float(minor_spacing_min)


def matplotlib_line_style(value: object) -> str:
    return MATPLOTLIB_LINE_STYLES[normalize_line_style(value)]


@dataclass(frozen=True)
class ScreenRendererCapabilities:
    """Backend-neutral features exposed by an interactive screen surface."""

    backend_id: str
    supports_vector_export: bool = False
    supports_native_snapshot: bool = False
    supports_matplotlib_artists: bool = False


def default_trace_color(wavelength_nm, ordinal: int = 0) -> Optional[str]:
    """Return a wavelength-family default; explicit dataset colors stay authoritative."""

    if wavelength_nm is None:
        return None
    try:
        wavelength = float(wavelength_nm)
    except (TypeError, ValueError):
        return None
    for target, palette in WAVELENGTH_COLOR_PALETTES.items():
        if abs(wavelength - target) <= 0.5:
            return palette[max(0, int(ordinal)) % len(palette)]
    return None


def normalize_render_quality(value: object, default: str = HIGH_QUALITY) -> str:
    """Return a supported rendering-quality identifier."""

    candidate = str(value or "").strip().lower()
    if candidate in RENDER_QUALITIES:
        return candidate
    return default if default in RENDER_QUALITIES else HIGH_QUALITY


def default_render_quality(
    platform: Optional[str] = None,
    windows_version: Optional[Sequence[int]] = None,
) -> str:
    """Choose lightweight only for Windows 7; all newer systems stay high quality.

    Optional arguments make the operating-system decision independently
    testable without pretending that the development host is Windows 7.
    """

    platform_name = sys.platform if platform is None else str(platform)
    version = windows_version
    if platform_name == "win32" and version is None and hasattr(sys, "getwindowsversion"):
        current = sys.getwindowsversion()
        version = (current.major, current.minor, current.build)
    if platform_name == "win32" and version is not None:
        parts = tuple(int(part) for part in version)
        if len(parts) >= 2 and parts[:2] == (6, 1):
            return LIGHTWEIGHT
    return HIGH_QUALITY


def screen_point_budget(
    pixel_width: float,
    overview: bool = False,
    interactive: bool = False,
) -> int:
    """Return a pixel-aware point budget for a lightweight screen line."""

    try:
        width = float(pixel_width)
    except (TypeError, ValueError):
        width = 1000.0
    if not math.isfinite(width) or width <= 0:
        width = 1000.0
    if overview:
        return int(min(2000, max(800, round(width * 1.5))))
    if interactive:
        return int(min(1800, max(800, round(width * 1.5))))
    return int(min(5000, max(2000, round(width * 3.0))))


def _visible_slice(x_values: np.ndarray, limits: Optional[Tuple[float, float]]):
    if limits is None or x_values.size < 2:
        return slice(None)
    left, right = sorted((float(limits[0]), float(limits[1])))
    if not np.isfinite(left) or not np.isfinite(right):
        return slice(None)
    # Shimadzu chromatogram time is ordered.  Include one neighbouring point
    # on each side so clipped lines still meet the plot boundary cleanly.
    start = max(0, int(np.searchsorted(x_values, left, side="left")) - 1)
    stop = min(x_values.size, int(np.searchsorted(x_values, right, side="right")) + 1)
    if stop <= start:
        return slice(None)
    return slice(start, stop)


def minmax_decimate(
    x_values,
    y_values,
    max_points: int,
) -> Tuple[np.ndarray, np.ndarray]:
    """Reduce a line while retaining each bucket's minimum and maximum.

    Unlike selecting every Nth point, this envelope keeps narrow positive and
    negative peaks.  First and last points are also retained.  The result is
    intended only for interactive display.
    """

    x_array = np.asarray(x_values)
    y_array = np.asarray(y_values)
    if x_array.ndim != 1 or y_array.ndim != 1 or x_array.size != y_array.size:
        raise ValueError("x and y must be one-dimensional arrays of equal length")
    count = x_array.size
    limit = max(4, int(max_points))
    if count <= limit:
        return x_array, y_array

    interior_count = count - 2
    bucket_target = max(1, (limit - 2) // 2)
    bucket_size = max(1, int(math.ceil(float(interior_count) / bucket_target)))
    interior = y_array[1:-1]
    bucket_count = int(math.ceil(float(interior.size) / bucket_size))
    padded_size = bucket_count * bucket_size

    # Padding keeps the operation vectorized.  Rows containing real NaNs use
    # their first/last real sample as a safe fallback instead of failing.
    matrix = np.empty(padded_size, dtype=float)
    matrix.fill(np.nan)
    matrix[: interior.size] = np.asarray(interior, dtype=float)
    matrix = matrix.reshape(bucket_count, bucket_size)
    finite = np.isfinite(matrix)
    safe_min = np.where(finite, matrix, np.inf)
    safe_max = np.where(finite, matrix, -np.inf)
    min_positions = np.argmin(safe_min, axis=1)
    max_positions = np.argmax(safe_max, axis=1)
    real_counts = np.minimum(
        bucket_size,
        interior.size - np.arange(bucket_count, dtype=int) * bucket_size,
    )
    empty_rows = ~finite.any(axis=1)
    min_positions[empty_rows] = 0
    max_positions[empty_rows] = np.maximum(0, real_counts[empty_rows] - 1)

    offsets = 1 + np.arange(bucket_count, dtype=int) * bucket_size
    pair_indices = np.column_stack(
        (offsets + min_positions, offsets + max_positions)
    )
    pair_indices.sort(axis=1)
    indices = np.concatenate(([0], pair_indices.ravel(), [count - 1]))
    indices = np.unique(indices[indices < count])
    return x_array[indices], y_array[indices]


def screen_series(
    x_values,
    y_values,
    quality: str,
    pixel_width: float,
    x_limits: Optional[Tuple[float, float]] = None,
    overview: bool = False,
    interactive: bool = False,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return full data for high quality or an envelope for lightweight GUI use."""

    x_array = np.asarray(x_values)
    y_array = np.asarray(y_values)
    if x_array.ndim != 1 or y_array.ndim != 1 or x_array.size != y_array.size:
        raise ValueError("x and y must be one-dimensional arrays of equal length")
    if normalize_render_quality(quality) == HIGH_QUALITY:
        return x_array, y_array
    subset = _visible_slice(x_array, None if overview or interactive else x_limits)
    x_subset = x_array[subset]
    y_subset = y_array[subset]
    budget = screen_point_budget(
        pixel_width,
        overview=overview,
        interactive=interactive,
    )
    return minmax_decimate(x_subset, y_subset, budget)
