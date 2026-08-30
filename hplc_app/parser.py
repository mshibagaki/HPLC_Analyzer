from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
from typing import Dict, List, Sequence, Tuple

import numpy as np

from .gcd_parser import CFB_SIGNATURE, GcdParseError, parse_gcd_bytes
from .models import Dataset, MeasurementMetadata
from .timestamps import acquisition_timestamp


class ParseError(ValueError):
    pass


def infer_y_axis(source_path: str) -> int:
    """Infer the initial axis from parent directories, never from the filename."""

    normalized = str(source_path or "").replace("/", "\\").casefold()
    directory = normalized.rsplit("\\", 1)[0] if "\\" in normalized else ""
    channel_tokens = set(
        re.findall(r"(?<![a-z0-9])ch([12])(?![a-z0-9])", directory)
    )
    has_ch1 = "1" in channel_tokens
    has_ch2 = "2" in channel_tokens
    return 2 if has_ch2 and not has_ch1 else 1


@dataclass
class ParsedAscii:
    encoding: str
    sections: Dict[str, List[str]]
    metadata: Dict[str, str]
    peak_table: List[Dict[str, str]]
    time_min: np.ndarray
    intensity_uv: np.ndarray


def decode_shimadzu_bytes(raw: bytes) -> Tuple[str, str]:
    """Decode typical Shimadzu ASCII exports without silently losing text."""
    candidates = ("cp932", "utf-8-sig", "shift_jis", "utf-8")
    for encoding in candidates:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("cp932", errors="replace"), "cp932-replace"


def split_sections(text: str) -> Dict[str, List[str]]:
    sections: Dict[str, List[str]] = {}
    current = "Preamble"
    sections[current] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r\n")
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            sections.setdefault(current, [])
        else:
            sections[current].append(line)
    return sections


def _key_values(lines: Sequence[str]) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for line in lines:
        if not line or "\t" not in line:
            continue
        key, value = line.split("\t", 1)
        if key and key not in values:
            values[key.strip()] = value.strip()
    return values


def _find_section(sections: Dict[str, List[str]], prefix: str) -> Tuple[str, List[str]]:
    for name, lines in sections.items():
        if name.lower().startswith(prefix.lower()):
            return name, lines
    raise ParseError("Required section not found: %s" % prefix)


def _parse_chromatogram(lines: Sequence[str]) -> Tuple[np.ndarray, np.ndarray, Dict[str, str]]:
    header_index = -1
    for index, line in enumerate(lines):
        columns = line.split("\t")
        if len(columns) >= 2 and columns[0].strip() == "R.Time" and columns[1].strip() == "Intensity":
            header_index = index
            break
    if header_index < 0:
        raise ParseError("R.Time / Intensity columns were not found")

    times: List[float] = []
    intensities: List[float] = []
    for line in lines[header_index + 1 :]:
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            times.append(float(parts[0]))
            intensities.append(float(parts[1]))
        except ValueError:
            break

    if len(times) < 2:
        raise ParseError("Chromatogram contains fewer than two numeric points")
    info = _key_values(lines[:header_index])
    expected = info.get("# of Points")
    if expected:
        try:
            expected_count = int(float(expected))
        except ValueError:
            expected_count = len(times)
        if abs(expected_count - len(times)) > 1:
            raise ParseError(
                "Point count mismatch: header=%d, parsed=%d" % (expected_count, len(times))
            )
    return np.asarray(times, dtype=float), np.asarray(intensities, dtype=float), info


def _parse_peak_table(lines: Sequence[str]) -> List[Dict[str, str]]:
    header_index = -1
    headers: List[str] = []
    for index, line in enumerate(lines):
        if line.startswith("Peak#\t"):
            header_index = index
            headers = [part.strip() for part in line.split("\t")]
            break
    if header_index < 0:
        return []
    result: List[Dict[str, str]] = []
    for line in lines[header_index + 1 :]:
        if not line.strip():
            continue
        parts = line.split("\t")
        if not parts[0].strip().isdigit():
            break
        padded = parts + [""] * max(0, len(headers) - len(parts))
        result.append({key: padded[i].strip() for i, key in enumerate(headers)})
    return result


