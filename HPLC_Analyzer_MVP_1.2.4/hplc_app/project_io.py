from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import re
from typing import Any, Dict
import zipfile

from . import APP_NAME, APP_VERSION, PROJECT_FORMAT_MAJOR, PROJECT_SCHEMA_VERSION
from .models import (
    AnalysisMethod,
    Dataset,
    GradientPoint,
    LEGEND_COMPONENTS,
    MeasurementMetadata,
    PeakRegion,
    Project,
    Run,
    Solvent,
    TextAnnotation,
    WorkDirectory,
    sanitize_condition_presets,
)
from .parser import dataset_from_bytes
from .project_migrations import ProjectMigrationError, migrate_project_manifest


class ProjectError(ValueError):
    pass


def _safe_filename(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return clean or "source.TXT"


def _metadata_from_dict(data: Dict[str, Any]) -> MeasurementMetadata:
    if not isinstance(data, dict):
        raise ProjectError("Dataset measurement must be an object")
    copied = dict(data)
    solvents_data = copied.pop("solvents", {}) or {}
    gradient_data = copied.pop("gradient", []) or []
    if not isinstance(solvents_data, dict):
        raise ProjectError("Dataset measurement solvents must be an object")
    if not isinstance(gradient_data, list):
        raise ProjectError("Dataset measurement gradient must be an array")
    allowed = set(MeasurementMetadata.__dataclass_fields__)
    metadata = MeasurementMetadata(**{key: value for key, value in copied.items() if key in allowed})
    metadata.solvents = {
        line: Solvent(**(solvents_data.get(line, {}) or {})) for line in "ABCD"
    }
    metadata.gradient = [GradientPoint(**point) for point in gradient_data]
    return metadata


def _run_from_dict(data: Dict[str, Any]) -> Run:
    if not isinstance(data, dict):
        raise ProjectError("Each project Run must be an object")
    copied = dict(data)
    solvents_data = copied.pop("solvents", {}) or {}
    gradient_data = copied.pop("gradient", []) or []
    if not isinstance(solvents_data, dict):
        raise ProjectError("Run solvents must be an object")
    if not isinstance(gradient_data, list):
        raise ProjectError("Run gradient must be an array")
    allowed = set(Run.__dataclass_fields__)
    values = {key: value for key, value in copied.items() if key in allowed}
    run = Run(**values)
    if not run.id:
        raise ProjectError("Run ID is missing")
    run.solvents = {
        line: Solvent(**(solvents_data.get(line, {}) or {})) for line in "ABCD"
    }
    run.gradient = [GradientPoint(**point) for point in gradient_data]
    return run


def _work_directories_from_value(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProjectError("Project work directories must be an array")
    directories = []
    for item in value:
        if not isinstance(item, dict):
            raise ProjectError("Each project work directory must be an object")
        path_value = item.get("path", "")
        label_value = item.get("label", "")
        recursive = item.get("recursive", False)
        enabled = item.get("enabled", True)
        if not isinstance(path_value, str) or not isinstance(label_value, str):
            raise ProjectError("Project work directory path and label must be text")
        if not isinstance(recursive, bool) or not isinstance(enabled, bool):
            raise ProjectError("Project work directory flags must be boolean")
        path = path_value.strip()
        if not path:
            raise ProjectError("Project work directory path is missing")
        directories.append(
            WorkDirectory(
                path=path,
                label=label_value,
                recursive=recursive,
                enabled=enabled,
            )
        )
    return directories


def _method_from_dict(data: Dict[str, Any], schema_version: int = PROJECT_SCHEMA_VERSION) -> AnalysisMethod:
    allowed = set(AnalysisMethod.__dataclass_fields__)
    values = {key: value for key, value in data.items() if key in allowed}
    components = values.get("legend_components")
    if components is not None:
        if not isinstance(components, list) or not all(
            isinstance(item, str) and item in LEGEND_COMPONENTS
            for item in components
        ):
            raise ProjectError("Legend components must be a valid string array")
        values["legend_components"] = list(dict.fromkeys(components))
    separator = values.get("legend_separator")
    if separator is not None and not isinstance(separator, str):
        raise ProjectError("Legend separator must be text")
    # v0.7.0 replaces the old fixed wheel-zoom default with cursor-sensitive
    # zoom.  Existing projects therefore receive the new behaviour once, while
    # v0.7.0 projects can still persist an explicitly selected fixed mode.
    if schema_version <= 6:
        values["zoom_axis"] = "auto"
    # v1.0 used an empty family to mean Matplotlib's platform default.  v1.1
    # standardizes new and migrated projects on Arial while retaining all
    # explicitly selected font families.
    if schema_version <= 100:
        for field_name in (
            "axis_label_font_family",
            "tick_label_font_family",
            "legend_font_family",
            "retention_label_font_family",
        ):
            if not str(values.get(field_name, "") or "").strip():
                values[field_name] = "Arial"
    return AnalysisMethod(**values)


def _peak_from_dict(data: Dict[str, Any]) -> PeakRegion:
    """Load both v1.1.4 second areas and earlier v1 minute areas.

    New projects retain the minute fields as compatibility values so an older
    v1.x application does not mistake a seconds value for a minutes value.
    """
    allowed = set(PeakRegion.__dataclass_fields__)
    values = {key: value for key, value in data.items() if key in allowed}
    raw_min = values.get("raw_area_uv_min")
    raw_sec = values.get("raw_area_uv_sec")
    if raw_sec is None and raw_min is not None:
        values["raw_area_uv_sec"] = float(raw_min) * 60.0
    elif raw_min is None and raw_sec is not None:
        values["raw_area_uv_min"] = float(raw_sec) / 60.0
    mau_min = values.get("area_mau_min")
    mau_sec = values.get("area_mau_sec")
    if mau_sec is None and mau_min is not None:
        values["area_mau_sec"] = float(mau_min) * 60.0
    elif mau_min is None and mau_sec is not None:
        values["area_mau_min"] = float(mau_sec) / 60.0
    return PeakRegion(**values)


def save_project(path: str, project: Project) -> None:
    destination = os.path.abspath(path)
    if not destination.lower().endswith(".hplcproj"):
        destination += ".hplcproj"
    project.modified_at = datetime.now().isoformat(timespec="seconds")
    try:
        project.rebuild_run_index(create_missing=True)
    except ValueError as exc:
        raise ProjectError("Run references are invalid: %s" % exc) from exc
    manifest = {
        "format": "hplc-analyzer-project",
        "format_major": PROJECT_FORMAT_MAJOR,
        "schema_version": PROJECT_SCHEMA_VERSION,
        "application": APP_NAME,
        "application_version": APP_VERSION,
        "project_id": project.project_id,
        "title": project.title,
        "analysis_date": project.analysis_date,
        "column_name": project.column_name,
        "condition_name": project.condition_name,
        "author": project.author,
        "created_at": project.created_at,
        "modified_at": project.modified_at,
        "ui_language": project.ui_language,
        "method": asdict(project.method),
        "runs": [asdict(run) for run in project.runs],
        "condition_presets": sanitize_condition_presets(project.condition_presets),
        "gradient_presets": project.gradient_presets,
        "annotations": [asdict(annotation) for annotation in project.annotations],
        "work_directories": [asdict(item) for item in project.work_directories],
        "datasets": [],
    }
    used_names = set()
    for dataset in project.datasets:
        embedded = dataset.embedded_source_name
        if not embedded:
            embedded = "sources/%s_%s" % (dataset.id, _safe_filename(dataset.original_filename))
        suffix = 2
        base_embedded = embedded
        while embedded in used_names:
            stem, extension = os.path.splitext(base_embedded)
            embedded = "%s_%d%s" % (stem, suffix, extension)
            suffix += 1
        used_names.add(embedded)
        dataset.embedded_source_name = embedded
        item = dataset.to_manifest()
        item["embedded_source_name"] = embedded
        run = project.run_for(dataset)
        item["measurement"] = asdict(project.compatibility_measurement_for(dataset))
        item["gradient_preset_name"] = run.gradient_preset_name
        manifest["datasets"].append(item)

    temp_path = destination + ".tmp"
    Path(os.path.dirname(destination) or ".").mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("project.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for dataset in project.datasets:
                if not dataset.raw_bytes:
                    raise ProjectError("Raw source is missing for dataset: %s" % dataset.label)
                archive.writestr(dataset.embedded_source_name, dataset.raw_bytes)
        os.replace(temp_path, destination)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    project.project_path = destination
    project.dirty = False


def load_project(path: str) -> Project:
    source = os.path.abspath(path)
    try:
        with zipfile.ZipFile(source, "r") as archive:
            manifest = json.loads(archive.read("project.json").decode("utf-8"))
            if manifest.get("format") != "hplc-analyzer-project":
                raise ProjectError("This is not an HPLC Analyzer project")
            schema_version = int(manifest.get("schema_version", 0))
            format_major = int(manifest.get("format_major", 0) or 0)
            if format_major > PROJECT_FORMAT_MAJOR:
                raise ProjectError("Project was created by a newer application version")
            # v1 uses the stable format_major field as its compatibility
            # boundary. Unknown fields are ignored by the dataclass loaders,
            # allowing later v1.x readers to extend the manifest without
            # breaking v1.0 files. Legacy v0.x projects use schema numbers 1-7.
            if format_major == 0 and schema_version > 7:
                raise ProjectError("Project was created by a newer application version")
            try:
                manifest = migrate_project_manifest(manifest)
            except ProjectMigrationError as exc:
                raise ProjectError("Could not migrate project: %s" % exc) from exc
            schema_version = int(manifest.get("schema_version", schema_version))
            runs_data = manifest.get("runs", [])
            if not isinstance(runs_data, list):
                raise ProjectError("Project runs must be an array")
            try:
                project = Project(
                    project_id=manifest.get("project_id", "") or Project().project_id,
                    title=manifest.get("title", "Untitled project"),
                    analysis_date=manifest.get("analysis_date", ""),
                    column_name=manifest.get("column_name", ""),
                    condition_name=manifest.get("condition_name", ""),
                    author=manifest.get("author", ""),
                    created_at=manifest.get("created_at", ""),
                    modified_at=manifest.get("modified_at", ""),
                    ui_language=manifest.get("ui_language", "ja"),
                    method=_method_from_dict(manifest.get("method", {}), schema_version),
                    runs=[_run_from_dict(run) for run in runs_data],
                    condition_presets=sanitize_condition_presets(
                        manifest.get("condition_presets", {}) or {}
                    ),
                    gradient_presets=manifest.get("gradient_presets", {}) or {},
                    annotations=[
                        TextAnnotation(
                            **{
                                key: value
                                for key, value in annotation.items()
                                if key in TextAnnotation.__dataclass_fields__
                            }
                        )
                        for annotation in (manifest.get("annotations", []) or [])
                        if isinstance(annotation, dict)
                    ],
                    work_directories=_work_directories_from_value(
                        manifest.get("work_directories", [])
                    ),
                    project_path=source,
                )
            except ValueError as exc:
                raise ProjectError("Project Run collection is invalid: %s" % exc) from exc
            datasets_data = manifest.get("datasets", [])
            if not isinstance(datasets_data, list):
                raise ProjectError("Project datasets must be an array")
            for item in datasets_data:
                if not isinstance(item, dict):
                    raise ProjectError("Each project dataset must be an object")
                embedded = item.get("embedded_source_name", "")
                if not embedded:
                    raise ProjectError("Embedded source path is missing")
                raw = archive.read(embedded)
                dataset = dataset_from_bytes(raw, source_path=item.get("original_path", ""), label=item.get("label", ""))
                scalar_fields = (
                    "id",
                    "run_id",
                    "label",
                    "short_label",
                    "original_filename",
                    "original_directory",
                    "original_path",
                    "embedded_source_name",
                    "sha256",
                    "imported_at",
                    "source_metadata",
                    "source_peak_table",
                    "gradient_preset_name",
                    "y_axis",
                    "x_shift_min",
                    "offset",
                    "visible",
                    "color",
                )
                for field_name in scalar_fields:
                    if field_name in item:
                        setattr(dataset, field_name, item[field_name])
                dataset.measurement = _metadata_from_dict(item.get("measurement", {}))
                dataset.peaks = [
                    _peak_from_dict(peak)
                    for peak in item.get("peaks", [])
                    if isinstance(peak, dict)
                ]
                try:
                    run = project.run_for(dataset)
                except ValueError as exc:
                    raise ProjectError("Dataset Run reference is invalid: %s" % exc) from exc
                project.add_dataset(dataset, run=run)
    except (KeyError, OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise ProjectError("Could not open project: %s" % exc) from exc
    project.dirty = False
    return project
