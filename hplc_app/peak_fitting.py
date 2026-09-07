"""Dependency-free, non-destructive peak-shape fitting models."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Dict, Optional, Tuple

import numpy as np

from .analysis import _fwhm, _trapz, amount_from_trace, calculate_baseline
from .models import Dataset, PeakRegion


SECONDS_PER_MINUTE = 60.0
GAUSSIAN_FWHM_FACTOR = 2.0 * math.sqrt(2.0 * math.log(2.0))
# A clipped detector writes the same ceiling value for several samples in a row.
# Three consecutive samples inside one part per thousand of the peak height is
# the smallest run that is not simply a rounded apex.
SATURATION_MIN_RUN = 3
SATURATION_TOLERANCE_RATIO = 1.0e-3
# Any smooth apex is flat to within the tolerance over a few samples, so the
# run also has to cover a real part of the peak. A Gaussian apex stays within
# one part per thousand for about 4% of its width at half height; a clipped top
# covers far more, so a tenth of that width separates the two cleanly.
SATURATION_MIN_WIDTH_RATIO = 0.10
LIMITED_FLANK_NOTE = "Saturation correction warning: limited unsaturated flanks."


@dataclass(frozen=True)
class SaturatedSpan:
    """The flat top treated as clipped, in acquisition-time minutes."""

    start_min: float
    end_min: float
    point_count: int


@dataclass(frozen=True)
class PeakFitResult:
    model: str
    parameters: Dict[str, float]
    retention_time_min: float
    rmse_uv: float
    r_squared: float
    aic: float
    point_count: int


_LEGACY_FIT_FIELDS = (
    "fit_model",
    "fit_parameters",
    "fit_retention_time_min",
    "fit_rmse_uv",
    "fit_r_squared",
    "fit_aic",
)


def _model_grid(center: float, sigma: float, tau: float):
    """Uniform grid wide and fine enough for the EMG tail to stop mattering."""

    left = center - 8.0 * sigma
    right = center + 8.0 * sigma + 20.0 * tau
    step = min(sigma, tau) / 50.0
    count = int(min(200001, max(2001, math.ceil((right - left) / step) + 1)))
    return np.linspace(left, right, count)


def fitted_area_uv_min(result: PeakFitResult) -> float:
    """Return the area under the fitted model curve in µV·min.

    A Gaussian uses its closed form, ``amplitude * sigma * sqrt(2*pi)``. The EMG
    profile in this module is normalized to unit height and has no matching
    closed form, so it is integrated with the same trapezoid helper the
    measured integration uses, on a grid wide and fine enough that the tail
    stops contributing. Both are the area of the complete model curve, not of
    the samples inside the integration window, because the point of a fitted
    area is the peak the detector would have recorded.
    """

    parameters = result.parameters
    amplitude = float(parameters["amplitude_uv"])
    sigma = max(float(parameters["sigma_min"]), 1.0e-12)
    if result.model == "gaussian":
        return amplitude * sigma * math.sqrt(2.0 * math.pi)
    if result.model != "emg":
        raise ValueError("Unknown fitted model: %s" % result.model)
    center = float(parameters["center_min"])
    tau = max(float(parameters["tau_min"]), 1.0e-12)
    grid = _model_grid(center, sigma, tau)
    return float(_trapz(evaluate_fit_profile(grid, result), grid))


def saturation_corrected_trace(
    dataset: Dataset,
    region: PeakRegion,
    result: PeakFitResult,
    saturated: SaturatedSpan,
):
    """Return the within-window signal used by a saturation correction.

    Samples outside the clipped interval remain the measured,
    baseline-corrected signal. Only samples inside that interval are replaced
    by the fitted model. This keeps the correction on the same integration
    bounds as every measured peak instead of counting the model's tails beyond
    the selected range.
    """

    start, end = sorted((float(region.start_min), float(region.end_min)))
    window = (dataset.time_min >= start) & (dataset.time_min <= end)
    if int(np.count_nonzero(window)) < 3:
        raise ValueError("Integration range contains fewer than three data points")
    time = np.asarray(dataset.time_min[window], dtype=float)
    raw = np.asarray(dataset.intensity_uv[window], dtype=float)
    baseline, _start, _end = calculate_baseline(raw, region)
    corrected = raw - baseline
    clipped = (time >= saturated.start_min) & (time <= saturated.end_min)
    corrected[clipped] = evaluate_fit_profile(time[clipped], result)
    return time, corrected


def saturation_fit_has_limited_flanks(
    region: PeakRegion, saturated: SaturatedSpan
) -> bool:
    """Flag a correction when either measured flank is shorter than the gap."""

    start, end = sorted((float(region.start_min), float(region.end_min)))
    clipped_width = max(0.0, float(saturated.end_min - saturated.start_min))
    left_width = max(0.0, float(saturated.start_min - start))
    right_width = max(0.0, float(end - saturated.end_min))
    return clipped_width > 0 and min(left_width, right_width) < clipped_width


def fitted_apex_min(model: str, parameters: Dict[str, float]) -> float:
    """Return the apex of the model curve itself, in minutes.

    Reading the apex off the fitted samples puts it at the edge of the gap when
    the saturated-peak correction removes the clipped top, so the model decides
    instead: a Gaussian peaks at its centre, and the EMG mode is located on the
    same grid its area uses.
    """

    center = float(parameters["center_min"])
    if model == "gaussian":
        return center
    if model != "emg":
        raise ValueError("Unknown fitted model: %s" % model)
    sigma = max(float(parameters["sigma_min"]), 1.0e-12)
    tau = max(float(parameters["tau_min"]), 1.0e-12)
    grid = _model_grid(center, sigma, tau)
    profile = emg_profile(grid, center, sigma, tau)
    return float(grid[int(np.argmax(profile))])


def fitted_fwhm_min(result: PeakFitResult) -> Optional[float]:
    """Return the width at half height of the fitted curve, in minutes."""

    parameters = result.parameters
    sigma = max(float(parameters["sigma_min"]), 1.0e-12)
    if result.model == "gaussian":
        return GAUSSIAN_FWHM_FACTOR * sigma
    center = float(parameters["center_min"])
    tau = max(float(parameters["tau_min"]), 1.0e-12)
    grid = _model_grid(center, sigma, tau)
    profile = emg_profile(grid, center, sigma, tau)
    above = np.flatnonzero(profile >= 0.5)
    if above.size < 2:
        return None
    return float(grid[above[-1]] - grid[above[0]])


def detect_saturated_span(
    dataset: Dataset,
    region: PeakRegion,
    min_run: int = SATURATION_MIN_RUN,
    tolerance_ratio: float = SATURATION_TOLERANCE_RATIO,
    min_width_ratio: float = SATURATION_MIN_WIDTH_RATIO,
) -> Optional[SaturatedSpan]:
    """Return the longest flat top of a clipped peak, or None when unclipped.

    A sample counts as clipped when its baseline-corrected value is within
    ``tolerance_ratio`` of the region's maximum. Only the longest consecutive
    run of such samples is reported, and only when it reaches both ``min_run``
    samples and ``min_width_ratio`` of the peak's width at half height, so an
    ordinary rounded apex is not mistaken for saturation.
    """

    start, end = sorted((float(region.start_min), float(region.end_min)))
    mask = (dataset.time_min >= start) & (dataset.time_min <= end)
    if int(np.count_nonzero(mask)) < 3:
        return None
    time = np.asarray(dataset.time_min[mask], dtype=float)
    raw = np.asarray(dataset.intensity_uv[mask], dtype=float)
    baseline, _start, _end = calculate_baseline(raw, region)
    corrected = raw - baseline
    peak_height = float(np.max(corrected))
    if not np.isfinite(peak_height) or peak_height <= 0:
        return None
    tolerance = peak_height * float(tolerance_ratio)
    plateau = corrected >= peak_height - tolerance
    best_start = best_length = current_start = current_length = 0
    for index, flagged in enumerate(plateau):
        if flagged:
            if current_length == 0:
                current_start = index
            current_length += 1
            if current_length > best_length:
                best_start, best_length = current_start, current_length
        else:
            current_length = 0
    if best_length < max(2, int(min_run)):
        return None
    apex_index = int(np.argmax(corrected))
    half_width = _fwhm(time, corrected, apex_index, peak_height)
    if half_width is None or half_width <= 0:
        return None
    plateau_width = float(time[best_start + best_length - 1] - time[best_start])
    if plateau_width < half_width * float(min_width_ratio):
        return None
    return SaturatedSpan(
        float(time[best_start]),
        float(time[best_start + best_length - 1]),
        int(best_length),
    )


def apply_fit_result(
    fitted_peak: PeakRegion,
    parent_peak: PeakRegion,
    result: PeakFitResult,
    dataset: Optional[Dataset] = None,
    saturated: Optional[SaturatedSpan] = None,
) -> PeakRegion:
    """Update one explicit fitted child without changing parent integration values."""

    fitted_peak.peak_kind = "fitted"
    fitted_peak.parent_peak_id = parent_peak.id
    fitted_peak.start_min = parent_peak.start_min
    fitted_peak.end_min = parent_peak.end_min
    fitted_peak.baseline_mode = parent_peak.baseline_mode
    fitted_peak.baseline_start_uv = parent_peak.baseline_start_uv
    fitted_peak.baseline_end_uv = parent_peak.baseline_end_uv
    fitted_peak.calculated_baseline_start_uv = parent_peak.calculated_baseline_start_uv
    fitted_peak.calculated_baseline_end_uv = parent_peak.calculated_baseline_end_uv
    fitted_peak.integration_source = "fit"
    fitted_peak.retention_time_min = result.retention_time_min
    fitted_peak.fit_model = result.model
    fitted_peak.fit_parameters = dict(result.parameters)
    fitted_peak.fit_retention_time_min = result.retention_time_min
    fitted_peak.fit_rmse_uv = result.rmse_uv
    fitted_peak.fit_r_squared = result.r_squared
    fitted_peak.fit_aic = result.aic
    # Ordinary fitted rows retain the complete model area introduced by #218.
    # A saturation correction instead uses the measured signal within the
    # integration window and replaces only its clipped interval with the model.
    # Both values remain derived estimates and are marked as such by every
    # display/export surface.
    area_uv_min = fitted_area_uv_min(result)
    corrected_time = corrected_uv = None
    if saturated is not None and dataset is not None:
        corrected_time, corrected_uv = saturation_corrected_trace(
            dataset, parent_peak, result, saturated
        )
        area_uv_min = float(_trapz(corrected_uv, corrected_time))
    height_uv = float(result.parameters["amplitude_uv"])
    aux = None
    if dataset is not None:
        aux = dataset.measurement.aux_range_au_per_v
    scale = aux * 1.0e-3 if aux is not None and aux > 0 else None
    fitted_peak.raw_height_uv = height_uv
    fitted_peak.raw_area_uv_min = area_uv_min
    fitted_peak.raw_area_uv_sec = area_uv_min * SECONDS_PER_MINUTE
    fitted_peak.height_mau = height_uv * scale if scale is not None else None
    fitted_peak.area_mau_min = area_uv_min * scale if scale is not None else None
    fitted_peak.area_mau_sec = (
        fitted_peak.area_mau_min * SECONDS_PER_MINUTE
        if fitted_peak.area_mau_min is not None
        else None
    )
    # recalculate_dataset_peaks assigns %Area after replacing the clipped
    # parent's contribution with this corrected row.
    fitted_peak.area_percent = None
    fitted_peak.fwhm_min = fitted_fwhm_min(result)
    fitted_peak.gradient_a_pct = None
    fitted_peak.gradient_b_pct = None
    fitted_peak.gradient_c_pct = None
    fitted_peak.gradient_d_pct = None
    fitted_peak.amount_nmol = None
    fitted_peak.amount_ug = None
    note_lines = [
        line for line in fitted_peak.notes.splitlines()
        if line != LIMITED_FLANK_NOTE
    ]
    fitted_peak.notes = "\n".join(note_lines)
    if saturated is not None:
        fitted_peak.integration_source = "saturation_fit"
        fitted_peak.fit_parameters["saturated_start_min"] = float(
            saturated.start_min
        )
        fitted_peak.fit_parameters["saturated_end_min"] = float(saturated.end_min)
        fitted_peak.fit_parameters["saturated_point_count"] = float(
            saturated.point_count
        )
        limited_flanks = saturation_fit_has_limited_flanks(parent_peak, saturated)
        fitted_peak.fit_parameters["limited_unsaturated_flanks"] = float(
            limited_flanks
        )
        if limited_flanks:
            note_lines.append(LIMITED_FLANK_NOTE)
        fitted_peak.notes = "\n".join(note_lines)
        if dataset is not None and corrected_time is not None:
            corrected_mau = corrected_uv * scale if scale is not None else None
            fitted_peak.amount_nmol, fitted_peak.amount_ug = amount_from_trace(
                dataset,
                corrected_time,
                corrected_mau,
                fitted_peak.area_mau_sec,
            )
    return fitted_peak


def refresh_saturation_corrected_peak(
    dataset: Dataset, parent_peak: PeakRegion, fitted_peak: PeakRegion
) -> PeakRegion:
    """Recalculate a saved correction from its existing fit and span."""

    parameters = dict(fitted_peak.fit_parameters)
    span = SaturatedSpan(
        float(parameters["saturated_start_min"]),
        float(parameters["saturated_end_min"]),
        int(parameters.get("saturated_point_count", 0)),
    )
    result = PeakFitResult(
        fitted_peak.fit_model,
        parameters,
        float(fitted_peak.fit_retention_time_min or fitted_peak.retention_time_min),
        float(fitted_peak.fit_rmse_uv or 0.0),
        float(fitted_peak.fit_r_squared or 0.0),
        float(fitted_peak.fit_aic or 0.0),
        int(parameters.get("fit_point_count", 0)),
    )
    return apply_fit_result(fitted_peak, parent_peak, result, dataset, span)


def fitted_peak_from_result(
    parent_peak: PeakRegion,
    result: PeakFitResult,
    dataset: Optional[Dataset] = None,
    saturated: Optional[SaturatedSpan] = None,
) -> PeakRegion:
    return apply_fit_result(
        PeakRegion(), parent_peak, result, dataset, saturated
    )


def is_saturation_corrected(peak: PeakRegion) -> bool:
    """Report whether a fitted row came from the saturated-peak correction."""

    return bool(peak.is_fitted) and peak.integration_source == "saturation_fit"


def mirror_fitted_peak_for_legacy(
    parent_peak: PeakRegion, fitted_peak: PeakRegion
) -> None:
    """Mirror one fit on its parent for older v1 readers that ignore children."""

    for field_name in _LEGACY_FIT_FIELDS:
        value = getattr(fitted_peak, field_name)
        setattr(
            parent_peak,
            field_name,
            dict(value) if field_name == "fit_parameters" else value,
        )


def clear_legacy_fit(parent_peak: PeakRegion) -> None:
    parent_peak.fit_model = ""
    parent_peak.fit_parameters = {}
    parent_peak.fit_retention_time_min = None
    parent_peak.fit_rmse_uv = None
    parent_peak.fit_r_squared = None
    parent_peak.fit_aic = None


def gaussian_profile(x, center: float, sigma: float):
    x = np.asarray(x, dtype=float)
    sigma = max(float(sigma), 1.0e-12)
    return np.exp(-0.5 * ((x - float(center)) / sigma) ** 2)


def emg_profile(x, center: float, sigma: float, tau: float):
    """Right-tailed exponentially modified Gaussian, normalized to unit height."""

    x = np.asarray(x, dtype=float)
    sigma = max(float(sigma), 1.0e-12)
    tau = max(float(tau), 1.0e-12)
    rate = 1.0 / tau
    exponent = rate * 0.5 * (2.0 * center + rate * sigma * sigma - 2.0 * x)
    argument = (center + rate * sigma * sigma - x) / (math.sqrt(2.0) * sigma)
    erfc_values = np.asarray([math.erfc(float(value)) for value in argument])
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        profile = 0.5 * rate * np.exp(np.clip(exponent, -700.0, 700.0)) * erfc_values
    profile[~np.isfinite(profile)] = 0.0
    maximum = float(np.max(profile)) if profile.size else 0.0
    return profile / maximum if maximum > 0 else np.zeros_like(x)


def evaluate_fit_profile(x, result: PeakFitResult):
    """Evaluate the fitted baseline-corrected signal in raw µV."""

    parameters = result.parameters
    if result.model == "gaussian":
        shape = gaussian_profile(
            x, parameters["center_min"], parameters["sigma_min"]
        )
    elif result.model == "emg":
        shape = emg_profile(
            x,
            parameters["center_min"],
            parameters["sigma_min"],
            parameters["tau_min"],
        )
    else:
        raise ValueError("Unknown fitted model: %s" % result.model)
    return parameters["amplitude_uv"] * shape


def _score_profile(y, profile, parameter_count: int) -> Tuple[float, float, float, float]:
    denominator = float(np.dot(profile, profile))
    amplitude = max(0.0, float(np.dot(y, profile) / denominator)) if denominator else 0.0
    residual = y - amplitude * profile
    sse = max(float(np.dot(residual, residual)), 1.0e-300)
    rmse = math.sqrt(sse / y.size)
    total = float(np.dot(y - np.mean(y), y - np.mean(y)))
    r_squared = 1.0 - sse / total if total > 0 else 0.0
    aic = y.size * math.log(sse / y.size) + 2.0 * parameter_count
    return amplitude, rmse, r_squared, aic


def _search_grid(x, y, centers, sigmas, taus, best=None):
    for center in centers:
        for sigma in sigmas:
            for tau in taus:
                profile = (
                    gaussian_profile(x, center, sigma)
                    if tau is None
                    else emg_profile(x, center, sigma, tau)
                )
                score = _score_profile(y, profile, 3 if tau is None else 4)
                if best is None or score[3] < best[0][3]:
                    best = (score, float(center), float(sigma), tau, profile)
    return best


def _emg_true_amplitude(x, amplitude: float, center: float, sigma: float, tau: float) -> float:
    """Rescale a least-squares EMG amplitude fit on ``x`` to the model's true peak height.

    ``emg_profile`` normalizes by the maximum value within whatever samples it
    is given, so the raw least-squares amplitude found while fitting only
    ``x`` describes the profile's height at the edge of ``x`` -- not its true
    peak -- whenever ``x`` does not reach that peak. This happens by
    construction in the saturated-peak correction, which excludes the clipped
    top from ``x``, and to a smaller degree in an ordinary fit whenever the
    coarse search grid does not land a sample exactly on the discretized
    apex.

    Evaluating the profile once on ``x`` together with ``_model_grid`` -- the
    same wide grid ``fitted_apex_min``/``fitted_area_uv_min`` already use to
    locate and integrate the true peak -- and reading the ratio back from
    that single normalized array avoids a second, un-normalized profile
    implementation and keeps ``amplitude_uv`` consistent with those helpers.
    """

    combined = np.concatenate([x, _model_grid(center, sigma, tau)])
    profile = emg_profile(combined, center, sigma, tau)
    peak_within_x = float(np.max(profile[: len(x)]))
    return amplitude / peak_within_x if peak_within_x > 0 else amplitude


def _fit_model(x, y, model: str) -> PeakFitResult:
    span = float(x[-1] - x[0])
    step = max(float(np.median(np.diff(x))), span / 1000.0, 1.0e-9)
    apex = float(x[int(np.argmax(y))])
    centers = np.linspace(
        max(x[0], apex - span * 0.15),
        min(x[-1], apex + span * 0.15),
        25,
    )
    sigmas = np.geomspace(
        max(step, span / 200.0), max(step * 1.01, span / 2.5), 55
    )
    taus = (None,) if model == "gaussian" else np.geomspace(max(step, span / 300.0), max(step * 1.01, span), 18)
    best = _search_grid(x, y, centers, sigmas, taus)
    # The coarse sweep can land a fraction of a grid step away from the best
    # centre. That is harmless while the apex samples are present and dominate
    # the fit, but the saturated-peak correction fits the flanks alone, where
    # the same offset changes the reconstructed height and area noticeably.
    # Each refinement grid is centred on the current optimum and therefore
    # contains it, so the fit can only improve.
    center_step = float(centers[1] - centers[0]) if len(centers) > 1 else step
    sigma_ratio = float(sigmas[1] / sigmas[0]) if len(sigmas) > 1 else 1.5
    tau_ratio = float(taus[1] / taus[0]) if taus[0] is not None and len(taus) > 1 else 1.5
    for _round in range(3):
        _score, center, sigma, tau, _profile = best
        refined_centers = np.linspace(center - center_step, center + center_step, 9)
        refined_sigmas = np.geomspace(sigma / sigma_ratio, sigma * sigma_ratio, 9)
        refined_taus = (
            (None,)
            if tau is None
            else np.geomspace(tau / tau_ratio, tau * tau_ratio, 9)
        )
        best = _search_grid(x, y, refined_centers, refined_sigmas, refined_taus, best)
        center_step /= 4.0
        sigma_ratio = sigma_ratio ** 0.25
        tau_ratio = tau_ratio ** 0.25
    score, center, sigma, tau, profile = best
    amplitude, rmse, r_squared, aic = score
    if tau is not None:
        # amplitude_uv means the height of the model curve at its own true
        # apex -- not at whichever sample in x happened to be tallest -- so
        # every consumer (area/apex/FWHM helpers, screen and report display)
        # can multiply it by a profile normalized on a range that includes
        # that apex and get the correct absolute height back.
        amplitude = _emg_true_amplitude(x, amplitude, center, sigma, tau)
    parameters = {"amplitude_uv": amplitude, "center_min": center, "sigma_min": sigma}
    if tau is not None:
        parameters["tau_min"] = float(tau)
    fitted_apex = fitted_apex_min(model, parameters)
    return PeakFitResult(model, parameters, fitted_apex, rmse, r_squared, aic, int(x.size))


def fit_peak(
    dataset: Dataset,
    region: PeakRegion,
    model: str = "auto",
    exclude_range=None,
) -> PeakFitResult:
    """Fit a baseline-corrected region without mutating Dataset or PeakRegion.

    ``exclude_range`` drops a closed time interval from the samples the model
    sees. The saturated-peak correction uses it to fit the unclipped flanks
    only; nothing is smoothed or interpolated, the points are simply not used.
    """

    if model not in ("auto", "gaussian", "emg"):
        raise ValueError("Unknown peak fit model: %s" % model)
    start, end = sorted((float(region.start_min), float(region.end_min)))
    mask = (dataset.time_min >= start) & (dataset.time_min <= end)
    if int(np.count_nonzero(mask)) < 8:
        raise ValueError("Peak fitting requires at least eight data points")
    x = np.asarray(dataset.time_min[mask], dtype=float)
    raw = np.asarray(dataset.intensity_uv[mask], dtype=float)
    baseline, _start, _end = calculate_baseline(raw, region)
    y = raw - baseline
    if exclude_range is not None:
        low, high = sorted((float(exclude_range[0]), float(exclude_range[1])))
        keep = (x < low) | (x > high)
        if int(np.count_nonzero(keep)) < 8:
            raise ValueError(
                "Peak fitting requires at least eight unsaturated data points"
            )
        x, y = x[keep], y[keep]
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise ValueError("Peak fitting requires finite values")
    if float(np.max(y)) <= 0:
        raise ValueError("Peak fitting requires a positive baseline-corrected peak")
    if x.size > 1200:
        indices = np.linspace(0, x.size - 1, 1200).astype(int)
        x, y = x[indices], y[indices]
    gaussian = _fit_model(x, y, "gaussian")
    if model == "gaussian":
        return gaussian
    emg = _fit_model(x, y, "emg")
    if model == "emg":
        return emg
    # EMG contains one extra shape parameter and can mimic a Gaussian with a
    # tiny tau. Require a material goodness-of-fit gain before calling a peak
    # tailed, in addition to the information-criterion improvement.
    materially_better = emg.r_squared - gaussian.r_squared >= 0.001
    return emg if materially_better and emg.aic + 2.0 < gaussian.aic else gaussian


def fit_saturated_peak(
    dataset: Dataset,
    region: PeakRegion,
    model: str = "auto",
    saturated_range=None,
):
    """Rebuild a clipped peak from its unsaturated flanks.

    The flat top is either detected or supplied by the caller, then excluded
    from the fit, so the model describes the shape the detector could still
    record. Raw arrays and the parent integration are untouched.
    """

    if saturated_range is None:
        span = detect_saturated_span(dataset, region)
        if span is None:
            raise ValueError("not_saturated")
    else:
        low, high = sorted(
            (float(saturated_range[0]), float(saturated_range[1]))
        )
        start, end = sorted((float(region.start_min), float(region.end_min)))
        if low < start or high > end or low >= high:
            raise ValueError("saturated_range_outside_peak")
        mask = (dataset.time_min >= low) & (dataset.time_min <= high)
        count = int(np.count_nonzero(mask))
        if count < 2:
            raise ValueError("saturated_range_too_narrow")
        span = SaturatedSpan(low, high, count)
    result = fit_peak(dataset, region, model, exclude_range=(span.start_min, span.end_min))
    return result, span
