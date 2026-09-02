from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

import numpy as np
from matplotlib import font_manager
from matplotlib import rcParams
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib.text import Text

from .analysis import baseline_trace, display_values, reference_values_for_display
from .models import Dataset, PeakRegion, Project
from .peak_fitting import (
    PeakFitResult,
    evaluate_fit_profile,
    is_saturation_corrected,
)
from .rendering import matplotlib_line_style


A4_SIZE_INCHES = (8.2677165, 11.6929134)
REPORT_DPI = 150
INTEGRATION_BOUNDARY_COLOR = "#9ca3af"


@dataclass(frozen=True)
class ReportOptions:
    """Session-only choices controlling report content without changing analysis."""

    integration_range: bool = True
    baseline: bool = True
    retention_time: bool = True
    gradient_b: bool = True
    gradient_conditions: bool = True
    quantitation: bool = True


def _number(value, digits=4) -> str:
    if value is None:
        return ""
    return ("%%.%dg" % digits) % value


def _axis_label(project: Project, dataset: Dataset, unit: str) -> str:
    custom = (
        project.method.y_axis_2_label
        if dataset.y_axis == 2
        else project.method.y_axis_1_label
    )
    if custom.strip():
        return custom.strip()
    return {
        "uV": "Intensity (µV)",
        "mAU": "Absorbance (mAU)",
        "AU": "Absorbance (AU)",
        "normalized": "Normalized intensity",
    }.get(unit, "Intensity")


def _report_fonts():
    preferred = ("Arial", "Yu Gothic", "Meiryo", "Noto Sans CJK JP", "IPAexGothic")
    available = {font.name for font in font_manager.fontManager.ttflist}
    selected = [name for name in preferred if name in available]
    selected.append("DejaVu Sans")
    return selected


def _resolved_report_font(family: str):
    requested = str(family or "").strip()
    available = {font.name for font in font_manager.fontManager.ttflist}
    if requested.casefold() == "arial":
        candidates = (
            "Arial",
            "Yu Gothic",
            "Meiryo",
            "Noto Sans CJK JP",
            "IPAexGothic",
            "DejaVu Sans",
        )
        resolved = [name for name in candidates if name in available]
        if resolved:
            return resolved
    if requested in available:
        return requested
    return _report_fonts()[0]


def _apply_report_fonts(figure: Figure) -> None:
    families = _report_fonts()
    for text in figure.findobj(match=Text):
        if text.get_gid() == "hplc-user-annotation":
            continue
        text.set_fontfamily(families)


# Windows normally has Yu Gothic or Meiryo.  Listing Japanese-capable fonts
# before DejaVu Sans also keeps Japanese sample names usable in PDF reports and
# in the on-screen legend without bundling a platform-specific font file.
rcParams["font.family"] = "sans-serif"
rcParams["font.sans-serif"] = _report_fonts()
rcParams["axes.unicode_minus"] = False


def _metadata_lines(dataset: Dataset, language: str) -> List[str]:
    meta = dataset.measurement
    if language == "ja":
        labels = (
            ("サンプル", meta.sample_name),
            ("ID", meta.sample_id),
            ("グループ／反復", " / ".join(value for value in (meta.group, meta.replicate) if value)),
            ("測定波長", ("%s nm" % _number(meta.wavelength_nm)) if meta.wavelength_nm is not None else ""),
            ("AU/V", _number(meta.aux_range_au_per_v)),
            ("流量", ("%s mL/min" % _number(meta.flow_rate_ml_min)) if meta.flow_rate_ml_min is not None else ""),
            ("カラム", meta.column_name),
            ("カラム温度", ("%s °C" % _number(meta.column_temperature_c)) if meta.column_temperature_c is not None else ""),
            ("分析対象物", meta.analyte_name),
            ("注入量", ("%s µL" % _number(meta.injection_volume_ul)) if meta.injection_volume_ul is not None else ""),
        )
    else:
        labels = (
            ("Sample", meta.sample_name),
            ("ID", meta.sample_id),
            ("Group / replicate", " / ".join(value for value in (meta.group, meta.replicate) if value)),
            ("Wavelength", ("%s nm" % _number(meta.wavelength_nm)) if meta.wavelength_nm is not None else ""),
            ("AU/V", _number(meta.aux_range_au_per_v)),
            ("Flow", ("%s mL/min" % _number(meta.flow_rate_ml_min)) if meta.flow_rate_ml_min is not None else ""),
            ("Column", meta.column_name),
            ("Column temperature", ("%s °C" % _number(meta.column_temperature_c)) if meta.column_temperature_c is not None else ""),
            ("Analyte", meta.analyte_name),
            ("Injection", ("%s µL" % _number(meta.injection_volume_ul)) if meta.injection_volume_ul is not None else ""),
        )
    return ["%s: %s" % (label, value or "—") for label, value in labels]


