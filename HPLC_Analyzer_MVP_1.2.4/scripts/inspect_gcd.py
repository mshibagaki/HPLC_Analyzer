"""List streams in a Shimadzu GCD compound file for format research."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hplc_app.gcd_parser import list_gcd_streams


_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def _safe_dump_name(name: str) -> str:
    invalid = '<>:"/\\|?*'
    safe = "".join(
        "_" if character in invalid or ord(character) < 32 else character
        for character in name
    ).strip(" .")
    if not safe:
        return "unnamed"
    if safe.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        return "_" + safe
    return safe


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--dump", type=Path)
    args = parser.parse_args()
    streams = list_gcd_streams(args.path.read_bytes())
    for name, data in streams.items():
        preview = "".join(chr(value) if 32 <= value < 127 else "." for value in data[:48])
        print("%8d  %-40s %s" % (len(data), name, preview))
        if args.dump:
            args.dump.mkdir(parents=True, exist_ok=True)
            safe_name = _safe_dump_name(name)
            (args.dump / safe_name).write_bytes(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
