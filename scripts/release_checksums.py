"""Generate and verify SHA256SUMS.txt for the canonical Release assets."""

import argparse
import hashlib
from pathlib import Path
import re
import sys

try:
    from .artifact_names import artifact_names
    from .read_version import read_version
except ImportError:
    from artifact_names import artifact_names
    from read_version import read_version


CHECKSUM_FILENAME = "SHA256SUMS.txt"
CHECKSUM_PATTERN = re.compile(r"^([0-9a-f]{64})  ([^/\\]+)$")


def release_asset_names(version):
    return tuple(sorted(artifact_names(version).values()))


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def checksum_text(release_dir, version):
    directory = Path(release_dir)
    rows = []
    for filename in release_asset_names(version):
        path = directory / filename
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError("final Release asset is missing: {0}".format(path))
        rows.append("{0}  {1}".format(sha256_file(path), filename))
    return "\n".join(rows) + "\n"


def write_sha256sums(release_dir, version, output=None, overwrite=False):
    directory = Path(release_dir)
    destination = Path(output) if output else directory / CHECKSUM_FILENAME
    text = checksum_text(directory, version)
    destination.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if overwrite else "x"
    with destination.open(mode, encoding="ascii", newline="\n") as stream:
        stream.write(text)
    return destination


def read_sha256sums(path):
    entries = {}
    try:
        lines = Path(path).read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError("could not read checksum file: {0}".format(exc))
    if not lines:
        raise ValueError("checksum file is empty")
    for line_number, line in enumerate(lines, 1):
        match = CHECKSUM_PATTERN.fullmatch(line)
        if not match:
            raise ValueError(
                "invalid SHA256SUMS line {0}: {1!r}".format(line_number, line)
            )
        digest, filename = match.groups()
        if filename in entries:
            raise ValueError("duplicate checksum filename: {0}".format(filename))
        entries[filename] = digest
    return entries


def verify_sha256sums(release_dir, version, checksum_path=None):
    directory = Path(release_dir)
    path = Path(checksum_path) if checksum_path else directory / CHECKSUM_FILENAME
    try:
        entries = read_sha256sums(path)
    except ValueError as exc:
        return [str(exc)]
    expected_names = set(release_asset_names(version))
    actual_names = set(entries)
    errors = []
    for filename in sorted(expected_names - actual_names):
        errors.append("checksum entry is missing: {0}".format(filename))
    for filename in sorted(actual_names - expected_names):
        errors.append("unexpected checksum entry: {0}".format(filename))
    for filename in sorted(expected_names & actual_names):
        asset = directory / filename
        if not asset.is_file() or asset.is_symlink():
            errors.append("Release asset is missing: {0}".format(filename))
            continue
        actual = sha256_file(asset)
        if actual != entries[filename]:
            errors.append("SHA-256 mismatch: {0}".format(filename))
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("write", "verify"))
    parser.add_argument("--release-dir", required=True)
    parser.add_argument("--checksum-file")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--version-file")
    args = parser.parse_args(argv)
    version_path = (
        Path(args.version_file)
        if args.version_file
        else Path(__file__).resolve().parents[1] / "hplc_app" / "version.py"
    )
    try:
        version = read_version(version_path)
        if args.mode == "write":
            output = write_sha256sums(
                args.release_dir,
                version,
                output=args.checksum_file,
                overwrite=args.force,
            )
            print("[OK] Final Release checksums: {0}".format(output))
            print("[INFO] Generate this file only after signing and final renaming.")
            return 0
        errors = verify_sha256sums(
            args.release_dir, version, checksum_path=args.checksum_file
        )
    except (FileExistsError, FileNotFoundError, OSError, ValueError) as exc:
        print("[ERROR] {0}".format(exc))
        return 1
    for error in errors:
        print("[ERROR] {0}".format(error))
    if errors:
        return 1
    print("[OK] SHA256SUMS covers exactly the three canonical Release assets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
