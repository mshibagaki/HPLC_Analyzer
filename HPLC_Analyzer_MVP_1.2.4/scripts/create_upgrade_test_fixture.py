"""Create a non-production user-data fixture for installer upgrade testing."""

import argparse
import json
from pathlib import Path
import shutil
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from hplc_app.database import sync_project_to_database
from hplc_app.parser import load_ascii_file
from hplc_app.preset_store import save_preset_store
from hplc_app.project_io import save_project
from hplc_app.models import Project


def create_fixture(root, source_root=None):
    destination = Path(root).resolve()
    if destination == Path(destination.anchor) or destination == Path.home().resolve():
        raise ValueError("fixture root must be a dedicated subdirectory")
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError("fixture root must be new or empty: {0}".format(destination))
    destination.mkdir(parents=True, exist_ok=True)
    project_root = (
        Path(source_root).resolve()
        if source_root
        else PROJECT_ROOT
    )
    source_ascii = project_root / "sample_data" / "210601.TXT"
    raw_dir = destination / "raw-ascii"
    project_dir = destination / "projects"
    export_dir = destination / "user-exports"
    config_dir = destination / "user-config"
    for directory in (raw_dir, project_dir, export_dir, config_dir):
        directory.mkdir()
    raw_path = raw_dir / "upgrade-canary.TXT"
    shutil.copyfile(str(source_ascii), str(raw_path))
    dataset = load_ascii_file(str(raw_path))
    dataset.label = "UPGRADE-PRESERVATION-CANARY"
    project = Project(
        title="Installer upgrade preservation canary",
        author="Release validation",
        ui_language="en",
        datasets=[dataset],
        condition_presets={
            "Canary 280 nm": {
                "wavelength_nm": 280.0,
                "column_name": "Upgrade Canary C4",
                "metadata_note": "must survive installer operations",
            }
        },
        gradient_presets={
            "Canary gradient": {
                "gradient": [
                    {
                        "time_min": 0.0,
                        "a_pct": 90.0,
                        "b_pct": 10.0,
                        "c_pct": 0.0,
                        "d_pct": 0.0,
                        "flow_ml_min": 1.0,
                    }
                ],
                "solvents": {"A": {"name": "Water"}, "B": {"name": "ACN"}},
                "metadata_note": "must survive installer operations",
            }
        },
    )
    project_path = project_dir / "upgrade-preservation.hplcproj"
    save_project(str(project_path), project)
    database_path = destination / "laboratory.sqlite3"
    sync_project_to_database(str(database_path), project)
    preset_path = config_dir / "presets.json"
    save_preset_store(
        project.condition_presets, project.gradient_presets, preset_path
    )
    export_path = export_dir / "user-export-canary.csv"
    export_path.write_text(
        "marker,value\nupgrade-preservation,do-not-delete\n",
        encoding="utf-8",
        newline="\n",
    )
    paths = {
        "presets": str(preset_path),
        "database": str(database_path),
        "projects": str(project_dir),
        "raw": str(raw_dir),
        "exports": str(export_dir),
    }
    manifest = destination / "fixture_paths.json"
    manifest.write_text(
        json.dumps(paths, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return paths, manifest


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    try:
        paths, manifest = create_fixture(args.root)
    except (FileExistsError, OSError, ValueError) as exc:
        print("[ERROR] {0}".format(exc))
        return 1
    print("[OK] Installer preservation fixture: {0}".format(Path(args.root).resolve()))
    print("[OK] Protected-path record: {0}".format(manifest))
    for label in sorted(paths):
        print('--path "{0}={1}"'.format(label, paths[label]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
