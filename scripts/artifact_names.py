"""Canonical Release artifact names derived from the application version."""

import argparse
from pathlib import Path
import sys

try:
    from .read_version import read_version, validate_version
except ImportError:
    from read_version import read_version, validate_version


ARTIFACT_PATTERNS = {
    "windows11-installer": "HPLC_Analyzer_Setup_{0}_Windows11_x64.exe",
    "windows7-installer": "HPLC_Analyzer_Setup_{0}_Windows7_x86.exe",
    "windows7-offline": "HPLC_Analyzer_{0}_Windows7_Offline_Build.zip",
}
BATCH_VARIABLES = {
    "windows11-installer": "WINDOWS11_INSTALLER_NAME",
    "windows7-installer": "WINDOWS7_INSTALLER_NAME",
    "windows7-offline": "WINDOWS7_OFFLINE_ARCHIVE_NAME",
}


def artifact_filename(target, version):
    validate_version(version)
    try:
        pattern = ARTIFACT_PATTERNS[target]
    except KeyError:
        raise ValueError("unknown artifact target: {0}".format(target))
    return pattern.format(version)


def artifact_names(version):
    return {
        target: artifact_filename(target, version)
        for target in sorted(ARTIFACT_PATTERNS)
    }


def installer_basename(target, version):
    filename = artifact_filename(target, version)
    if not filename.lower().endswith(".exe"):
        raise ValueError("target is not an installer: {0}".format(target))
    return filename[:-4]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--version-file")
    parser.add_argument("--target", choices=sorted(ARTIFACT_PATTERNS))
    parser.add_argument("--format", choices=("name", "batch"), default="name")
    args = parser.parse_args(argv)
    version_path = (
        Path(args.version_file)
        if args.version_file
        else Path(__file__).resolve().parents[1] / "hplc_app" / "version.py"
    )
    try:
        version = read_version(version_path)
        names = artifact_names(version)
    except ValueError as exc:
        print("[ERROR] {0}".format(exc), file=sys.stderr)
        return 1
    if args.format == "batch":
        for target in sorted(names):
            variable = BATCH_VARIABLES[target]
            name = names[target]
            print('set "{0}={1}"'.format(variable, name))
            if target.endswith("-installer"):
                print(
                    'set "{0}_BASE={1}"'.format(variable, name[:-4])
                )
        return 0
    if not args.target:
        parser.error("--target is required with --format name")
    print(names[args.target])
    return 0


if __name__ == "__main__":
    sys.exit(main())