def _peak_rows(
    peaks: Iterable[PeakRegion],
    options: ReportOptions,
    start_number: int = 1,
    number_labels=None,
    parent_numbers=None,
):
    rows = []
    for number, peak in enumerate(peaks, start=start_number):
        row = [
            str((number_labels or {}).get(peak.id, number)),
            (
                # A fitted row's numbers are read off the model curve, so the
                # type column says so before the estimated area is read.
                "Fit %s%s -> #%s (estimated)" % (
                    peak.fit_model.upper(),
                    " sat." if is_saturation_corrected(peak) else "",
                    (parent_numbers or {}).get(peak.parent_peak_id, "?"),
                )
                if peak.is_fitted else "Integration"
            ),
        ]
        if options.retention_time:
            row.append(_number(peak.retention_time_min))
        if options.integration_range:
            row.append("%s–%s" % (_number(peak.start_min), _number(peak.end_min)))
        row.extend(
            (
                _number(peak.area_mau_sec),
                _number(peak.area_percent),
                _number(peak.fwhm_min),
            )
        )
        if options.gradient_b:
            row.append(_number(peak.gradient_b_pct))
        if options.quantitation:
            row.append(_number(peak.amount_ug))
        if options.baseline:
            row.append(
                peak.fit_model.upper()
                if peak.is_fitted
                else (
                    "Auto" if peak.integration_source == "auto" else "Manual"
                )
            )
        rows.append(tuple(row))
    return rows


def _add_peak_table(
    axis,
    peaks,
    start_number: int,
    language: str,
    options: ReportOptions,
    number_labels=None,
    parent_numbers=None,
    fit_axis=False,
):
    axis.axis("off")
    headers = ["#", "種別／親" if language == "ja" else "Type / parent"]
    widths = [0.045, 0.135]
    if options.retention_time:
        headers.append("RT (min)")
        widths.append(0.085)
    if options.integration_range:
        headers.append("Range (min)")
        widths.append(0.15)
    headers.extend(("Area (mAU·sec)", "%Area", "FWHM (min)"))
    widths.extend((0.14, 0.075, 0.1))
    if options.gradient_b:
        headers.append("%B")
        widths.append(0.06)
    if options.quantitation:
        headers.append("Amount (µg)")
        widths.append(0.11)
    if options.baseline:
        headers.append("Method")
        widths.append(0.09)
    width_total = sum(widths)
    rows = _peak_rows(
        peaks,
        options,
        start_number,
        number_labels=number_labels,
        parent_numbers=parent_numbers,
    )
    if not rows:
        axis.text(
            0.5,
            0.5,
            "積分ピークはありません" if language == "ja" else "No integrated peaks",
            ha="center",
            va="center",
            fontsize=9,
        )
        return
    table_kwargs = {
        "cellText": rows,
        "colLabels": headers,
        "cellLoc": "center",
        "colWidths": tuple(width / width_total for width in widths),
    }
    if fit_axis:
        table_kwargs["bbox"] = (0.0, 0.0, 1.0, 1.0)
    else:
        table_kwargs["loc"] = "center"
    table = axis.table(**table_kwargs)
    table.auto_set_font_size(False)
    table.set_fontsize(6.2)
    table.scale(1.0, 1.08)
    for (row, _column), cell in table.get_celld().items():
        cell.set_linewidth(0.35)
        if row == 0:
            cell.set_facecolor("#e5e7eb")
            cell.set_text_props(weight="bold")


