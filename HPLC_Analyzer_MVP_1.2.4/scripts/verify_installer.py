"""Static and output checks for the two HPLC Analyzer installers."""

import argparse
import struct
import sys
from pathlib import Path


APP_ID = "{{D21F975D-644A-48D3-8A67-40DC7BD85AAF}"
APP_VERSION = "1.2.4"

TARGETS = {
    "windows11-x64": {
        "required": (
            "AppVersion=1.2.4",
            "MinVersion=10.0.22000",
            "ArchitecturesAllowed=x64compatible",
            "ArchitecturesInstallIn64BitMode=x64compatible",
            "dist\\windows11-x64\\HPLC_Analyzer.exe",
            "OutputBaseFilename=HPLC_Analyzer_Setup_1.2.4_Windows11_x64",
        ),
        "forbidden": (
            "HPLC_Analyzer_Debug.exe",
            "ArchitecturesAllowed=x86compatible",
        ),
    },
    "windows7-x86": {
        "required": (
            "AppVersion=1.2.4",
            "MinVersion=6.1sp1",
            "ArchitecturesAllowed=x86compatible and not x64compatible",
            "ArchitecturesInstallIn64BitMode=",
            "dist\\windows7-x86\\HPLC_Analyzer.exe",
            "dist\\windows7-x86\\HPLC_Analyzer_Debug.exe",
            "installer\\redist\\VC_redist.x86.exe",
            'Parameters: "/install /quiet /norestart"',
            "Check: VCRedistNeedsInstall",
            "function VCRedistNeedsInstall: Boolean;",
            "OutputBaseFilename=HPLC_Analyzer_Setup_1.2.4_Windows7_x86",
        ),
        "forbidden": (
            "ArchitecturesInstallIn64BitMode=x64compatible",
        ),
    },
}

COMMON_REQUIRED = (
    "AppId=" + APP_ID,
    "AppName=HPLC Analyzer",
    "PrivilegesRequired=admin",
    "SetupIconFile=assets\\app_icon.ico",
    "UninstallDisplayIcon={app}\\HPLC_Analyzer.exe",
    "THIRD_PARTY_NOTICES.txt",
    "third_party_licenses\\*",
    '[Tasks]',
    'Name: "desktopicon"',
    '[Icons]',
    '[Run]',
)

PROTECTED_DATA_MARKERS = (
    "[UninstallDelete]",
    "[InstallDelete]",
    "Research Tools\\HPLC Analyzer",
    "{userappdata}",
    "{userdocs}",
    "presets.json",
    ".hplcproj",
    ".sqlite",
    "uninsdelete",
)


def verify_installer_script(target, path):
    """Return a list of human-readable configuration errors."""
    script_path = Path(path)
    try:
        text = script_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return ["could not read {0}: {1}".format(script_path, exc)]
    errors = []
    for fragment in COMMON_REQUIRED + TARGETS[target]["required"]:
        if fragment not in text:
            errors.append("missing required installer setting: {0}".format(fragment))
    for fragment in TARGETS[target]["forbidden"]:
        if fragment in text:
            errors.append("unexpected installer setting: {0}".format(fragment))
    lowered = text.lower()
    for marker in PROTECTED_DATA_MARKERS:
        if marker.lower() in lowered:
            errors.append(
                "installer must not manage protected user data: {0}".format(marker)
            )
    return errors


def verify_setup_executable(path):
    """Return a list of errors after checking the generated Setup EXE container."""
    setup_path = Path(path)
    try:
        size = setup_path.stat().st_size
        with setup_path.open("rb") as stream:
            if stream.read(2) != b"MZ":
                return ["DOS header (MZ) was not found in {0}".format(setup_path)]
            stream.seek(0x3C)
            offset_data = stream.read(4)
            if len(offset_data) != 4:
                return ["PE header offset is missing in {0}".format(setup_path)]
            pe_offset = struct.unpack("<I", offset_data)[0]
            stream.seek(pe_offset)
            if stream.read(4) != b"PE\0\0":
                return ["PE signature was not found in {0}".format(setup_path)]
    except OSError as exc:
        return ["could not inspect {0}: {1}".format(setup_path, exc)]
    if size < 4096:
        return ["generated installer is unexpectedly small: {0} bytes".format(size)]
    return []


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--script", required=True)
    parser.add_argument("--setup")
    args = parser.parse_args(argv)

    errors = verify_installer_script(args.target, args.script)
    if not errors:
        print("[OK] Installer configuration: {0}".format(args.script))
    if args.setup:
        setup_errors = verify_setup_executable(args.setup)
        errors.extend(setup_errors)
        if not setup_errors:
            print("[OK] Offline Setup EXE: {0}".format(args.setup))
    for error in errors:
        print("[ERROR] {0}".format(error))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
