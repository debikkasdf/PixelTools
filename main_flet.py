"""Pixel Tools — Material You Expressive UI для Flet.

Интерфейс полностью построен на системных токенах Material 3. Палитра
загружается из JSON-экспорта Material Theme Builder (по умолчанию
``material-you-expressive.json``), а при отсутствии или повреждении файла
используется встроенная пастельная палитра.

Бэкенд определения устройства находится в ``pixel_device_detector.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, fields as dataclass_fields
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import flet as ft

from pixel_device_detector import (
    PixelDeviceInfo,
    UNAUTHORIZED_MESSAGE,
    detect_pixel_device,
)

LOGGER = logging.getLogger("pixel_tools.ui")

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_THEME_FILES: Tuple[str, ...] = (
    "material-you-expressive.json",
    "theme_tokens.json",
)


# ---------------------------------------------------------------------------
# Палитра по умолчанию
# ---------------------------------------------------------------------------

# Пастельные цвета Material 3. Используются, если JSON-файл не найден,
# повреждён или в нём отсутствуют обязательные токены.
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


# ---------------------------------------------------------------------------
# Разбор JSON-темы
# ---------------------------------------------------------------------------

_COLOR_SCHEME_FIELDS = {
    field.name for field in dataclass_fields(ft.ColorScheme)
}
_COMPACT_FIELD_INDEX = {
    name.replace("_", ""): name for name in _COLOR_SCHEME_FIELDS
}
_KEY_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


@dataclass(frozen=True)
class LoadedTheme:
    """Результат загрузки темы."""

    light: Dict[str, str]
    dark: Dict[str, str]
    source: Optional[str] = None
    warning: Optional[str] = None

    @property
    def source_label(self) -> str:
        return self.source or "встроенная палитра"


def _snake_case(value: str) -> str:
    """Привести ключ токена к snake_case."""
    text = str(value).strip().replace("-", "_").replace(" ", "_")
    text = _KEY_BOUNDARY.sub("_", text)
    return re.sub(r"_+", "_", text).lower()


def _resolve_token_name(raw_key: str) -> Optional[str]:
    """Найти поле ColorScheme по имени токена из разных форматов экспорта."""
    key = _snake_case(raw_key)
    if key in _COLOR_SCHEME_FIELDS:
        return key
    return _COMPACT_FIELD_INDEX.get(key.replace("_", ""))


def _normalize_color(raw_value: Any) -> Optional[str]:
    """Проверить HEX-цвет и вернуть его в пригодном для Flet виде."""
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
    if len(value) == 4:  # #RGB -> #RRGGBB
        value = "#" + "".join(ch * 2 for ch in value[1:])
    return value


def _normalize_palette(raw: Any) -> Dict[str, str]:
    """Преобразовать сырой словарь токенов в аргументы ft.ColorScheme."""
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
            # В Material Theme Builder есть background/onBackground, но в
            # ColorScheme Flet таких полей нет. Используем их как запасной
            # вариант для surface/onSurface.
            alias = _snake_case(str(raw_key))
            if alias == "background":
                background_fallback["surface"] = color
            elif alias == "on_background":
                on_background_fallback["on_surface"] = color
            continue
        direct[token] = color

    return {**background_fallback, **on_background_fallback, **direct}


def _extract_palettes(data: Any) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Достать светлую и тёмную схемы из разных вариантов JSON-экспорта."""
    if not isinstance(data, Mapping):
        return {}, {}

    containers: Sequence[Mapping[str, Any]] = []
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

    # Файл может содержать только одну плоскую палитру.
    flat = _normalize_palette(data)
    return flat, {}


def _theme_candidates() -> Tuple[Path, ...]:
    """Список путей к JSON-теме: env, корень проекта, assets."""
    names = DEFAULT_THEME_FILES
    candidates = []

    env_path = os.environ.get("PIXELTOOLS_THEME", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())

    for name in names:
        candidates.append(BASE_DIR / name)
        candidates.append(BASE_DIR / "assets" / name)

    unique: list[Path] = []
    seen = set()
    for path in candidates:
        marker = str(path.resolve()) if not path.is_absolute() else str(path)
        if marker not in seen:
            seen.add(marker)
            unique.append(path)
    return tuple(unique)


def load_theme(candidates: Optional[Sequence[Path]] = None) -> LoadedTheme:
    """Загрузить светлую и тёмную палитры, никогда не выбрасывая исключение."""
    candidate_paths = tuple(candidates) if candidates is not None else _theme_candidates()
    last_error: Optional[str] = None

    for path in candidate_paths:
        try:
            if not path.is_file():
                continue
            with path.open("r", encoding="utf-8-sig") as file:
                data = json.load(file)
            light, dark = _extract_palettes(data)
            if not light and not dark:
                last_error = f"в файле {path.name} не найдены светлая и тёмная схемы"
                LOGGER.warning("Тема %s пропущена: %s", path, last_error)
                continue

            warnings: list[str] = []
            if not light:
                warnings.append("В теме отсутствует светлая схема — используется встроенная палитра.")
                light = dict(DEFAULT_LIGHT_PALETTE)
            else:
                light = {**DEFAULT_LIGHT_PALETTE, **light}

            if not dark:
                warnings.append("В теме отсутствует тёмная схема — используется встроенная палитра.")
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
        except Exception as exc:  # защита от неожиданного формата
            last_error = str(exc)
            LOGGER.exception("Непредвиденная ошибка при чтении темы %s", path)

    warning = "JSON-тема не найдена — используется встроенная палитра Material 3."
    if last_error:
        warning = f"Тема не загружена ({last_error}). Используется встроенная палитра."
    LOGGER.warning(warning)
    return LoadedTheme(
        light=dict(DEFAULT_LIGHT_PALETTE),
        dark=dict(DEFAULT_DARK_PALETTE),
        source=None,
        warning=warning,
    )