def _plot_dataset(
    axis,
    project: Project,
    dataset: Dataset,
    options: ReportOptions,
):
    unit = project.method.display_unit
    try:
        values = display_values(dataset, unit)
    except ValueError:
        unit = "uV"
        values = display_values(dataset, unit)
    color = dataset.color or "#1f77b4"
    time = dataset.time_min + dataset.x_shift_min
    axis.plot(
        time,
        values + dataset.offset,
        color=color,
        linewidth=project.method.line_width,
        linestyle=matplotlib_line_style(dataset.line_style),
        label=project.legend_label_for(dataset),
    )
    for peak in dataset.peaks:
        start = peak.start_min + dataset.x_shift_min
        end = peak.end_min + dataset.x_shift_min
        if options.integration_range:
            axis.axvspan(
                start,
                end,
                color=color,
                alpha=0.08,
            )
            for boundary in (start, end):
                axis.axvline(
                    boundary,
                    color=INTEGRATION_BOUNDARY_COLOR,
                    linestyle="--",
                    linewidth=0.65,
                    alpha=0.75,
                )
        if options.retention_time and peak.retention_time_min is not None:
            displayed_retention = peak.retention_time_min + dataset.x_shift_min
            axis.axvline(
                displayed_retention,
                color=color,
                linewidth=0.8,
                alpha=0.65,
            )
            label_y = float(
                np.interp(peak.retention_time_min, dataset.time_min, values)
            ) + dataset.offset
            axis.annotate(
                "%.2f" % displayed_retention,
                xy=(displayed_retention, label_y),
                xytext=(0, 4),
                textcoords="offset points",
                ha="center",
                va="bottom",
                rotation=90,
                fontfamily=_resolved_report_font(
                    project.method.retention_label_font_family or "Arial"
                ),
                fontsize=project.method.retention_label_font_size,
                color=project.method.retention_label_color or "#000000",
            )
        baseline_time, baseline_uv = baseline_trace(dataset, peak)
        if options.baseline and baseline_time.size:
            baseline = reference_values_for_display(dataset, baseline_uv, unit)
            axis.plot(
                baseline_time + dataset.x_shift_min,
                baseline + dataset.offset,
                color=color,
                linestyle="--",
                linewidth=0.7,
                alpha=0.7,
            )
    parent_numbers = {
        peak.id: number for number, peak in enumerate(dataset.peaks, start=1)
    }
    fitted_plotted = False
    fitted_rows = [peak for peak in dataset.display_peaks() if peak.is_fitted]
    for fitted_number, fitted_peak in enumerate(fitted_rows, start=1):
        parent = dataset.parent_peak_for(fitted_peak)
        if parent is None or not fitted_peak.fit_model or not fitted_peak.fit_parameters:
            continue
        mask = (
            (dataset.time_min >= fitted_peak.start_min)
            & (dataset.time_min <= fitted_peak.end_min)
        )
        fit_time = dataset.time_min[mask]
        if fit_time.size < 3:
            continue
        retention_time = fitted_peak.fit_retention_time_min
        if retention_time is None:
            retention_time = fit_time[0]
        result = PeakFitResult(
            model=fitted_peak.fit_model,
            parameters=dict(fitted_peak.fit_parameters),
            retention_time_min=float(retention_time),
            rmse_uv=float(fitted_peak.fit_rmse_uv or 0.0),
            r_squared=float(fitted_peak.fit_r_squared or 0.0),
            aic=float(fitted_peak.fit_aic or 0.0),
            point_count=int(fit_time.size),
        )
        fitted_uv = evaluate_fit_profile(fit_time, result)
        baseline_time, baseline_uv = baseline_trace(dataset, fitted_peak)
        if baseline_time.size == fit_time.size:
            fitted_uv = fitted_uv + baseline_uv
        fitted_values = reference_values_for_display(dataset, fitted_uv, unit)
        axis.plot(
            fit_time + dataset.x_shift_min,
            fitted_values + dataset.offset,
            color="#c026d3",
            linestyle=":",
            linewidth=max(1.0, project.method.line_width),
            label="F%d Fit %s%s (#%d, estimated)" % (
                fitted_number,
                fitted_peak.fit_model.upper(),
                " sat." if is_saturation_corrected(fitted_peak) else "",
                parent_numbers[parent.id],
            ),
        )
        fitted_plotted = True
    for annotation in project.annotations:
        if annotation.dataset_id not in ("", dataset.id) or not annotation.text.strip():
            continue
        artist = axis.text(
            annotation.x_min,
            annotation.y_value,
            annotation.text,
            ha="left",
            va="bottom",
            fontfamily=_resolved_report_font(annotation.font_family or "Arial"),
            fontsize=annotation.font_size,
            color=annotation.color or "#000000",
            bbox={
                "boxstyle": "round,pad=0.28",
                "facecolor": annotation.background_color or "#ffffff",
                "edgecolor": annotation.border_color or "#6b7280",
                "linewidth": 0.8,
                "alpha": 0.9,
            },
            zorder=30,
        )
        artist.set_gid("hplc-user-annotation")
    axis.set_xlabel(project.method.x_axis_label.strip() or "Retention time (min)")
    axis.set_ylabel(_axis_label(project, dataset, unit))
    right = float(time[-1]) if time.size else 1.0
    if options.gradient_conditions and dataset.measurement.gradient:
        right = max(right, max(point.time_min for point in dataset.measurement.gradient))
    axis.set_xlim(0.0, max(1.0e-9, right))
    axis.margins(x=0)
    axis.grid(False)
    if options.gradient_conditions and dataset.measurement.gradient:
        gradient = sorted(dataset.measurement.gradient, key=lambda point: point.time_min)
        gradient_axis = axis.twinx()
        gradient_axis.plot(
            [point.time_min for point in gradient],
            [point.b_pct for point in gradient],
            color="#111827",
            linestyle=":",
            linewidth=0.9,
        )
        gradient_axis.set_ylim(0.0, 100.0)
        gradient_axis.set_ylabel(project.method.gradient_axis_label or "Mobile phase B (%)")
    if fitted_plotted:
        axis.legend(frameon=False, fontsize=6.5)


