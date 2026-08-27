from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

import numpy as np


CONDITION_PRESET_LABEL_FIELDS = frozenset(("label", "short_label"))

# Run is authoritative for these measurement fields.  Wavelength and AU/V
# remain Dataset-level because one physical Run can contain separate detector
# channels with different wavelengths and output ranges.
RUN_MEASUREMENT_FIELD_MAP = {
    "sample_name": "sample_name",
    "sample_id": "sample_id",
    "group": "group",
    "replicate": "replicate",
    "tags": "tags",
    "comments": "comments",
    "instrument_name": "instrument_name",
    "method_name": "method_name",
    "acquisition_datetime": "timestamp",
    "flow_rate_ml_min": "flow_rate_ml_min",
    "column_name": "column_name",
    "column_temperature_c": "column_temperature_c",
    "injection_volume_ul": "injection_volume_ul",
    "cell_path_length_cm": "cell_path_length_cm",
    "analyte_name": "analyte_name",
    "molar_absorptivity_214": "molar_absorptivity_214",
    "molar_absorptivity_280": "molar_absorptivity_280",
    "molecular_weight_g_mol": "molecular_weight_g_mol",
    "solvents": "solvents",
    "gradient": "gradient",
}
DATASET_MEASUREMENT_FIELDS = frozenset(("wavelength_nm", "aux_range_au_per_v"))


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

    def __getattribute__(self, name: str):
        run_field = RUN_MEASUREMENT_FIELD_MAP.get(name)
        if run_field is not None:
            run = object.__getattribute__(self, "__dict__").get("_run")
            if run is not None:
                return getattr(run, run_field)
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value) -> None:
        run_field = RUN_MEASUREMENT_FIELD_MAP.get(name)
        run = self.__dict__.get("_run")
        if run_field is not None and run is not None:
            setattr(run, run_field, value)
            return
        object.__setattr__(self, name, value)

    def __deepcopy__(self, memo):
        copied = MeasurementMetadata(
            **{
                name: deepcopy(getattr(self, name), memo)
                for name in self.__dataclass_fields__
            }
        )
        memo[id(self)] = copied
        return copied

    def bind_run(self, run: "Run") -> None:
        """Bind legacy measurement access to the authoritative Run object."""
        object.__setattr__(self, "_run", run)

    def unbind_run(self) -> None:
        self.__dict__.pop("_run", None)

    def epsilon_for_wavelength(self) -> Optional[float]:
        if self.wavelength_nm is None:
            return None
        if abs(self.wavelength_nm - 214.0) < 0.5:
            return self.molar_absorptivity_214
        if abs(self.wavelength_nm - 280.0) < 0.5:
            return self.molar_absorptivity_280
        return None


