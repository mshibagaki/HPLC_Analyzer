"""Dependency-free, non-destructive peak-shape fitting models."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Dict, Tuple

import numpy as np

from .analysis import calculate_baseline
from .models import Dataset, PeakRegion


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


def apply_fit_result(
    fitted_peak: PeakRegion,
    parent_peak: PeakRegion,
    result: PeakFitResult,
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
    # Fitted rows are descriptive results, not additional integrations.
    fitted_peak.raw_height_uv = None
    fitted_peak.raw_area_uv_min = None
    fitted_peak.raw_area_uv_sec = None
    fitted_peak.height_mau = None
    fitted_peak.area_mau_min = None
    fitted_peak.area_mau_sec = None
    fitted_peak.area_percent = None
    fitted_peak.fwhm_min = None
    fitted_peak.gradient_a_pct = None
    fitted_peak.gradient_b_pct = None
    fitted_peak.gradient_c_pct = None
    fitted_peak.gradient_d_pct = None
    fitted_peak.amount_nmol = None
    fitted_peak.amount_ug = None
    return fitted_peak


def fitted_peak_from_result(
    parent_peak: PeakRegion, result: PeakFitResult
) -> PeakRegion:
    return apply_fit_result(PeakRegion(), parent_peak, result)


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
    best = None
    taus = (None,) if model == "gaussian" else np.geomspace(max(step, span / 300.0), max(step * 1.01, span), 18)
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
    score, center, sigma, tau, profile = best
    amplitude, rmse, r_squared, aic = score
    parameters = {"amplitude_uv": amplitude, "center_min": center, "sigma_min": sigma}
    if tau is not None:
        parameters["tau_min"] = float(tau)
    fitted_apex = float(x[int(np.argmax(profile))])
    return PeakFitResult(model, parameters, fitted_apex, rmse, r_squared, aic, int(x.size))


def fit_peak(dataset: Dataset, region: PeakRegion, model: str = "auto") -> PeakFitResult:
    """Fit a baseline-corrected region without mutating Dataset or PeakRegion."""

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
