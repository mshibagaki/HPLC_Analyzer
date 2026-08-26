"""Source-level CI contracts that do not require GUI or scientific packages."""

import argparse
import ast
from pathlib import Path
import re
import sys

try:
    from .read_version import read_version, windows_numeric_version
    from .verify_installer import verify_installer_script
except ImportError:
    from read_version import read_version, windows_numeric_version
    from verify_installer import verify_installer_script


EXCLUDED_DIRECTORIES = {
    ".git",
    ".venv",
    ".venv-win11-x64",
    ".venv-win7-x86",
    "build",
    "dist",
    "win7_offline",
    "__pycache__",
}
PIN_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)$")
CRITICAL_PINS = {
    "requirements-win11.txt": {
        "numpy": "2.2.3",
        "matplotlib": "3.10.1",
        "pyside6": "6.8.3",
        "pyinstaller": "6.12.0",
    },
    "requirements-win7.txt": {
        "numpy": "1.20.3",
        "matplotlib": "3.7.5",
        "pyside2": "5.15.2.1",
        "shiboken2": "5.15.2.1",
        "pyinstaller": "5.13.2",
        "pillow": "9.5.0",
        "importlib-resources": "6.4.5",
        "zipp": "3.20.2",
    },
    "requirements-win7-bootstrap.txt": {
        "pip": "23.3.2",
        "setuptools": "68.2.2",
        "wheel": "0.41.2",
    },
}


def normalized_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def read_exact_pins(path):
    pins = {}
    errors = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {}, ["could not read {0}: {1}".format(path, exc)]
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = PIN_PATTERN.fullmatch(line)
        if match is None:
            errors.append(
                "{0}:{1} is not an exact name==version pin: {2}".format(
                    path, line_number, line
                )
            )
            continue
        name = normalized_name(match.group(1))
        if name in pins:
            errors.append("{0}:{1} duplicates pin {2}".format(path, line_number, name))
        pins[name] = match.group(2)
    return pins, errors


def validate_requirement_pins(root):
    errors = []
    for filename, expected in CRITICAL_PINS.items():
        pins, pin_errors = read_exact_pins(root / filename)
        errors.extend(pin_errors)
        for name, version in expected.items():
            if pins.get(name) != version:
                errors.append(
                    "{0} must pin {1}=={2}; found {3}".format(
                        filename, name, version, pins.get(name, "missing")
                    )
                )
    return errors


def iter_python_files(root):
    candidates = [root / "app.py"]
    for directory_name in ("hplc_app", "scripts", "tests"):
        directory = root / directory_name
        if directory.is_dir():
            candidates.extend(directory.rglob("*.py"))
    for path in sorted(set(candidates)):
        if path.is_file() and not set(path.relative_to(root).parts) & EXCLUDED_DIRECTORIES:
            yield path


def validate_python38_grammar(root):
    errors = []
    for path in iter_python_files(root):
        try:
            source = path.read_text(encoding="utf-8-sig")
            ast.parse(source, filename=str(path), feature_version=(3, 8))
        except (OSError, SyntaxError) as exc:
            errors.append("Python 3.8 grammar failed for {0}: {1}".format(path, exc))
    return errors


