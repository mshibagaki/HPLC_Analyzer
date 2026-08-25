from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Dict, Iterable, List, Sequence, Tuple

from . import APP_VERSION, PROJECT_SCHEMA_VERSION
from .models import Dataset, Project


DATABASE_SCHEMA_VERSION = 2


class LabDatabaseError(RuntimeError):
    pass


def _connect(path: str) -> sqlite3.Connection:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = sqlite3.connect(str(destination), timeout=15.0)
    except sqlite3.Error as exc:
        raise LabDatabaseError("Could not open the lab database: %s" % exc) from exc
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 15000")
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database(path: str) -> str:
    if not str(path or "").strip():
        raise LabDatabaseError("Lab database path is empty")
    connection = _connect(path)
    try:
        with connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS database_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    analysis_date TEXT,
                    column_name TEXT,
                    condition_name TEXT,
                    author TEXT,
                    project_path TEXT,
                    project_created_at TEXT,
                    project_modified_at TEXT,
                    saved_with_version TEXT,
                    project_schema_version INTEGER,
                    dataset_count INTEGER NOT NULL DEFAULT 0,
                    total_peak_count INTEGER NOT NULL DEFAULT 0,
                    result_summary TEXT,
                    synced_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS datasets (
                    project_id TEXT NOT NULL,
                    dataset_id TEXT NOT NULL,
                    order_index INTEGER NOT NULL,
                    label TEXT,
                    legend_label TEXT,
                    sample_name TEXT,
                    sample_id TEXT,
                    analyte_name TEXT,
                    sample_group TEXT,
                    replicate TEXT,
                    tags TEXT,
                    comments TEXT,
                    wavelength_nm REAL,
                    instrument_name TEXT,
                    method_name TEXT,
                    acquisition_datetime TEXT,
                    column_name TEXT,
                    column_temperature_c REAL,
                    injection_volume_ul REAL,
                    flow_rate_ml_min REAL,
                    cell_path_length_cm REAL,
                    aux_range_au_per_v REAL,
                    solvent_a TEXT,
                    solvent_b TEXT,
                    solvent_c TEXT,
                    solvent_d TEXT,
                    gradient_preset_name TEXT,
                    original_path TEXT,
                    source_sha256 TEXT,
                    peak_count INTEGER NOT NULL DEFAULT 0,
                    major_peak_retention_min REAL,
                    major_peak_area_uv_min REAL,
                    total_area_uv_min REAL,
                    total_amount_nmol REAL,
                    total_amount_ug REAL,
                    PRIMARY KEY (project_id, dataset_id),
                    FOREIGN KEY (project_id) REFERENCES projects(project_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS gradient_points (
                    project_id TEXT NOT NULL,
                    dataset_id TEXT NOT NULL,
                    point_index INTEGER NOT NULL,
                    time_min REAL NOT NULL,
                    a_pct REAL NOT NULL,
                    b_pct REAL NOT NULL,
                    c_pct REAL NOT NULL,
                    d_pct REAL NOT NULL,
                    flow_ml_min REAL,
                    PRIMARY KEY (project_id, dataset_id, point_index),
                    FOREIGN KEY (project_id, dataset_id)
                        REFERENCES datasets(project_id, dataset_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS peaks (
                    project_id TEXT NOT NULL,
                    dataset_id TEXT NOT NULL,
                    peak_id TEXT NOT NULL,
                    peak_index INTEGER NOT NULL,
                    start_min REAL,
                    end_min REAL,
                    retention_time_min REAL,
                    raw_height_uv REAL,
                    raw_area_uv_min REAL,
                    area_mau_min REAL,
                    area_percent REAL,
                    fwhm_min REAL,
                    gradient_a_pct REAL,
                    gradient_b_pct REAL,
                    gradient_c_pct REAL,
                    gradient_d_pct REAL,
                    amount_nmol REAL,
                    amount_ug REAL,
                    integration_source TEXT,
                    PRIMARY KEY (project_id, dataset_id, peak_id),
                    FOREIGN KEY (project_id, dataset_id)
                        REFERENCES datasets(project_id, dataset_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_projects_analysis_date
                    ON projects(analysis_date);
                CREATE INDEX IF NOT EXISTS idx_datasets_sample
                    ON datasets(sample_name);
                CREATE INDEX IF NOT EXISTS idx_datasets_column
                    ON datasets(column_name);
                CREATE INDEX IF NOT EXISTS idx_peaks_retention
                    ON peaks(retention_time_min);
                """
            )
            current = connection.execute(
                "SELECT value FROM database_meta WHERE key = 'schema_version'"
            ).fetchone()
            if current is not None and int(current[0]) > DATABASE_SCHEMA_VERSION:
                raise LabDatabaseError(
                    "The lab database was created by a newer HPLC Analyzer version"
                )
            connection.execute(
                "INSERT OR REPLACE INTO database_meta(key, value) VALUES (?, ?)",
                ("schema_version", str(DATABASE_SCHEMA_VERSION)),
            )
    except (sqlite3.Error, ValueError) as exc:
        if isinstance(exc, LabDatabaseError):
            raise
        raise LabDatabaseError("Could not initialize the lab database: %s" % exc) from exc
    finally:
        connection.close()
    return str(Path(path).expanduser().resolve())


def _solvent_text(dataset: Dataset, line: str) -> str:
    solvent = dataset.measurement.solvents.get(line)
    if solvent is None:
        return ""
    if solvent.name and solvent.composition:
        return "%s: %s" % (solvent.name, solvent.composition)
    return solvent.name or solvent.composition or ""


def _dataset_result(dataset: Dataset) -> Tuple[int, float, float, float, float, float]:
    peaks = list(dataset.peaks)
    areas = [float(peak.raw_area_uv_min) for peak in peaks if peak.raw_area_uv_min is not None]
    amounts_nmol = [float(peak.amount_nmol) for peak in peaks if peak.amount_nmol is not None]
    amounts_ug = [float(peak.amount_ug) for peak in peaks if peak.amount_ug is not None]
    major = max(
        (peak for peak in peaks if peak.raw_area_uv_min is not None),
        key=lambda peak: float(peak.raw_area_uv_min),
        default=None,
    )
    return (
        len(peaks),
        float(major.retention_time_min) if major and major.retention_time_min is not None else None,
        float(major.raw_area_uv_min) if major else None,
        sum(areas) if areas else None,
        sum(amounts_nmol) if amounts_nmol else None,
        sum(amounts_ug) if amounts_ug else None,
    )


def sync_project_to_database(path: str, project: Project) -> None:
    """Atomically replace one project's complete database representation."""
    initialize_database(path)
    connection = _connect(path)
    synced_at = datetime.now().isoformat(timespec="seconds")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM projects WHERE project_id = ?", (project.project_id,))
        connection.execute(
            """
            INSERT INTO projects (
                project_id, title, analysis_date, column_name, condition_name,
                author, project_path, project_created_at, project_modified_at,
                saved_with_version, project_schema_version, dataset_count, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project.project_id,
                project.title,
                project.analysis_date,
                project.column_name,
                project.condition_name,
                project.author,
                project.project_path,
                project.created_at,
                project.modified_at,
                APP_VERSION,
                PROJECT_SCHEMA_VERSION,
                len(project.datasets),
                synced_at,
            ),
        )
        for order_index, dataset in enumerate(project.datasets):
            meta = dataset.measurement
            connection.execute(
                """
                INSERT INTO datasets (
                    project_id, dataset_id, order_index, label, legend_label,
                    sample_name, sample_id, analyte_name, sample_group, replicate,
                    tags, comments, wavelength_nm, instrument_name, method_name,
                    acquisition_datetime, column_name, column_temperature_c,
                    injection_volume_ul, flow_rate_ml_min, cell_path_length_cm,
                    aux_range_au_per_v, solvent_a, solvent_b, solvent_c, solvent_d,
                    gradient_preset_name, original_path, source_sha256
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    project.project_id,
                    dataset.id,
                    order_index,
                    dataset.label,
                    dataset.legend_label(),
                    meta.sample_name,
                    meta.sample_id,
                    meta.analyte_name,
                    meta.group,
                    meta.replicate,
                    "; ".join(meta.tags),
                    meta.comments,
                    meta.wavelength_nm,
                    meta.instrument_name,
                    meta.method_name,
                    meta.acquisition_datetime,
                    meta.column_name,
                    meta.column_temperature_c,
                    meta.injection_volume_ul,
                    meta.flow_rate_ml_min,
                    meta.cell_path_length_cm,
                    meta.aux_range_au_per_v,
                    _solvent_text(dataset, "A"),
                    _solvent_text(dataset, "B"),
                    _solvent_text(dataset, "C"),
                    _solvent_text(dataset, "D"),
                    dataset.effective_gradient_preset_name(),
                    dataset.original_path,
                    dataset.sha256,
                ),
            )
            for point_index, point in enumerate(meta.gradient):
                connection.execute(
                    """
                    INSERT INTO gradient_points (
                        project_id, dataset_id, point_index, time_min,
                        a_pct, b_pct, c_pct, d_pct, flow_ml_min
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project.project_id,
                        dataset.id,
                        point_index,
                        point.time_min,
                        point.a_pct,
                        point.b_pct,
                        point.c_pct,
                        point.d_pct,
                        point.flow_ml_min,
                    ),
                )
        connection.commit()
    except sqlite3.Error as exc:
        connection.rollback()
        raise LabDatabaseError("Could not update the lab database: %s" % exc) from exc
    finally:
        connection.close()


_SECTIONS: Dict[str, Tuple[str, str]] = {
    "projects": (
        "Projects",
        """
        SELECT analysis_date, title, column_name, condition_name, author,
               dataset_count, project_path, project_modified_at, synced_at,
               project_id
        FROM projects
        ORDER BY REPLACE(analysis_date, '-', '') DESC,
                 project_modified_at DESC, title
        """,
    ),
    "datasets": (
        "Chromatograms",
        """
        SELECT p.analysis_date, p.title AS project_title, p.author,
               d.order_index + 1 AS chromatogram_no, d.label, d.wavelength_nm,
               d.sample_name, d.sample_id, d.analyte_name, d.sample_group,
               d.replicate, d.column_name, d.method_name, d.instrument_name,
               d.acquisition_datetime, d.flow_rate_ml_min,
               d.column_temperature_c, d.injection_volume_ul,
               d.gradient_preset_name, d.comments, d.original_path,
               p.project_id, d.dataset_id
        FROM datasets d JOIN projects p ON p.project_id = d.project_id
        ORDER BY REPLACE(p.analysis_date, '-', '') DESC,
                 p.project_modified_at DESC, p.title, d.order_index
        """,
    ),
}


def _number(value) -> str:
    if value is None:
        return ""
    return "%g" % float(value)


def _date_key(analysis_date: str, modified_at: str = "") -> str:
    digits = "".join(character for character in str(analysis_date or "") if character.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    fallback = "".join(character for character in str(modified_at or "") if character.isdigit())
    return fallback[:8]


def _gradient_program_section(connection: sqlite3.Connection):
    """Return one row per unique time/percentage/flow gradient program."""

    cursor = connection.execute(
        """
        SELECT p.analysis_date, p.project_modified_at, p.title, p.author,
               d.column_name, d.gradient_preset_name,
               d.solvent_a, d.solvent_b, d.solvent_c, d.solvent_d,
               p.project_id, d.dataset_id, g.point_index,
               g.time_min, g.a_pct, g.b_pct, g.c_pct, g.d_pct, g.flow_ml_min
        FROM gradient_points g
        JOIN datasets d
          ON d.project_id = g.project_id AND d.dataset_id = g.dataset_id
        JOIN projects p ON p.project_id = g.project_id
        ORDER BY p.project_id, d.dataset_id, g.point_index
        """
    )
    uses = {}
    for row in cursor.fetchall():
        use_key = (row["project_id"], row["dataset_id"])
        use = uses.setdefault(
            use_key,
            {
                "analysis_date": row["analysis_date"] or "",
                "modified_at": row["project_modified_at"] or "",
                "project": row["title"] or "",
                "author": row["author"] or "",
                "column": row["column_name"] or "",
                "name": row["gradient_preset_name"] or "",
                "solvents": tuple(row["solvent_%s" % line] or "" for line in "abcd"),
                "points": [],
            },
        )
        use["points"].append(
            (
                row["time_min"],
                row["a_pct"],
                row["b_pct"],
                row["c_pct"],
                row["d_pct"],
                row["flow_ml_min"],
            )
        )

    programs = {}
    for use in uses.values():
        signature = tuple(use["points"])
        program = programs.setdefault(
            signature,
            {
                "latest_date": use["analysis_date"],
                "latest_key": _date_key(use["analysis_date"], use["modified_at"]),
                "names": set(),
                "projects": set(),
                "authors": set(),
                "columns": set(),
                "usage_count": 0,
                "solvents": [set() for _line in "ABCD"],
                "points": use["points"],
            },
        )
        current_key = _date_key(use["analysis_date"], use["modified_at"])
        if current_key > program["latest_key"]:
            program["latest_key"] = current_key
            program["latest_date"] = use["analysis_date"]
        program["names"].add(use["name"] or "(unnamed)")
        if use["project"]:
            program["projects"].add(use["project"])
        if use["author"]:
            program["authors"].add(use["author"])
        if use["column"]:
            program["columns"].add(use["column"])
        for index, solvent in enumerate(use["solvents"]):
            if solvent:
                program["solvents"][index].add(solvent)
        program["usage_count"] += 1

    rows = []
    ordered = sorted(
        programs.values(),
        key=lambda item: (item["latest_key"], sorted(item["names"])),
        reverse=True,
    )
    for program in ordered:
        lines = []
        for time_min, a_pct, b_pct, c_pct, d_pct, flow in program["points"]:
            line = "%s min: A %s%%, B %s%%, C %s%%, D %s%%" % (
                _number(time_min),
                _number(a_pct),
                _number(b_pct),
                _number(c_pct),
                _number(d_pct),
            )
            if flow is not None:
                line += ", %s mL/min" % _number(flow)
            lines.append(line)
        rows.append(
            (
                program["latest_date"],
                " / ".join(sorted(program["names"])),
                "\n".join(lines),
                *(" / ".join(sorted(values)) for values in program["solvents"]),
                " / ".join(sorted(program["columns"])),
                " / ".join(sorted(program["projects"])),
                " / ".join(sorted(program["authors"])),
                program["usage_count"],
            )
        )
    columns = [
        "latest_analysis_date",
        "gradient_names",
        "program",
        "solvent_a",
        "solvent_b",
        "solvent_c",
        "solvent_d",
        "columns",
        "projects",
        "authors",
        "usage_count",
    ]
    return columns, rows


def database_sections(path: str) -> Dict[str, Tuple[List[str], List[Tuple]]]:
    initialize_database(path)
    connection = _connect(path)
    result: Dict[str, Tuple[List[str], List[Tuple]]] = {}
    try:
        for key, (_title, query) in _SECTIONS.items():
            cursor = connection.execute(query)
            columns = [description[0] for description in cursor.description]
            rows = [tuple(row) for row in cursor.fetchall()]
            result[key] = (columns, rows)
        result["gradients"] = _gradient_program_section(connection)
    except sqlite3.Error as exc:
        raise LabDatabaseError("Could not read the lab database: %s" % exc) from exc
    finally:
        connection.close()
    return result


def database_section_titles() -> Dict[str, str]:
    titles = {key: value[0] for key, value in _SECTIONS.items()}
    titles["gradients"] = "Gradients"
    return titles


def export_database_csvs(path: str, directory: str) -> List[str]:
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    sections = database_sections(path)
    exported: List[str] = []
    for key, (columns, rows) in sections.items():
        output = destination / ("HPLC_Lab_Database_%s.csv" % key)
        with output.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(columns)
            writer.writerows(rows)
        exported.append(str(output))
    return exported
