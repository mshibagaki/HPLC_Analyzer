"""Check source and final Release assets against the release identity."""

import argparse
import ast
from pathlib import Path
import re
import sys
import zipfile

try:
    from .artifact_names import artifact_filename
    from .read_version import read_version, validate_version, windows_numeric_version
    from .release_checksums import CHECKSUM_FILENAME, verify_sha256sums
    from .verify_installer import verify_installer_script
    from .verify_windows7_offline_bundle import verify_bundle
    from .verify_windows7_wheelhouse import verify_dependency_closure
except ImportError:
    from artifact_names import artifact_filename
    from read_version import read_version, validate_version, windows_numeric_version
    from release_checksums import CHECKSUM_FILENAME, verify_sha256sums
    from verify_installer import verify_installer_script
    from verify_windows7_offline_bundle import verify_bundle
    from verify_windows7_wheelhouse import verify_dependency_closure


PROJECT_CONSTANTS = ("PROJECT_FORMAT_MAJOR", "PROJECT_SCHEMA_VERSION")
REQUIREMENT_FILES = (
    "requirements-win11.txt",
    "requirements-win11-pyqtgraph.txt",
    "requirements-win7-bootstrap.txt",
    "requirements-win7.txt",
)
MODULE_DISTRIBUTIONS = {"PIL": "Pillow"}


def normalized_release_version(value):
    version = value[1:] if value.startswith("v") else value
    validate_version(version)
    return version


