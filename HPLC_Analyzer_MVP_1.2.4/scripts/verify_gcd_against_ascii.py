"""Reproduce the GCD reverse-engineering check against Shimadzu ASCII exports.

The input directory must contain matching ``name.gcd`` and ``name.TXT`` files.
No source files are modified.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hplc_app.parser import load_chromatogram_file


PEAK_FIELDS = (
    "Peak#",
    "R.Time",
    "I.Time",
    "F.Time",
    "Area",
    "Height",
    "A/H",
    "Conc.",
    "Mark",
)


def _round_half_away(values: np.ndarray) -> np.ndarray:
    return np.where(values >= 0, np.floor(values + 0.5), np.ceil(values - 0.5))


def verify_pair(gcd_path: Path, txt_path: Path) -> str:
    gcd = load_chromatogram_file(str(gcd_path))
    ascii_export = load_chromatogram_file(str(txt_path))
    if gcd.time_min.size != ascii_export.time_min.size:
        raise AssertionError(
            "%s: point count differs (GCD=%d, TXT=%d)"
            % (gcd_path.name, gcd.time_min.size, ascii_export.time_min.size)
        )
    time_error = float(np.max(np.abs(gcd.time_min - ascii_export.time_min)))
    raw_intensity_delta = float(np.max(np.abs(gcd.intensity_uv - ascii_export.intensity_uv)))
    rounded_intensity_error = float(
        np.max(
            np.abs(_round_half_away(gcd.intensity_uv) - ascii_export.intensity_uv)
        )
    )
    if time_error > 0.0000051:
        raise AssertionError("%s: retention-time error is %g min" % (gcd_path.name, time_error))
    if rounded_intensity_error != 0.0:
        raise AssertionError(
            "%s: rounded intensity error is %g uV"
            % (gcd_path.name, rounded_intensity_error)
        )
    if len(gcd.source_peak_table) != len(ascii_export.source_peak_table):
        raise AssertionError("%s: peak count differs" % gcd_path.name)
    for gcd_peak, txt_peak in zip(gcd.source_peak_table, ascii_export.source_peak_table):
        for field in PEAK_FIELDS:
            if gcd_peak[field] != txt_peak[field]:
                raise AssertionError(
                    "%s peak %s %s differs (GCD=%r, TXT=%r)"
                    % (gcd_path.name, gcd_peak["Peak#"], field, gcd_peak[field], txt_peak[field])
                )
    return (
        "%s: %d points, %d peaks, max |dt|=%g min, "
        "TXT quantization=%g uV, rounded |dI|=%g uV"
        % (
            gcd_path.name,
            gcd.time_min.size,
            len(gcd.source_peak_table),
            time_error,
            raw_intensity_delta,
            rounded_intensity_error,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path, help="Directory tree containing matching GCD/TXT files")
    args = parser.parse_args()
    gcd_files = sorted(args.directory.rglob("*.gcd"))
    if not gcd_files:
        parser.error("No .gcd files were found under %s" % args.directory)
    verified = 0
    for gcd_path in gcd_files:
        txt_path = gcd_path.with_suffix(".TXT")
        if not txt_path.is_file():
            print("SKIP %s (matching TXT not found)" % gcd_path)
            continue
        print(verify_pair(gcd_path, txt_path))
        verified += 1
    if verified == 0:
        parser.error("No matching GCD/TXT pairs were found")
    print("Verified %d GCD/TXT pair(s)." % verified)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
