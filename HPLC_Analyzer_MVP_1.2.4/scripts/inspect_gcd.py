"""List streams in a Shimadzu GCD compound file for format research."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hplc_app.gcd_parser import list_gcd_streams


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
            safe_name = name.replace("/", "_").replace("\\", "_") or "unnamed"
            (args.dump / safe_name).write_bytes(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
