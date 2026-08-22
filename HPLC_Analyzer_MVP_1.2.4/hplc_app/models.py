from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

import numpy as np


CONDITION_PRESET_LABEL_FIELDS = frozenset(("label", "short_label"))


def sanitize_condition_presets(
    presets: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Return condition presets without chromatogram display-label fields."""
    sanitized = {}
    for name, payload in (presets or {}).items():
        if not isinstance(payload, dict):
            continue
        sanitized[name] = {
            key: deepcopy(value)
            for key, value in payload.items()
            if key not in CONDITION_PRESET_LABEL_FIELDS
        }
    return sanitized


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Solvent:
    name: str = ""
    composition: str = ""


@dataclass
class GradientPoint:
    time_min: float = 0.0
    a_pct: float = 100.0
    b_pct: float = 0.0
    c_pct: float = 0.0
    d_pct: float = 0.0
    flow_ml_min: Optional[float] = None


@dataclass
class MeasurementMetadata:
    sample_name: str = ""
    sample_id: str = ""
    group: str = ""
    replicate: str = ""
    tags: List[str] = field(default_factory=list)
    comments: str = ""
    instrument_name: str = ""
    method_name: str = ""
    acquisition_datetime: str = ""
    wavelength_nm: Optional[float] = None
    aux_range_au_per_v: Optional[float] = None
    flow_rate_ml_min: Optional[float] = None
    column_name: str = ""
    column_temperature_c: Optional[float] = None
    injection_volume_ul: Optional[float] = None
    cell_path_length_cm: Optional[float] = 1.0
    analyte_name: str = ""
    molar_absorptivity_214: Optional[float] = None
    molar_absorptivity_280: Optional[float] = None
    molecular_weight_g_mol: Optional[float] = None
    solvents: Dict[str, Solvent] = field(
        default_factory=lambda: {line: Solvent() for line in "ABCD"}
    )
    gradient: List[GradientPoint] = field(default_factory=list)

    def epsilon_for_wavelength(self) -> Optional[float]:
        if self.wavelength_nm is None:
            return None
        if abs(self.wavelength_nm - 214.0) < 0.5:
            return self.molar_absorptivity_214
        if abs(self.wavelength_nm - 280.0) < 0.5:
            return self.molar_absorptivity_280
        return None


@dataclass
class PeakRegion:
    id: str = field(default_factory=new_id)
    start_min: float = 0.0
    end_min: float = 0.0
    baseline_mode: str = "linear"
    baseline_start_uv: Optional[float] = None
    baseline_end_uv: Optional[float] = None
    calculated_baseline_start_uv: Optional[float] = None
    calculated_baseline_end_uv: Optional[float] = None
    retention_time_min: Optional[float] = None
    raw_height_uv: Optional[float] = None
    # Minute-based values remain in v1 project files so earlier v1.x releases
    # can still read a v1.1.4 project without interpreting seconds as minutes.
    raw_area_uv_min: Optional[float] = None
    raw_area_uv_sec: Optional[float] = None
    height_mau: Optional[float] = None
    area_mau_min: Optional[float] = None
    area_mau_sec: Optional[float] = None
    area_percent: Optional[float] = None
    fwhm_min: Optional[float] = None
    gradient_a_pct: Optional[float] = None
    gradient_b_pct: Optional[float] = None
    gradient_c_pct: Optional[float] = None
    gradient_d_pct: Optional[float] = None
    amount_nmol: Optional[float] = None
    amount_ug: Optional[float] = None
    split_group_id: str = ""
    integration_source: str = "manual"
    notes: str = ""


@dataclass
class TextAnnotation:
    """User-defined text box positioned in chromatogram data coordinates."""

    id: str = field(default_factory=new_id)
    text: str = ""
    x_min: float = 0.0
    y_value: float = 0.0
    dataset_id: str = ""
    y_axis: int = 1
    font_family: str = "Arial"
    font_size: float = 10.0
    color: str = "#000000"
    background_color: str = "#ffffff"
    border_color: str = "#6b7280"


@dataclass
class Dataset:
    id: str = field(default_factory=new_id)
    label: str = ""
    short_label: str = ""
    original_filename: str = ""
    original_directory: str = ""
    original_path: str = ""
    embedded_source_name: str = ""
    sha256: str = ""
    imported_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    source_metadata: Dict[str, Any] = field(default_factory=dict)
    source_peak_table: List[Dict[str, str]] = field(default_factory=list)
    measurement: MeasurementMetadata = field(default_factory=MeasurementMetadata)
    gradient_preset_name: str = ""
    y_axis: int = 1
    x_shift_min: float = 0.0
    offset: float = 0.0
    visible: bool = True
    color: str = ""
    peaks: List[PeakRegion] = field(default_factory=list)
    time_min: np.ndarray = field(default_factory=lambda: np.array([], dtype=float), repr=False)
    intensity_uv: np.ndarray = field(default_factory=lambda: np.array([], dtype=float), repr=False)
    raw_bytes: bytes = field(default=b"", repr=False)

    def to_manifest(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("time_min", None)
        data.pop("intensity_uv", None)
        data.pop("raw_bytes", None)
        return data

    def wavelength_text(self) -> str:
        """Return a compact wavelength label suitable for tables and legends."""
        value = self.measurement.wavelength_nm
        if value is None:
            return ""
        numeric = float(value)
        if numeric.is_integer():
            return "%d nm" % int(numeric)
        return "%g nm" % numeric

    def legend_label(self) -> str:
        label = self.short_label or self.label or self.original_filename
        wavelength = self.wavelength_text()
        if not wavelength:
            return label
        # Avoid duplicating a wavelength already carried by a legacy label,
        # while new labels consistently use the requested Label_Wavelength form.
        if wavelength.casefold() in label.casefold():
            return label
        return "%s_%s" % (label, wavelength)


@dataclass
class AnalysisMethod:
    name: str = "Default"
    display_unit: str = "uV"
    baseline_mode: str = "linear"
    show_integration_areas: bool = True
    show_retention_labels: bool = False
    retention_label_font_family: str = "Arial"
    retention_label_font_size: float = 8.0
    retention_label_color: str = "#000000"
    show_gradient_b: bool = False
    legend_location: str = "best"
    legend_font_family: str = "Arial"
    legend_font_size: float = 9.0
    legend_font_color: str = "#000000"
    x_tick_mode: str = "auto"
    x_major_tick_min: float = 5.0
    x_minor_tick_min: float = 1.0
    # Retained for v0.4.0 project compatibility.  v0.5.0 and later always use English
    # automatic axis labels and no longer exposes the former toggle.
    plot_labels_english: bool = True
    figure_width_mm: float = 160.0
    figure_height_mm: float = 100.0
    dpi: int = 300
    line_width: float = 1.2
    x_axis_label: str = ""
    y_axis_1_label: str = ""
    y_axis_2_label: str = ""
    gradient_axis_label: str = "Mobile phase B (%)"
    axis_label_font_family: str = "Arial"
    axis_label_font_size: float = 10.0
    axis_label_color: str = "#000000"
    tick_label_font_family: str = "Arial"
    tick_label_font_size: float = 9.0
    tick_label_color: str = "#000000"
    view_mode: str = "single"
    # "auto" selects X, Y1, Y2, or X+Y from the cursor position.  The former
    # explicit modes remain available for users who prefer fixed wheel zoom.
    zoom_axis: str = "auto"
    auto_peak_snr_threshold: float = 8.0
    auto_peak_min_prominence_uv: float = 50.0
    auto_peak_smoothing_min: float = 0.02
    auto_peak_min_width_min: float = 0.02
    auto_peak_max_width_min: float = 5.0
    auto_peak_min_distance_min: float = 0.05
    auto_peak_boundary_percent: float = 2.0
    auto_peak_max_count: int = 200


@dataclass
class Project:
    project_id: str = field(default_factory=new_id)
    title: str = "Untitled project"
    analysis_date: str = ""
    column_name: str = ""
    condition_name: str = ""
    author: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    modified_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    ui_language: str = "ja"
    method: AnalysisMethod = field(default_factory=AnalysisMethod)
    datasets: List[Dataset] = field(default_factory=list)
    annotations: List[TextAnnotation] = field(default_factory=list)
    condition_presets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    gradient_presets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    project_path: str = ""
    dirty: bool = False
