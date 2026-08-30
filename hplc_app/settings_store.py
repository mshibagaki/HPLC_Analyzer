"""Typed access to application-wide QSettings values.

The QSettings organization, application name, and existing key strings are a
compatibility contract: changing them would make installed applications lose
their current Windows Registry values.  Preset bodies remain authoritative in
``presets.json``; the two preset keys below are legacy mirrors/fallbacks only.
Project analysis, display, Run, and Dataset state belongs in ``.hplcproj`` and
must not be added here.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import os
from typing import Any, Callable, Dict

from . import APP_NAME
from .rendering import default_render_quality, normalize_render_quality


ORGANIZATION_NAME = "Research Tools"

UI_LANGUAGE = "ui/language"
AUTOMATIC_UPDATE_CHECK = "updates/automatic_check"
IMPORT_DIRECTORY = "paths/import_directory"
SAVE_DIRECTORY = "paths/save_directory"
LAST_IMPORT_DIRECTORY = "paths/last_import_directory"
LAST_SAVE_DIRECTORY = "paths/last_save_directory"
LAST_PROJECT_DIRECTORY = "paths/last_project_directory"
DATABASE_PATH = "database/path"
RENDERING_QUALITY = "rendering/quality"
SCREEN_RENDERER = "rendering/screen_renderer"
FIGURE_FORMAT = "export/figure_format"
NAMING_AUTHOR = "naming/author"
LEGACY_CONDITION_PRESETS = "presets/conditions"
LEGACY_GRADIENT_PRESETS = "presets/gradients"


@dataclass(frozen=True)
class SettingSpec:
    default: Callable[[], Any]
    decode: Callable[[Any, Any], Any]
    encode: Callable[[Any], Any]


def _constant(value: Any) -> Callable[[], Any]:
    return lambda: deepcopy(value)


def _text(value: Any, fallback: str) -> str:
    if value is None:
        return fallback
    if isinstance(value, (str, os.PathLike)):
        return str(value)
    return fallback


def _language(value: Any, fallback: str) -> str:
    normalized = _text(value, fallback).strip().lower()
    return normalized if normalized in ("ja", "en") else fallback


def _figure_format(value: Any, fallback: str) -> str:
    normalized = _text(value, fallback).strip().lower()
    return normalized if normalized in ("png", "svg", "pdf") else fallback


def _render_quality(value: Any, fallback: str) -> str:
    return normalize_render_quality(value, fallback)


def _screen_renderer(value: Any, fallback: str) -> str:
    normalized = _text(value, fallback).strip().lower()
    return normalized if normalized in ("pyqtgraph", "matplotlib") else fallback


def _boolean(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in ("true", "1", "yes", "on"):
        return True
    if normalized in ("false", "0", "no", "off"):
        return False
    return fallback


def _json_object(value: Any, fallback: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    if not value:
        return deepcopy(fallback)
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return deepcopy(fallback)
    return decoded if isinstance(decoded, dict) else deepcopy(fallback)


def _encode_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _text_spec(default: str = "") -> SettingSpec:
    return SettingSpec(default=_constant(default), decode=_text, encode=str)


SETTING_SPECS: Dict[str, SettingSpec] = {
    UI_LANGUAGE: SettingSpec(_constant("ja"), _language, str),
    AUTOMATIC_UPDATE_CHECK: SettingSpec(_constant(True), _boolean, bool),
    IMPORT_DIRECTORY: _text_spec(),
    SAVE_DIRECTORY: _text_spec(),
    LAST_IMPORT_DIRECTORY: _text_spec(),
    LAST_SAVE_DIRECTORY: _text_spec(),
    LAST_PROJECT_DIRECTORY: _text_spec(),
    DATABASE_PATH: _text_spec(),
    RENDERING_QUALITY: SettingSpec(
        default=default_render_quality,
        decode=_render_quality,
        encode=str,
    ),
    SCREEN_RENDERER: SettingSpec(
        default=_constant("pyqtgraph"),
        decode=_screen_renderer,
        encode=str,
    ),
    FIGURE_FORMAT: SettingSpec(_constant("png"), _figure_format, str),
    NAMING_AUTHOR: _text_spec(),
    LEGACY_CONDITION_PRESETS: SettingSpec(
        _constant({}), _json_object, _encode_json
    ),
    LEGACY_GRADIENT_PRESETS: SettingSpec(
        _constant({}), _json_object, _encode_json
    ),
}


class ApplicationSettings:
    """Safe typed wrapper around the existing application QSettings backend."""

    def __init__(self, backend=None):
        self._backend = backend if backend is not None else self._new_backend()

    @staticmethod
    def _new_backend():
        # Imported lazily so non-GUI core tests can use a fake backend without
        # importing Qt or selecting a Qt binding.
        from .qt_compat import QtCore

        return QtCore.QSettings(ORGANIZATION_NAME, APP_NAME)

    @property
    def backend(self):
        return self._backend

    def get(self, key: str) -> Any:
        spec = self._spec(key)
        fallback = spec.default()
        try:
            raw = self._backend.value(key, fallback)
        except Exception:
            return deepcopy(fallback)
        try:
            return spec.decode(raw, fallback)
        except Exception:
            return deepcopy(fallback)

    def set(self, key: str, value: Any, sync: bool = False) -> bool:
        spec = self._spec(key)
        fallback = spec.default()
        try:
            normalized = spec.decode(value, fallback)
            self._backend.setValue(key, spec.encode(normalized))
        except Exception:
            return False
        return self.sync() if sync else True

    def set_many(self, values: Dict[str, Any], sync: bool = True) -> bool:
        success = True
        for key, value in values.items():
            success = self.set(key, value, sync=False) and success
        if sync:
            return self.sync() and success
        return success

    def sync(self) -> bool:
        try:
            self._backend.sync()
            status = getattr(self._backend, "status", None)
            if not callable(status):
                return True
            result = status()
            numeric = getattr(result, "value", result)
            return int(numeric) == 0
        except Exception:
            return False

    @staticmethod
    def _spec(key: str) -> SettingSpec:
        try:
            return SETTING_SPECS[key]
        except KeyError as exc:
            raise KeyError("Unknown application setting: %s" % key) from exc