def build_color_scheme(palette: Mapping[str, str]) -> ft.ColorScheme:
    """Создать ft.ColorScheme, отбросив неизвестные поля и пустые значения."""
    kwargs = {
        key: value
        for key, value in palette.items()
        if key in _COLOR_SCHEME_FIELDS and isinstance(value, str) and value
    }
    return ft.ColorScheme(**kwargs)


def build_flet_theme(palette: Mapping[str, str]) -> ft.Theme:
    """Собрать Material 3 тему Flet на основе палитры."""
    return ft.Theme(
        color_scheme=build_color_scheme(palette),
        use_material3=True,
    )


# ---------------------------------------------------------------------------
# Вспомогательные элементы интерфейса
# ---------------------------------------------------------------------------


def _soft_shadow() -> ft.BoxShadow:
    return ft.BoxShadow(
        blur_radius=32,
        spread_radius=0,
        color=ft.Colors.with_opacity(0.10, ft.Colors.SHADOW),
        offset=(0, 12),
    )


def _text(
    value: str,
    *,
    size: float = 14,
    weight: ft.FontWeight = ft.FontWeight.W_400,
    color: ft.ColorValue = ft.Colors.ON_SURFACE,
    max_lines: Optional[int] = None,
    text_align: ft.TextAlign = ft.TextAlign.LEFT,
    selectable: bool = False,
    letter_spacing: Optional[float] = None,
    expand: Optional[bool] = None,
) -> ft.Text:
    return ft.Text(
        value,
        size=size,
        weight=weight,
        color=color,
        max_lines=max_lines,
        overflow=ft.TextOverflow.ELLIPSIS if max_lines else None,
        text_align=text_align,
        selectable=selectable,
        style=ft.TextStyle(letter_spacing=letter_spacing) if letter_spacing else None,
        expand=expand,
        no_wrap=False,
    )


def _icon_badge(
    icon: ft.IconData,
    *,
    size: int = 52,
    radius: int = 16,
    bgcolor: ft.ColorValue = ft.Colors.PRIMARY_CONTAINER,
    color: ft.ColorValue = ft.Colors.ON_PRIMARY_CONTAINER,
    icon_size: int = 26,
) -> ft.Container:
    return ft.Container(
        width=size,
        height=size,
        border_radius=radius,
        bgcolor=bgcolor,
        alignment=ft.Alignment.CENTER,
        content=ft.Icon(icon, size=icon_size, color=color),
    )


def _pill(
    text: str,
    *,
    bgcolor: ft.ColorValue = ft.Colors.SURFACE_CONTAINER_HIGH,
    color: ft.ColorValue = ft.Colors.ON_SURFACE_VARIANT,
    icon: Optional[ft.IconData] = None,
    dot_color: Optional[ft.ColorValue] = None,
) -> ft.Container:
    children: list[ft.Control] = []
    if dot_color is not None:
        children.append(
            ft.Container(width=7, height=7, border_radius=999, bgcolor=dot_color)
        )
    if icon is not None:
        children.append(ft.Icon(icon, size=14, color=color))
    children.append(_text(text, size=11, weight=ft.FontWeight.W_600, color=color, max_lines=1))
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=12, vertical=6),
        border_radius=999,
        bgcolor=bgcolor,
        content=ft.Row(children, spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
    )


def _value_size(value: str) -> float:
    if len(value) <= 10:
        return 28
    if len(value) <= 24:
        return 19
    return 14


# ---------------------------------------------------------------------------
# Приложение
# ---------------------------------------------------------------------------


