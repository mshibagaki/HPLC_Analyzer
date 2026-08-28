from __future__ import annotations

from datetime import datetime
import re
from typing import Dict, Iterable

from .models import Project


_WINDOWS_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_SEPARATORS = re.compile(r"[\s_]+")
_DASHES = re.compile(r"-+")


def sanitize_filename_component(value: str, fallback: str) -> str:
    """Make one readable Windows-safe component for the project filename."""
    text = _WINDOWS_ILLEGAL.sub("-", str(value or "").strip())
    text = _SEPARATORS.sub("-", text)
    text = _DASHES.sub("-", text).strip(" .-")
    if not text:
        text = fallback
    # Keep the full filename comfortably below common Windows path limits.
    return text[:40].rstrip(" .-") or fallback


def normalize_analysis_date(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) >= 8:
        candidate = digits[:8]
        try:
            datetime.strptime(candidate, "%Y%m%d")
            return candidate
        except ValueError:
            pass
    return ""


def _first_acquisition_date(project: Project) -> str:
    for dataset in project.datasets:
        normalized = normalize_analysis_date(dataset.measurement.acquisition_datetime)
        if normalized:
            return normalized
    return ""


def _single_or_mixed(values: Iterable[str], empty: str, mixed: str) -> str:
    unique = []
    for value in values:
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in unique:
            unique.append(cleaned)
    if len(unique) == 1:
        return unique[0]
    if len(unique) > 1:
        return mixed
    return empty


def suggest_project_name_parts(
    project: Project,
    default_author: str = "",
    today: str = "",
) -> Dict[str, str]:
    date = normalize_analysis_date(project.analysis_date)
    if not date:
        date = _first_acquisition_date(project)
    if not date:
        date = normalize_analysis_date(today) or datetime.now().strftime("%Y%m%d")

    title = str(project.title or "").strip()
    if not title or title.lower() in ("untitled project", "project"):
        title = _single_or_mixed(
            (
                dataset.measurement.sample_name
                or dataset.measurement.analyte_name
                or dataset.label
                for dataset in project.datasets
            ),
            "Title",
            "MultipleSamples",
        )

    column = str(project.column_name or "").strip() or _single_or_mixed(
        (dataset.measurement.column_name for dataset in project.datasets),
        "Column",
        "MultipleColumns",
    )
    condition = str(project.condition_name or "").strip() or _single_or_mixed(
        (
            dataset.measurement.method_name
            or dataset.effective_gradient_preset_name()
            for dataset in project.datasets
        ),
        "Condition",
        "MultipleConditions",
    )
    author = str(project.author or default_author or "").strip() or "Author"
    return {
        "date": date,
        "title": title,
        "column": column,
        "condition": condition,
        "author": author,
    }


def build_project_filename(parts: Dict[str, str]) -> str:
    date = normalize_analysis_date(parts.get("date", "")) or datetime.now().strftime(
        "%Y%m%d"
    )
    components = (
        date,
        sanitize_filename_component(parts.get("title", ""), "Title"),
        sanitize_filename_component(parts.get("column", ""), "Column"),
        sanitize_filename_component(parts.get("condition", ""), "Condition"),
        sanitize_filename_component(parts.get("author", ""), "Author"),
    )
    return "_".join(components) + ".hplcproj"


def apply_project_name_parts(project: Project, parts: Dict[str, str]) -> None:
    project.analysis_date = normalize_analysis_date(parts.get("date", ""))
    project.title = str(parts.get("title", "")).strip() or "Untitled project"
    project.column_name = str(parts.get("column", "")).strip()
    project.condition_name = str(parts.get("condition", "")).strip()
    project.author = str(parts.get("author", "")).strip()
