"""Verify the complete offline dependency closure for CPython 3.8 win32."""

from __future__ import print_function

import argparse
import email
import sys
import zipfile
from pathlib import Path

try:
    from pip._vendor.packaging.markers import default_environment
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.utils import canonicalize_name, parse_wheel_filename
except ImportError:  # pragma: no cover - used by maintainer environments
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name, parse_wheel_filename


TARGET_ENVIRONMENT = {
    "implementation_name": "cpython",
    "implementation_version": "3.8.10",
    "os_name": "nt",
    "platform_machine": "x86",
    "platform_python_implementation": "CPython",
    "platform_release": "7",
    "platform_system": "Windows",
    "platform_version": "6.1.7601",
    "python_full_version": "3.8.10",
    "python_version": "3.8",
    "sys_platform": "win32",
    "extra": "",
}


def target_environment():
    environment = default_environment()
    environment.update(TARGET_ENVIRONMENT)
    return environment


def read_pinned_requirements(paths):
    requirements = []
    errors = []
    for path in paths:
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            errors.append("could not read requirements file {0}: {1}".format(path, exc))
            continue
        for line_number, raw_line in enumerate(lines, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                requirement = Requirement(line)
            except Exception as exc:
                errors.append(
                    "invalid requirement {0}:{1}: {2}".format(path, line_number, exc)
                )
                continue
            pins = [
                item
                for item in requirement.specifier
                if item.operator in ("==", "===") and "*" not in item.version
            ]
            if len(pins) != 1 or len(list(requirement.specifier)) != 1:
                errors.append(
                    "requirement is not exactly pinned: {0}:{1}: {2}".format(
                        path, line_number, line
                    )
                )
            requirements.append(requirement)
    return requirements, errors


def compatible_with_cp38_win32(tags):
    for tag in tags:
        interpreter_ok = tag.interpreter in ("cp38", "py38", "py3")
        abi_ok = tag.abi in ("cp38", "abi3", "none")
        platform_ok = tag.platform in ("win32", "any")
        if interpreter_ok and abi_ok and platform_ok:
            return True
    return False


def read_wheel_metadata(path):
    with zipfile.ZipFile(str(path)) as archive:
        metadata_names = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1:
            raise ValueError("wheel must contain exactly one .dist-info/METADATA file")
        return email.message_from_bytes(archive.read(metadata_names[0]))


def scan_wheelhouse(wheel_dir):
    distributions = {}
    errors = []
    for path in sorted(Path(wheel_dir).glob("*.whl")):
        try:
            parsed_name, parsed_version, _build, tags = parse_wheel_filename(path.name)
            if not compatible_with_cp38_win32(tags):
                errors.append("wheel is not compatible with CPython 3.8 win32: {0}".format(path.name))
            metadata = read_wheel_metadata(path)
            name = canonicalize_name(metadata["Name"])
            version = metadata["Version"]
            if canonicalize_name(parsed_name) != name or str(parsed_version) != version:
                errors.append("wheel filename and METADATA disagree: {0}".format(path.name))
            if name in distributions:
                errors.append("duplicate wheel distribution: {0}".format(name))
                continue
            distributions[name] = {
                "path": path,
                "version": parsed_version,
                "requires": metadata.get_all("Requires-Dist", []),
            }
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            errors.append("could not inspect wheel {0}: {1}".format(path.name, exc))
    return distributions, errors


def verify_dependency_closure(root):
    root = Path(root).resolve()
    requirement_paths = (
        root / "requirements-win7-bootstrap.txt",
        root / "requirements-win7.txt",
    )
    requirements, errors = read_pinned_requirements(requirement_paths)
    wheel_dir = root / "win7_offline" / "wheels"
    distributions, wheel_errors = scan_wheelhouse(wheel_dir)
    errors.extend(wheel_errors)
    required_wheels_path = root / "win7_offline" / "REQUIRED_WHEELS.txt"
    try:
        required_wheels = {
            line.strip()
            for line in required_wheels_path.read_text(encoding="ascii").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
    except OSError as exc:
        errors.append("could not read REQUIRED_WHEELS.txt: {0}".format(exc))
        required_wheels = set()
    actual_wheels = {path.name for path in wheel_dir.glob("*.whl")}
    for filename in sorted(required_wheels - actual_wheels):
        errors.append("REQUIRED_WHEELS.txt names a missing file: {0}".format(filename))
    for filename in sorted(actual_wheels - required_wheels):
        errors.append("wheel is not named in REQUIRED_WHEELS.txt: {0}".format(filename))
    environment = target_environment()

    pinned_names = set()
    queue = []
    for requirement in requirements:
        if requirement.marker and not requirement.marker.evaluate(environment):
            continue
        name = canonicalize_name(requirement.name)
        pinned_names.add(name)
        queue.append((requirement, "pinned requirements"))

    visited = set()
    while queue:
        requirement, parent = queue.pop(0)
        name = canonicalize_name(requirement.name)
        distribution = distributions.get(name)
        if distribution is None:
            errors.append("missing wheel for {0} (required by {1})".format(requirement, parent))
            continue
        if distribution["version"] not in requirement.specifier:
            errors.append(
                "wheel {0} does not satisfy {1} (required by {2})".format(
                    distribution["path"].name, requirement, parent
                )
            )
            continue
        if name in visited:
            continue
        visited.add(name)
        for raw_dependency in distribution["requires"]:
            try:
                dependency = Requirement(raw_dependency)
            except Exception as exc:
                errors.append(
                    "invalid Requires-Dist in {0}: {1}: {2}".format(
                        distribution["path"].name, raw_dependency, exc
                    )
                )
                continue
            if dependency.marker and not dependency.marker.evaluate(environment):
                continue
            queue.append((dependency, distribution["path"].name))

    wheel_names = set(distributions)
    for name in sorted(wheel_names - pinned_names):
        errors.append("wheel is not pinned in requirements: {0}".format(distributions[name]["path"].name))
    for name in sorted(pinned_names - wheel_names):
        errors.append("pinned requirement has no wheel: {0}".format(name))
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args(argv)
    errors = verify_dependency_closure(args.root)
    for error in errors:
        print("[ERROR] {0}".format(error))
    if errors:
        return 1
    print("[OK] CPython 3.8 win32 offline dependency closure is complete and pinned.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
