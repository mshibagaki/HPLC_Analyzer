"""Package source and bundled dependencies for transport to an offline Win7 PC."""

import argparse
from pathlib import Path
import sys
import zipfile

try:
    from .verify_windows7_offline_bundle import verify_bundle
    from .verify_windows7_wheelhouse import verify_dependency_closure
    from .read_version import read_version
except ImportError:
    from verify_windows7_offline_bundle import verify_bundle
    from verify_windows7_wheelhouse import verify_dependency_closure
    from read_version import read_version


ROOT_FILES = (
    "README.md",
    "README_Windows7_Offline.txt",
    "LICENSE.txt",
    "THIRD_PARTY_NOTICES.txt",
    "app.py",
    "HPLC_Analyzer.spec",
    "requirements-win11.txt",
    "requirements-win7.txt",
    "requirements-win7-bootstrap.txt",
    "run_source_windows11.bat",
    "build_windows11.bat",
    "build_windows7.bat",
    "build_windows7_offline.bat",
    "build_all_windows.bat",
    "prepare_windows7_offline_wheels.bat",
    "package_windows7_offline_bundle.bat",
    "scripts/load_version.bat",
    "scripts/read_version.py",
)
ROOT_DIRECTORIES = (
    "assets",
    "hplc_app",
    "installer",
    "sample_data",
    "scripts",
    "tests",
    "third_party_licenses",
    "win7_offline",
)
EXCLUDED_PARTS = {
    "__pycache__",
    ".build-tools",
    ".build-test-settings",
    ".venv-win7-x86",
    ".venv-win11-x64",
    "build",
    "dist",
    "native_check",
    "wheeltest",
    "wheels-stage",
}


def iter_source_files(root):
    for relative in ROOT_FILES:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(str(path))
        yield path
    for relative in ROOT_DIRECTORIES:
        directory = root / relative
        if not directory.is_dir():
            raise FileNotFoundError(str(directory))
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            parts = set(path.relative_to(root).parts)
            if parts & EXCLUDED_PARTS or path.suffix.lower() in (".pyc", ".pyo"):
                continue
            yield path


def compression_for(path):
    if path.suffix.lower() in (".exe", ".whl", ".ico", ".png", ".pdf", ".zip"):
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


def archive_root_name(version):
    return "HPLC_Analyzer_MVP_{0}".format(version)


def default_archive_path(root, version):
    return (
        root
        / "dist"
        / "offline"
        / "HPLC_Analyzer_{0}_Windows7_Offline_Build.zip".format(version)
    )


def build_archive(root, output, version):
    seen = set()
    archive_root = archive_root_name(version)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", allowZip64=True) as archive:
        for path in iter_source_files(root):
            relative = path.relative_to(root).as_posix()
            if relative in seen:
                continue
            seen.add(relative)
            archive.write(
                path,
                "{0}/{1}".format(archive_root, relative),
                compress_type=compression_for(path),
                compresslevel=9,
            )
    with zipfile.ZipFile(output, "r") as archive:
        bad = archive.testzip()
        if bad is not None:
            raise OSError("ZIP integrity check failed at {0}".format(bad))
    return len(seen)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        version = read_version(root / "hplc_app" / "version.py")
    except ValueError as exc:
        print("[ERROR] {0}".format(exc))
        return 1
    errors = verify_bundle(root)
    errors.extend(verify_dependency_closure(root))
    if errors:
        for error in errors:
            print("[ERROR] {0}".format(error))
        return 1
    output = (
        Path(args.output).resolve()
        if args.output
        else default_archive_path(root, version)
    )
    count = build_archive(root, output, version)
    print("[OK] Windows 7 offline build kit: {0}".format(output))
    print("[OK] Packaged {0} files.".format(count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