@dataclass
class Run:
    """One physical acquisition shared by one or more detector Datasets.

    Display labels, timestamp, and all non-channel measurement/quantitation
    conditions are authoritative here.
    """

    id: str = field(default_factory=new_id)
    timestamp: str = ""
    label: str = ""
    short_label: str = ""
    sample_name: str = ""
    sample_id: str = ""
    group: str = ""
    replicate: str = ""
    tags: List[str] = field(default_factory=list)
    comments: str = ""
    instrument_name: str = ""
    method_name: str = ""
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
    gradient_preset_name: str = ""

    @classmethod
    def from_measurement(
        cls,
        metadata: MeasurementMetadata,
        gradient_preset_name: str = "",
        run_id: str = "",
        label: str = "",
        short_label: str = "",
    ) -> "Run":
        values = {
            run_field: deepcopy(getattr(metadata, measurement_field))
            for measurement_field, run_field in RUN_MEASUREMENT_FIELD_MAP.items()
        }
        return cls(
            id=run_id or new_id(),
            label=str(label or ""),
            short_label=str(short_label or label or ""),
            gradient_preset_name=str(gradient_preset_name or ""),
            **values,
        )

    def update_from_measurement(self, metadata: MeasurementMetadata) -> None:
        for measurement_field, run_field in RUN_MEASUREMENT_FIELD_MAP.items():
            setattr(self, run_field, deepcopy(getattr(metadata, measurement_field)))

    def compatibility_measurement(
        self, dataset_metadata: MeasurementMetadata
    ) -> MeasurementMetadata:
        values = {}
        for name in MeasurementMetadata.__dataclass_fields__:
            if name in DATASET_MEASUREMENT_FIELDS:
                values[name] = deepcopy(getattr(dataset_metadata, name))
            else:
                values[name] = deepcopy(
                    getattr(self, RUN_MEASUREMENT_FIELD_MAP[name])
                )
        return MeasurementMetadata(**values)


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
    """Text positioned in data coordinates with screen-point font and box size."""

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
    run_id: str = ""
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

    def __getattribute__(self, name: str):
        if name in ("label", "short_label", "gradient_preset_name"):
            run = object.__getattribute__(self, "__dict__").get("_run")
            if run is not None:
                return getattr(run, name)
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value) -> None:
        if name == "measurement":
            run = self.__dict__.get("_run")
            if run is not None:
                run.update_from_measurement(value)
                object.__setattr__(self, name, value)
                value.bind_run(run)
                return
        if name == "gradient_preset_name":
            run = self.__dict__.get("_run")
            if run is not None:
                run.gradient_preset_name = str(value or "")
        if name in ("label", "short_label"):
            run = self.__dict__.get("_run")
            if run is not None:
                setattr(run, name, str(value or ""))
        object.__setattr__(self, name, value)

    def bind_run(self, run: Run) -> None:
        local_label = object.__getattribute__(self, "label")
        local_short_label = object.__getattribute__(self, "short_label")
        if not run.label:
            run.label = str(local_label or self.original_filename or "")
        if not run.short_label:
            run.short_label = str(local_short_label or run.label or "")
        self.run_id = run.id
        object.__setattr__(self, "_run", run)
        object.__setattr__(self, "label", run.label)
        object.__setattr__(self, "short_label", run.short_label)
        self.measurement.bind_run(run)
        object.__setattr__(self, "gradient_preset_name", run.gradient_preset_name)

    def bound_run(self) -> Optional[Run]:
        return self.__dict__.get("_run")

    def effective_gradient_preset_name(self) -> str:
        run = self.bound_run()
        return run.gradient_preset_name if run is not None else self.gradient_preset_name

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
    runs: List[Run] = field(default_factory=list)
    datasets: List[Dataset] = field(default_factory=list)
    annotations: List[TextAnnotation] = field(default_factory=list)
    condition_presets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    gradient_presets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    project_path: str = ""
    dirty: bool = False

    def __post_init__(self) -> None:
        self.rebuild_run_index(create_missing=True)

    def rebuild_run_index(self, create_missing: bool = False) -> None:
        index = {}
        for run in self.runs:
            if not run.id or run.id in index:
                raise ValueError("Run IDs must be non-empty and unique")
            index[run.id] = run
        object.__setattr__(self, "_run_index", index)
        for dataset in self.datasets:
            run = index.get(dataset.run_id)
            if run is None:
                if not create_missing:
                    raise ValueError("Dataset references a missing Run: %s" % dataset.run_id)
                requested_id = dataset.run_id if dataset.run_id not in index else ""
                run = Run.from_measurement(
                    dataset.measurement,
                    dataset.gradient_preset_name,
                    run_id=requested_id,
                    label=dataset.label,
                    short_label=dataset.short_label,
                )
                self.runs.append(run)
                index[run.id] = run
            dataset.bind_run(run)

    def run_for(self, dataset: Dataset) -> Run:
        index = self.__dict__.get("_run_index", {})
        run = index.get(dataset.run_id)
        if run is None:
            raise ValueError("Dataset references a missing Run: %s" % dataset.run_id)
        return run

    def add_dataset(self, dataset: Dataset, run: Optional[Run] = None) -> Run:
        index = self.__dict__.get("_run_index", {})
        selected = run or index.get(dataset.run_id)
        if selected is None:
            selected = Run.from_measurement(
                dataset.measurement,
                dataset.gradient_preset_name,
                run_id=dataset.run_id,
                label=dataset.label,
                short_label=dataset.short_label,
            )
            if selected.id in index:
                raise ValueError("Duplicate Run ID: %s" % selected.id)
            self.runs.append(selected)
            index[selected.id] = selected
        elif not selected.id:
            raise ValueError("Run ID must be non-empty")
        elif selected.id in index:
            selected = index[selected.id]
        else:
            self.runs.append(selected)
            index[selected.id] = selected
        dataset.bind_run(selected)
        self.datasets.append(dataset)
        return selected

    def remove_dataset_at(self, index: int) -> Dataset:
        dataset = self.datasets.pop(index)
        run_id = dataset.run_id
        if not any(item.run_id == run_id for item in self.datasets):
            self.runs[:] = [run for run in self.runs if run.id != run_id]
            self.__dict__.get("_run_index", {}).pop(run_id, None)
        return dataset

    def _remove_orphan_runs(self) -> None:
        used = {dataset.run_id for dataset in self.datasets}
        self.runs[:] = [run for run in self.runs if run.id in used]
        object.__setattr__(self, "_run_index", {run.id: run for run in self.runs})

    def group_datasets_into_run(
        self, datasets: List[Dataset], target_run: Run
    ) -> None:
        """Bind explicit Dataset choices to one authoritative existing Run."""
        selected_ids = {dataset.id for dataset in datasets}
        project_ids = {dataset.id for dataset in self.datasets}
        if len(selected_ids) < 2:
            raise ValueError("Select at least two Datasets to group")
        if not selected_ids.issubset(project_ids):
            raise ValueError("Every grouped Dataset must belong to the Project")
        indexed = self.__dict__.get("_run_index", {}).get(target_run.id)
        if indexed is None or indexed is not target_run:
            raise ValueError("The target Run must belong to the Project")
        for dataset in self.datasets:
            if dataset.id in selected_ids:
                dataset.bind_run(target_run)
        self._remove_orphan_runs()

    def ungroup_datasets(self, datasets: List[Dataset]) -> None:
        """Give every explicit Dataset its own snapshot of shared Run values."""
        selected_ids = {dataset.id for dataset in datasets}
        project_ids = {dataset.id for dataset in self.datasets}
        if not selected_ids:
            raise ValueError("Select at least one Dataset to ungroup")
        if not selected_ids.issubset(project_ids):
            raise ValueError("Every ungrouped Dataset must belong to the Project")
        replacements = []
        for dataset in self.datasets:
            if dataset.id not in selected_ids:
                continue
            replacements.append(
                (
                    dataset,
                    Run.from_measurement(
                        dataset.measurement,
                        dataset.effective_gradient_preset_name(),
                        label=dataset.label,
                        short_label=dataset.short_label,
                    ),
                )
            )
        for dataset, run in replacements:
            self.runs.append(run)
            self.__dict__.get("_run_index", {})[run.id] = run
            dataset.bind_run(run)
        self._remove_orphan_runs()

    def replace_dataset_measurement(
        self, dataset: Dataset, metadata: MeasurementMetadata
    ) -> None:
        run = self.run_for(dataset)
        run.update_from_measurement(metadata)
        object.__setattr__(dataset, "measurement", metadata)
        dataset.bind_run(run)

    def compatibility_measurement_for(
        self, dataset: Dataset
    ) -> MeasurementMetadata:
        return self.run_for(dataset).compatibility_measurement(dataset.measurement)
