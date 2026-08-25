"""Shimadzu PACsolution GCD chromatogram reader.

GCD files observed in PACsolution 2.2 are OLE Compound File Binary (CFB)
containers.  This module intentionally implements only the read-only CFB
features needed to extract their named streams; it does not modify vendor
files or depend on Windows COM.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from typing import Dict, Iterator, List

import numpy as np


CFB_SIGNATURE = bytes.fromhex("d0cf11e0a1b11ae1")
_FREE_SECTOR = 0xFFFFFFFF
_END_OF_CHAIN = 0xFFFFFFFE
_FAT_SECTOR = 0xFFFFFFFD
_DIFAT_SECTOR = 0xFFFFFFFC
_PEAK_RECORD_SIZE = 236


class GcdParseError(ValueError):
    pass


@dataclass
class ParsedGcd:
    metadata: Dict[str, str]
    peak_table: List[Dict[str, str]]
    time_min: np.ndarray
    intensity_uv: np.ndarray


@dataclass
class _DirectoryEntry:
    name: str
    object_type: int
    start_sector: int
    size: int


class _CompoundFile:
    """Minimal, bounded CFB v3/v4 reader for named streams."""

    def __init__(self, raw: bytes):
        if len(raw) < 512 or raw[:8] != CFB_SIGNATURE:
            raise GcdParseError("GCD is not an OLE compound file")
        byte_order = struct.unpack_from("<H", raw, 28)[0]
        major_version = struct.unpack_from("<H", raw, 26)[0]
        if byte_order != 0xFFFE or major_version not in (3, 4):
            raise GcdParseError("Unsupported OLE compound-file version")

        sector_shift = struct.unpack_from("<H", raw, 30)[0]
        mini_sector_shift = struct.unpack_from("<H", raw, 32)[0]
        expected_sector_shift = 9 if major_version == 3 else 12
        if sector_shift != expected_sector_shift or mini_sector_shift != 6:
            raise GcdParseError("Unsupported OLE sector size")

        self.raw = raw
        self.major_version = major_version
        self.sector_size = 1 << sector_shift
        self.mini_sector_size = 1 << mini_sector_shift
        if len(raw) < self.sector_size:
            raise GcdParseError("Truncated OLE header")
        self.sector_count = len(raw) // self.sector_size - 1

        fat_sector_count = struct.unpack_from("<I", raw, 44)[0]
        first_directory_sector = struct.unpack_from("<I", raw, 48)[0]
        self.mini_stream_cutoff = struct.unpack_from("<I", raw, 56)[0]
        first_mini_fat_sector = struct.unpack_from("<I", raw, 60)[0]
        mini_fat_sector_count = struct.unpack_from("<I", raw, 64)[0]
        first_difat_sector = struct.unpack_from("<I", raw, 68)[0]
        difat_sector_count = struct.unpack_from("<I", raw, 72)[0]

        if fat_sector_count == 0 or fat_sector_count > self.sector_count:
            raise GcdParseError("OLE FAT sector count exceeds the file size")
        if mini_fat_sector_count > self.sector_count:
            raise GcdParseError("OLE mini FAT sector count exceeds the file size")
        if difat_sector_count > self.sector_count:
            raise GcdParseError("OLE DIFAT sector count exceeds the file size")

        difat = list(struct.unpack_from("<109I", raw, 76))
        current = first_difat_sector
        seen_difat = set()
        for _unused in range(difat_sector_count):
            if current == _END_OF_CHAIN:
                raise GcdParseError("OLE DIFAT chain is shorter than declared")
            if current in seen_difat:
                raise GcdParseError("Cyclic OLE DIFAT sector chain")
            seen_difat.add(current)
            values = list(self._unpack_sector_u32(current))
            difat.extend(values[:-1])
            current = values[-1]
        if difat_sector_count and current != _END_OF_CHAIN:
            raise GcdParseError("OLE DIFAT chain is longer than declared")
        fat_sector_ids = [
            sector_id
            for sector_id in difat
            if sector_id not in (_FREE_SECTOR, _END_OF_CHAIN)
        ]
        if len(fat_sector_ids) < fat_sector_count:
            raise GcdParseError("OLE FAT sector list is truncated")
        selected_fat_sector_ids = fat_sector_ids[:fat_sector_count]
        if len(set(selected_fat_sector_ids)) != len(selected_fat_sector_ids):
            raise GcdParseError("OLE FAT sector list contains duplicates")
        self.fat: List[int] = []
        for sector_id in selected_fat_sector_ids:
            self.fat.extend(self._unpack_sector_u32(sector_id))

        directory = self._read_regular_stream(first_directory_sector)
        self.entries = self._parse_directory(directory)
        roots = [entry for entry in self.entries if entry.object_type == 5]
        if len(roots) != 1:
            raise GcdParseError("OLE root storage is missing")
        root = roots[0]
        self.mini_stream = self._read_regular_stream(root.start_sector, root.size)

        mini_fat_raw = self._read_regular_stream(
            first_mini_fat_sector, mini_fat_sector_count * self.sector_size
        )
        if len(mini_fat_raw) % 4:
            raise GcdParseError("OLE mini FAT is malformed")
        self.mini_fat = list(
            struct.unpack("<%dI" % (len(mini_fat_raw) // 4), mini_fat_raw)
        )

    def _sector(self, sector_id: int) -> bytes:
        if sector_id in (_FREE_SECTOR, _END_OF_CHAIN, _FAT_SECTOR, _DIFAT_SECTOR):
            raise GcdParseError("Invalid OLE sector reference")
        offset = (sector_id + 1) * self.sector_size
        end = offset + self.sector_size
        if end > len(self.raw):
            raise GcdParseError("OLE sector points outside the file")
        return self.raw[offset:end]

    def _unpack_sector_u32(self, sector_id: int):
        return struct.unpack("<%dI" % (self.sector_size // 4), self._sector(sector_id))

    @staticmethod
    def _chain(table: List[int], start: int) -> Iterator[int]:
        if start == _END_OF_CHAIN:
            return
        seen = set()
        current = start
        while current != _END_OF_CHAIN:
            if current in (_FREE_SECTOR, _FAT_SECTOR, _DIFAT_SECTOR):
                raise GcdParseError("Invalid sector in OLE stream chain")
            if current < 0 or current >= len(table) or current in seen:
                raise GcdParseError("Broken or cyclic OLE stream chain")
            seen.add(current)
            yield current
            current = table[current]

    def _read_regular_stream(self, start: int, size: int = -1) -> bytes:
        if size == 0:
            return b""
        data = b"".join(self._sector(sector_id) for sector_id in self._chain(self.fat, start))
        if size >= 0:
            if len(data) < size:
                raise GcdParseError("OLE stream is truncated")
            return data[:size]
        return data

    def _parse_directory(self, data: bytes) -> List[_DirectoryEntry]:
        entries: List[_DirectoryEntry] = []
        for offset in range(0, len(data), 128):
            item = data[offset : offset + 128]
            if len(item) < 128:
                break
            name_size = struct.unpack_from("<H", item, 64)[0]
            object_type = item[66]
            if object_type == 0:
                continue
            if name_size < 2 or name_size > 64 or name_size % 2:
                raise GcdParseError("Invalid OLE directory name")
            name = item[: name_size - 2].decode("utf-16le", errors="strict")
            start_sector = struct.unpack_from("<I", item, 116)[0]
            size = struct.unpack_from("<Q", item, 120)[0]
            if self.major_version == 3:
                size &= 0xFFFFFFFF
            entries.append(_DirectoryEntry(name, object_type, start_sector, size))
        return entries

    def streams(self) -> Dict[str, bytes]:
        result: Dict[str, bytes] = {}
        for entry in self.entries:
            if entry.object_type != 2:
                continue
            if entry.size == 0:
                data = b""
            elif entry.size < self.mini_stream_cutoff:
                chunks = []
                for mini_sector_id in self._chain(self.mini_fat, entry.start_sector):
                    offset = mini_sector_id * self.mini_sector_size
                    end = offset + self.mini_sector_size
                    if end > len(self.mini_stream):
                        raise GcdParseError("OLE mini stream is truncated")
                    chunks.append(self.mini_stream[offset:end])
                data = b"".join(chunks)
                if len(data) < entry.size:
                    raise GcdParseError("OLE mini stream is shorter than declared")
                data = data[: entry.size]
            else:
                data = self._read_regular_stream(entry.start_sector, entry.size)
            name = entry.name
            suffix = 2
            while name in result:
                name = "%s [%d]" % (entry.name, suffix)
                suffix += 1
            result[name] = data
        return result


def _required_stream(streams: Dict[str, bytes], name: str) -> bytes:
    try:
        return streams[name]
    except KeyError as exc:
        raise GcdParseError("Required GCD stream not found: %s" % name) from exc


def _c_string(raw: bytes) -> str:
    value = raw.split(b"\0", 1)[0]
    for encoding in ("cp932", "utf-8", "shift_jis"):
        try:
            return value.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return value.decode("cp932", errors="replace").strip()


def _round_integer(value: float) -> str:
    if value >= 0:
        return str(int(math.floor(value + 0.5)))
    return str(int(math.ceil(value - 0.5)))


def _parse_peak_table(raw: bytes) -> List[Dict[str, str]]:
    if len(raw) < 4:
        raise GcdParseError("GCD Peak Table stream is truncated")
    count = struct.unpack_from("<I", raw, 0)[0]
    required_size = 4 + count * _PEAK_RECORD_SIZE
    if count > 100000 or len(raw) < required_size:
        raise GcdParseError("GCD Peak Table record count is invalid")

    result: List[Dict[str, str]] = []
    for index in range(count):
        offset = 4 + index * _PEAK_RECORD_SIZE
        peak_flags = struct.unpack_from("<I", raw, offset + 4)[0]
        retention_ms = struct.unpack_from("<I", raw, offset + 8)[0]
        area = struct.unpack_from("<d", raw, offset + 12)[0]
        height = struct.unpack_from("<d", raw, offset + 20)[0]
        initial_ms = struct.unpack_from("<I", raw, offset + 44)[0]
        final_ms = struct.unpack_from("<I", raw, offset + 48)[0]
        area_height = struct.unpack_from("<d", raw, offset + 52)[0]
        concentration = struct.unpack_from("<d", raw, offset + 152)[0]
        numeric_values = (area, height, area_height, concentration)
        if not all(math.isfinite(value) for value in numeric_values):
            raise GcdParseError("GCD Peak Table contains a non-finite value")
        result.append(
            {
                "Peak#": str(index + 1),
                "R.Time": "%.3f" % (retention_ms / 60000.0),
                "I.Time": "%.3f" % (initial_ms / 60000.0),
                "F.Time": "%.3f" % (final_ms / 60000.0),
                "Area": _round_integer(area),
                "Height": _round_integer(height),
                "A/H": "%.2f" % area_height,
                "Conc.": "%.5f" % concentration,
                "Mark": ("V" if not peak_flags & 0x10 else "")
                + ("E" if peak_flags & 0x40 else ""),
                "ID#": "",
                "Name": "",
                # These vendor fields have not yet been mapped in the GCD
                # record.  Blank means unavailable; zero would falsely look
                # like a measured value.
                "k'": "",
                "Plate #": "",
                "Plate Ht.": "",
                "Tailing": "",
                "Resolution": "",
                "Sep.Factor": "",
            }
        )
    return result


def parse_gcd_streams(streams: Dict[str, bytes]) -> ParsedGcd:
    """Parse already-extracted GCD streams (also useful for focused tests)."""
    status = _required_stream(streams, "Status")
    intensity_data = _required_stream(streams, "Intensity Data")
    if len(status) < 12:
        raise GcdParseError("GCD Status stream is truncated")
    interval_ms = struct.unpack_from("<I", status, 0)[0]
    point_count = struct.unpack_from("<I", status, 8)[0]
    if interval_ms <= 0 or point_count < 2:
        raise GcdParseError("GCD sampling interval or point count is invalid")
    expected_bytes = point_count * 8
    if len(intensity_data) != expected_bytes:
        raise GcdParseError(
            "GCD intensity size mismatch: status=%d points, stream=%d bytes"
            % (point_count, len(intensity_data))
        )

    intensity_uv = np.frombuffer(intensity_data, dtype="<f8").astype(float, copy=True)
    time_min = np.arange(point_count, dtype=float) * (interval_ms / 60000.0)
    metadata = {
        "GCD.Container": "OLE Compound File",
        "Chromatogram.Interval(msec)": str(interval_ms),
        "Chromatogram.# of Points": str(point_count),
        "Chromatogram.Start Time(min)": "%.5f" % time_min[0],
        "Chromatogram.End Time(min)": "%.5f" % time_min[-1],
    }
    file_property = streams.get("File Property", b"")
    if len(file_property) > 4:
        version = _c_string(file_property[4:20])
        if version:
            metadata["GCD.File Version"] = version
    system = streams.get("System", b"")
    if system:
        instrument_name = _c_string(system[:32])
        if instrument_name:
            metadata["Configuration.Instrument Name"] = instrument_name

    peak_data = streams.get("Peak Table")
    peak_table = _parse_peak_table(peak_data) if peak_data is not None else []
    return ParsedGcd(metadata, peak_table, time_min, intensity_uv)


def parse_gcd_bytes(raw: bytes) -> ParsedGcd:
    return parse_gcd_streams(_CompoundFile(raw).streams())


def list_gcd_streams(raw: bytes) -> Dict[str, bytes]:
    """Return named streams for the repository's reproducibility inspector."""
    return _CompoundFile(raw).streams()
