"""Pure manifest migrations for HPLC Analyzer project files.

Migrations in this module operate on JSON-compatible dictionaries only.  They
must not parse embedded ASCII data, construct GUI objects, or touch NumPy
arrays.  This keeps project compatibility changes deterministic and testable
on both supported Windows builds.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Dict

from . import PROJECT_FORMAT_MAJOR, PROJECT_SCHEMA_VERSION


Manifest = Dict[str, Any]
Migration = Callable[[Manifest], Manifest]


class ProjectMigrationError(ValueError):
    """Raised when a project manifest cannot be migrated safely."""


def _with_schema(manifest: Manifest, schema_version: int) -> Manifest:
    migrated = deepcopy(manifest)
    migrated["schema_version"] = schema_version
    return migrated


def _legacy_noop(manifest: Manifest, next_version: int) -> Manifest:
    return _with_schema(manifest, next_version)


def migrate_legacy_0_to_1(manifest: Manifest) -> Manifest:
    return _legacy_noop(manifest, 1)


def migrate_legacy_1_to_2(manifest: Manifest) -> Manifest:
    return _legacy_noop(manifest, 2)


def migrate_legacy_2_to_3(manifest: Manifest) -> Manifest:
    return _legacy_noop(manifest, 3)


def migrate_legacy_3_to_4(manifest: Manifest) -> Manifest:
    return _legacy_noop(manifest, 4)


def migrate_legacy_4_to_5(manifest: Manifest) -> Manifest:
    return _legacy_noop(manifest, 5)


def migrate_legacy_5_to_6(manifest: Manifest) -> Manifest:
    return _legacy_noop(manifest, 6)


def migrate_legacy_6_to_7(manifest: Manifest) -> Manifest:
    migrated = _with_schema(manifest, 7)
    method = migrated.setdefault("method", {})
    if not isinstance(method, dict):
        raise ProjectMigrationError("Project method must be an object")
    # v0.7 replaced the old fixed wheel-zoom default with cursor-sensitive
    # zoom.  Preserve the existing loader behavior for schema 0-6 projects.
    method["zoom_axis"] = "auto"
    return migrated


def migrate_legacy_7_to_100(manifest: Manifest) -> Manifest:
    migrated = _with_schema(manifest, 100)
    migrated["format_major"] = PROJECT_FORMAT_MAJOR
    return migrated


def migrate_100_to_101(manifest: Manifest) -> Manifest:
    migrated = _with_schema(manifest, 101)
    method = migrated.setdefault("method", {})
    if not isinstance(method, dict):
        raise ProjectMigrationError("Project method must be an object")
    for field_name in (
        "axis_label_font_family",
        "tick_label_font_family",
        "legend_font_family",
        "retention_label_font_family",
    ):
        if not str(method.get(field_name, "") or "").strip():
            method[field_name] = "Arial"
    return migrated


def _migrate_peak_area_units(peak: Manifest) -> None:
    raw_min = peak.get("raw_area_uv_min")
    raw_sec = peak.get("raw_area_uv_sec")
    if raw_sec is None and raw_min is not None:
        peak["raw_area_uv_sec"] = float(raw_min) * 60.0
    elif raw_min is None and raw_sec is not None:
        peak["raw_area_uv_min"] = float(raw_sec) / 60.0

    mau_min = peak.get("area_mau_min")
    mau_sec = peak.get("area_mau_sec")
    if mau_sec is None and mau_min is not None:
        peak["area_mau_sec"] = float(mau_min) * 60.0
    elif mau_min is None and mau_sec is not None:
        peak["area_mau_min"] = float(mau_sec) / 60.0


def migrate_101_to_102(manifest: Manifest) -> Manifest:
    migrated = _with_schema(manifest, 102)
    datasets = migrated.get("datasets", [])
    if datasets is None:
        datasets = []
    if not isinstance(datasets, list):
        raise ProjectMigrationError("Project datasets must be an array")
    for dataset in datasets:
        if not isinstance(dataset, dict):
            raise ProjectMigrationError("Each project dataset must be an object")
        peaks = dataset.get("peaks", [])
        if peaks is None:
            peaks = []
        if not isinstance(peaks, list):
            raise ProjectMigrationError("Dataset peaks must be an array")
        for peak in peaks:
            if not isinstance(peak, dict):
                raise ProjectMigrationError("Each peak must be an object")
            try:
                _migrate_peak_area_units(peak)
            except (TypeError, ValueError) as exc:
                raise ProjectMigrationError(
                    "Peak area compatibility values must be numeric"
                ) from exc
    return migrated


_RUN_MEASUREMENT_FIELDS = (
    "sample_name",
    "sample_id",
    "group",
    "replicate",
    "tags",
    "comments",
    "instrument_name",
    "method_name",
    "flow_rate_ml_min",
    "column_name",
    "column_temperature_c",
    "injection_volume_ul",
    "cell_path_length_cm",
    "analyte_name",
    "molar_absorptivity_214",
    "molar_absorptivity_280",
    "molecular_weight_g_mol",
    "solvents",
    "gradient",
)

_RUN_MEASUREMENT_DEFAULTS = {
    "sample_name": "",
    "sample_id": "",
    "group": "",
    "replicate": "",
    "tags": [],
    "comments": "",
    "instrument_name": "",
    "method_name": "",
    "flow_rate_ml_min": None,
    "column_name": "",
    "column_temperature_c": None,
    "injection_volume_ul": None,
    "cell_path_length_cm": 1.0,
    "analyte_name": "",
    "molar_absorptivity_214": None,
    "molar_absorptivity_280": None,
    "molecular_weight_g_mol": None,
    "solvents": {},
    "gradient": [],
}


def _migration_run_id(dataset: Manifest, index: int, used: set) -> str:
    source_id = str(dataset.get("id", "") or (index + 1))
    base = "run-" + source_id
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = "%s-%d" % (base, suffix)
        suffix += 1
    used.add(candidate)
    return candidate


def migrate_102_to_103(manifest: Manifest) -> Manifest:
    """Create exactly one authoritative Run for each legacy Dataset."""
    migrated = _with_schema(manifest, 103)
    datasets = migrated.get("datasets", [])
    if datasets is None:
        datasets = []
        migrated["datasets"] = datasets
    if not isinstance(datasets, list):
        raise ProjectMigrationError("Project datasets must be an array")

    runs = []
    used_ids = set()
    for index, dataset in enumerate(datasets):
        if not isinstance(dataset, dict):
            raise ProjectMigrationError("Each project dataset must be an object")
        measurement = dataset.get("measurement", {})
        if measurement is None:
            measurement = {}
        if not isinstance(measurement, dict):
            raise ProjectMigrationError("Dataset measurement must be an object")
        run_id = _migration_run_id(dataset, index, used_ids)
        run = {
            "id": run_id,
            "timestamp": deepcopy(measurement.get("acquisition_datetime", "")),
            "gradient_preset_name": deepcopy(
                dataset.get("gradient_preset_name", "")
            ),
        }
        for field_name in _RUN_MEASUREMENT_FIELDS:
            run[field_name] = deepcopy(
                measurement.get(field_name, _RUN_MEASUREMENT_DEFAULTS[field_name])
            )
        if not isinstance(run["tags"], list):
            run["tags"] = []
        if not isinstance(run["solvents"], dict):
            run["solvents"] = {}
        if not isinstance(run["gradient"], list):
            run["gradient"] = []
        dataset["run_id"] = run_id
        runs.append(run)
    migrated["runs"] = runs
    return migrated


LEGACY_MIGRATIONS: Dict[int, Migration] = {
    0: migrate_legacy_0_to_1,
    1: migrate_legacy_1_to_2,
    2: migrate_legacy_2_to_3,
    3: migrate_legacy_3_to_4,
    4: migrate_legacy_4_to_5,
    5: migrate_legacy_5_to_6,
    6: migrate_legacy_6_to_7,
    7: migrate_legacy_7_to_100,
}

V1_MIGRATIONS: Dict[int, Migration] = {
    100: migrate_100_to_101,
    101: migrate_101_to_102,
    102: migrate_102_to_103,
}


def migrate_project_manifest(manifest: Manifest) -> Manifest:
    """Return a migrated copy while leaving the caller's manifest unchanged.

    Later v1 schemas retain the existing best-effort behavior: known fields are
    loaded and unknown fields are ignored by the deserializer.  They are not
    downgraded to the current schema.
    """

    if not isinstance(manifest, dict):
        raise ProjectMigrationError("Project manifest must be an object")
    migrated = deepcopy(manifest)
    try:
        schema_version = int(migrated.get("schema_version", 0))
        format_major = int(migrated.get("format_major", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise ProjectMigrationError("Project schema version is invalid") from exc

    if schema_version < 0:
        raise ProjectMigrationError("Project schema version is invalid")
    if format_major > PROJECT_FORMAT_MAJOR:
        raise ProjectMigrationError(
            "Project was created by a newer application version"
        )
    if format_major == 0 and schema_version > 7:
        raise ProjectMigrationError(
            "Project was created by a newer application version"
        )
    if format_major == PROJECT_FORMAT_MAJOR and schema_version > PROJECT_SCHEMA_VERSION:
        return migrated

    if format_major == 0:
        while int(migrated.get("schema_version", 0)) <= 7:
            current = int(migrated.get("schema_version", 0))
            migration = LEGACY_MIGRATIONS.get(current)
            if migration is None:
                raise ProjectMigrationError(
                    "No project migration is available for legacy schema %d" % current
                )
            migrated = migration(migrated)

    while int(migrated.get("schema_version", 0)) < PROJECT_SCHEMA_VERSION:
        current = int(migrated.get("schema_version", 0))
        migration = V1_MIGRATIONS.get(current)
        if migration is None:
            # Preserve the old v1 best-effort reader for nonstandard schema
            # numbers rather than rejecting a project it previously opened.
            return migrated
        migrated = migration(migrated)
    return migrated