class PixelToolsApp:
    """Контроллер и представление приложения Pixel Tools."""

    TAB_TITLES = (
        ("Информация об устройстве", "Данные считываются напрямую через ADB или Fastboot"),
        ("Прошивка", "OTA-обновления, factory images и проверка сборки"),
        ("Приложения", "Установленные пакеты, APK и разрешения"),
        ("Бэкап", "Резервные копии данных и медиа"),
    )

    def __init__(self, page: ft.Page, theme: LoadedTheme) -> None:
        self.page = page
        self.theme = theme

        self.info: Optional[PixelDeviceInfo] = None
        self._busy = False
        self._selected_serial: Optional[str] = None
        self._tab_index = 0
        self._last_updated: Optional[str] = None

        self._build_header()
        self._build_navigation()
        self._build_body_host()

        self._update_header()

    # ------------------------------------------------------------------
    # Построение каркаса
    # ------------------------------------------------------------------

    def _build_header(self) -> None:
        source_color = (
            ft.Colors.PRIMARY if self.theme.source else ft.Colors.ON_SURFACE_VARIANT
        )
        self.theme_caption = _text(
            self.theme.source_label,
            size=10,
            weight=ft.FontWeight.W_500,
            color=ft.Colors.ON_SURFACE_VARIANT,
            max_lines=1,
        )
        self.theme_caption.tooltip = (
            self.theme.warning
            or f"Источник палитры: {self.theme.source_label}"
        )
        overline = ft.Row(
            [
                _text(
                    "PIXEL TOOLS",
                    size=10,
                    weight=ft.FontWeight.W_800,
                    color=source_color,
                    letter_spacing=2.2,
                ),
                ft.Container(width=4, height=4, border_radius=999, bgcolor=ft.Colors.OUTLINE),
                self.theme_caption,
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        self.header_title = _text(
            "Информация об устройстве",
            size=26,
            weight=ft.FontWeight.W_700,
            max_lines=1,
        )
        self.header_subtitle = _text(
            "Проверяем подключение…",
            size=13,
            color=ft.Colors.ON_SURFACE_VARIANT,
            max_lines=2,
        )

        self.header_busy = ft.ProgressRing(
            width=18,
            height=18,
            stroke_width=2,
            color=ft.Colors.PRIMARY,
            visible=False,
        )
        self.status_chip = _pill(
            "Поиск устройства",
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            dot_color=ft.Colors.OUTLINE,
        )
        self.refresh_button = ft.FilledButton(
            content="Обновить",
            icon=ft.Icons.REFRESH,
            on_click=self._on_refresh_click,
            disabled=True,
            height=44,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=999),
                padding=ft.Padding.symmetric(horizontal=20, vertical=12),
            ),
        )

        self.header = ft.Container(
            padding=ft.Padding.only(left=32, right=32, top=22, bottom=18),
            content=ft.Column(
                [
                    overline,
                    ft.Row(
                        [
                            ft.Column(
                                [self.header_title, self.header_subtitle],
                                spacing=4,
                                expand=True,
                            ),
                            ft.Row(
                                [
                                    self.header_busy,
                                    self.status_chip,
                                    self.refresh_button,
                                ],
                                spacing=10,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=16,
                    ),
                ],
                spacing=10,
            ),
        )

    def _build_navigation(self) -> None:
        self.nav_rail = ft.NavigationRail(
            selected_index=0,
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=112,
            group_alignment=-0.92,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            indicator_color=ft.Colors.SECONDARY_CONTAINER,
            leading=ft.Container(
                width=44,
                height=44,
                border_radius=14,
                gradient=ft.LinearGradient(
                    colors=[ft.Colors.PRIMARY, ft.Colors.TERTIARY],
                    begin=ft.Alignment.TOP_LEFT,
                    end=ft.Alignment.BOTTOM_RIGHT,
                ),
                alignment=ft.Alignment.CENTER,
                content=_text(
                    "PX",
                    size=14,
                    weight=ft.FontWeight.W_800,
                    color=ft.Colors.ON_PRIMARY,
                ),
            ),
            trailing=ft.Container(
                content=ft.Column(
                    [
                        ft.Icon(
                            ft.Icons.PALETTE_OUTLINED,
                            size=18,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        _text(
                            "M3",
                            size=9,
                            weight=ft.FontWeight.W_600,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                    ],
                    spacing=4,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                tooltip=f"Тема: {self.theme.source_label}",
            ),
            destinations=[
                ft.NavigationRailDestination(
                    icon=ft.Icons.INFO_OUTLINE,
                    selected_icon=ft.Icons.INFO,
                    label="Инфо",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.SYSTEM_UPDATE_ALT_OUTLINED,
                    selected_icon=ft.Icons.SYSTEM_UPDATE_ALT,
                    label="Прошивка",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.APPS_OUTLINED,
                    selected_icon=ft.Icons.APPS,
                    label="Приложения",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.BACKUP_OUTLINED,
                    selected_icon=ft.Icons.BACKUP,
                    label="Бэкап",
                ),
            ],
            on_change=self._on_nav_change,
        )

    def _build_body_host(self) -> None:
        self.body_host = ft.AnimatedSwitcher(
            content=self._build_loading_view(),
            duration=ft.Duration(milliseconds=320),
            reverse_duration=ft.Duration(milliseconds=180),
            transition=ft.AnimatedSwitcherTransition.FADE,
            switch_in_curve=ft.AnimationCurve.EASE_OUT_CUBIC,
            switch_out_curve=ft.AnimationCurve.EASE_IN_CUBIC,
            expand=True,
        )

        self.layout = ft.Container(
            expand=True,
            content=ft.Row(
                [
                    ft.Container(content=self.nav_rail, width=112),
                    ft.Container(width=1, bgcolor=ft.Colors.OUTLINE_VARIANT),
                    ft.Column(
                        [self.header, self.body_host],
                        expand=True,
                        spacing=0,
                    ),
                ],
                expand=True,
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )

    def mount(self) -> None:
        self.page.add(self.layout)
        self.page.on_resize = self._on_resize

    def _on_resize(self, event: Optional[ft.Event] = None) -> None:
        self._update_header()
        self.page.update()

    # ------------------------------------------------------------------
    # Состояние и навигация
    # ------------------------------------------------------------------

    def _update_header(self) -> None:
        title, subtitle = self.TAB_TITLES[self._tab_index]
        self.header_title.value = title

        if self._busy:
            subtitle = "Проверяем подключение…"
        elif self.info is not None and self.info.detected:
            parts = [self.info.display_name]
            mode = self._mode_label(self.info)
            if mode:
                parts.append(mode)
            if self.info.state:
                parts.append(str(self.info.state))
            if self._last_updated:
                parts.append(f"обновлено {self._last_updated}")
            subtitle = " · ".join(parts)
        elif self.info is not None and self.info.error:
            subtitle = "Устройство не обнаружено — проверьте подключение"

        self.header_subtitle.value = subtitle
        width = getattr(self.page, "width", None) or 1200
        self.refresh_button.visible = self._tab_index == 0
        self.refresh_button.disabled = self._busy
        self.refresh_button.content = "Обновить" if width >= 720 else None
        self.refresh_button.tooltip = "Обновить данные устройства"
        self.header_busy.visible = self._busy and self._tab_index == 0
        self.status_chip.visible = self._tab_index == 0 and width >= 900
        self._refresh_status_chip()

    def _refresh_status_chip(self) -> None:
        info = self.info
        if self._busy and info is None:
            chip = _pill(
                "Поиск устройства",
                bgcolor=ft.Colors.SURFACE_CONTAINER,
                dot_color=ft.Colors.PRIMARY,
            )
        elif info is None:
            chip = _pill(
                "Ожидание",
                bgcolor=ft.Colors.SURFACE_CONTAINER,
                dot_color=ft.Colors.OUTLINE,
            )
        elif not info.detected:
            chip = _pill(
                "Не подключено",
                bgcolor=ft.Colors.ERROR_CONTAINER,
                color=ft.Colors.ON_ERROR_CONTAINER,
                dot_color=ft.Colors.ERROR,
            )
        elif (info.state or "").lower() == "unauthorized":
            chip = _pill(
                "Ожидает разрешения",
                bgcolor=ft.Colors.TERTIARY_CONTAINER,
                color=ft.Colors.ON_TERTIARY_CONTAINER,
                dot_color=ft.Colors.TERTIARY,
            )
        else:
            mode = self._mode_label(info) or "USB"
            state = info.state or "device"
            chip = _pill(
                f"{mode} · {state}",
                bgcolor=ft.Colors.PRIMARY_CONTAINER,
                color=ft.Colors.ON_PRIMARY_CONTAINER,
                dot_color=ft.Colors.PRIMARY,
            )

        self.status_chip.bgcolor = chip.bgcolor
        self.status_chip.content = chip.content

    def _on_nav_change(self, event: ft.Event) -> None:
        index = self._event_index(event)
        if index == self._tab_index:
            return
        self._tab_index = index
        self.nav_rail.selected_index = index
        self._update_header()
        self._show_body(self._render_tab_body())
        self.page.update()

    @staticmethod
    def _event_index(event: ft.Event) -> int:
        control = getattr(event, "control", None)
        index = getattr(control, "selected_index", None)
        if index is None:
            try:
                index = int(getattr(event, "data", 0))
            except (TypeError, ValueError):
                index = 0
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = 0
        return max(0, min(index, 3))

    # ------------------------------------------------------------------
    # Действия
    # ------------------------------------------------------------------

    def _on_refresh_click(self, event: Optional[ft.Event] = None) -> None:
        if self._busy:
            return
        self.page.run_task(self.refresh)

    def _on_retry_click(self, event: Optional[ft.Event] = None) -> None:
        if self._busy:
            return
        self._selected_serial = None
        self.page.run_task(self.refresh)

    def _on_select_device(self, serial: Optional[str]) -> None:
        if self._busy:
            return
        self._selected_serial = serial
        self.page.run_task(self.refresh)

    async def refresh(self) -> None:
        """Запустить определение устройства, не блокируя UI-поток."""
        if self._busy:
            return

        self._busy = True
        self._update_header()
        self.page.update()

        try:
            info = await asyncio.to_thread(
                detect_pixel_device,
                self._selected_serial,
                timeout=12.0,
            )
        except Exception as exc:  # бэкенд должен возвращать ошибки, но UI не падает
            LOGGER.exception("Не удалось определить устройство")
            info = PixelDeviceInfo(
                error=f"Не удалось выполнить проверку устройства: {exc}"
            )

        self.info = info
        self._busy = False
        self._last_updated = datetime.now().strftime("%H:%M:%S")
        self._update_header()
        self._show_body(self._render_tab_body())
        self.page.update()

    # ------------------------------------------------------------------
    # Рендеринг вкладок
    # ------------------------------------------------------------------

    def _show_body(self, control: ft.Control) -> None:
        self.body_host.content = control

    def _render_tab_body(self) -> ft.Control:
        if self._tab_index == 0:
            return self._render_info_body()
        return self._build_placeholder_tab(self._tab_index)

    def _render_info_body(self) -> ft.Control:
        info = self.info

        if self._busy and info is None:
            return self._build_loading_view()

        if info is None:
            return self._build_empty_view(None)

        if not info.detected:
            return self._build_empty_view(info)

        state = (info.state or "").lower()
        if state == "unauthorized":
            return self._build_permission_view(info)
        if state in {"offline", "no permissions", "authorizing", "sideload"}:
            return self._build_issue_view(info)

        return self._build_device_view(info)

    # ------------------------------------------------------------------
    # Экраны состояния
    # ------------------------------------------------------------------

    def _build_loading_view(self) -> ft.Container:
        return ft.Container(
            key="loading",
            expand=True,
            alignment=ft.Alignment.CENTER,
            content=ft.Column(
                [
                    ft.ProgressRing(width=42, height=42, stroke_width=3, color=ft.Colors.PRIMARY),
                    _text(
                        "Ищем подключённое устройство…",
                        size=18,
                        weight=ft.FontWeight.W_600,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    _text(
                        "Проверяем ADB и Fastboot",
                        size=13,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                spacing=12,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
        )

    def _build_empty_view(self, info: Optional[PixelDeviceInfo]) -> ft.Container:
        details = info.error if info is not None else None

        extra: list[ft.Control] = []
        devices = list(info.available_devices) if info is not None else []
        if devices:
            extra.append(
                ft.Container(
                    padding=ft.Padding.only(left=16, right=16, top=14, bottom=14),
                    border_radius=16,
                    bgcolor=ft.Colors.SURFACE_CONTAINER,
                    content=ft.Column(
                        [
                            _text(
                                "Найдено несколько устройств — выберите нужное:",
                                size=12,
                                weight=ft.FontWeight.W_600,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                            *[
                                ft.OutlinedButton(
                                    content=ft.Column(
                                        [
                                            _text(
                                                device.get("serial", "без серийного номера"),
                                                size=12,
                                                weight=ft.FontWeight.W_600,
                                            ),
                                            _text(
                                                " · ".join(
                                                    filter(
                                                        None,
                                                        [
                                                            device.get("mode"),
                                                            device.get("state"),
                                                            device.get("model"),
                                                        ],
                                                    )
                                                ),
                                                size=10,
                                                color=ft.Colors.ON_SURFACE_VARIANT,
                                            ),
                                        ],
                                        spacing=2,
                                        tight=True,
                                    ),
                                    on_click=lambda e, serial=device.get("serial"): self._on_select_device(serial),
                                    style=ft.ButtonStyle(
                                        shape=ft.RoundedRectangleBorder(radius=14),
                                        padding=ft.Padding.symmetric(horizontal=14, vertical=8),
                                    ),
                                )
                                for device in devices
                            ],
                        ],
                        spacing=8,
                    ),
                )
            )

        controls: list[ft.Control] = [
            _icon_badge(
                ft.Icons.USB,
                size=88,
                radius=28,
                bgcolor=ft.Colors.PRIMARY_CONTAINER,
                color=ft.Colors.ON_PRIMARY_CONTAINER,
                icon_size=42,
            ),
            _text(
                "Устройство не обнаружено...",
                size=27,
                weight=ft.FontWeight.W_700,
                text_align=ft.TextAlign.CENTER,
            ),
            _text(
                "Подключите Google Pixel по USB и включите отладку по USB. "
                "Убедитесь, что ADB или Fastboot установлены и доступны в PATH.",
                size=14,
                color=ft.Colors.ON_SURFACE_VARIANT,
                text_align=ft.TextAlign.CENTER,
                max_lines=3,
            ),
        ]

        if details:
            controls.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=16, vertical=12),
                    border_radius=14,
                    bgcolor=ft.Colors.ERROR_CONTAINER,
                    content=ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.WARNING_AMBER,
                                size=18,
                                color=ft.Colors.ON_ERROR_CONTAINER,
                            ),
                            _text(
                                details,
                                size=12,
                                color=ft.Colors.ON_ERROR_CONTAINER,
                                max_lines=6,
                                expand=True,
                            ),
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                )
            )

        controls.extend(extra)
        controls.append(
            ft.FilledButton(
                content="Повторить поиск",
                icon=ft.Icons.REFRESH,
                on_click=self._on_retry_click,
                height=44,
                style=ft.ButtonStyle(
                    shape=ft.RoundedRectangleBorder(radius=999),
                    padding=ft.Padding.symmetric(horizontal=20, vertical=12),
                ),
            )
        )

        return self._message_screen("empty", controls)

    def _build_permission_view(self, info: PixelDeviceInfo) -> ft.Container:
        controls: list[ft.Control] = [
            _icon_badge(
                ft.Icons.DEVELOPER_MODE,
                size=88,
                radius=28,
                bgcolor=ft.Colors.TERTIARY_CONTAINER,
                color=ft.Colors.ON_TERTIARY_CONTAINER,
                icon_size=42,
            ),
            _text(
                "Нужно разрешение",
                size=27,
                weight=ft.FontWeight.W_700,
                text_align=ft.TextAlign.CENTER,
            ),
            _text(
                UNAUTHORIZED_MESSAGE,
                size=15,
                color=ft.Colors.ON_SURFACE_VARIANT,
                text_align=ft.TextAlign.CENTER,
                max_lines=3,
            ),
            _text(
                "Разблокируйте Pixel и подтвердите диалог «Разрешить отладку по USB». "
                "После этого нажмите «Проверить снова».",
                size=12,
                color=ft.Colors.ON_SURFACE_VARIANT,
                text_align=ft.TextAlign.CENTER,
                max_lines=3,
            ),
        ]
        if info.serial:
            controls.append(
                _pill(
                    f"Серийный номер: {info.serial}",
                    bgcolor=ft.Colors.SURFACE_CONTAINER,
                )
            )
        controls.append(
            ft.FilledButton(
                content="Проверить снова",
                icon=ft.Icons.REFRESH,
                on_click=self._on_refresh_click,
                height=44,
                style=ft.ButtonStyle(
                    shape=ft.RoundedRectangleBorder(radius=999),
                    padding=ft.Padding.symmetric(horizontal=20, vertical=12),
                ),
            )
        )
        return self._message_screen("permission", controls)

    def _build_issue_view(self, info: PixelDeviceInfo) -> ft.Container:
        state = (info.state or "").lower()
        if state == "offline":
            title = "Устройство недоступно"
            message = "ADB видит устройство, но не может с ним связаться."
            hint = "Переподключите USB-кабель или перезагрузите Pixel."
            icon = ft.Icons.LINK_OFF
        elif state == "sideload":
            title = "Режим sideload"
            message = "Устройство находится в режиме установки обновления."
            hint = "Завершите установку или загрузите Android, затем повторите поиск."
            icon = ft.Icons.SYSTEM_UPDATE_ALT
        else:
            title = "Нет доступа к устройству"
            message = "Устройство подключено, но ADB не может прочитать данные."
            hint = "Проверьте кабель, драйверы и разрешение на отладку по USB."
            icon = ft.Icons.PHONE_LOCKED

        controls: list[ft.Control] = [
            _icon_badge(
                icon,
                size=88,
                radius=28,
                bgcolor=ft.Colors.ERROR_CONTAINER,
                color=ft.Colors.ON_ERROR_CONTAINER,
                icon_size=42,
            ),
            _text(title, size=27, weight=ft.FontWeight.W_700, text_align=ft.TextAlign.CENTER),
            _text(
                message,
                size=15,
                color=ft.Colors.ON_SURFACE_VARIANT,
                text_align=ft.TextAlign.CENTER,
            ),
            _text(
                hint,
                size=12,
                color=ft.Colors.ON_SURFACE_VARIANT,
                text_align=ft.TextAlign.CENTER,
                max_lines=2,
            ),
        ]
        if info.serial:
            controls.append(
                _pill(
                    f"Серийный номер: {info.serial}",
                    bgcolor=ft.Colors.SURFACE_CONTAINER,
                )
            )
        controls.append(
            ft.FilledButton(
                content="Проверить снова",
                icon=ft.Icons.REFRESH,
                on_click=self._on_refresh_click,
                height=44,
                style=ft.ButtonStyle(
                    shape=ft.RoundedRectangleBorder(radius=999),
                    padding=ft.Padding.symmetric(horizontal=20, vertical=12),
                ),
            )
        )
        return self._message_screen("issue", controls)

    def _message_screen(self, key: str, controls: list[ft.Control]) -> ft.Container:
        card = ft.Container(
            col={"xs": 12, "sm": 10, "md": 8, "lg": 6},
            padding=36,
            border_radius=28,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            shadow=_soft_shadow(),
            content=ft.Column(
                controls,
                spacing=14,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        return ft.Container(
            key=key,
            expand=True,
            alignment=ft.Alignment.CENTER,
            padding=32,
            content=ft.ResponsiveRow(
                controls=[card],
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    # ------------------------------------------------------------------
    # Основной экран устройства
    # ------------------------------------------------------------------

    def _build_device_view(self, info: PixelDeviceInfo) -> ft.Container:
        rows: list[ft.Control] = [
            ft.ResponsiveRow(
                [
                    self._build_phone_card(info),
                    self._build_data_column(info),
                ],
                spacing=20,
                run_spacing=20,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
        ]

        warning_banner = self._build_warning_banner(info)
        if warning_banner is not None:
            rows.append(warning_banner)

        rows.append(
            _text(
                "Данные получены через ADB/Fastboot. Доступность параметров зависит "
                "от модели, версии Android и состояния загрузки.",
                size=11,
                color=ft.Colors.ON_SURFACE_VARIANT,
                max_lines=2,
            )
        )

        return ft.Container(
            key="device",
            expand=True,
            padding=ft.Padding.only(left=32, right=32, top=8, bottom=28),
            content=ft.Column(
                rows,
                spacing=16,
                expand=True,
                scroll=ft.ScrollMode.AUTO,
            ),
        )

    def _build_phone_card(self, info: PixelDeviceInfo) -> ft.Container:
        generation = (
            f"Поколение {info.generation}"
            if info.generation is not None and info.generation > 0
            else ("Планшет" if info.generation == 0 else "Google Pixel")
        )
        subtitle_parts = []
        if info.codename:
            subtitle_parts.append(info.codename)
        if info.android_version:
            subtitle_parts.append(f"Android {info.android_version}")
        if not subtitle_parts:
            subtitle_parts.append(self._mode_label(info) or "USB")

        return ft.Container(
            col={"xs": 12, "md": 5, "lg": 5},
            height=500,
            padding=24,
            border_radius=28,
            gradient=ft.LinearGradient(
                colors=[ft.Colors.PRIMARY_CONTAINER, ft.Colors.TERTIARY_CONTAINER],
                begin=ft.Alignment.TOP_LEFT,
                end=ft.Alignment.BOTTOM_RIGHT,
            ),
            shadow=_soft_shadow(),
            content=ft.Column(
                [
                    ft.Row(
                        [
                            self._mode_pill(info),
                            ft.Container(expand=True),
                            _pill(
                                generation,
                                bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=8,
                    ),
                    ft.Container(
                        expand=True,
                        alignment=ft.Alignment.CENTER,
                        content=self._build_phone_mock(info),
                    ),
                    ft.Column(
                        [
                            _text(
                                info.display_name,
                                size=23,
                                weight=ft.FontWeight.W_700,
                                color=ft.Colors.ON_SURFACE,
                                max_lines=2,
                                text_align=ft.TextAlign.CENTER,
                            ),
                            _text(
                                " · ".join(subtitle_parts),
                                size=12,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                                max_lines=2,
                                text_align=ft.TextAlign.CENTER,
                            ),
                        ],
                        spacing=4,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=12,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _build_phone_mock(self, info: PixelDeviceInfo) -> ft.Container:
        screen_gradient = ft.LinearGradient(
            colors=[ft.Colors.SURFACE, ft.Colors.SURFACE_CONTAINER],
            begin=ft.Alignment.TOP_LEFT,
            end=ft.Alignment.BOTTOM_RIGHT,
        )
        return ft.Container(
            width=178,
            height=330,
            border_radius=38,
            padding=10,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            shadow=ft.BoxShadow(
                blur_radius=24,
                color=ft.Colors.with_opacity(0.18, ft.Colors.SHADOW),
                offset=(0, 10),
            ),
            content=ft.Container(
                border_radius=30,
                gradient=screen_gradient,
                content=ft.Column(
                    [
                        ft.Container(
                            width=10,
                            height=10,
                            border_radius=999,
                            bgcolor=ft.Colors.OUTLINE_VARIANT,
                            margin=ft.Margin.only(top=8),
                        ),
                        ft.Container(
                            expand=True,
                            alignment=ft.Alignment.CENTER,
                            content=ft.Icon(
                                ft.Icons.PHONE_ANDROID,
                                size=78,
                                color=ft.Colors.PRIMARY,
                            ),
                        ),
                        _text(
                            "PIXEL",
                            size=11,
                            weight=ft.FontWeight.W_800,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                            letter_spacing=3,
                        ),
                        ft.Container(height=10),
                    ],
                    spacing=4,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ),
        )

    def _build_data_column(self, info: PixelDeviceInfo) -> ft.Column:
        model_subtitle_parts = []
        if info.model_name and info.codename:
            model_subtitle_parts.append(f"Кодовое имя: {info.codename}")
        if not info.is_pixel:
            model_subtitle_parts.append("Нет в базе Pixel")
        elif info.generation is not None and info.generation > 0:
            model_subtitle_parts.append(f"Поколение {info.generation}")

        if info.android_version:
            android_value = f"Android {info.android_version}"
            android_subtitle = (
                f"API {info.android_api_level}"
                if info.android_api_level is not None
                else "Версия прошивки"
            )
        elif (info.mode or "").lower() == "fastboot":
            android_value = "Недоступно"
            android_subtitle = "Доступно после загрузки Android (ADB)"
        elif (info.state or "").lower() == "recovery":
            android_value = "Недоступно"
            android_subtitle = "Загрузите Android"
        else:
            android_value = "Недоступно"
            android_subtitle = "Не удалось прочитать"

        serial_value = info.serial or "—"
        serial_subtitle = " · ".join(
            part for part in [self._mode_label(info), info.state] if part
        )

        cycle_raw = info.battery_cycles
        cycle_is_number = isinstance(cycle_raw, int)
        cycle_value = str(cycle_raw)
        cycle_subtitle = "Считано с устройства" if cycle_is_number else None

        if cycle_is_number:
            battery_bg = ft.Colors.PRIMARY_CONTAINER
            battery_fg = ft.Colors.ON_PRIMARY_CONTAINER
        elif info.battery_cycle_status == "unsupported":
            battery_bg = ft.Colors.TERTIARY_CONTAINER
            battery_fg = ft.Colors.ON_TERTIARY_CONTAINER
        else:
            battery_bg = ft.Colors.SURFACE_CONTAINER_HIGHEST
            battery_fg = ft.Colors.ON_SURFACE_VARIANT

        return ft.Column(
            col={"xs": 12, "md": 7, "lg": 7},
            spacing=12,
            controls=[
                self._info_card(
                    icon=ft.Icons.PHONE_ANDROID,
                    icon_bg=ft.Colors.SECONDARY_CONTAINER,
                    icon_color=ft.Colors.ON_SECONDARY_CONTAINER,
                    label="Модель",
                    value=info.display_name,
                    subtitle=" · ".join(model_subtitle_parts) or None,
                    value_size=_value_size(info.display_name),
                ),
                self._info_card(
                    icon=ft.Icons.ANDROID,
                    icon_bg=ft.Colors.PRIMARY_CONTAINER,
                    icon_color=ft.Colors.ON_PRIMARY_CONTAINER,
                    label="Версия Android",
                    value=android_value,
                    subtitle=android_subtitle,
                    value_size=_value_size(android_value),
                ),
                self._info_card(
                    icon=ft.Icons.TAG,
                    icon_bg=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                    icon_color=ft.Colors.ON_SURFACE_VARIANT,
                    label="Серийный номер",
                    value=serial_value,
                    subtitle=serial_subtitle or None,
                    value_size=_value_size(serial_value),
                    selectable=True,
                ),
                self._info_card(
                    icon=ft.Icons.BATTERY_6_BAR,
                    icon_bg=battery_bg,
                    icon_color=battery_fg,
                    label="Циклы батареи",
                    value=cycle_value,
                    subtitle=cycle_subtitle,
                    value_size=30 if cycle_is_number else _value_size(cycle_value),
                ),
            ],
        )

    def _info_card(
        self,
        *,
        icon: ft.IconData,
        icon_bg: ft.ColorValue,
        icon_color: ft.ColorValue,
        label: str,
        value: str,
        subtitle: Optional[str] = None,
        value_size: float = 20,
        selectable: bool = False,
    ) -> ft.Container:
        value_control = _text(
            value,
            size=value_size,
            weight=ft.FontWeight.W_600,
            max_lines=2,
            selectable=selectable,
        )
        if len(value) > 30:
            value_control.tooltip = value

        details: list[ft.Control] = [
            _text(
                label,
                size=11,
                weight=ft.FontWeight.W_600,
                color=ft.Colors.ON_SURFACE_VARIANT,
                max_lines=1,
            ),
            value_control,
        ]
        if subtitle:
            details.append(
                _text(
                    subtitle,
                    size=11,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                    max_lines=1,
                )
            )

        return ft.Container(
            height=116,
            padding=ft.Padding.symmetric(horizontal=20, vertical=14),
            border_radius=16,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            content=ft.Row(
                [
                    _icon_badge(
                        icon,
                        size=46,
                        radius=14,
                        bgcolor=icon_bg,
                        color=icon_color,
                    ),
                    ft.Column(
                        details,
                        spacing=2,
                        expand=True,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                ],
                spacing=14,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _build_warning_banner(self, info: PixelDeviceInfo) -> Optional[ft.Container]:
        messages = list(info.warnings)
        if info.error and info.detected:
            messages.append(info.error)
        if not messages:
            return None
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=16, vertical=13),
            border_radius=16,
            bgcolor=ft.Colors.SECONDARY_CONTAINER,
            content=ft.Row(
                [
                    ft.Icon(
                        ft.Icons.INFO_OUTLINE,
                        size=20,
                        color=ft.Colors.ON_SECONDARY_CONTAINER,
                    ),
                    _text(
                        "\n".join(messages),
                        size=12,
                        color=ft.Colors.ON_SECONDARY_CONTAINER,
                        expand=True,
                    ),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _mode_pill(self, info: PixelDeviceInfo) -> ft.Container:
        mode = self._mode_label(info) or "USB"
        state = info.state or "device"
        if (state or "").lower() == "unauthorized":
            dot = ft.Colors.TERTIARY
        elif not info.detected:
            dot = ft.Colors.ERROR
        else:
            dot = ft.Colors.PRIMARY
        return _pill(
            f"{mode} · {state}",
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
            color=ft.Colors.ON_SURFACE_VARIANT,
            dot_color=dot,
        )

    @staticmethod
    def _mode_label(info: Optional[PixelDeviceInfo]) -> Optional[str]:
        if info is None or not info.mode:
            return None
        mode = str(info.mode).lower()
        if mode == "adb":
            return "ADB"
        if mode == "fastboot":
            return "Fastboot"
        return str(info.mode).upper()

    # ------------------------------------------------------------------
    # Заглушки для будущих разделов
    # ------------------------------------------------------------------

    def _build_placeholder_tab(self, index: int) -> ft.Container:
        configs = {
            1: (
                ft.Icons.SYSTEM_UPDATE_ALT,
                "Прошивка",
                "Установка OTA-обновлений и полных заводских образов, проверка "
                "текущей сборки и переход в Fastboot.",
                "OTA и factory images",
                "Проверка сборки",
                "Перезагрузка в режимы",
                ft.Colors.PRIMARY_CONTAINER,
                ft.Colors.ON_PRIMARY_CONTAINER,
                ft.Colors.PRIMARY,
            ),
            2: (
                ft.Icons.APPS,
                "Приложения",
                "Просмотр установленных пакетов, установка APK и управление "
                "разрешениями приложений на устройстве.",
                "Список пакетов",
                "Установка APK",
                "Разрешения",
                ft.Colors.SECONDARY_CONTAINER,
                ft.Colors.ON_SECONDARY_CONTAINER,
                ft.Colors.SECONDARY,
            ),
            3: (
                ft.Icons.BACKUP,
                "Бэкап",
                "Сохранение данных, контактов и медиа на компьютер с "
                "возможностью последующего восстановления.",
                "Данные приложений",
                "Контакты и медиа",
                "Восстановление",
                ft.Colors.TERTIARY_CONTAINER,
                ft.Colors.ON_TERTIARY_CONTAINER,
                ft.Colors.TERTIARY,
            ),
        }
        icon, title, description, *rest = configs[index]
        feature_1, feature_2, feature_3, icon_bg, icon_fg, accent = rest

        return ft.Container(
            key=f"tab-{index}",
            expand=True,
            alignment=ft.Alignment.CENTER,
            padding=32,
            content=ft.ResponsiveRow(
                controls=[
                    ft.Container(
                        col={"xs": 12, "sm": 10, "md": 8, "lg": 6},
                        padding=36,
                        border_radius=28,
                        bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
                        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                        shadow=_soft_shadow(),
                        content=ft.Column(
                            [
                                _icon_badge(
                                    icon,
                                    size=88,
                                    radius=28,
                                    bgcolor=icon_bg,
                                    color=icon_fg,
                                    icon_size=42,
                                ),
                                _text(
                                    title,
                                    size=27,
                                    weight=ft.FontWeight.W_700,
                                    text_align=ft.TextAlign.CENTER,
                                ),
                                _text(
                                    description,
                                    size=14,
                                    color=ft.Colors.ON_SURFACE_VARIANT,
                                    text_align=ft.TextAlign.CENTER,
                                    max_lines=4,
                                ),
                                ft.Container(
                                    padding=ft.Padding.symmetric(horizontal=14, vertical=7),
                                    border_radius=999,
                                    bgcolor=icon_bg,
                                    content=_text(
                                        "В разработке",
                                        size=11,
                                        weight=ft.FontWeight.W_600,
                                        color=icon_fg,
                                    ),
                                ),
                                ft.Column(
                                    [
                                        ft.Row(
                                            [
                                                ft.Icon(ft.Icons.CHECK_CIRCLE, size=16, color=accent),
                                                _text(feature_1, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                                            ],
                                            spacing=8,
                                        ),
                                        ft.Row(
                                            [
                                                ft.Icon(ft.Icons.CHECK_CIRCLE, size=16, color=accent),
                                                _text(feature_2, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                                            ],
                                            spacing=8,
                                        ),
                                        ft.Row(
                                            [
                                                ft.Icon(ft.Icons.CHECK_CIRCLE, size=16, color=accent),
                                                _text(feature_3, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                                            ],
                                            spacing=8,
                                        ),
                                    ],
                                    spacing=8,
                                ),
                                _text(
                                    "Определение устройства уже доступно во вкладке «Инфо».",
                                    size=11,
                                    color=ft.Colors.ON_SURFACE_VARIANT,
                                    text_align=ft.TextAlign.CENTER,
                                    max_lines=2,
                                ),
                            ],
                            spacing=14,
                            tight=True,
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    )
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


async def main(page: ft.Page) -> None:
    """Собрать интерфейс, применить тему и запустить первое определение."""
    page.title = "Pixel Tools"
    page.padding = 0
    page.spacing = 0
    page.bgcolor = ft.Colors.SURFACE

    theme = load_theme()
    page.theme = build_flet_theme(theme.light)
    page.dark_theme = build_flet_theme(theme.dark)
    page.theme_mode = ft.ThemeMode.SYSTEM

    app = PixelToolsApp(page, theme)
    app.mount()
    page.update()

    await app.refresh()


if __name__ == "__main__":
    try:
        ft.run(main)
    except Exception:
        LOGGER.exception("Не удалось запустить Pixel Tools")
        raise
