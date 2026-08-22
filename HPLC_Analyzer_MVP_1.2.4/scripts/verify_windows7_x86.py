"""Preflight checks for the Windows 7 / CPython 3.8.10 x86 build."""

import argparse
import ctypes
import importlib
import struct
import sys
from pathlib import Path


EXPECTED_PYTHON = (3, 8, 10)
EXPECTED_BITS = 32
EXPECTED_PACKAGES = {
    "matplotlib": "3.7.5",
    "numpy": "1.20.3",
    "PIL": "9.5.0",
    "PyInstaller": "5.13.2",
    "PySide2": "5.15.2.1",
    "shiboken2": "5.15.2.1",
}
PE_MACHINE_I386 = 0x014C


def verify_host():
    """Verify that the build is running on updated Windows 7 SP1 x86."""
    if sys.platform != "win32" or not hasattr(sys, "getwindowsversion"):
        print("[ERROR] The Windows 7 target must be built on Windows 7 itself.")
        return False
    version = sys.getwindowsversion()
    service_pack = getattr(version, "service_pack_major", 0)
    bits = struct.calcsize("P") * 8
    if (version.major, version.minor, version.build) != (6, 1, 7601):
        print(
            "[ERROR] Windows 7 SP1 build 7601 is required; found {0}.{1}.{2}.".format(
                version.major, version.minor, version.build
            )
        )
        return False
    if service_pack < 1 or bits != EXPECTED_BITS:
        print(
            "[ERROR] Windows 7 SP1 32-bit is required; service pack={0}, bits={1}.".format(
                service_pack, bits
            )
        )
        return False

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_proc_address = kernel32.GetProcAddress
    get_proc_address.argtypes = (ctypes.c_void_p, ctypes.c_char_p)
    get_proc_address.restype = ctypes.c_void_p
    missing = [
        name.decode("ascii")
        for name in (b"AddDllDirectory", b"SetDefaultDllDirectories")
        if not get_proc_address(kernel32._handle, name)
    ]
    if missing:
        print(
            "[ERROR] Required Windows loader APIs are missing: {0}.".format(
                ", ".join(missing)
            )
        )
        print("Install all Windows 7 SP1 updates, including KB2533623, and reboot.")
        return False
    print("[OK] Windows 7 SP1 build 7601 x86 with updated DLL loader APIs.")
    return True


def interpreter_description():
    version = ".".join(str(part) for part in sys.version_info[:3])
    bits = struct.calcsize("P") * 8
    return "Python {0} ({1}-bit)".format(version, bits)


def verify_interpreter():
    version = tuple(sys.version_info[:3])
    bits = struct.calcsize("P") * 8
    if version != EXPECTED_PYTHON or bits != EXPECTED_BITS:
        print(
            "[ERROR] Python 3.8.10 32-bit is required; found {0}.".format(
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
    if machine != PE_MACHINE_I386:
        print(
            "[ERROR] {0} is not a Windows x86 executable (PE machine 0x{1:04X}).".format(
                path, machine
            )
        )
        return False
    print("[OK] {0} is a Windows x86 / 32-bit executable.".format(path))
    return True


def main(argv=None):
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--host", action="store_true")
    modes.add_argument("--interpreter", action="store_true")
    modes.add_argument("--packages", action="store_true")
    modes.add_argument("--exe", metavar="PATH")
    args = parser.parse_args(argv)

    if args.host:
        valid = verify_host()
    elif args.interpreter:
        valid = verify_interpreter()
    elif args.packages:
        valid = verify_packages()
    else:
        valid = verify_executable(args.exe)
    return 0 if valid else 1


if __name__ == "__main__":
    sys.exit(main())
