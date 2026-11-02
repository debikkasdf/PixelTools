"""Загрузка Material 3 токенов из JSON-экспорта Material Theme Builder.

Модуль не зависит от GUI-фреймворка и используется нативным интерфейсом
Pixel Tools. Если файл темы отсутствует или повреждён, возвращается
встроенная пастельная палитра Material 3 — приложение не падает.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

LOGGER = logging.getLogger("pixel_tools.theme")

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_THEME_FILES: Tuple[str, ...] = (
    "material-you-expressive.json",
    "theme_tokens.json",
)

# Полный набор ролей Material 3, которые могут встречаться в экспорте.
MATERIAL_TOKENS = {
    "primary",
    "on_primary",
    "primary_container",
    "on_primary_container",
    "secondary",
    "on_secondary",
    "secondary_container",
    "on_secondary_container",
    "tertiary",
    "on_tertiary",
    "tertiary_container",
    "on_tertiary_container",
    "error",
    "on_error",
    "error_container",
    "on_error_container",
    "surface",
    "on_surface",
    "on_surface_variant",
    "outline",
    "outline_variant",
    "shadow",
    "scrim",
    "inverse_surface",
    "on_inverse_surface",
    "inverse_primary",
    "surface_tint",
    "surface_dim",
    "surface_bright",
    "surface_container_lowest",
    "surface_container_low",
    "surface_container",
    "surface_container_high",
    "surface_container_highest",
    "primary_fixed",
    "on_primary_fixed",
    "primary_fixed_dim",
    "on_primary_fixed_variant",
    "secondary_fixed",
    "on_secondary_fixed",
    "secondary_fixed_dim",
    "on_secondary_fixed_variant",
    "tertiary_fixed",
    "on_tertiary_fixed",
    "tertiary_fixed_dim",
    "on_tertiary_fixed_variant",
}

DEFAULT_LIGHT_PALETTE: Dict[str, str] = {
    "primary": "#6750A4",
    "on_primary": "#FFFFFF",
    "primary_container": "#EADDFF",
    "on_primary_container": "#21005D",
    "secondary": "#625B71",
    "on_secondary": "#FFFFFF",
    "secondary_container": "#E8DEF8",
    "on_secondary_container": "#1D192B",
    "tertiary": "#7D5260",
    "on_tertiary": "#FFFFFF",
    "tertiary_container": "#FFD8E4",
    "on_tertiary_container": "#31111D",
    "error": "#B3261E",
    "on_error": "#FFFFFF",
    "error_container": "#F9DEDC",
    "on_error_container": "#410E0B",
    "surface": "#FEF7FF",
    "on_surface": "#1D1B20",
    "on_surface_variant": "#49454F",
    "outline": "#79747E",
    "outline_variant": "#CAC4D0",
    "shadow": "#000000",
    "scrim": "#000000",
    "inverse_surface": "#322F35",
    "on_inverse_surface": "#F5EFF7",
    "inverse_primary": "#D0BCFF",
    "surface_tint": "#6750A4",
    "surface_dim": "#DED8E1",
    "surface_bright": "#FEF7FF",
    "surface_container_lowest": "#FFFFFF",
    "surface_container_low": "#F7F2FA",
    "surface_container": "#F3EDF7",
    "surface_container_high": "#ECE6F0",
    "surface_container_highest": "#E6E0E9",
}

DEFAULT_DARK_PALETTE: Dict[str, str] = {
    "primary": "#D0BCFE",
    "on_primary": "#381E72",
    "primary_container": "#4F378B",
    "on_primary_container": "#EADDFF",
    "secondary": "#CCC2DC",
    "on_secondary": "#332D41",
    "secondary_container": "#4A4458",
    "on_secondary_container": "#E8DEF8",
    "tertiary": "#EFB8C8",
    "on_tertiary": "#492532",
    "tertiary_container": "#633B48",
    "on_tertiary_container": "#FFD8E4",
    "error": "#F2B8B5",
    "on_error": "#601410",
    "error_container": "#8C1D18",
    "on_error_container": "#F9DEDC",
    "surface": "#141218",
    "on_surface": "#E6E0E9",
    "on_surface_variant": "#CAC4D0",
    "outline": "#938F99",
    "outline_variant": "#49454F",
    "shadow": "#000000",
    "scrim": "#000000",
    "inverse_surface": "#E6E0E9",
    "on_inverse_surface": "#322F35",
    "inverse_primary": "#6750A4",
    "surface_tint": "#D0BCFF",
    "surface_dim": "#141218",
    "surface_bright": "#3B383E",
    "surface_container_lowest": "#0F0D13",
    "surface_container_low": "#1D1B20",
    "surface_container": "#211F26",
    "surface_container_high": "#2B2930",
    "surface_container_highest": "#36343B",
}

_KEY_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_COMPACT_INDEX = {name.replace("_", ""): name for name in MATERIAL_TOKENS}
_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


@dataclass(frozen=True)
class LoadedTheme:
    """Светлая и тёмная палитры и информация об источнике."""

    light: Dict[str, str]
    dark: Dict[str, str]
    source: Optional[str] = None
    warning: Optional[str] = None

    @property
    def source_label(self) -> str:
        return self.source or "встроенная палитра Material 3"


def _snake_case(value: str) -> str:
    text = str(value).strip().replace("-", "_").replace(" ", "_")
    text = _KEY_BOUNDARY.sub("_", text)
    return re.sub(r"_+", "_", text).lower()


def _resolve_token_name(raw_key: str) -> Optional[str]:
    key = _snake_case(raw_key)
    if key in MATERIAL_TOKENS:
        return key
    return _COMPACT_INDEX.get(key.replace("_", ""))


def _normalize_color(raw_value: Any) -> Optional[str]:
    if isinstance(raw_value, Mapping):
        for key in ("value", "$value", "hex", "color"):
            if key in raw_value:
                return _normalize_color(raw_value[key])
        return None
    if not isinstance(raw_value, str):
        return None

    value = raw_value.strip()
    if not value:
        return None
    if not value.startswith("#"):
        value = f"#{value}"
    if not _HEX_COLOR.match(value):
        return None
    if len(value) == 4:
        value = "#" + "".join(ch * 2 for ch in value[1:])
    return value


def _normalize_palette(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}

    direct: Dict[str, str] = {}
    background_fallback: Dict[str, str] = {}
    on_background_fallback: Dict[str, str] = {}

    for raw_key, raw_value in raw.items():
        color = _normalize_color(raw_value)
        if not color:
            continue
        token = _resolve_token_name(str(raw_key))
        if token is None:
            alias = _snake_case(str(raw_key))
            if alias == "background":
                background_fallback["surface"] = color
            elif alias == "on_background":
                on_background_fallback["on_surface"] = color
            continue
        direct[token] = color

    return {**background_fallback, **on_background_fallback, **direct}


def _extract_palettes(data: Any) -> Tuple[Dict[str, str], Dict[str, str]]:
    if not isinstance(data, Mapping):
        return {}, {}

    containers = []
    for key in ("schemes", "themes", "colors"):
        value = data.get(key)
        if isinstance(value, Mapping):
            containers.append(value)
    containers.append(data)

    for container in containers:
        light_raw = (
            container.get("light")
            or container.get("lightTheme")
            or container.get("light_theme")
            or container.get("light-medium-contrast")
        )
        dark_raw = (
            container.get("dark")
            or container.get("darkTheme")
            or container.get("dark_theme")
            or container.get("dark-medium-contrast")
        )
        light = _normalize_palette(light_raw)
        dark = _normalize_palette(dark_raw)
        if light or dark:
            return light, dark

    flat = _normalize_palette(data)
    return flat, {}


def theme_candidates() -> Tuple[Path, ...]:
    candidates = []
    env_file = (
        os.environ.get("PIXELTOOLS_THEME_FILE", "").strip()
        or os.environ.get("PIXELTOOLS_THEME", "").strip()
    )
    if env_file:
        candidates.append(Path(env_file).expanduser())
    for name in DEFAULT_THEME_FILES:
        candidates.append(BASE_DIR / name)
        candidates.append(BASE_DIR / "assets" / name)

    unique = []
    seen = set()
    for path in candidates:
        marker = str(path.resolve()) if not path.is_absolute() else str(path)
        if marker not in seen:
            seen.add(marker)
            unique.append(path)
    return tuple(unique)


def load_theme(candidates: Optional[Sequence[Path]] = None) -> LoadedTheme:
    """Прочитать тему или вернуть безопасный fallback."""
    paths = tuple(candidates) if candidates is not None else theme_candidates()
    last_error: Optional[str] = None

    for path in paths:
        try:
            if not path.is_file():
                continue
            with path.open("r", encoding="utf-8-sig") as stream:
                data = json.load(stream)
            light, dark = _extract_palettes(data)
            if not light and not dark:
                last_error = f"в файле {path.name} не найдены светлая и тёмная схемы"
                LOGGER.warning("Тема %s пропущена: %s", path, last_error)
                continue

            warnings = []
            if not light:
                warnings.append(
                    "В теме отсутствует светлая схема — используется встроенная палитра."
                )
                light = dict(DEFAULT_LIGHT_PALETTE)
            else:
                light = {**DEFAULT_LIGHT_PALETTE, **light}

            if not dark:
                warnings.append(
                    "В теме отсутствует тёмная схема — используется встроенная палитра."
                )
                dark = dict(DEFAULT_DARK_PALETTE)
            else:
                dark = {**DEFAULT_DARK_PALETTE, **dark}

            LOGGER.info("Загружена тема: %s", path)
            return LoadedTheme(
                light=light,
                dark=dark,
                source=path.name,
                warning=" ".join(warnings) or None,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            LOGGER.warning("Не удалось прочитать тему %s: %s", path, exc)
        except Exception as exc:
            last_error = str(exc)
            LOGGER.exception("Непредвиденная ошибка при чтении темы %s", path)

    warning = "JSON-тема не найдена — используется встроенная палитра Material 3."
    if last_error:
        warning = (
            f"Тема не загружена ({last_error}). "
            "Используется встроенная палитра Material 3."
        )
    LOGGER.warning(warning)
    return LoadedTheme(
        light=dict(DEFAULT_LIGHT_PALETTE),
        dark=dict(DEFAULT_DARK_PALETTE),
        source=None,
        warning=warning,
    )
