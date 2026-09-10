"""Version-independent storage for application-wide named presets."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Optional, Tuple
import uuid

from . import APP_VERSION
from .models import sanitize_condition_presets


PRESET_STORE_FORMAT = 3
PRESET_KINDS = ("conditions", "gradients", "analytes")
ANALYTE_PRESET_FIELDS = (
    "molar_absorptivity_214",
    "molar_absorptivity_280",
    "molecular_weight_g_mol",
)
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


def sanitize_analyte_presets(presets) -> Dict[str, Dict[str, Optional[float]]]:
    """Return analyte presets containing only supported positive values."""
    result = {}
    if not isinstance(presets, dict):
        return result
    for raw_name, raw_payload in presets.items():
        name = str(raw_name or "").strip()
        if not name or not isinstance(raw_payload, dict):
            continue
        payload = {}
        valid = True
        for field in ANALYTE_PRESET_FIELDS:
            value = raw_payload.get(field)
            if value in (None, ""):
                payload[field] = None
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                valid = False
                break
            if not math.isfinite(numeric) or numeric <= 0:
                valid = False
                break
            payload[field] = numeric
        if valid:
            result[name] = payload
    return result


def normalize_preset_metadata(
    condition_presets: Dict[str, Dict[str, Any]],
    gradient_presets: Dict[str, Dict[str, Any]],
    metadata=None,
    analyte_presets=None,
) -> Dict[str, Dict[str, Dict[str, str]]]:
    """Keep metadata separate and create no fictional legacy timestamps."""
    raw = metadata if isinstance(metadata, dict) else {}
    raw_analytes = raw.get("analytes", {})
    if analyte_presets is None:
        analyte_presets = raw_analytes if isinstance(raw_analytes, dict) else {}
    result = {"conditions": {}, "gradients": {}, "analytes": {}}
    for kind, presets in (
        ("conditions", condition_presets),
        ("gradients", gradient_presets),
        ("analytes", analyte_presets),
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


def apply_preset_operation(presets, metadata, kind, action, name, new_name=""):
    """Apply one manager operation atomically to copied preset state."""

    if kind not in PRESET_KINDS:
        raise ValueError("unknown preset kind: {0}".format(kind))
    if action not in ("rename", "duplicate", "delete"):
        raise ValueError("unknown preset operation: {0}".format(action))
    source = str(name or "").strip()
    target = str(new_name or "").strip()
    if source not in presets:
        raise ValueError("Preset does not exist: {0}".format(source))
    if action != "delete":
        if not target:
            raise ValueError("Preset name must not be blank")
        if target in presets and (action == "duplicate" or target != source):
            raise ValueError("Preset already exists: {0}".format(target))

    updated_presets = deepcopy(presets)
    updated_metadata = deepcopy(metadata)
    if action == "delete":
        updated_presets.pop(source)
        record_preset_deleted(updated_metadata, kind, source)
    elif action == "rename":
        if target != source:
            payload = updated_presets.pop(source)
            updated_presets[target] = payload
            record_preset_saved(updated_metadata, kind, source, target)
    else:
        updated_presets[target] = deepcopy(updated_presets[source])
        record_preset_saved(updated_metadata, kind, "", target)
    return updated_presets, updated_metadata


def apply_preset_content_edit(presets, metadata, kind, name, payload, new_name=""):
    """Atomically replace one preset's payload, optionally renaming it too.

    This is the atomic write-back counterpart to opening one of the existing
    condition/gradient/analyte edit screens on a preset and writing its
    result back into the store (WIN11_FEEDBACK_WORKFLOW.md 27.12): the name
    and every field can change together as one step under the same conflict
    policy as rename/duplicate/delete.
    """

    if kind not in PRESET_KINDS:
        raise ValueError("unknown preset kind: {0}".format(kind))
    source = str(name or "").strip()
    target = str(new_name or name or "").strip()
    if source not in presets:
        raise ValueError("Preset does not exist: {0}".format(source))
    if not target:
        raise ValueError("Preset name must not be blank")
    if target != source and target in presets:
        raise ValueError("Preset already exists: {0}".format(target))
    if not isinstance(payload, dict):
        raise ValueError("Preset content must be a mapping")

    updated_presets = deepcopy(presets)
    updated_metadata = deepcopy(metadata)
    if target != source:
        updated_presets.pop(source)
    updated_presets[target] = deepcopy(payload)
    record_preset_saved(updated_metadata, kind, source, target)
    return updated_presets, updated_metadata


def build_preset_package(
    conditions, gradients, metadata, names=None, analytes=None
):
    """Build a portable package containing preset payloads and metadata only."""

    selected = names or {
        "conditions": list(conditions),
        "gradients": list(gradients),
        "analytes": list(analytes or {}),
    }
    package = {"format": 1, "presets": {}, "metadata": {}}
    for kind, presets in (
        ("conditions", conditions),
        ("gradients", gradients),
        ("analytes", analytes or {}),
    ):
        requested = selected.get(kind, [])
        package["presets"][kind] = {
            name: deepcopy(presets[name]) for name in requested if name in presets
        }
        records = metadata.get(kind, {}) if isinstance(metadata, dict) else {}
        package["metadata"][kind] = {
            name: deepcopy(records[name])
            for name in package["presets"][kind]
            if name in records
        }
    return package


def merge_preset_package(
    conditions, gradients, metadata, package, conflicts=None, analytes=None
):
    """Validate and atomically merge a portable package with explicit policies."""

    if not isinstance(package, dict) or package.get("format") != 1:
        raise ValueError("Unsupported preset package format")
    incoming = package.get("presets")
    incoming_metadata = package.get("metadata", {})
    if not isinstance(incoming, dict) or not isinstance(incoming_metadata, dict):
        raise ValueError("Invalid preset package")
    policies = conflicts or {}
    updated = {
        "conditions": deepcopy(conditions),
        "gradients": deepcopy(gradients),
        "analytes": deepcopy(analytes or {}),
    }
    updated_metadata = deepcopy(metadata)
    used_ids = {
        str(record.get("id", ""))
        for records in updated_metadata.values()
        if isinstance(records, dict)
        for record in records.values()
        if isinstance(record, dict) and record.get("id")
    }
    imported_names = {"conditions": [], "gradients": [], "analytes": []}
    for kind in PRESET_KINDS:
        values = incoming.get(kind, {})
        records = incoming_metadata.get(kind, {})
        if not isinstance(values, dict) or not isinstance(records, dict):
            raise ValueError("Invalid %s presets" % kind)
        for name, payload in values.items():
            if not isinstance(name, str) or not name.strip() or not isinstance(payload, dict):
                raise ValueError("Invalid preset entry")
            target = name.strip()
            if kind == "analytes":
                sanitized = sanitize_analyte_presets({target: payload})
                if target not in sanitized:
                    raise ValueError("Invalid analyte preset: %s" % name)
                payload = sanitized[target]
            policy = policies.get((kind, target), policies.get(target, "error"))
            exists = target in updated[kind]
            if exists and policy == "skip":
                continue
            if exists and policy == "keep_both":
                base = target + " (imported)"
                target = base
                suffix = 2
                while target in updated[kind]:
                    target = "%s %d" % (base, suffix)
                    suffix += 1
            elif exists and policy != "replace":
                raise ValueError("Preset conflict requires a policy: %s" % name)
            if exists and policy == "replace":
                old_record = updated_metadata.get(kind, {}).get(target, {})
                used_ids.discard(str(old_record.get("id", "")))
            updated[kind][target] = deepcopy(payload)
            raw_record = records.get(name, {})
            record = _metadata_record(kind, target, raw_record)
            candidate_id = str(record.get("id", ""))
            if policy == "keep_both" or not candidate_id or candidate_id in used_ids:
                record["id"] = uuid.uuid4().hex
            used_ids.add(record["id"])
            updated_metadata.setdefault(kind, {})[target] = record
            imported_names[kind].append(target)
    legacy_result = (
        updated["conditions"],
        updated["gradients"],
        updated_metadata,
        imported_names,
    )
    if analytes is None:
        return legacy_result
    return legacy_result + (sanitize_analyte_presets(updated["analytes"]),)


def stable_preset_names(names, metadata, kind: str, sort_by: str = "created"):
    """Sort deterministically while keeping unknown legacy dates honest."""
    records = metadata.get(kind, {}) if isinstance(metadata, dict) else {}
    if sort_by == "name":
        return sorted(names, key=lambda name: (name.casefold(), name))
    timestamp_field = {
        "created": "created_at",
        "updated": "updated_at",
        "used": "last_used_at",
    }.get(sort_by, "created_at")
    known = []
    unknown = []
    for name in names:
        timestamp = str(
            (records.get(name) or {}).get(timestamp_field, "") or ""
        )
        if timestamp:
            known.append((timestamp, name))
        else:
            unknown.append(name)
    known.sort(key=lambda item: (item[1].casefold(), item[1]))
    known.sort(key=lambda item: item[0], reverse=True)
    unknown.sort(key=lambda name: (name.casefold(), name))
    return [name for _timestamp, name in known] + unknown


def filter_preset_names(names, query: str):
    """Return a case-insensitive name substring filter without reordering."""
    needle = str(query or "").strip().casefold()
    if not needle:
        return list(names)
    return [name for name in names if needle in name.casefold()]


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
    """Load legacy payloads and application-only metadata."""
    conditions, gradients, _analytes, metadata = load_complete_preset_store(path)
    return conditions, gradients, metadata


def load_complete_preset_store(
    path: Optional[Path] = None,
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, Dict[str, Any]],
    Dict[str, Dict[str, Optional[float]]],
    Dict[str, Dict[str, Dict[str, str]]],
]:
    """Load every application-wide preset kind and its metadata."""
    source = Path(path) if path is not None else preset_store_path()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}, {}, {}, normalize_preset_metadata({}, {})
    if not isinstance(payload, dict):
        return {}, {}, {}, normalize_preset_metadata({}, {})
    raw_conditions = payload.get("condition_presets", {})
    if not isinstance(raw_conditions, dict):
        raw_conditions = {}
    conditions = sanitize_condition_presets(raw_conditions)
    gradients = payload.get("gradient_presets", {})
    if not isinstance(gradients, dict):
        gradients = {}
    gradients = deepcopy(gradients)
    analytes = sanitize_analyte_presets(payload.get("analyte_presets", {}))
    metadata = normalize_preset_metadata(
        conditions,
        gradients,
        payload.get("preset_metadata", {}),
        analytes,
    )
    return conditions, gradients, analytes, metadata


def save_preset_store(
    condition_presets: Dict[str, Dict[str, Any]],
    gradient_presets: Dict[str, Dict[str, Any]],
    path: Optional[Path] = None,
    metadata=None,
    analyte_presets=None,
) -> Path:
    """Atomically save presets so an interrupted upgrade cannot corrupt them."""
    destination = Path(path) if path is not None else preset_store_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    conditions = sanitize_condition_presets(condition_presets)
    gradients = deepcopy(gradient_presets or {})
    previous = None
    if analyte_presets is None or metadata is None:
        previous = load_complete_preset_store(destination)
    analytes = sanitize_analyte_presets(
        previous[2] if analyte_presets is None and previous is not None else analyte_presets
    )
    if metadata is None:
        (
            old_conditions,
            old_gradients,
            old_analytes,
            old_metadata,
        ) = previous
        metadata = old_metadata
        for kind, old_presets, new_presets in (
            ("conditions", old_conditions, conditions),
            ("gradients", old_gradients, gradients),
            ("analytes", old_analytes, analytes),
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
        conditions, gradients, metadata, analytes
    )
    payload = {
        "format_version": PRESET_STORE_FORMAT,
        "written_by": APP_VERSION,
        "condition_presets": conditions,
        "gradient_presets": gradients,
        "analyte_presets": analytes,
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
