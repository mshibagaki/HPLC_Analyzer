"""Version-independent storage for application-wide named presets."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Optional, Tuple

from . import APP_VERSION
from .models import sanitize_condition_presets


PRESET_STORE_FORMAT = 1


def merge_preset_sources(
    legacy_conditions: Dict[str, Dict[str, Any]],
    legacy_gradients: Dict[str, Dict[str, Any]],
    stored_conditions: Dict[str, Dict[str, Any]],
    stored_gradients: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Merge QSettings fallback values under authoritative JSON presets."""
    conditions = sanitize_condition_presets(legacy_conditions)
    conditions.update(sanitize_condition_presets(stored_conditions))
    gradients = deepcopy(legacy_gradients or {})
    gradients.update(deepcopy(stored_gradients or {}))
    return conditions, gradients


def preset_store_path(config_directory: Optional[Path] = None) -> Path:
    """Return the stable preset path shared by every HPLC Analyzer v1.x build."""
    if config_directory is not None:
        base = Path(config_directory)
    else:
        override = os.environ.get("HPLC_ANALYZER_CONFIG_DIR", "").strip()
        if override:
            base = Path(override)
        elif os.name == "nt":
            roaming = os.environ.get("APPDATA", "").strip()
            base = Path(roaming) if roaming else Path.home() / "AppData" / "Roaming"
            base = base / "Research Tools" / "HPLC Analyzer"
        else:
            xdg_config = os.environ.get("XDG_CONFIG_HOME", "").strip()
            base = Path(xdg_config) if xdg_config else Path.home() / ".config"
            base = base / "Research Tools" / "HPLC Analyzer"
    return base / "presets.json"


def load_preset_store(
    path: Optional[Path] = None,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Load condition and gradient presets, tolerating absent/invalid files."""
    source = Path(path) if path is not None else preset_store_path()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}, {}
    if not isinstance(payload, dict):
        return {}, {}
    raw_conditions = payload.get("condition_presets", {})
    if not isinstance(raw_conditions, dict):
        raw_conditions = {}
    conditions = sanitize_condition_presets(raw_conditions)
    gradients = payload.get("gradient_presets", {})
    if not isinstance(gradients, dict):
        gradients = {}
    return conditions, deepcopy(gradients)


def save_preset_store(
    condition_presets: Dict[str, Dict[str, Any]],
    gradient_presets: Dict[str, Dict[str, Any]],
    path: Optional[Path] = None,
) -> Path:
    """Atomically save presets so an interrupted upgrade cannot corrupt them."""
    destination = Path(path) if path is not None else preset_store_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format_version": PRESET_STORE_FORMAT,
        "written_by": APP_VERSION,
        "condition_presets": sanitize_condition_presets(condition_presets),
        "gradient_presets": deepcopy(gradient_presets or {}),
    }
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="presets-", suffix=".tmp", dir=str(destination.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(str(temporary), str(destination))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return destination