def analysis_report_figures(
    project: Project,
    datasets: Iterable[Dataset],
    language: str = "ja",
    options: Optional[ReportOptions] = None,
) -> List[Figure]:
    options = options or ReportOptions()
    figures: List[Figure] = []
    report_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    for dataset in datasets:
        display_peaks = dataset.display_peaks()
        parent_numbers = {
            peak.id: number for number, peak in enumerate(dataset.peaks, start=1)
        }
        number_labels = dict(parent_numbers)
        number_labels.update({
            peak.id: "F%d" % number
            for number, peak in enumerate(
                (peak for peak in display_peaks if peak.is_fitted), start=1
            )
        })
        figure = Figure(figsize=A4_SIZE_INCHES, dpi=REPORT_DPI)
        figure.subplots_adjust(left=0.075, right=0.9, top=0.95, bottom=0.055, hspace=0.38)
        grid = figure.add_gridspec(4, 1, height_ratios=(0.42, 0.95, 3.8, 2.65))
        title_axis = figure.add_subplot(grid[0])
        title_axis.axis("off")
        title_axis.text(0.0, 0.72, project.title, fontsize=13, weight="bold", va="center")
        title_axis.text(
            0.0,
            0.18,
            dataset.label or dataset.original_filename,
            fontsize=11,
            weight="bold",
            va="center",
        )
        title_axis.text(1.0, 0.18, report_time, fontsize=7, ha="right", color="#4b5563")

        metadata_axis = figure.add_subplot(grid[1])
        metadata_axis.axis("off")
        lines = _metadata_lines(dataset, language)
        midpoint = (len(lines) + 1) // 2
        metadata_axis.text(0.0, 1.0, "\n".join(lines[:midpoint]), va="top", fontsize=7.4, linespacing=1.35)
        metadata_axis.text(0.51, 1.0, "\n".join(lines[midpoint:]), va="top", fontsize=7.4, linespacing=1.35)

        plot_axis = figure.add_subplot(grid[2])
        _plot_dataset(plot_axis, project, dataset, options)
        plot_axis.set_title("Chromatogram", fontsize=9, loc="left")

        table_axis = figure.add_subplot(grid[3])
        _add_peak_table(
            table_axis,
            display_peaks[:20],
            1,
            language,
            options,
            number_labels=number_labels,
            parent_numbers=parent_numbers,
            fit_axis=True,
        )
        table_axis.set_title(
            "ピーク表" if language == "ja" else "Peak table",
            fontsize=9,
            loc="left",
            pad=5,
        )
        figure.text(
            0.075,
            0.025,
            dataset.original_path,
            fontsize=5.5,
            color="#6b7280",
            ha="left",
        )
        _apply_report_fonts(figure)
        figures.append(figure)

        remaining = display_peaks[20:]
        for offset in range(0, len(remaining), 40):
            page_peaks = remaining[offset : offset + 40]
            continuation = Figure(figsize=A4_SIZE_INCHES, dpi=REPORT_DPI)
            continuation.subplots_adjust(left=0.06, right=0.94, top=0.93, bottom=0.06)
            table_axis = continuation.add_subplot(111)
            table_axis.set_title(
                "%s — %s"
                % (
                    dataset.label or dataset.original_filename,
                    "ピーク表（続き）" if language == "ja" else "Peak table (continued)",
                ),
                fontsize=11,
                loc="left",
                pad=12,
            )
            _add_peak_table(
                table_axis,
                page_peaks,
                21 + offset,
                language,
                options,
                number_labels=number_labels,
                parent_numbers=parent_numbers,
            )
            continuation.text(0.94, 0.025, report_time, fontsize=6, ha="right", color="#6b7280")
            _apply_report_fonts(continuation)
            figures.append(continuation)

    return figures


def export_analysis_report_pdf(
    path: str,
    project: Project,
    datasets: Iterable[Dataset],
    language: str = "ja",
    options: Optional[ReportOptions] = None,
) -> str:
    destination = str(path)
    if not destination.lower().endswith(".pdf"):
        destination += ".pdf"
    figures = analysis_report_figures(project, datasets, language, options)
    if not figures:
        raise ValueError("No chromatograms are available for the report")
    with PdfPages(
        destination,
        metadata={
            "Title": project.title,
            "Author": project.author or "HPLC Analyzer",
            "Subject": "HPLC analysis report",
        },
    ) as document:
        for figure in figures:
            document.savefig(figure)
            figure.clear()
    return destination


def render_analysis_report_pages(
    directory: str,
    project: Project,
    datasets: Iterable[Dataset],
    language: str = "ja",
    options: Optional[ReportOptions] = None,
) -> List[str]:
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    figures = analysis_report_figures(project, datasets, language, options)
    paths: List[str] = []
    for number, figure in enumerate(figures, start=1):
        path = destination / ("report_page_%03d.png" % number)
        figure.savefig(str(path), dpi=REPORT_DPI)
        figure.clear()
        paths.append(str(path))
    return paths
