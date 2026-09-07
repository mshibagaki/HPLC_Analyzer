from __future__ import annotations

from dataclasses import replace
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

from .models import AnalysisMethod, Dataset, GradientPoint, PeakRegion


SECONDS_PER_MINUTE = 60.0


def _trapz(values: np.ndarray, time: np.ndarray) -> float:
    """Integrate on both the Win7 legacy NumPy 1.20.3 and current NumPy."""
    trapezoid = getattr(np, "trapezoid", None)
    return float(trapezoid(values, time) if trapezoid is not None else np.trapz(values, time))


def convert_uv(values_uv: np.ndarray, aux_range_au_per_v: Optional[float], unit: str) -> np.ndarray:
    values = values_uv.astype(float, copy=True)
    if unit == "uV":
        return values
    if unit == "normalized":
        low = float(np.nanmin(values))
        shifted = values - low
        high = float(np.nanmax(shifted))
        return shifted / high if high > 0 else shifted
    if aux_range_au_per_v is None or aux_range_au_per_v <= 0:
        raise ValueError("AU/V is required for absorbance conversion")
    if unit == "mAU":
        return values * float(aux_range_au_per_v) * 1.0e-3
    if unit == "AU":
        return values * float(aux_range_au_per_v) * 1.0e-6
    raise ValueError("Unknown display unit: %s" % unit)


def display_values(dataset: Dataset, unit: str) -> np.ndarray:
    return convert_uv(dataset.intensity_uv, dataset.measurement.aux_range_au_per_v, unit)


def reference_values_for_display(
    dataset: Dataset,
    values_uv: np.ndarray,
    unit: str,
) -> np.ndarray:
    """Convert a baseline using the same normalization reference as its trace."""
    values = values_uv.astype(float, copy=True)
    if unit == "uV":
        return values
    if unit == "mAU":
        aux = dataset.measurement.aux_range_au_per_v
        if aux is None or aux <= 0:
            raise ValueError("AU/V is required for absorbance conversion")
        return values * aux * 1.0e-3
    if unit == "AU":
        aux = dataset.measurement.aux_range_au_per_v
        if aux is None or aux <= 0:
            raise ValueError("AU/V is required for absorbance conversion")
        return values * aux * 1.0e-6
    if unit == "normalized":
        low = float(np.nanmin(dataset.intensity_uv))
        high = float(np.nanmax(dataset.intensity_uv) - low)
        return (values - low) / high if high > 0 else values - low
    raise ValueError("Unknown display unit: %s" % unit)


