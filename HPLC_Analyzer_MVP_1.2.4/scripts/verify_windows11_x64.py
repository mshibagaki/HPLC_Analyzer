"""Preflight checks for the Windows 11 / CPython 3.11 x64 build."""

import argparse
import importlib
import struct
import sys
from pathlib import Path


EXPECTED_PYTHON = (3, 11)
EXPECTED_BITS = 64
EXPECTED_PACKAGES = {
    "matplotlib": "3.10.1",
    "numpy": "2.2.3",
    "PyInstaller": "6.12.0",
    "PySide6": "6.8.3",
}
PE_MACHINE_AMD64 = 0x8664


def interpreter_description():
    version = ".".join(str(part) for part in sys.version_info[:3])
    bits = struct.calcsize("P") * 8
    return "Python {0} ({1}-bit)".format(version, bits)


def verify_interpreter():
    version = tuple(sys.version_info[:2])
    bits = struct.calcsize("P") * 8
    if version != EXPECTED_PYTHON or bits != EXPECTED_BITS:
        print(
            "[ERROR] Python 3.11 64-bit is required; found {0}.".format(
                interpreter_description()
            )
        )
        return False
    print("[OK] {0}".format(interpreter_description()))
    return True


def verify_packages():
    if not verify_interpreter():
        return False
    valid = True
    for module_name, expected in sorted(EXPECTED_PACKAGES.items()):
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            print("[ERROR] {0} could not be imported: {1}".format(module_name, exc))
            valid = False
            continue
        actual = getattr(module, "__version__", "")
        if actual != expected:
            print(
                "[ERROR] {0} {1} is required; found {2}.".format(
                    module_name, expected, actual or "unknown"
                )
            )
            valid = False
        else:
            print("[OK] {0} {1}".format(module_name, actual))
    return valid


def read_pe_machine(path):
    executable = Path(path)
    with executable.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError("DOS header (MZ) was not found")
        stream.seek(0x3C)
        offset_data = stream.read(4)
        if len(offset_data) != 4:
            raise ValueError("PE header offset is missing")
        pe_offset = struct.unpack("<I", offset_data)[0]
        stream.seek(pe_offset)
        if stream.read(4) != b"PE\0\0":
            raise ValueError("PE signature was not found")
        machine_data = stream.read(2)
        if len(machine_data) != 2:
            raise ValueError("PE machine field is missing")
        return struct.unpack("<H", machine_data)[0]


def verify_executable(path):
    try:
        machine = read_pe_machine(path)
    except (OSError, ValueError) as exc:
        print("[ERROR] Could not inspect {0}: {1}".format(path, exc))
        return False
    if machine != PE_MACHINE_AMD64:
        print(
            "[ERROR] {0} is not a Windows x64 executable (PE machine 0x{1:04X}).".format(
                path, machine
            )
        )
        return False
    print("[OK] {0} is a Windows x64 / 64-bit executable.".format(path))
    return True


def main(argv=None):
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--interpreter", action="store_true")
    modes.add_argument("--packages", action="store_true")
    modes.add_argument("--exe", metavar="PATH")
    args = parser.parse_args(argv)

    if args.interpreter:
        valid = verify_interpreter()
    elif args.packages:
        valid = verify_packages()
    else:
        valid = verify_executable(args.exe)
    return 0 if valid else 1


if __name__ == "__main__":
    sys.exit(main())
