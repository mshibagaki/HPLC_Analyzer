"""Generate a PyInstaller Windows version-resource text file."""

import argparse
from pathlib import Path
import sys

try:
    from .read_version import read_version, validate_version, windows_numeric_version
except ImportError:
    from read_version import read_version, validate_version, windows_numeric_version


TARGET_LABELS = {
    "windows11-x64": "Windows 11 x64",
    "windows7-x86": "Windows 7 SP1 x86",
}


def expected_version_strings(target, debug=False, version=None):
    if target not in TARGET_LABELS:
        raise ValueError("unknown executable target: {0}".format(target))
    if debug and target != "windows7-x86":
        raise ValueError("the diagnostic console executable is Windows 7 only")
    if version is None:
        version = read_version()
    validate_version(version)
    platform = TARGET_LABELS[target]
    filename = "HPLC_Analyzer_Debug.exe" if debug else "HPLC_Analyzer.exe"
    description = "HPLC Analyzer"
    if debug:
        description += " diagnostic console"
    description += " for {0}".format(platform)
    return {
        "CompanyName": "Research Tools",
        "FileDescription": description,
        "FileVersion": version,
        "InternalName": filename[:-4],
        "LegalCopyright": "Copyright (C) 2026",
        "OriginalFilename": filename,
        "ProductName": "HPLC Analyzer",
        "ProductVersion": version,
        "Comments": "Target platform: {0}".format(platform),
    }


def render_version_info(version, target, debug=False):
    strings = expected_version_strings(target, debug=debug, version=version)
    numeric = tuple(int(part) for part in windows_numeric_version(version).split("."))
    string_rows = "\n".join(
        "        StringStruct({0}, {1}),".format(repr(key), repr(value))
        for key, value in strings.items()
    )
    return """# UTF-8 PyInstaller version resource; generated, do not edit.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numeric!r},
    prodvers={numeric!r},
    mask=0x3f,
    flags={flags},
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
{string_rows}
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""".format(numeric=numeric, flags=(0x1 if debug else 0x0), string_rows=string_rows)


def write_version_info(path, version, target, debug=False):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        render_version_info(version, target, debug=debug), encoding="utf-8"
    )
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=sorted(TARGET_LABELS), required=True)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--output", required=True)
    parser.add_argument("--version-file")
    args = parser.parse_args(argv)
    version_path = (
        Path(args.version_file)
        if args.version_file
        else Path(__file__).resolve().parents[1] / "hplc_app" / "version.py"
    )
    try:
        version = read_version(version_path)
        destination = write_version_info(
            args.output, version, args.target, debug=args.debug
        )
    except (OSError, ValueError) as exc:
        print("[ERROR] {0}".format(exc), file=sys.stderr)
        return 1
    print("[OK] Windows version resource: {0}".format(destination))
    return 0


if __name__ == "__main__":
    sys.exit(main())
