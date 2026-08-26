"""Read and validate the canonical application version without importing it."""

import argparse
import ast
from pathlib import Path
import re
import sys


VERSION_PATTERN = re.compile(
    r"^(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
DEFAULT_VERSION_FILE = Path(__file__).resolve().parents[1] / "hplc_app" / "version.py"


class VersionError(ValueError):
    """Raised when the canonical version file is missing or malformed."""


def validate_version(value):
    match = VERSION_PATTERN.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise VersionError(
            "APP_VERSION must be valid SemVer (MAJOR.MINOR.PATCH with optional pre-release/build metadata)"
        )
    prerelease = match.group(4)
    if prerelease and any(
        identifier.isdigit()
        and len(identifier) > 1
        and identifier.startswith("0")
        for identifier in prerelease.split(".")
    ):
        raise VersionError(
            "numeric SemVer pre-release identifiers must not contain leading zeroes"
        )
    return value


def read_version(path=DEFAULT_VERSION_FILE):
    version_path = Path(path)
    try:
        source = version_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise VersionError("could not read version file: {0}".format(exc))
    try:
        module = ast.parse(source, filename=str(version_path))
    except SyntaxError as exc:
        raise VersionError("version file has invalid Python syntax: {0}".format(exc))
    values = []
    for statement in module.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        if not isinstance(target, ast.Name) or target.id != "APP_VERSION":
            continue
        value = statement.value
        if not isinstance(value, ast.Str):
            raise VersionError("APP_VERSION must be a literal string")
        values.append(value.s)
    if len(values) != 1:
        raise VersionError("version file must define APP_VERSION exactly once")
    return validate_version(values[0])


def windows_numeric_version(value):
    match = VERSION_PATTERN.fullmatch(validate_version(value))
    components = tuple(int(part) for part in match.groups()[:3])
    if any(part > 65535 for part in components):
        raise VersionError(
            "MAJOR, MINOR, and PATCH must be at most 65535 for Windows metadata"
        )
    return "{0}.{1}.{2}.0".format(*components)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--version-file", default=str(DEFAULT_VERSION_FILE))
    parser.add_argument(
        "--format",
        choices=("semver", "windows", "batch"),
        default="semver",
    )
    args = parser.parse_args(argv)
    try:
        version = read_version(args.version_file)
        numeric_version = windows_numeric_version(version)
    except VersionError as exc:
        print("[ERROR] {0}".format(exc), file=sys.stderr)
        return 1
    if args.format == "windows":
        print(numeric_version)
    elif args.format == "batch":
        print('set "APP_VERSION={0}"'.format(version))
        print(
            'set "APP_VERSION_NUMERIC={0}"'.format(
                numeric_version
            )
        )
    else:
        print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
