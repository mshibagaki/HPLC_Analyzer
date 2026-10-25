"""Deterministic discovery of chromatogram files for directory import."""

from pathlib import Path
import hashlib
import os
import re
from typing import NamedTuple

from .parser import CFB_SIGNATURE, infer_y_axis, load_chromatogram_file


SUPPORTED_CHROMATOGRAM_SUFFIXES = {".gcd", ".txt"}
DATA_FILE_NAME_KEY = "Header.Data File Name"


class ChromatogramDiscovery(NamedTuple):
    files: list
    skipped_txt_files: list
    # Files left out because another file (a GCD and its TXT export, possibly
    # in a different folder) holds the same measurement.
    same_measurement_skipped: int = 0


def _file_stem(name):
    """Casefolded stem of a file name that may use Windows separators."""
    leaf = re.split(r"[\\/]", str(name or ""))[-1]
    return os.path.splitext(leaf)[0].casefold()


def dataset_measurement_identity(dataset):
    """(channel, stem, acquisition time) of an imported dataset, or None.

    A TXT export is identified by the GCD path recorded in its header, so the
    export and the original data file compare equal. Without an acquisition
    time nothing is considered the same measurement (the file is imported).
    """
    try:
        acquired = str(dataset.measurement.acquisition_datetime or "").strip()
        if not acquired:
            return None
        is_gcd = str(dataset.original_filename).lower().endswith(".gcd") or bytes(
            dataset.raw_bytes or b""
        )[: len(CFB_SIGNATURE)] == CFB_SIGNATURE
        named = ""
        if not is_gcd:
            named = str(dataset.source_metadata.get(DATA_FILE_NAME_KEY, "") or "")
        reference = named or dataset.original_path or dataset.original_filename
    except (AttributeError, TypeError):
        # Not a fully imported dataset (e.g. a minimal stand-in): no identity.
        return None
    return (infer_y_axis(reference), _file_stem(reference), acquired)


def _parse_measurement_identity(path):
    try:
        return dataset_measurement_identity(load_chromatogram_file(str(path)))
    except Exception:
        return None


class _IdentityResolver:
    """Parses a file at most once, and only when its stem collides."""

    def __init__(self, loader=None):
        self._loader = loader or _parse_measurement_identity
        self._cache = {}

    def identity(self, path):
        key = normalized_source_path(path)
        if key not in self._cache:
            self._cache[key] = self._loader(path)
        return self._cache[key]


def _drop_same_measurements(paths, resolver):
    """Keep one file per measurement among ``paths`` (GCD preferred)."""
    by_stem = {}
    for path in paths:
        by_stem.setdefault(path.stem.casefold(), []).append(path)
    dropped = set()
    for group in by_stem.values():
        if len(group) < 2:
            continue
        kept = {}
        for path in group:
            identity = resolver.identity(path)
            if identity is None:
                continue
            first = kept.get(identity)
            if first is None:
                kept[identity] = path
            elif path.suffix.lower() == ".gcd" and first.suffix.lower() != ".gcd":
                dropped.add(first)
                kept[identity] = path
            else:
                dropped.add(path)
    return [path for path in paths if path not in dropped], len(dropped)


def discover_chromatogram_files_with_report(
    directory, recursive=False, identity_loader=None
):
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
    files = sorted(
        files,
        key=lambda path: (
            path.relative_to(root).as_posix().casefold(),
            path.relative_to(root).as_posix(),
        ),
    )
    gcd_keys = {
        (path.parent, path.stem.casefold())
        for path in files
        if path.suffix.lower() == ".gcd"
    }
    preferred = []
    skipped_txt_files = []
    for path in files:
        if (
            path.suffix.lower() == ".txt"
            and (path.parent, path.stem.casefold()) in gcd_keys
        ):
            skipped_txt_files.append(path)
        else:
            preferred.append(path)
    preferred, same_measurement = _drop_same_measurements(
        preferred, _IdentityResolver(identity_loader)
    )
    return ChromatogramDiscovery(preferred, skipped_txt_files, same_measurement)


def discover_chromatogram_files(directory, recursive=False):
    return discover_chromatogram_files_with_report(directory, recursive).files


def normalized_source_path(path):
    try:
        return str(Path(path).resolve()).casefold()
    except (OSError, RuntimeError):
        return str(Path(path).absolute()).casefold()


class ReloadResult(tuple):
    """The classic 5-tuple plus ``same_measurement_skipped``."""

    same_measurement_skipped = 0


def discover_reload_candidates(
    directories, existing_datasets, identity_loader=None
):
    """Classify reload files without mutating a Project.

    Files are parsed only to compare measurements, and only when their stem
    collides with an imported dataset or with another candidate.
    """
    resolver = _IdentityResolver(identity_loader)
    existing_by_stem = {}
    for item in existing_datasets:
        identity = dataset_measurement_identity(item)
        if identity is not None:
            existing_by_stem.setdefault(identity[1], set()).add(identity)
            existing_by_stem.setdefault(
                _file_stem(item.original_filename), set()
            ).add(identity)
    candidate_by_stem = {}
    same_measurement_skipped = 0
    same_in_discovery = []
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
    preferred_txt_paths = set()
    for entry in directories:
        if not entry.enabled:
            continue
        try:
            discovery = discover_chromatogram_files_with_report(
                entry.path, entry.recursive
            )
        except (OSError, ValueError) as exc:
            errors.append("%s: %s" % (entry.path, exc))
            continue
        preferred_txt_paths.update(
            normalized_source_path(path)
            for path in discovery.skipped_txt_files
        )
        for path in discovery.files:
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
                stem = path.stem.casefold()
                earlier = candidate_by_stem.get(stem, [])
                if stem in existing_by_stem or earlier:
                    identity = resolver.identity(path)
                    if identity is not None and identity in existing_by_stem.get(
                        stem, ()
                    ):
                        same_measurement_skipped += 1
                        continue
                    twin = next(
                        (
                            other
                            for other in earlier
                            if identity is not None
                            and resolver.identity(other) == identity
                        ),
                        None,
                    )
                    if twin is not None:
                        same_measurement_skipped += 1
                        if (
                            path.suffix.lower() == ".gcd"
                            and twin.suffix.lower() != ".gcd"
                        ):
                            # Keep the GCD, in the position the TXT held.
                            position = next(
                                index
                                for index, (value, _label) in enumerate(candidates)
                                if value == str(twin)
                            )
                            candidates[position] = (
                                str(path),
                                entry.label or Path(entry.path).name,
                            )
                            earlier.remove(twin)
                            earlier.append(path)
                            seen_hashes.add(digest)
                        continue
                candidate_by_stem.setdefault(stem, []).append(path)
                candidates.append((str(path), entry.label or Path(entry.path).name))
                seen_hashes.add(digest)
    result = ReloadResult(
        (candidates, duplicate_count, changed, errors, len(preferred_txt_paths))
    )
    result.same_measurement_skipped = same_measurement_skipped + sum(
        report_count for report_count in same_in_discovery
    )
    return result
