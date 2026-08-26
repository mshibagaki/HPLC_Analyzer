"""Version-independent storage for application-wide named presets."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Optional, Tuple
import uuid

from . import APP_VERSION
from .models import sanitize_condition_presets


PRESET_STORE_FORMAT = 2
PRESET_KINDS = ("conditions", "gradients")
_LEGACY_ID_NAMESPACE = uuid.UUID("311b96bd-a94a-4dc9-ae65-3591512ac6a8")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _legacy_preset_id(kind: str, name: str) -> str:
    return uuid.uuid5(_LEGACY_ID_NAMESPACE, "{0}:{1}".format(kind, name)).hex


def _metadata_record(kind: str, name: str, raw=None) -> Dict[str, str]:
    source = raw if isinstance(raw, dict) else {}
    preset_id = str(source.get("id", "") or "").strip()
    if not preset_id:
        preset_id = _legacy_preset_id(kind, name)
    return {
        "id": preset_id,
        "created_at": str(source.get("created_at", "") or ""),
        "updated_at": str(source.get("updated_at", "") or ""),
        "last_used_at": str(source.get("last_used_at", "") or ""),
    }


def normalize_preset_metadata(
    condition_presets: Dict[str, Dict[str, Any]],
    gradient_presets: Dict[str, Dict[str, Any]],
    metadata=None,
) -> Dict[str, Dict[str, Dict[str, str]]]:
    """Keep metadata separate and create no fictional legacy timestamps."""
    raw = metadata if isinstance(metadata, dict) else {}
    result = {"conditions": {}, "gradients": {}}
    for kind, presets in (
        ("conditions", condition_presets),
        ("gradients", gradient_presets),
    ):
        raw_kind = raw.get(kind, {})
        if not isinstance(raw_kind, dict):
            raw_kind = {}
        for name in sorted((presets or {}).keys()):
            result[kind][name] = _metadata_record(
                kind, name, raw_kind.get(name)
            )
    return result


def record_preset_saved(
    metadata,
    kind: str,
    old_name: str,
    new_name: str,
    now: Optional[str] = None,
) -> None:
    if kind not in PRESET_KINDS:
        raise ValueError("unknown preset kind: {0}".format(kind))
    timestamp = now or _now()
    records = metadata.setdefault(kind, {})
    record = records.get(old_name) or records.get(new_name)
    if record is None:
        record = {
            "id": uuid.uuid4().hex,
            "created_at": timestamp,
            "updated_at": timestamp,
            "last_used_at": "",
        }
    else:
        record = _metadata_record(kind, old_name or new_name, record)
        if not record["created_at"]:
            # An existing format-1 preset keeps its unknown creation time.
            record["created_at"] = ""
        record["updated_at"] = timestamp
    if old_name and old_name != new_name:
        records.pop(old_name, None)
    records[new_name] = record


def record_preset_used(
    metadata, kind: str, name: str, now: Optional[str] = None
) -> None:
    if kind not in PRESET_KINDS:
        raise ValueError("unknown preset kind: {0}".format(kind))
    records = metadata.setdefault(kind, {})
    record = _metadata_record(kind, name, records.get(name))
    record["last_used_at"] = now or _now()
    records[name] = record


def record_preset_deleted(metadata, kind: str, name: str) -> None:
    if kind not in PRESET_KINDS:
        raise ValueError("unknown preset kind: {0}".format(kind))
    metadata.setdefault(kind, {}).pop(name, None)


def stable_preset_names(names, metadata, kind: str):
    """Newest known creations first; legacy/unknown dates use name order."""
    records = metadata.get(kind, {}) if isinstance(metadata, dict) else {}
    known = []
    unknown = []
    for name in names:
        created_at = str((records.get(name) or {}).get("created_at", "") or "")
        if created_at:
            known.append((created_at, name))
        else:
            unknown.append(name)
    known.sort(key=lambda item: (item[0], item[1].casefold()), reverse=True)
    unknown.sort(key=lambda name: name.casefold())
    return [name for _created, name in known] + unknown


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
    """Load payloads with the legacy two-value API."""
    conditions, gradients, _metadata = load_preset_store_with_metadata(path)
    return conditions, gradients


def load_preset_store_with_metadata(
    path: Optional[Path] = None,
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, Dict[str, Any]],
    Dict[str, Dict[str, Dict[str, str]]],
]:
    """Load payloads and application-only metadata from format 1 or 2."""
    source = Path(path) if path is not None else preset_store_path()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}, {}, normalize_preset_metadata({}, {})
    if not isinstance(payload, dict):
        return {}, {}, normalize_preset_metadata({}, {})
    raw_conditions = payload.get("condition_presets", {})
    if not isinstance(raw_conditions, dict):
        raw_conditions = {}
    conditions = sanitize_condition_presets(raw_conditions)
    gradients = payload.get("gradient_presets", {})
    if not isinstance(gradients, dict):
        gradients = {}
    gradients = deepcopy(gradients)
    metadata = normalize_preset_metadata(
        conditions, gradients, payload.get("preset_metadata", {})
    )
    return conditions, gradients, metadata


def save_preset_store(
    condition_presets: Dict[str, Dict[str, Any]],
    gradient_presets: Dict[str, Dict[str, Any]],
    path: Optional[Path] = None,
    metadata=None,
) -> Path:
    """Atomically save presets so an interrupted upgrade cannot corrupt them."""
    destination = Path(path) if path is not None else preset_store_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    conditions = sanitize_condition_presets(condition_presets)
    gradients = deepcopy(gradient_presets or {})
    if metadata is None:
        old_conditions, old_gradients, old_metadata = load_preset_store_with_metadata(
            destination
        )
        metadata = old_metadata
        for kind, old_presets, new_presets in (
            ("conditions", old_conditions, conditions),
            ("gradients", old_gradients, gradients),
        ):
            removed = {
                name: payload
                for name, payload in old_presets.items()
                if name not in new_presets
            }
            for name, payload in new_presets.items():
                if name in old_presets:
                    if old_presets[name] != payload:
                        record_preset_saved(metadata, kind, name, name)
                    continue
                renamed_from = next(
                    (
                        old_name
                        for old_name, old_payload in removed.items()
                        if old_payload == payload
                    ),
                    "",
                )
                record_preset_saved(metadata, kind, renamed_from, name)
                removed.pop(renamed_from, None)
    normalized_metadata = normalize_preset_metadata(
        conditions, gradients, metadata
    )
    payload = {
        "format_version": PRESET_STORE_FORMAT,
        "written_by": APP_VERSION,
        "condition_presets": conditions,
        "gradient_presets": gradients,
        "preset_metadata": normalized_metadata,
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
