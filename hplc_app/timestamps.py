"""Deterministic acquisition timestamp extraction without file I/O."""

from datetime import datetime
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


def acquisition_timestamp(metadata: Dict[str, str], filename: str) -> str:
    """Use vendor acquisition metadata, then a strict filename fallback."""
    explicit = str(
        (metadata or {}).get("Sample Information.Acquisition Date", "") or ""
    ).strip()
    if explicit:
        return _iso_timestamp(explicit, EXPLICIT_TIMESTAMP_FORMATS) or explicit
    return timestamp_from_filename(filename)


def run_id_timestamp(value: str) -> str:
    """Format a known acquisition time without inventing missing date/time."""
    normalized = _iso_timestamp(str(value or "").strip(), EXPLICIT_TIMESTAMP_FORMATS)
    if not normalized:
        return "unknown-datetime"
    return normalized.replace("-", "").replace("T", "_").replace(":", "")
