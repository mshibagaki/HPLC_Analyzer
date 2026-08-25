"""Verify PyInstaller EXE version-resource fields without changing the file."""

try:
    from .read_version import read_version, windows_numeric_version
    from .write_windows_version_info import expected_version_strings
except ImportError:
    from read_version import read_version, windows_numeric_version
    from write_windows_version_info import expected_version_strings


def _decode(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def read_version_resource(path):
    try:
        import pefile
    except ImportError as exc:
        raise RuntimeError("pefile is required to inspect Windows version resources") from exc
    pe = pefile.PE(str(path), fast_load=False)
    try:
        strings = {}
        for group in getattr(pe, "FileInfo", []) or []:
            for entry in group:
                if _decode(getattr(entry, "Key", "")) != "StringFileInfo":
                    continue
                for table in getattr(entry, "StringTable", []) or []:
                    strings.update(
                        {_decode(key): _decode(value) for key, value in table.entries.items()}
                    )
        fixed_entries = getattr(pe, "VS_FIXEDFILEINFO", []) or []
        if not fixed_entries:
            raise ValueError("VS_FIXEDFILEINFO is missing")
        fixed = fixed_entries[0]
        file_version = (
            fixed.FileVersionMS >> 16,
            fixed.FileVersionMS & 0xFFFF,
            fixed.FileVersionLS >> 16,
            fixed.FileVersionLS & 0xFFFF,
        )
        product_version = (
            fixed.ProductVersionMS >> 16,
            fixed.ProductVersionMS & 0xFFFF,
            fixed.ProductVersionLS >> 16,
            fixed.ProductVersionLS & 0xFFFF,
        )
        return strings, file_version, product_version, int(fixed.FileFlags)
    finally:
        pe.close()


def verify_executable_metadata(path, target, debug=False, version=None):
    if version is None:
        version = read_version()
    expected_strings = expected_version_strings(target, debug=debug, version=version)
    expected_numeric = tuple(
        int(part) for part in windows_numeric_version(version).split(".")
    )
    try:
        strings, file_version, product_version, flags = read_version_resource(path)
    except (OSError, RuntimeError, ValueError) as exc:
        return ["could not inspect EXE version metadata: {0}".format(exc)]
    errors = []
    for key, expected in expected_strings.items():
        if strings.get(key) != expected:
            errors.append(
                "EXE {0} mismatch: expected {1!r}, found {2!r}".format(
                    key, expected, strings.get(key)
                )
            )
    if file_version != expected_numeric:
        errors.append(
            "EXE fixed FileVersion mismatch: expected {0}, found {1}".format(
                expected_numeric, file_version
            )
        )
    if product_version != expected_numeric:
        errors.append(
            "EXE fixed ProductVersion mismatch: expected {0}, found {1}".format(
                expected_numeric, product_version
            )
        )
    debug_flag = bool(flags & 0x1)
    if debug_flag != bool(debug):
        errors.append(
            "EXE debug flag mismatch: expected {0}, found {1}".format(
                bool(debug), debug_flag
            )
        )
    return errors
