"""Application-wide sensitivity presets for automatic peak detection.

The project format continues to persist the eight ``AnalysisMethod`` fields.
These named presets are application preferences; selecting one copies its
values into the current project method before detection.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Dict, Mapping

from .models import AnalysisMethod


AUTO_PEAK_FIELDS = (
    "auto_peak_snr_threshold",
    "auto_peak_min_prominence_uv",
    "auto_peak_smoothing_min",
    "auto_peak_min_width_min",
    "auto_peak_max_width_min",
    "auto_peak_min_distance_min",
    "auto_peak_boundary_percent",
    "auto_peak_max_count",
)

AUTO_PEAK_SENSITIVITIES = ("low", "medium", "high")


def auto_peak_thresholds_from_method(method: AnalysisMethod) -> Dict[str, Any]:
    return {field: deepcopy(getattr(method, field)) for field in AUTO_PEAK_FIELDS}


def default_auto_peak_sensitivity_presets() -> Dict[str, Dict[str, Any]]:
    """Return defaults derived from the established AnalysisMethod defaults.

    Low sensitivity raises the noise, prominence, smoothing, width, and
    distance filters. High sensitivity lowers the same filters. Maximum width
    and boundary placement are geometric/integration choices, so they remain
    unchanged. The candidate cap is scaled to match the expected candidate
    volume.
    """
    medium = auto_peak_thresholds_from_method(AnalysisMethod())
    low = deepcopy(medium)
    high = deepcopy(medium)
    for field in (
        "auto_peak_snr_threshold",
        "auto_peak_min_prominence_uv",
        "auto_peak_smoothing_min",
        "auto_peak_min_width_min",
        "auto_peak_min_distance_min",
    ):
        low[field] = float(medium[field]) * 2.0
        high[field] = float(medium[field]) * 0.5
    low["auto_peak_max_count"] = max(1, int(medium["auto_peak_max_count"]) // 2)
    high["auto_peak_max_count"] = int(medium["auto_peak_max_count"]) * 2
    return {"low": low, "medium": medium, "high": high}


def _valid_number(value: Any, minimum: float, maximum: float) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and minimum <= float(value) <= maximum
    )


def normalize_auto_peak_sensitivity_presets(value: Any) -> Dict[str, Dict[str, Any]]:
    defaults = default_auto_peak_sensitivity_presets()
    if not isinstance(value, Mapping):
        return defaults
    limits = {
        "auto_peak_snr_threshold": (0.0, 1000.0),
        "auto_peak_min_prominence_uv": (0.0, 1.0e12),
        "auto_peak_smoothing_min": (0.0, 100.0),
        "auto_peak_min_width_min": (0.0, 100.0),
        "auto_peak_max_width_min": (0.0001, 1000.0),
        "auto_peak_min_distance_min": (0.0, 100.0),
        "auto_peak_boundary_percent": (0.0, 50.0),
        "auto_peak_max_count": (1.0, 10000.0),
    }
    normalized = {}
    for sensitivity in AUTO_PEAK_SENSITIVITIES:
        source = value.get(sensitivity)
        if not isinstance(source, Mapping):
            normalized[sensitivity] = deepcopy(defaults[sensitivity])
            continue
        stage = {}
        for field in AUTO_PEAK_FIELDS:
            candidate = source.get(field)
            if not _valid_number(candidate, *limits[field]):
                candidate = defaults[sensitivity][field]
            stage[field] = (
                int(candidate)
                if field == "auto_peak_max_count"
                else float(candidate)
            )
        if stage["auto_peak_max_width_min"] < stage["auto_peak_min_width_min"]:
            stage["auto_peak_min_width_min"] = defaults[sensitivity][
                "auto_peak_min_width_min"
            ]
            stage["auto_peak_max_width_min"] = defaults[sensitivity][
                "auto_peak_max_width_min"
            ]
        normalized[sensitivity] = stage
    return normalized


def apply_auto_peak_thresholds(
    method: AnalysisMethod, thresholds: Mapping[str, Any]
) -> None:
    normalized = normalize_auto_peak_sensitivity_presets(
        {"low": thresholds, "medium": thresholds, "high": thresholds}
    )["medium"]
    for field in AUTO_PEAK_FIELDS:
        setattr(method, field, normalized[field])