def validate_version_and_artifacts(root):
    errors = []
    try:
        version = read_version(root / "hplc_app" / "version.py")
        numeric = windows_numeric_version(version)
    except ValueError as exc:
        return ["application version is invalid: {0}".format(exc)]
    init_source = (root / "hplc_app" / "__init__.py").read_text(encoding="utf-8")
    if "from .version import APP_VERSION" not in init_source:
        errors.append("hplc_app.__init__ must re-export the canonical APP_VERSION")
    literal_pattern = re.compile(r"^APP_VERSION\s*=\s*['\"]", re.MULTILINE)
    literal_files = []
    for directory_name in ("hplc_app", "scripts"):
        for path in (root / directory_name).glob("*.py"):
            if literal_pattern.search(path.read_text(encoding="utf-8")):
                literal_files.append(path.relative_to(root).as_posix())
    if literal_files != ["hplc_app/version.py"]:
        errors.append("literal APP_VERSION definitions: {0}".format(literal_files))

    expected_batch_fragments = {
        "build_windows11.bat": (
            "HPLC_Analyzer_Setup_%APP_VERSION%_Windows11_x64.exe",
            "%WINDOWS11_INSTALLER_NAME%",
        ),
        "build_windows7_offline.bat": (
            "HPLC_Analyzer_Setup_%APP_VERSION%_Windows7_x86.exe",
            "%WINDOWS7_INSTALLER_NAME%",
        ),
        "build_all_windows.bat": (
            "HPLC_Analyzer_%APP_VERSION%_Windows7_Offline_Build.zip",
            "%WINDOWS7_OFFLINE_ARCHIVE_NAME%",
        ),
        "package_windows7_offline_bundle.bat": (
            "HPLC_Analyzer_%APP_VERSION%_Windows7_Offline_Build.zip",
            "%WINDOWS7_OFFLINE_ARCHIVE_NAME%",
        ),
    }
    for filename, accepted_fragments in expected_batch_fragments.items():
        text = (root / filename).read_text(encoding="utf-8")
        if "load_version.bat" not in text or not any(
            fragment in text for fragment in accepted_fragments
        ):
            errors.append("{0} does not use the canonical artifact version".format(filename))
    helper = (root / "scripts" / "build_installer.bat").read_text(encoding="utf-8")
    for fragment in (
        "--define=AppVersion=%APP_VERSION%",
        "--define=AppVersionNumeric=%APP_VERSION_NUMERIC%",
    ):
        if fragment not in helper:
            errors.append("installer build is missing {0}".format(fragment))
    for target, filename in (
        ("windows11-x64", "windows11_x64.iss"),
        ("windows7-x86", "windows7_x86.iss"),
    ):
        errors.extend(
            verify_installer_script(
                target, root / "installer" / filename, version
            )
        )
    if not version or not numeric:
        errors.append("version output unexpectedly empty")
    return errors


def validate_sample_inputs(root):
    samples = sorted((root / "sample_data").glob("*.TXT"))
    return [] if samples else ["sample_data must contain at least one ASCII .TXT fixture"]


def validate_offline_assets(root, policy):
    offline_root = root / "win7_offline"
    if policy == "skip":
        return [], "[SKIP] Windows 7 offline assets were explicitly skipped."
    if not offline_root.is_dir():
        if policy == "required":
            return ["win7_offline assets are required but were not provided"], ""
        return [], (
            "[SKIP] win7_offline is absent in this checkout. Source pins were checked; "
            "binary manifest/wheelhouse verification remains a physical-build gate."
        )
    try:
        from .verify_windows7_offline_bundle import verify_bundle
        from .verify_windows7_wheelhouse import verify_dependency_closure
    except ImportError:
        from verify_windows7_offline_bundle import verify_bundle
        from verify_windows7_wheelhouse import verify_dependency_closure
    errors = verify_bundle(root)
    errors.extend(verify_dependency_closure(root))
    return errors, "[OK] Windows 7 offline manifest and wheelhouse were verified."


def run_validation(root, offline_policy="auto"):
    errors = []
    errors.extend(validate_python38_grammar(root))
    errors.extend(validate_requirement_pins(root))
    errors.extend(validate_version_and_artifacts(root))
    errors.extend(validate_sample_inputs(root))
    offline_errors, offline_message = validate_offline_assets(root, offline_policy)
    errors.extend(offline_errors)
    return errors, offline_message


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument(
        "--offline-assets", choices=("auto", "required", "skip"), default="auto"
    )
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    errors, offline_message = run_validation(root, args.offline_assets)
    if offline_message:
        print(offline_message)
    for error in errors:
        print("[ERROR] {0}".format(error))
    if errors:
        return 1
    print("[OK] Level 2 source contracts validated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
