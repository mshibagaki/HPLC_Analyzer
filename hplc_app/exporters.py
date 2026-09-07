from __future__ import annotations

import csv
import json
from pathlib import Path
import re
from typing import Iterable, List, Optional

from .analysis import display_values
from .models import Dataset
from .peak_fitting import is_saturation_corrected


PEAK_HEADERS = (
    "dataset_label",
    "peak_number",
    "start_min",
    "end_min",
    "retention_time_min",
    "raw_height_uV",
    "raw_area_uV_sec",
    "height_mAU",
    "area_mAU_sec",
    "area_percent",
    "FWHM_min",
    "gradient_A_percent",
    "gradient_B_percent",
    "gradient_C_percent",
    "gradient_D_percent",
    "amount_nmol",
    "amount_ug",
    "baseline_mode",
    "baseline_start_uV",
    "baseline_end_uV",
    "integration_source",
    "manual_integration",
    "notes",
    "peak_type",
    "parent_peak_id",
    "fit_model",
    "fit_retention_time_min",
    "fit_rmse_uV",
    "fit_r_squared",
    "fit_aic",
    "fit_parameters_json",
    "area_source",
)


def export_peak_csv(path: str, datasets: Iterable[Dataset]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(PEAK_HEADERS)
        for dataset in datasets:
            integration_numbers = {
                peak.id: index
                for index, peak in enumerate(dataset.peaks, start=1)
            }
            fitted_number = 0
            for peak in dataset.display_peaks():
                if peak.is_fitted:
                    fitted_number += 1
                    peak_number = "F%d" % fitted_number
                else:
                    peak_number = integration_numbers[peak.id]
                writer.writerow(
                    (
                        dataset.label,
                        peak_number,
                        peak.start_min,
                        peak.end_min,
                        peak.retention_time_min,
                        peak.raw_height_uv,
                        peak.raw_area_uv_sec,
                        peak.height_mau,
                        peak.area_mau_sec,
                        peak.area_percent,
                        peak.fwhm_min,
                        peak.gradient_a_pct,
                        peak.gradient_b_pct,
                        peak.gradient_c_pct,
                        peak.gradient_d_pct,
                        peak.amount_nmol,
                        peak.amount_ug,
                        peak.baseline_mode,
                        peak.calculated_baseline_start_uv,
                        peak.calculated_baseline_end_uv,
                        peak.integration_source,
                        (
                            "" if peak.is_fitted
                            else peak.integration_source != "auto"
                        ),
                        peak.notes,
                        (
                            (
                                "fitted_saturation_corrected"
                                if is_saturation_corrected(peak)
                                else "fitted"
                            )
                            if peak.is_fitted
                            else "integrated"
                        ),
                        peak.parent_peak_id if peak.is_fitted else "",
                        peak.fit_model if peak.is_fitted else "",
                        (
                            peak.fit_retention_time_min
                            if peak.is_fitted else ""
                        ),
                        peak.fit_rmse_uv if peak.is_fitted else "",
                        peak.fit_r_squared if peak.is_fitted else "",
                        peak.fit_aic if peak.is_fitted else "",
                        (
                            json.dumps(
                                peak.fit_parameters,
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                            if peak.is_fitted else ""
                        ),
                        # The area column now carries estimated values on fitted
                        # rows, so every row states where its area came from.
                        (
                            "measured_plus_fitted_saturated_span"
                            if is_saturation_corrected(peak)
                            else "fitted_curve"
                        ) if peak.is_fitted else "measured",
                    )
                )


def export_chromatogram_csv(
    path: str,
    dataset: Dataset,
    unit: str,
) -> None:
    values = display_values(dataset, unit)
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("Time_min", "Intensity_%s" % unit))
        for time_min, value in zip(dataset.time_min, values):
            writer.writerow((time_min, value))


def _safe_csv_stem(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", value).strip(" .")
    return cleaned or "chromatogram"


def export_chromatograms_csv(
    directory: str,
    datasets: Iterable[Dataset],
    unit: str,
) -> List[str]:
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    used = set()
    for dataset in datasets:
        base = _safe_csv_stem(dataset.short_label or dataset.label or dataset.original_filename)
        stem = base
        suffix = 2
        while stem.lower() in used or (destination / (stem + ".csv")).exists():
            stem = "%s_%d" % (base, suffix)
            suffix += 1
        used.add(stem.lower())
        path = destination / (stem + ".csv")
        export_chromatogram_csv(str(path), dataset, unit)
        written.append(str(path))
    return written


def export_metadata_csv(path: str, datasets: Iterable[Dataset], language: str = "ja") -> None:
    if language == "ja":
        headers = (
            "表示ラベル", "短縮ラベル", "サンプル名", "ID", "グループ", "反復", "タグ",
            "測定波長 (nm)", "AU/V", "流量 (mL/min)", "セル光路長 (cm)",
            "カラム", "カラム温度 (℃)", "分析対象物", "注入量 (µL)",
            "ε214 (M⁻¹ cm⁻¹)", "ε280 (M⁻¹ cm⁻¹)", "分子量 (g/mol)",
            "グラジエント", "縦軸", "時間シフト (min)", "縦オフセット", "表示", "コメント", "元のパス",
        )
    else:
        headers = (
            "Display label", "Short label", "Sample name", "ID", "Group", "Replicate", "Tags",
            "Wavelength (nm)", "AU/V", "Flow rate (mL/min)", "Cell path length (cm)",
            "Column", "Column temperature (°C)", "Analyte", "Injection volume (µL)",
            "ε214 (M⁻¹ cm⁻¹)", "ε280 (M⁻¹ cm⁻¹)", "Molecular weight (g/mol)",
            "Gradient", "Y axis", "Time shift (min)", "Y offset", "Visible", "Comments", "Original path",
        )
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for dataset in datasets:
            meta = dataset.measurement
            writer.writerow(
                (
                    dataset.label,
                    dataset.short_label,
                    meta.sample_name,
                    meta.sample_id,
                    meta.group,
                    meta.replicate,
                    ", ".join(meta.tags),
                    meta.wavelength_nm,
                    meta.aux_range_au_per_v,
                    meta.flow_rate_ml_min,
                    meta.cell_path_length_cm,
                    meta.column_name,
                    meta.column_temperature_c,
                    meta.analyte_name,
                    meta.injection_volume_ul,
                    meta.molar_absorptivity_214,
                    meta.molar_absorptivity_280,
                    meta.molecular_weight_g_mol,
                    dataset.effective_gradient_preset_name(),
                    dataset.y_axis,
                    dataset.x_shift_min,
                    dataset.offset,
                    dataset.visible,
                    meta.comments,
                    dataset.original_path,
                )
            )
