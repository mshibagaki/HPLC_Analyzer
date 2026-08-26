"""Deterministic discovery of chromatogram files for directory import."""

from pathlib import Path
import os


SUPPORTED_CHROMATOGRAM_SUFFIXES = {".gcd", ".txt"}


def discover_chromatogram_files(directory, recursive=False):
    root = Path(directory)
    if not root.is_dir():
        raise ValueError("Import directory does not exist: {0}".format(root))
    files = []
    if recursive:
        for current, directory_names, file_names in os.walk(str(root)):
            directory_names.sort(key=lambda value: (value.casefold(), value))
            for file_name in file_names:
                path = Path(current) / file_name
                if path.suffix.lower() in SUPPORTED_CHROMATOGRAM_SUFFIXES:
                    files.append(path)
    else:
        files = [
            path
            for path in root.iterdir()
            if path.is_file()
            and path.suffix.lower() in SUPPORTED_CHROMATOGRAM_SUFFIXES
        ]
    return sorted(
        files,
        key=lambda path: (
            path.relative_to(root).as_posix().casefold(),
            path.relative_to(root).as_posix(),
        ),
    )
