"""Create the deterministic SHA-256 manifest for bundled Windows 7 assets."""

import argparse
from pathlib import Path

from verify_windows7_offline_bundle import (
    EXPECTED_RELATIVE_FILES,
    MANIFEST_RELATIVE,
    sha256_file,
)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    missing = [relative for relative in EXPECTED_RELATIVE_FILES if not (root / relative).is_file()]
    if missing:
        for relative in missing:
            print("[ERROR] Missing offline asset: {0}".format(relative))
        return 1
    lines = [
        "{0} *{1}".format(sha256_file(root / relative), relative)
        for relative in EXPECTED_RELATIVE_FILES
    ]
    output = root / MANIFEST_RELATIVE
    output.write_text("\n".join(lines) + "\n", encoding="ascii")
    print("[OK] Wrote {0}".format(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
