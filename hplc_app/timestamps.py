"""Deterministic acquisition timestamp extraction with explicit provenance."""

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Dict


KNOWN_TIMESTAMP_FILENAME_PATTERNS = (
    (re.compile(r"^(\d{8})_(\d{6})$"), "%Y%m%d%H%M%S"),
    (re.compile(r"^(\d{8})-(\d{6})$"), "%Y%m%d%H%M%S"),
    (
        re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})$"),
        "%Y-%m-%d%H-%M-%S",
    ),
    (
        re.compile(r"^(\d{4}_\d{2}_\d{2})_(\d{2}_\d{2}_\d{2})$"),
        "%Y_%m_%d%H_%M_%S",
    ),
)
EXPLICIT_TIMESTAMP_FORMATS = (
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
)
GCD_FILETIME_UTC_KEY = "GCD.File Property Timestamp UTC"
ACQUISITION_TIMESTAMP_SOURCE_KEY = "Acquisition.Timestamp Source"
TIMESTAMP_SOURCE_VENDOR = "Sample Information.Acquisition Date"
TIMESTAMP_SOURCE_GCD_FILETIME = "GCD.File Property FILETIME"
TIMESTAMP_SOURCE_FILENAME = "strict filename pattern"
TIMESTAMP_SOURCE_FILE_MTIME = "file modification time"
TIMESTAMP_SOURCE_UNAVAILABLE = "unavailable"


def _iso_timestamp(value, formats):
    for timestamp_format in formats:
        try:
            parsed = datetime.strptime(value, timestamp_format)
        except ValueError:
            continue
        return parsed.isoformat(timespec="seconds")
    return ""


def timestamp_from_filename(filename: str) -> str:
    """Return a timestamp only for an explicitly supported complete pattern."""
    stem = Path(str(filename or "")).stem
    for pattern, timestamp_format in KNOWN_TIMESTAMP_FILENAME_PATTERNS:
        match = pattern.fullmatch(stem)
        if match is None:
            continue
        return _iso_timestamp("".join(match.groups()), (timestamp_format,))
    return ""


def _record_timestamp_source(metadata, source: str) -> None:
    if isinstance(metadata, dict):
        metadata[ACQUISITION_TIMESTAMP_SOURCE_KEY] = source


def _local_timestamp_from_utc(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return ""
    return parsed.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


def acquisition_timestamp(
    metadata: Dict[str, str],
    filename: str,
    modified_timestamp: str = "",
) -> str:
    """Resolve acquisition time without hiding which fallback supplied it."""

    explicit = str(
        (metadata or {}).get("Sample Information.Acquisition Date", "") or ""
    ).strip()
    if explicit:
        _record_timestamp_source(metadata, TIMESTAMP_SOURCE_VENDOR)
        return _iso_timestamp(explicit, EXPLICIT_TIMESTAMP_FORMATS) or explicit
    gcd_filetime = _local_timestamp_from_utc(
        str((metadata or {}).get(GCD_FILETIME_UTC_KEY, "") or "").strip()
    )
    if gcd_filetime:
        _record_timestamp_source(metadata, TIMESTAMP_SOURCE_GCD_FILETIME)
        return gcd_filetime
    from_filename = timestamp_from_filename(filename)
    if from_filename:
        _record_timestamp_source(metadata, TIMESTAMP_SOURCE_FILENAME)
        return from_filename
    from_modified_time = _iso_timestamp(
        str(modified_timestamp or "").strip(), EXPLICIT_TIMESTAMP_FORMATS
    )
    if from_modified_time:
        _record_timestamp_source(metadata, TIMESTAMP_SOURCE_FILE_MTIME)
        return from_modified_time
    _record_timestamp_source(metadata, TIMESTAMP_SOURCE_UNAVAILABLE)
    return ""


def run_id_timestamp(value: str) -> str:
    """Format a known acquisition time without inventing missing date/time."""
    normalized = _iso_timestamp(str(value or "").strip(), EXPLICIT_TIMESTAMP_FORMATS)
    if not normalized:
        return "unknown-datetime"
    return normalized.replace("-", "").replace("T", "_").replace(":", "")
