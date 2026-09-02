"""Integrity and architecture checks for the self-contained Windows 7 build kit."""

from __future__ import print_function

import argparse
import hashlib
import os
import struct
import sys
from pathlib import Path


MANIFEST_RELATIVE = "win7_offline/MANIFEST.sha256"
PE_MACHINE_I386 = 0x014C
EXPECTED_RELATIVE_FILES = (
    "win7_offline/REQUIRED_WHEELS.txt",
    "win7_offline/installers/VC_redist.x86.exe",
    "win7_offline/installers/innosetup-6.7.3.exe",
    "win7_offline/installers/python-3.8.10.exe",
    "win7_offline/wheels/PySide2-5.15.2.1-5.15.2-cp35.cp36.cp37.cp38.cp39.cp310-none-win32.whl",
    "win7_offline/wheels/altgraph-0.17.4-py2.py3-none-any.whl",
    "win7_offline/wheels/contourpy-1.1.0-cp38-cp38-win32.whl",
    "win7_offline/wheels/cycler-0.12.1-py3-none-any.whl",
    "win7_offline/wheels/fonttools-4.57.0-cp38-cp38-win32.whl",
    "win7_offline/wheels/importlib_resources-6.4.5-py3-none-any.whl",
    "win7_offline/wheels/kiwisolver-1.4.7-cp38-cp38-win32.whl",
    "win7_offline/wheels/matplotlib-3.7.5-cp38-cp38-win32.whl",
    "win7_offline/wheels/numpy-1.20.3-cp38-cp38-win32.whl",
    "win7_offline/wheels/packaging-23.2-py3-none-any.whl",
    "win7_offline/wheels/pefile-2023.2.7-py3-none-any.whl",
    "win7_offline/wheels/Pillow-9.5.0-cp38-cp38-win32.whl",
    "win7_offline/wheels/pip-23.3.2-py3-none-any.whl",
    "win7_offline/wheels/pyinstaller-5.13.2-py3-none-win32.whl",
    "win7_offline/wheels/pyinstaller_hooks_contrib-2023.6-py2.py3-none-any.whl",
    "win7_offline/wheels/pyparsing-3.1.4-py3-none-any.whl",
    "win7_offline/wheels/pyqtgraph-0.13.3-py3-none-any.whl",
    "win7_offline/wheels/python_dateutil-2.9.0.post0-py2.py3-none-any.whl",
    "win7_offline/wheels/pywin32_ctypes-0.2.2-py3-none-any.whl",
    "win7_offline/wheels/setuptools-68.2.2-py3-none-any.whl",
    "win7_offline/wheels/shiboken2-5.15.2.1-5.15.2-cp35.cp36.cp37.cp38.cp39.cp310-none-win32.whl",
    "win7_offline/wheels/six-1.16.0-py2.py3-none-any.whl",
    "win7_offline/wheels/wheel-0.41.2-py3-none-any.whl",
    "win7_offline/wheels/zipp-3.20.2-py3-none-any.whl",
)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_manifest(path):
    entries = {}
    with Path(path).open("r", encoding="ascii") as stream:
        for line_number, raw_line in enumerate(stream, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) != 2 or len(parts[0]) != 64:
                raise ValueError("invalid manifest line {0}".format(line_number))
            relative = parts[1].lstrip("*").replace("\\", "/")
            if relative in entries:
                raise ValueError("duplicate manifest path: {0}".format(relative))
            entries[relative] = parts[0].lower()
    return entries


def read_pe_machine(path):
    with Path(path).open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError("DOS header (MZ) was not found")
        stream.seek(0x3C)
        offset_data = stream.read(4)
        if len(offset_data) != 4:
            raise ValueError("PE header offset is missing")
        stream.seek(struct.unpack("<I", offset_data)[0])
        if stream.read(4) != b"PE\0\0":
            raise ValueError("PE signature was not found")
        return struct.unpack("<H", stream.read(2))[0]


def verify_bundle(root):
    root = Path(root).resolve()
    errors = []
    manifest_path = root / MANIFEST_RELATIVE
    try:
        entries = read_manifest(manifest_path)
    except (OSError, ValueError) as exc:
        return ["could not read offline manifest: {0}".format(exc)]

    expected = set(EXPECTED_RELATIVE_FILES)
    actual = set(entries)
    if actual != expected:
        for relative in sorted(expected - actual):
            errors.append("manifest is missing: {0}".format(relative))
        for relative in sorted(actual - expected):
            errors.append("manifest has an unexpected entry: {0}".format(relative))

    root_text = str(root)
    for relative, expected_hash in sorted(entries.items()):
        candidate = (root / relative).resolve()
        try:
            if os.path.commonpath((root_text, str(candidate))) != root_text:
                errors.append("manifest path escapes the project root: {0}".format(relative))
                continue
        except ValueError:
            errors.append("manifest path is on another drive: {0}".format(relative))
            continue
        if not candidate.is_file():
            errors.append("offline asset is missing: {0}".format(relative))
            continue
        actual_hash = sha256_file(candidate)
        if actual_hash != expected_hash:
            errors.append("SHA-256 mismatch: {0}".format(relative))

    offline_root = root / "win7_offline"
    payload_files = {
        path.relative_to(root).as_posix()
        for path in offline_root.rglob("*")
        if path.is_file() and path.suffix.lower() in (".exe", ".whl")
    }
    for relative in sorted(payload_files - expected):
        errors.append("unmanifested offline payload: {0}".format(relative))

    for relative in sorted(expected):
        name = Path(relative).name.lower()
        if name.endswith(".whl") and any(
            marker in name
            for marker in ("win_amd64", "manylinux", "macosx", "aarch64", "arm64")
        ):
            errors.append("non-x86 wheel in Windows 7 bundle: {0}".format(relative))
        if name.endswith(".exe") and (root / relative).is_file():
            try:
                machine = read_pe_machine(root / relative)
            except (OSError, ValueError, struct.error) as exc:
                errors.append("could not inspect {0}: {1}".format(relative, exc))
            else:
                if machine != PE_MACHINE_I386:
                    errors.append("offline installer is not PE32 x86: {0}".format(relative))
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args(argv)
    errors = verify_bundle(args.root)
    for error in errors:
        print("[ERROR] {0}".format(error))
    if errors:
        return 1
    print("[OK] Windows 7 offline build kit: all hashes, files, and x86 payloads verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