def parse_ascii_bytes(raw: bytes) -> ParsedAscii:
    text, encoding = decode_shimadzu_bytes(raw)
    sections = split_sections(text)
    _chrom_name, chrom_lines = _find_section(sections, "Chromatogram (")
    time_min, intensity_uv, chrom_info = _parse_chromatogram(chrom_lines)

    merged: Dict[str, str] = {}
    for section_name in (
        "Header",
        "File Information",
        "Sample Information",
        "Original Files",
        "Configration",
        "Configuration",
    ):
        if section_name in sections:
            for key, value in _key_values(sections[section_name]).items():
                merged["%s.%s" % (section_name, key)] = value
    for key, value in chrom_info.items():
        merged["Chromatogram.%s" % key] = value

    peak_lines: Sequence[str] = []
    for name, lines in sections.items():
        if name.lower().startswith("peak table"):
            peak_lines = lines
            break
    return ParsedAscii(
        encoding=encoding,
        sections=sections,
        metadata=merged,
        peak_table=_parse_peak_table(peak_lines),
        time_min=time_min,
        intensity_uv=intensity_uv,
    )


def dataset_from_bytes(
    raw: bytes,
    source_path: str = "",
    label: str = "",
    source_modified_timestamp: str = "",
) -> Dataset:
    is_gcd = raw.startswith(CFB_SIGNATURE)
    if is_gcd:
        try:
            parsed = parse_gcd_bytes(raw)
        except GcdParseError as exc:
            raise ParseError(str(exc)) from exc
    else:
        parsed = parse_ascii_bytes(raw)
    original_filename = os.path.basename(source_path) if source_path else "chromatogram.TXT"
    original_directory = os.path.dirname(os.path.abspath(source_path)) if source_path else ""
    sample_name = parsed.metadata.get("Sample Information.Sample Name", "")
    display_label = label or sample_name or os.path.splitext(original_filename)[0]
    measurement = MeasurementMetadata(
        sample_name=sample_name,
        sample_id=parsed.metadata.get("Sample Information.Sample ID", ""),
        instrument_name=parsed.metadata.get("Configration.Instrument Name", "")
        or parsed.metadata.get("Configuration.Instrument Name", ""),
        method_name=parsed.metadata.get("Original Files.Method File", ""),
        acquisition_datetime=acquisition_timestamp(
            parsed.metadata,
            original_filename,
            source_modified_timestamp if is_gcd else "",
        ),
    )
    dataset = Dataset(
        label=display_label,
        short_label=display_label,
        original_filename=original_filename,
        original_directory=original_directory,
        original_path=os.path.abspath(source_path) if source_path else "",
        sha256=hashlib.sha256(raw).hexdigest(),
        source_metadata=parsed.metadata,
        source_peak_table=parsed.peak_table,
        measurement=measurement,
        y_axis=infer_y_axis(source_path),
        time_min=parsed.time_min,
        intensity_uv=parsed.intensity_uv,
        raw_bytes=raw,
    )
    dataset.embedded_source_name = "sources/%s_%s" % (dataset.id, original_filename)
    return dataset


def load_ascii_file(path: str) -> Dataset:
    source = Path(path)
    raw = source.read_bytes()
    modified_timestamp = ""
    if raw.startswith(CFB_SIGNATURE):
        try:
            modified_timestamp = datetime.fromtimestamp(
                source.stat().st_mtime
            ).isoformat(timespec="seconds")
        except (OSError, OverflowError, ValueError):
            pass
    return dataset_from_bytes(
        raw,
        source_path=path,
        source_modified_timestamp=modified_timestamp,
    )


def load_chromatogram_file(path: str) -> Dataset:
    """Load a supported Shimadzu ASCII or PACsolution GCD chromatogram."""
    return load_ascii_file(path)