def read_literal_constants(path, names):
    requested = set(names)
    values = {}
    tree = ast.parse(Path(path).read_text(encoding="utf-8-sig"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in requested:
            try:
                values[target.id] = ast.literal_eval(node.value)
            except (TypeError, ValueError):
                raise ValueError("{0} must be a literal".format(target.id))
    missing = requested - set(values)
    if missing:
        raise ValueError("missing constants: {0}".format(", ".join(sorted(missing))))
    return values


def _normalized_distribution(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pinned_requirements(path):
    pins = {}
    errors = []
    for line_number, raw in enumerate(
        Path(path).read_text(encoding="utf-8-sig").splitlines(), 1
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.count("==") != 1:
            errors.append(
                "{0}:{1} is not an exact == pin".format(path, line_number)
            )
            continue
        name, version = (part.strip() for part in line.split("==", 1))
        normalized = _normalized_distribution(name)
        if not name or not version:
            errors.append("{0}:{1} has an empty pin".format(path, line_number))
        elif normalized in pins:
            errors.append("{0} pins {1} more than once".format(path, name))
        else:
            pins[normalized] = version
    return pins, errors


def read_expected_packages(path):
    return read_literal_constants(path, ("EXPECTED_PACKAGES",))["EXPECTED_PACKAGES"]


def verify_dependency_pins(root):
    errors = []
    pin_sets = {}
    for relative in REQUIREMENT_FILES:
        pins, pin_errors = read_pinned_requirements(root / relative)
        pin_sets[relative] = pins
        errors.extend(pin_errors)
    environments = (
        (
            ("requirements-win11.txt", "requirements-win11-pyqtgraph.txt"),
            root / "scripts" / "verify_windows11_x64.py",
        ),
        (
            ("requirements-win7.txt",),
            root / "scripts" / "verify_windows7_x86.py",
        ),
    )
    for requirements_names, verifier_path in environments:
        pins = {}
        for requirements_name in requirements_names:
            pins.update(pin_sets[requirements_name])
        for module, expected in read_expected_packages(verifier_path).items():
            distribution = MODULE_DISTRIBUTIONS.get(module, module)
            actual = pins.get(_normalized_distribution(distribution))
            if actual != expected:
                errors.append(
                    "{0} expects {1}=={2}, but the requirement pin is {3!r}".format(
                        verifier_path.name, distribution, expected, actual
                    )
                )
    return errors


def verify_source_consistency(root, release_version, project_schema):
    root = Path(root)
    errors = []
    try:
        requested_version = normalized_release_version(release_version)
        application_version = read_version(root / "hplc_app" / "version.py")
    except ValueError as exc:
        return ["invalid release identity: {0}".format(exc)]
    if requested_version != application_version:
        errors.append(
            "Release version {0} does not match APP_VERSION {1}".format(
                requested_version, application_version
            )
        )
    try:
        constants = read_literal_constants(
            root / "hplc_app" / "__init__.py", PROJECT_CONSTANTS
        )
    except (OSError, SyntaxError, ValueError) as exc:
        errors.append("could not read project schema constants: {0}".format(exc))
        constants = {}
    if constants.get("PROJECT_SCHEMA_VERSION") != project_schema:
        errors.append(
            "release project schema {0} does not match source schema {1}".format(
                project_schema, constants.get("PROJECT_SCHEMA_VERSION")
            )
        )
    for target, relative in (
        ("windows11-x64", "installer/windows11_x64.iss"),
        ("windows7-x86", "installer/windows7_x86.iss"),
    ):
        errors.extend(
            verify_installer_script(target, root / relative, application_version)
        )
    try:
        errors.extend(verify_dependency_pins(root))
        errors.extend(verify_bundle(root))
        errors.extend(verify_dependency_closure(root))
    except (OSError, SyntaxError, ValueError) as exc:
        errors.append("dependency or Win7 manifest check failed: {0}".format(exc))
    return errors


def _read_pe_metadata(path):
    try:
        import pefile
    except ImportError as exc:
        raise RuntimeError("pefile is required to inspect installer metadata") from exc
    pe = pefile.PE(str(path), fast_load=False)
    try:
        strings = {}
        for group in getattr(pe, "FileInfo", []) or []:
            for entry in group:
                key = getattr(entry, "Key", b"")
                if isinstance(key, bytes):
                    key = key.decode("utf-8", errors="replace")
                if key != "StringFileInfo":
                    continue
                for table in getattr(entry, "StringTable", []) or []:
                    for name, value in table.entries.items():
                        decoded_name = name.decode("utf-8") if isinstance(name, bytes) else str(name)
                        decoded_value = value.decode("utf-8") if isinstance(value, bytes) else str(value)
                        strings[decoded_name] = decoded_value
        fixed_entries = getattr(pe, "VS_FIXEDFILEINFO", []) or []
        if not fixed_entries:
            raise ValueError("VS_FIXEDFILEINFO is missing")
        fixed = fixed_entries[0]
        product_version = (
            fixed.ProductVersionMS >> 16,
            fixed.ProductVersionMS & 0xFFFF,
            fixed.ProductVersionLS >> 16,
            fixed.ProductVersionLS & 0xFFFF,
        )
        return strings, product_version
    finally:
        pe.close()


def verify_installer_metadata(path, target, version):
    platform = "Windows 11 64-bit" if target == "windows11-x64" else "Windows 7 32-bit"
    expected_numeric = tuple(
        int(part) for part in windows_numeric_version(version).split(".")
    )
    try:
        strings, product_version = _read_pe_metadata(path)
    except (OSError, RuntimeError, ValueError) as exc:
        return ["could not inspect installer metadata for {0}: {1}".format(path, exc)]
    errors = []
    expected_strings = {
        "CompanyName": "Research Tools",
        "FileDescription": "HPLC Analyzer {0} installer for {1}".format(
            version, platform
        ),
        "ProductName": "HPLC Analyzer",
        "ProductVersion": version,
    }
    for key, expected in expected_strings.items():
        if strings.get(key) != expected:
            errors.append(
                "installer {0} mismatch: expected {1!r}, found {2!r}".format(
                    key, expected, strings.get(key)
                )
            )
    if product_version != expected_numeric:
        errors.append(
            "installer ProductVersion mismatch: expected {0}, found {1}".format(
                expected_numeric, product_version
            )
        )
    return errors


def verify_offline_archive(path, version, project_schema):
    archive_root = "HPLC_Analyzer_MVP_{0}".format(version)
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if not names or any(
                name != archive_root and not name.startswith(archive_root + "/")
                for name in names
            ):
                return ["offline archive has an unexpected root directory"]
            version_source = archive.read(
                archive_root + "/hplc_app/version.py"
            ).decode("utf-8-sig")
            init_source = archive.read(
                archive_root + "/hplc_app/__init__.py"
            ).decode("utf-8-sig")
    except (KeyError, OSError, UnicodeError, zipfile.BadZipFile) as exc:
        return ["could not inspect offline archive: {0}".format(exc)]
    errors = []
    try:
        version_values = _literal_values_from_text(
            version_source, ("APP_VERSION",)
        )
        schema_values = _literal_values_from_text(init_source, PROJECT_CONSTANTS)
    except (SyntaxError, TypeError, ValueError) as exc:
        return ["offline archive has invalid release constants: {0}".format(exc)]
    if version_values.get("APP_VERSION") != version:
        errors.append("offline archive APP_VERSION does not match the Release")
    if schema_values.get("PROJECT_SCHEMA_VERSION") != project_schema:
        errors.append("offline archive project schema does not match the Release")
    return errors


def _literal_values_from_text(source, names):
    values = {}
    requested = set(names)
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in requested:
                values[target.id] = ast.literal_eval(node.value)
    return values


def verify_release_assets(root, release_dir, release_version, project_schema):
    version = normalized_release_version(release_version)
    directory = Path(release_dir)
    errors = verify_source_consistency(root, version, project_schema)
    expected_files = {
        artifact_filename("windows11-installer", version),
        artifact_filename("windows7-installer", version),
        artifact_filename("windows7-offline", version),
        CHECKSUM_FILENAME,
    }
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.name not in expected_files:
                errors.append("unexpected file in Release directory: {0}".format(path.name))
    else:
        errors.append("Release directory is missing: {0}".format(directory))
    targets = (
        ("windows11-installer", "windows11-x64"),
        ("windows7-installer", "windows7-x86"),
    )
    for artifact_target, metadata_target in targets:
        path = directory / artifact_filename(artifact_target, version)
        if not path.is_file():
            errors.append("Release asset is missing: {0}".format(path.name))
        else:
            errors.extend(verify_installer_metadata(path, metadata_target, version))
    offline = directory / artifact_filename("windows7-offline", version)
    if not offline.is_file():
        errors.append("Release asset is missing: {0}".format(offline.name))
    else:
        errors.extend(verify_offline_archive(offline, version, project_schema))
    checksum_path = directory / CHECKSUM_FILENAME
    if not checksum_path.is_file():
        errors.append("Release asset is missing: {0}".format(CHECKSUM_FILENAME))
    else:
        errors.extend(verify_sha256sums(directory, version, checksum_path))
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("source", "assets"))
    parser.add_argument("--release-version", required=True)
    parser.add_argument("--project-schema", required=True, type=int)
    parser.add_argument(
        "--root", default=str(Path(__file__).resolve().parents[1])
    )
    parser.add_argument("--release-dir")
    args = parser.parse_args(argv)
    if args.mode == "assets" and not args.release_dir:
        parser.error("--release-dir is required in assets mode")
    if args.mode == "source":
        errors = verify_source_consistency(
            args.root, args.release_version, args.project_schema
        )
    else:
        errors = verify_release_assets(
            args.root,
            args.release_dir,
            args.release_version,
            args.project_schema,
        )
    for error in errors:
        print("[ERROR] {0}".format(error))
    if errors:
        return 1
    version = normalized_release_version(args.release_version)
    print("[OK] Release version: {0}".format(version))
    print("[OK] Project schema: {0}".format(args.project_schema))
    print("[OK] Release consistency mode: {0}".format(args.mode))
    return 0


if __name__ == "__main__":
    sys.exit(main())
