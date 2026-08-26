"""Small synthetic OLE/CFB fixtures used by GCD parser regression tests."""

from __future__ import annotations

import struct
from typing import Dict

from hplc_app.gcd_parser import CFB_SIGNATURE


FREE_SECTOR = 0xFFFFFFFF
END_OF_CHAIN = 0xFFFFFFFE
FAT_SECTOR = 0xFFFFFFFD
DIFAT_SECTOR = 0xFFFFFFFC
SECTOR_SIZE = 512
MINI_SECTOR_SIZE = 64


def _directory_entry(
    name: str,
    object_type: int,
    start_sector: int,
    size: int,
    child_id: int = FREE_SECTOR,
) -> bytes:
    encoded_name = (name + "\0").encode("utf-16le")
    if len(encoded_name) > 64:
        raise ValueError("Synthetic CFB directory name is too long")
    entry = bytearray(128)
    entry[: len(encoded_name)] = encoded_name
    struct.pack_into("<H", entry, 64, len(encoded_name))
    entry[66] = object_type
    entry[67] = 1
    struct.pack_into("<I", entry, 68, FREE_SECTOR)
    struct.pack_into("<I", entry, 72, FREE_SECTOR)
    struct.pack_into("<I", entry, 76, child_id)
    struct.pack_into("<I", entry, 116, start_sector)
    struct.pack_into("<Q", entry, 120, size)
    return bytes(entry)


def synthetic_gcd_bytes() -> bytes:
    """Return a minimal valid CFB v3 file containing three GCD streams."""
    status = bytearray(12)
    struct.pack_into("<I", status, 0, 500)
    struct.pack_into("<I", status, 8, 3)
    streams: Dict[str, bytes] = {
        "Status": bytes(status),
        "Intensity Data": struct.pack("<3d", -104.0, 12.5, 300.0),
        "Peak Table": struct.pack("<I", 0),
    }

    header = bytearray(SECTOR_SIZE)
    header[:8] = CFB_SIGNATURE
    struct.pack_into("<H", header, 24, 0x003E)
    struct.pack_into("<H", header, 26, 3)
    struct.pack_into("<H", header, 28, 0xFFFE)
    struct.pack_into("<H", header, 30, 9)
    struct.pack_into("<H", header, 32, 6)
    struct.pack_into("<I", header, 40, 0)
    struct.pack_into("<I", header, 44, 1)
    struct.pack_into("<I", header, 48, 1)
    struct.pack_into("<I", header, 56, 4096)
    struct.pack_into("<I", header, 60, 2)
    struct.pack_into("<I", header, 64, 1)
    struct.pack_into("<I", header, 68, END_OF_CHAIN)
    struct.pack_into("<I", header, 72, 0)
    for index in range(109):
        struct.pack_into("<I", header, 76 + index * 4, FREE_SECTOR)
    struct.pack_into("<I", header, 76, 0)

    fat = bytearray(b"\xff" * SECTOR_SIZE)
    struct.pack_into("<I", fat, 0 * 4, FAT_SECTOR)
    struct.pack_into("<I", fat, 1 * 4, END_OF_CHAIN)
    struct.pack_into("<I", fat, 2 * 4, END_OF_CHAIN)
    struct.pack_into("<I", fat, 3 * 4, END_OF_CHAIN)

    directory = bytearray(SECTOR_SIZE)
    root_size = len(streams) * MINI_SECTOR_SIZE
    entries = [
        _directory_entry("Root Entry", 5, 3, root_size, child_id=1),
        _directory_entry("Status", 2, 0, len(streams["Status"])),
        _directory_entry(
            "Intensity Data", 2, 1, len(streams["Intensity Data"])
        ),
        _directory_entry("Peak Table", 2, 2, len(streams["Peak Table"])),
    ]
    for index, entry in enumerate(entries):
        directory[index * 128 : (index + 1) * 128] = entry

    mini_fat = bytearray(b"\xff" * SECTOR_SIZE)
    for index in range(len(streams)):
        struct.pack_into("<I", mini_fat, index * 4, END_OF_CHAIN)

    mini_stream = bytearray(SECTOR_SIZE)
    for index, data in enumerate(streams.values()):
        offset = index * MINI_SECTOR_SIZE
        mini_stream[offset : offset + len(data)] = data

    return bytes(header + fat + directory + mini_fat + mini_stream)


def with_u16(raw: bytes, offset: int, value: int) -> bytes:
    changed = bytearray(raw)
    struct.pack_into("<H", changed, offset, value)
    return bytes(changed)


def with_u32(raw: bytes, offset: int, value: int) -> bytes:
    changed = bytearray(raw)
    struct.pack_into("<I", changed, offset, value)
    return bytes(changed)


def with_u64(raw: bytes, offset: int, value: int) -> bytes:
    changed = bytearray(raw)
    struct.pack_into("<Q", changed, offset, value)
    return bytes(changed)


def with_difat_cycle(raw: bytes, declared_count: int = 2) -> bytes:
    """Append one DIFAT sector whose next pointer references itself."""
    changed = bytearray(raw)
    difat_sector_id = len(raw) // SECTOR_SIZE - 1
    difat_sector = bytearray(b"\xff" * SECTOR_SIZE)
    struct.pack_into("<I", difat_sector, SECTOR_SIZE - 4, difat_sector_id)
    changed.extend(difat_sector)
    struct.pack_into("<I", changed, 68, difat_sector_id)
    struct.pack_into("<I", changed, 72, declared_count)

    # The original FAT must describe the appended DIFAT sector so that the
    # fixture remains structurally meaningful up to the intentional cycle.
    fat_offset = SECTOR_SIZE + difat_sector_id * 4
    struct.pack_into("<I", changed, fat_offset, DIFAT_SECTOR)
    return bytes(changed)