def _baseline_edge_size(size: int) -> int:
    return max(1, min(max(3, int(round(size * 0.03))), max(1, size // 4)))


def calculate_baseline(raw: np.ndarray, region: PeakRegion) -> Tuple[np.ndarray, float, float]:
    """Return a configurable baseline for one already-cropped integration region."""
    if raw.size < 2:
        raise ValueError("Integration range contains fewer than two data points")
    mode = region.baseline_mode or "linear"
    edge = _baseline_edge_size(raw.size)
    left_edge = float(np.nanmedian(raw[:edge]))
    right_edge = float(np.nanmedian(raw[-edge:]))
    if mode == "linear":
        start_value, end_value = float(raw[0]), float(raw[-1])
    elif mode == "edge_average":
        start_value, end_value = left_edge, right_edge
    elif mode == "constant_start":
        start_value = end_value = left_edge
    elif mode == "zero":
        start_value = end_value = 0.0
    elif mode == "manual":
        start_value = left_edge if region.baseline_start_uv is None else float(region.baseline_start_uv)
        end_value = right_edge if region.baseline_end_uv is None else float(region.baseline_end_uv)
    else:
        raise ValueError("Unknown baseline mode: %s" % mode)
    return np.linspace(start_value, end_value, raw.size), start_value, end_value


def baseline_trace(
    dataset: Dataset,
    region: PeakRegion,
) -> Tuple[np.ndarray, np.ndarray]:
    start, end = sorted((float(region.start_min), float(region.end_min)))
    mask = (dataset.time_min >= start) & (dataset.time_min <= end)
    if int(np.count_nonzero(mask)) < 3:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)
    time = dataset.time_min[mask]
    raw = dataset.intensity_uv[mask].astype(float, copy=True)
    baseline, _start, _end = calculate_baseline(raw, region)
    return time, baseline


def _interpolate_crossing(x0: float, y0: float, x1: float, y1: float, target: float) -> float:
    if y1 == y0:
        return (x0 + x1) / 2.0
    fraction = (target - y0) / (y1 - y0)
    return x0 + fraction * (x1 - x0)


def _fwhm(time: np.ndarray, corrected: np.ndarray, apex_index: int, peak_height: float) -> Optional[float]:
    if peak_height <= 0 or time.size < 3:
        return None
    half = peak_height / 2.0
    left: Optional[float] = None
    right: Optional[float] = None
    for i in range(apex_index, 0, -1):
        if corrected[i - 1] <= half <= corrected[i] or corrected[i - 1] >= half >= corrected[i]:
            left = _interpolate_crossing(time[i - 1], corrected[i - 1], time[i], corrected[i], half)
            break
    for i in range(apex_index, time.size - 1):
        if corrected[i] >= half >= corrected[i + 1] or corrected[i] <= half <= corrected[i + 1]:
            right = _interpolate_crossing(time[i], corrected[i], time[i + 1], corrected[i + 1], half)
            break
    if left is None or right is None or right < left:
        return None
    return right - left


def gradient_at(points: Iterable[GradientPoint], time_min: float) -> Optional[Dict[str, float]]:
    ordered = sorted(points, key=lambda point: point.time_min)
    if not ordered:
        return None
    if time_min <= ordered[0].time_min:
        point = ordered[0]
        return {"A": point.a_pct, "B": point.b_pct, "C": point.c_pct, "D": point.d_pct}
    if time_min >= ordered[-1].time_min:
        point = ordered[-1]
        return {"A": point.a_pct, "B": point.b_pct, "C": point.c_pct, "D": point.d_pct}
    for left, right in zip(ordered[:-1], ordered[1:]):
        if left.time_min <= time_min <= right.time_min:
            span = right.time_min - left.time_min
            fraction = 0.0 if span == 0 else (time_min - left.time_min) / span
            return {
                "A": left.a_pct + fraction * (right.a_pct - left.a_pct),
                "B": left.b_pct + fraction * (right.b_pct - left.b_pct),
                "C": left.c_pct + fraction * (right.c_pct - left.c_pct),
                "D": left.d_pct + fraction * (right.d_pct - left.d_pct),
            }
    return None


def amount_from_area(dataset: Dataset, area_mau_sec: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    if area_mau_sec is None:
        return None, None
    meta = dataset.measurement
    epsilon = meta.epsilon_for_wavelength()
    flow = meta.flow_rate_ml_min
    path_length = meta.cell_path_length_cm
    if epsilon is None or epsilon <= 0 or flow is None or flow <= 0 or path_length is None or path_length <= 0:
        return None, None
    # Area is mAU sec while flow is mL/min, hence the 1/60 conversion.
    # mAU -> AU (1e-3), mL -> L (1e-3), mol -> nmol (1e9).
    amount_nmol = area_mau_sec * flow * 1.0e3 / (
        SECONDS_PER_MINUTE * epsilon * path_length
    )
    amount_ug: Optional[float] = None
    if meta.molecular_weight_g_mol is not None and meta.molecular_weight_g_mol > 0:
        amount_ug = amount_nmol * meta.molecular_weight_g_mol / 1000.0
    return amount_nmol, amount_ug


def _flow_at(dataset: Dataset, time_min: float) -> Optional[float]:
    points = sorted(dataset.measurement.gradient, key=lambda point: point.time_min)
    if points and all(point.flow_ml_min is not None for point in points):
        if time_min <= points[0].time_min:
            return points[0].flow_ml_min
        if time_min >= points[-1].time_min:
            return points[-1].flow_ml_min
        for left, right in zip(points[:-1], points[1:]):
            if left.time_min <= time_min <= right.time_min:
                span = right.time_min - left.time_min
                fraction = 0.0 if span == 0 else (time_min - left.time_min) / span
                return left.flow_ml_min + fraction * (right.flow_ml_min - left.flow_ml_min)
    return dataset.measurement.flow_rate_ml_min


def amount_from_trace(
    dataset: Dataset,
    time_min: np.ndarray,
    corrected_mau: Optional[np.ndarray],
    fallback_area_mau_sec: Optional[float],
) -> Tuple[Optional[float], Optional[float]]:
    meta = dataset.measurement
    epsilon = meta.epsilon_for_wavelength()
    path_length = meta.cell_path_length_cm
    if epsilon is None or epsilon <= 0 or path_length is None or path_length <= 0:
        return None, None
    amount_nmol: Optional[float] = None
    if corrected_mau is not None:
        flow_values = np.asarray([_flow_at(dataset, value) or np.nan for value in time_min], dtype=float)
        if np.all(np.isfinite(flow_values)) and np.all(flow_values > 0):
            time_sec = time_min * SECONDS_PER_MINUTE
            flow_values_ml_sec = flow_values / SECONDS_PER_MINUTE
            weighted_area = _trapz(corrected_mau * flow_values_ml_sec, time_sec)
            amount_nmol = weighted_area * 1.0e3 / (epsilon * path_length)
    if amount_nmol is None:
        amount_nmol, _unused = amount_from_area(dataset, fallback_area_mau_sec)
    if amount_nmol is None:
        return None, None
    amount_ug = None
    if meta.molecular_weight_g_mol is not None and meta.molecular_weight_g_mol > 0:
        amount_ug = amount_nmol * meta.molecular_weight_g_mol / 1000.0
    return amount_nmol, amount_ug


def integrate_peak(dataset: Dataset, region: PeakRegion) -> PeakRegion:
    start, end = sorted((float(region.start_min), float(region.end_min)))
    mask = (dataset.time_min >= start) & (dataset.time_min <= end)
    if int(np.count_nonzero(mask)) < 3:
        raise ValueError("Integration range contains fewer than three data points")
    time = dataset.time_min[mask]
    raw = dataset.intensity_uv[mask].astype(float, copy=True)
    baseline, baseline_start, baseline_end = calculate_baseline(raw, region)
    corrected = raw - baseline
    apex_index = int(np.nanargmax(corrected))
    raw_height = float(corrected[apex_index])
    raw_area_min = _trapz(corrected, time)
    raw_area_sec = raw_area_min * SECONDS_PER_MINUTE
    fwhm = _fwhm(time, corrected, apex_index, raw_height)
    aux = dataset.measurement.aux_range_au_per_v
    height_mau = raw_height * aux * 1.0e-3 if aux is not None and aux > 0 else None
    area_mau_min = raw_area_min * aux * 1.0e-3 if aux is not None and aux > 0 else None
    area_mau_sec = (
        area_mau_min * SECONDS_PER_MINUTE if area_mau_min is not None else None
    )
    apex_time = float(time[apex_index])
    # The gradient remains on the acquisition time axis.  A displayed time
    # shift therefore changes the mobile-phase composition associated with the
    # shifted peak position.
    gradient = gradient_at(dataset.measurement.gradient, apex_time + dataset.x_shift_min)
    corrected_mau = corrected * aux * 1.0e-3 if aux is not None and aux > 0 else None
    amount_nmol, amount_ug = amount_from_trace(dataset, time, corrected_mau, area_mau_sec)
    manual_start = region.baseline_start_uv
    manual_end = region.baseline_end_uv
    if region.baseline_mode == "manual":
        manual_start = baseline_start
        manual_end = baseline_end
    return replace(
        region,
        start_min=start,
        end_min=end,
        baseline_start_uv=manual_start,
        baseline_end_uv=manual_end,
        calculated_baseline_start_uv=baseline_start,
        calculated_baseline_end_uv=baseline_end,
        retention_time_min=apex_time,
        raw_height_uv=raw_height,
        raw_area_uv_min=raw_area_min,
        raw_area_uv_sec=raw_area_sec,
        height_mau=height_mau,
        area_mau_min=area_mau_min,
        area_mau_sec=area_mau_sec,
        fwhm_min=fwhm,
        gradient_a_pct=gradient["A"] if gradient else None,
        gradient_b_pct=gradient["B"] if gradient else None,
        gradient_c_pct=gradient["C"] if gradient else None,
        gradient_d_pct=gradient["D"] if gradient else None,
        amount_nmol=amount_nmol,
        amount_ug=amount_ug,
    )


def split_peak_region(
    dataset: Dataset,
    region: PeakRegion,
    split_time_min: float,
) -> Tuple[PeakRegion, PeakRegion]:
    """Split one integrated region while preserving its original straight baseline.

    Both child regions use manual baseline endpoints sampled from the parent's
    calculated baseline.  For the baseline models supported by this MVP this
    keeps the combined corrected area equal to the parent area apart from
    floating-point rounding.
    """
    parent = integrate_peak(dataset, region)
    start, end = parent.start_min, parent.end_min
    mask_indices = np.flatnonzero((dataset.time_min >= start) & (dataset.time_min <= end))
    if mask_indices.size < 5:
        raise ValueError("Integration range is too narrow to split")
    valid_indices = mask_indices[2:-2]
    if valid_indices.size == 0:
        raise ValueError("Integration range is too narrow to split")
    requested = float(split_time_min)
    split_index = int(valid_indices[np.argmin(np.abs(dataset.time_min[valid_indices] - requested))])
    split_time = float(dataset.time_min[split_index])
    if not start < split_time < end:
        raise ValueError("Split point must be inside the selected integration range")

    baseline_start = parent.calculated_baseline_start_uv
    baseline_end = parent.calculated_baseline_end_uv
    if baseline_start is None or baseline_end is None:
        _time, baseline = baseline_trace(dataset, parent)
        if baseline.size < 2:
            raise ValueError("Could not calculate the parent baseline")
        baseline_start, baseline_end = float(baseline[0]), float(baseline[-1])
    fraction = (split_time - start) / (end - start)
    split_baseline = float(baseline_start + fraction * (baseline_end - baseline_start))
    group_id = parent.split_group_id or parent.id
    left = PeakRegion(
        start_min=start,
        end_min=split_time,
        baseline_mode="manual",
        baseline_start_uv=float(baseline_start),
        baseline_end_uv=split_baseline,
        split_group_id=group_id,
        integration_source=parent.integration_source,
        notes=parent.notes,
    )
    right = PeakRegion(
        start_min=split_time,
        end_min=end,
        baseline_mode="manual",
        baseline_start_uv=split_baseline,
        baseline_end_uv=float(baseline_end),
        split_group_id=group_id,
        integration_source=parent.integration_source,
        notes=parent.notes,
    )
    return (
        integrate_peak(dataset, left),
        integrate_peak(dataset, right),
    )


def recalculate_dataset_peaks(dataset: Dataset) -> None:
    recalculated = [integrate_peak(dataset, peak) for peak in dataset.peaks]
    recalculated.sort(
        key=lambda peak: (
            float("inf") if peak.retention_time_min is None else peak.retention_time_min,
            peak.start_min,
            peak.end_min,
        )
    )
    # Saturation-corrected fitted rows are the authoritative area for their
    # clipped parent. Ordinary fitted rows remain display-only estimates and
    # do not enter %Area, preserving their established behavior.
    from .peak_fitting import (
        is_saturation_corrected,
        refresh_saturation_corrected_peak,
    )

    parents = {peak.id: peak for peak in recalculated}
    corrections = {}
    for fitted_peak in dataset.fitted_peaks:
        fitted_peak.area_percent = None
        parent = parents.get(fitted_peak.parent_peak_id)
        if parent is None or not is_saturation_corrected(fitted_peak):
            continue
        try:
            refresh_saturation_corrected_peak(dataset, parent, fitted_peak)
        except (KeyError, TypeError, ValueError):
            # Incomplete legacy/stale fit metadata cannot safely replace a
            # measured integration. Keep the parent authoritative instead.
            continue
        corrections[parent.id] = fitted_peak

    contributors = [
        corrections.get(peak.id, peak) for peak in recalculated
    ]
    total = sum(max(0.0, peak.raw_area_uv_sec or 0.0) for peak in contributors)
    for peak in recalculated:
        contributor = corrections.get(peak.id)
        if contributor is not None:
            peak.area_percent = None
            positive = max(0.0, contributor.raw_area_uv_sec or 0.0)
            contributor.area_percent = positive / total * 100.0 if total > 0 else None
        else:
            positive = max(0.0, peak.raw_area_uv_sec or 0.0)
            peak.area_percent = positive / total * 100.0 if total > 0 else None
    dataset.peaks = recalculated


def _moving_average(values: np.ndarray, window: int) -> np.ndarray:
    window = max(1, int(window))
    if window <= 1 or values.size < 3:
        return values.astype(float, copy=True)
    if window % 2 == 0:
        window += 1
    half = window // 2
    padded = np.pad(values.astype(float), (half, half), mode="edge")
    cumulative = np.cumsum(np.insert(padded, 0, 0.0))
    return (cumulative[window:] - cumulative[:-window]) / float(window)


def _noise_sigma(values: np.ndarray) -> float:
    if values.size < 3:
        return 0.0
    differences = np.diff(values.astype(float))
    median = float(np.nanmedian(differences))
    mad = float(np.nanmedian(np.abs(differences - median)))
    return 1.4826 * mad / np.sqrt(2.0)


def _local_peak_prominence(values: np.ndarray, index: int, radius: int) -> float:
    """Approximate SciPy-style prominence without adding a SciPy dependency.

    Each side stops at the first sample higher than the candidate.  This is
    important on broad gradient humps: a tiny ripple on a rising shoulder must
    not inherit the full prominence of the broad hump beneath it.
    """
    peak_value = float(values[index])
    lower = max(0, index - radius)
    upper = min(values.size - 1, index + radius)
    left = index - 1
    while left > lower and values[left] <= peak_value:
        left -= 1
    left_start = left + 1 if values[left] > peak_value else left
    right = index + 1
    while right < upper and values[right] <= peak_value:
        right += 1
    right_end = right if values[right] <= peak_value else right - 1
    left_minimum = float(np.nanmin(values[left_start : index + 1]))
    right_minimum = float(np.nanmin(values[index : right_end + 1]))
    return peak_value - max(left_minimum, right_minimum)


def detect_peaks(
    dataset: Dataset,
    method: AnalysisMethod,
    time_range: Optional[Tuple[float, float]] = None,
) -> List[PeakRegion]:
    """Return positive peak candidates using noise-aware local prominence.

    The detector intentionally creates editable candidates rather than a final
    result. It uses only NumPy so the Windows 7 build does not need SciPy.
    When ``time_range`` is provided, candidate detection and integration
    boundaries are constrained to that inclusive raw-time interval. Omitting
    the range preserves the established whole-dataset behavior.
    """
    time = np.asarray(dataset.time_min, dtype=float)
    raw = np.asarray(dataset.intensity_uv, dtype=float)
    if time.size < 5 or raw.size != time.size:
        return []
    if time_range is not None:
        start, end = (float(time_range[0]), float(time_range[1]))
        if not (np.isfinite(start) and np.isfinite(end)):
            raise ValueError("Automatic peak-detection range must be finite")
        if start > end:
            start, end = end, start
        selected = (time >= start) & (time <= end)
        if int(np.count_nonzero(selected)) < 5:
            return []
        time = time[selected]
        raw = raw[selected]
    steps = np.diff(time)
    finite_steps = steps[np.isfinite(steps) & (steps > 0)]
    if finite_steps.size == 0:
        return []
    step = float(np.nanmedian(finite_steps))
    smoothing_samples = max(1, int(round(max(0.0, method.auto_peak_smoothing_min) / step)))
    smooth = _moving_average(raw, smoothing_samples)
    maximum_width_samples = max(3, int(round(max(step, method.auto_peak_max_width_min) / step)))
    maximum_width_samples = min(maximum_width_samples, max(3, time.size - 1))
    local_maxima = np.flatnonzero(
        (smooth[1:-1] > smooth[:-2]) & (smooth[1:-1] >= smooth[2:])
    ) + 1
    if local_maxima.size == 0:
        return []
    prominence = np.asarray(
        [
            _local_peak_prominence(smooth, int(index), maximum_width_samples)
            for index in local_maxima
        ],
        dtype=float,
    )
    sigma = _noise_sigma(raw)
    threshold = max(
        float(method.auto_peak_min_prominence_uv),
        max(0.0, float(method.auto_peak_snr_threshold)) * sigma,
    )
    keep = np.isfinite(prominence) & (prominence > 0) & (prominence >= threshold)
    candidates = [
        (int(index), float(value))
        for index, value in zip(local_maxima[keep], prominence[keep])
    ]
    if not candidates:
        return []

    min_width = max(0.0, float(method.auto_peak_min_width_min))
    max_width = max(min_width, float(method.auto_peak_max_width_min))
    min_distance_samples = max(1, int(round(max(0.0, method.auto_peak_min_distance_min) / step)))
    width_filtered = []
    for index, peak_prominence in candidates:
        half_height = smooth[index] - peak_prominence / 2.0
        left = index
        lower_bound = max(0, index - maximum_width_samples)
        while left > lower_bound and smooth[left] > half_height:
            left -= 1
        right = index
        upper_bound = min(time.size - 1, index + maximum_width_samples)
        while right < upper_bound and smooth[right] > half_height:
            right += 1
        width = float(time[right] - time[left])
        if min_width <= width <= max_width:
            width_filtered.append((index, peak_prominence))

    selected = []
    for index, peak_prominence in sorted(width_filtered, key=lambda item: item[1], reverse=True):
        if all(abs(index - other_index) >= min_distance_samples for other_index, _value in selected):
            selected.append((index, peak_prominence))
        if len(selected) >= max(1, int(method.auto_peak_max_count)):
            break
    selected.sort(key=lambda item: item[0])
    if not selected:
        return []

    boundary_fraction = min(0.5, max(0.0, float(method.auto_peak_boundary_percent) / 100.0))
    boundaries = []
    for index, peak_prominence in selected:
        target = smooth[index] - peak_prominence * (1.0 - boundary_fraction)
        left = index
        lower_bound = max(0, index - maximum_width_samples)
        while left > lower_bound and smooth[left] > target:
            left -= 1
        if left == lower_bound and smooth[left] > target:
            left = lower_bound + int(np.argmin(smooth[lower_bound : index + 1]))
        right = index
        upper_bound = min(time.size - 1, index + maximum_width_samples)
        while right < upper_bound and smooth[right] > target:
            right += 1
        if right == upper_bound and smooth[right] > target:
            right = index + int(np.argmin(smooth[index : upper_bound + 1]))
        boundaries.append([index, max(0, left), min(time.size - 1, right)])

    # Overlapping candidates share the valley between their apices.
    for current, following in zip(boundaries[:-1], boundaries[1:]):
        if current[2] > following[1]:
            valley = current[0] + int(np.argmin(smooth[current[0] : following[0] + 1]))
            current[2] = valley
            following[1] = valley

    detected: List[PeakRegion] = []
    for _index, left, right in boundaries:
        if right - left < 2:
            continue
        region = PeakRegion(
            start_min=float(time[left]),
            end_min=float(time[right]),
            baseline_mode=method.baseline_mode,
            integration_source="auto",
        )
        try:
            detected.append(integrate_peak(dataset, region))
        except ValueError:
            continue
    detected.sort(key=lambda peak: peak.retention_time_min or peak.start_min)
    return detected


def validate_gradient(points: Iterable[GradientPoint], tolerance: float = 0.05) -> Tuple[bool, str]:
    ordered = list(points)
    previous: Optional[float] = None
    for index, point in enumerate(ordered, start=1):
        if previous is not None and point.time_min <= previous:
            return False, "Gradient times must be strictly increasing (row %d)" % index
        previous = point.time_min
        values = (point.a_pct, point.b_pct, point.c_pct, point.d_pct)
        if any(value < 0 or value > 100 for value in values):
            return False, "Solvent percentages must be between 0 and 100 (row %d)" % index
        if abs(sum(values) - 100.0) > tolerance:
            return False, "A+B+C+D must equal 100%% (row %d: %.3f%%)" % (index, sum(values))
        if point.flow_ml_min is not None and point.flow_ml_min <= 0:
            return False, "Flow rate must be positive (row %d)" % index
    return True, ""
