"""Deterministic discovery of chromatogram files for directory import."""

from pathlib import Path
import hashlib
import os


SUPPORTED_CHROMATOGRAM_SUFFIXES = {".gcd", ".txt"}


def discover_chromatogram_files(directory, recursive=False):
    # Path("") silently means the current directory, which would scan wherever
    # the application happens to run from instead of failing.
    if not str(directory).strip():
        raise ValueError("Import directory does not exist: {0!r}".format(directory))
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


def normalized_source_path(path):
    try:
        return str(Path(path).resolve()).casefold()
    except (OSError, RuntimeError):
        return str(Path(path).absolute()).casefold()


def discover_reload_candidates(directories, existing_datasets):
    """Classify reload files without mutating a Project or parsing raw data."""
    known_hashes = {
        str(item.sha256 or "").lower()
        for item in existing_datasets
        if item.sha256
    }
    seen_hashes = set(known_hashes)
    known_paths = {
        normalized_source_path(item.original_path): str(item.sha256 or "").lower()
        for item in existing_datasets
        if item.original_path
    }
    candidates = []
    duplicate_count = 0
    changed = []
    errors = []
    seen_paths = set()
    for entry in directories:
        if not entry.enabled:
            continue
        try:
            files = discover_chromatogram_files(entry.path, entry.recursive)
        except (OSError, ValueError) as exc:
            errors.append("%s: %s" % (entry.path, exc))
            continue
        for path in files:
            normalized = normalized_source_path(path)
            if normalized in seen_paths:
                continue
            seen_paths.add(normalized)
            try:
                before = path.stat()
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                after = path.stat()
            except OSError as exc:
                errors.append("%s: %s" % (path, exc))
                continue
            if (before.st_size, before.st_mtime_ns) != (
                after.st_size,
                after.st_mtime_ns,
            ):
                errors.append("%s: file changed while being read" % path)
                continue
            if digest in seen_hashes:
                duplicate_count += 1
            elif normalized in known_paths:
                changed.append(str(path))
            else:
                candidates.append((str(path), entry.label or Path(entry.path).name))
                seen_hashes.add(digest)
    return candidates, duplicate_count, changed, errors
