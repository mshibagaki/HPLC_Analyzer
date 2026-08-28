"""Snapshot and compare user data around installer upgrade tests."""

import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys


SNAPSHOT_FORMAT = 1
REQUIRED_LABELS = ("presets", "database", "projects", "raw", "exports")
QSETTINGS_REGISTRY_KEY = r"Software\Research Tools\HPLC Analyzer"


class DataGuardError(ValueError):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def snapshot_path(path):
    source = Path(path).resolve()
    if source.is_symlink():
        raise DataGuardError("protected path must not be a symlink: {0}".format(source))
    if source.is_file():
        return {
            "kind": "file",
            "path": str(source),
            "size": source.stat().st_size,
            "sha256": sha256_file(source),
        }
    if not source.is_dir():
        raise DataGuardError("protected path does not exist: {0}".format(source))
    files = {}
    directories = []
    for candidate in sorted(source.rglob("*")):
        if candidate.is_symlink():
            raise DataGuardError(
                "protected directory contains a symlink: {0}".format(candidate)
            )
        relative = candidate.relative_to(source).as_posix()
        if candidate.is_dir():
            directories.append(relative)
        elif candidate.is_file():
            files[relative] = {
                "size": candidate.stat().st_size,
                "sha256": sha256_file(candidate),
            }
    return {
        "kind": "directory",
        "path": str(source),
        "directories": directories,
        "files": files,
    }


def _registry_json_value(value):
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, tuple):
        return list(value)
    return value


def read_qsettings_registry():
    if sys.platform != "win32":
        raise DataGuardError("QSettings registry capture requires Windows")
    import winreg

    def read_key(key):
        subkey_count, value_count, _modified = winreg.QueryInfoKey(key)
        values = {}
        for index in range(value_count):
            name, value, value_type = winreg.EnumValue(key, index)
            values[name] = {
                "type": value_type,
                "value": _registry_json_value(value),
            }
        subkeys = {}
        for index in range(subkey_count):
            name = winreg.EnumKey(key, index)
            with winreg.OpenKey(key, name) as child:
                subkeys[name] = read_key(child)
        return {"values": values, "subkeys": subkeys}

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, QSETTINGS_REGISTRY_KEY) as key:
            return {
                "hive": "HKEY_CURRENT_USER",
                "key": QSETTINGS_REGISTRY_KEY,
                "tree": read_key(key),
            }
    except FileNotFoundError:
        raise DataGuardError(
            "HPLC Analyzer QSettings registry key does not exist; configure the "
            "baseline application first"
        )


def validate_paths(paths):
    labels = set(paths)
    expected = set(REQUIRED_LABELS)
    if labels != expected:
        missing = sorted(expected - labels)
        extra = sorted(labels - expected)
        details = []
        if missing:
            details.append("missing: {0}".format(", ".join(missing)))
        if extra:
            details.append("unexpected: {0}".format(", ".join(extra)))
        raise DataGuardError("protected path labels are invalid ({0})".format("; ".join(details)))


def build_snapshot(paths, qsettings):
    validate_paths(paths)
    return {
        "format_version": SNAPSHOT_FORMAT,
        "qsettings": qsettings,
        "protected_data": {
            label: snapshot_path(paths[label]) for label in REQUIRED_LABELS
        },
    }


def write_snapshot(path, snapshot, overwrite=False):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if overwrite else "x"
    with destination.open(mode, encoding="utf-8", newline="\n") as stream:
        json.dump(snapshot, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    return destination


def read_snapshot(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DataGuardError("could not read baseline snapshot: {0}".format(exc))
    if not isinstance(payload, dict) or payload.get("format_version") != SNAPSHOT_FORMAT:
        raise DataGuardError("unsupported baseline snapshot format")
    if set(payload.get("protected_data", {})) != set(REQUIRED_LABELS):
        raise DataGuardError("baseline snapshot does not contain every protected label")
    return payload


def compare_snapshots(baseline, current):
    errors = []
    if baseline.get("qsettings") != current.get("qsettings"):
        errors.append("QSettings registry changed")
    old_data = baseline.get("protected_data", {})
    new_data = current.get("protected_data", {})
    for label in REQUIRED_LABELS:
        before = old_data.get(label)
        after = new_data.get(label)
        if before == after:
            continue
        if before is None or after is None:
            errors.append("protected data is missing: {0}".format(label))
            continue
        if before.get("path") != after.get("path"):
            errors.append("protected path changed: {0}".format(label))
        if before.get("kind") != after.get("kind"):
            errors.append("protected path type changed: {0}".format(label))
            continue
        if before.get("kind") == "file":
            if before.get("sha256") != after.get("sha256"):
                errors.append("protected file content changed: {0}".format(label))
            if before.get("size") != after.get("size"):
                errors.append("protected file size changed: {0}".format(label))
            continue
        old_files = before.get("files", {})
        new_files = after.get("files", {})
        for relative in sorted(set(old_files) - set(new_files)):
            errors.append("protected file was removed: {0}/{1}".format(label, relative))
        for relative in sorted(set(new_files) - set(old_files)):
            errors.append("protected file was added: {0}/{1}".format(label, relative))
        for relative in sorted(set(old_files) & set(new_files)):
            if old_files[relative] != new_files[relative]:
                errors.append("protected file changed: {0}/{1}".format(label, relative))
        if before.get("directories", []) != after.get("directories", []):
            errors.append("protected directory structure changed: {0}".format(label))
    return errors


def parse_paths(values):
    paths = {}
    for value in values or []:
        if "=" not in value:
            raise DataGuardError("--path must use LABEL=PATH: {0}".format(value))
        label, path = value.split("=", 1)
        label = label.strip().lower()
        if not label or not path.strip() or label in paths:
            raise DataGuardError("invalid or duplicate protected path: {0}".format(value))
        paths[label] = path.strip().strip('"')
    validate_paths(paths)
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("snapshot", "verify"))
    parser.add_argument("--snapshot", required=True)
    parser.add_argument(
        "--path",
        action="append",
        help="required LABEL=PATH; labels: presets, database, projects, raw, exports",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        paths = parse_paths(args.path)
        current = build_snapshot(paths, read_qsettings_registry())
        if args.mode == "snapshot":
            destination = write_snapshot(
                args.snapshot, current, overwrite=args.force
            )
            print("[OK] Protected-data baseline: {0}".format(destination))
            return 0
        baseline = read_snapshot(args.snapshot)
        errors = compare_snapshots(baseline, current)
    except (DataGuardError, FileExistsError, OSError) as exc:
        print("[ERROR] {0}".format(exc))
        return 1
    for error in errors:
        print("[ERROR] {0}".format(error))
    if errors:
        return 1
    print("[OK] QSettings and every protected user-data byte are unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
