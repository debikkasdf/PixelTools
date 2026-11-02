"""Page builders for the native Pixel Tools Material 3 interface."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from locales import tr
from pixel_device_detector import PixelDeviceInfo, UNAUTHORIZED_MESSAGE
from qt_material import IconBadge, IconWidget
from qt_widgets import (
    CardSurface,
    CenteredPage,
    DeviceChoiceButton,
    FilledButton,
    InfoCard,
    NoticeBanner,
    PhoneCard,
    Pill,
    ResponsiveContent,
    Spinner,
    make_label,
)


def mode_label(info: Optional[PixelDeviceInfo]) -> Optional[str]:
    if info is None or not info.mode:
        return None
    mode = str(info.mode).lower()
    if mode == "adb":
        return "ADB"
    if mode == "fastboot":
        return "Fastboot"
    return str(info.mode).upper()


def value_size(value: str) -> float:
    if len(value) <= 10:
        return 28
    if len(value) <= 24:
        return 19
    return 14


def generation_label(info: PixelDeviceInfo) -> str:
    if info.generation is not None and info.generation > 0:
        return tr("dev_gen_prefix", "Поколение {gen}", gen=info.generation)
    if info.generation == 0:
        return tr("dev_tablet", "Планшет")
    return "Google Pixel"


def _badge(
    palette: Mapping[str, str],
    icon: str,
    *,
    size: int = 96,
    radius: int = 32,
    icon_size: int = 46,
    background: str,
    foreground: str,
) -> IconBadge:
    return IconBadge(
        icon,
        size=size,
        radius=radius,
        icon_size=icon_size,
        background=palette.get(background, background),
        foreground=palette.get(foreground, foreground),
    )


def _serial_pill(palette: Mapping[str, str], serial: str) -> Pill:
    return Pill(
        tr("dev_serial_pill", "Серийный номер: {serial}", serial=serial),
        background=palette.get("surface_container", "#F3EDF7"),
        foreground=palette.get("on_surface_variant", "#49454F"),
        height=32,
    )


# ---------------------------------------------------------------------------
# Empty / permission / issue screens
# ---------------------------------------------------------------------------


def build_status_page(
    palette: Mapping[str, str],
    *,
    icon: str,
    icon_background: str,
    icon_foreground: str,
    title: str,
    description: str,
    button_text: str,
    on_retry: Callable[[], None],
    hint: Optional[str] = None,
    serial: Optional[str] = None,
    error_text: Optional[str] = None,
    devices: Sequence[Mapping[str, str]] = (),
    on_select: Optional[Callable[[str], None]] = None,
) -> QWidget:
    page = CenteredPage(palette=palette)

    badge = _badge(
        palette,
        icon,
        background=icon_background,
        foreground=icon_foreground,
    )
    page.add_card_widget(badge)

    title_label = make_label(
        title,
        size=28,
        weight=QFont.Weight.Bold,
        color=palette.get("on_surface", "#1D1B20"),
        wrap=True,
        align=Qt.AlignmentFlag.AlignHCenter,
        display=True,
    )
    page.card_layout.addWidget(title_label)

    description_label = make_label(
        description,
        size=14,
        weight=QFont.Weight.Normal,
        color=palette.get("on_surface_variant", "#49454F"),
        wrap=True,
        align=Qt.AlignmentFlag.AlignHCenter,
    )
    page.card_layout.addWidget(description_label)

    if hint:
        hint_label = make_label(
            hint,
            size=12,
            weight=QFont.Weight.Normal,
            color=palette.get("on_surface_variant", "#49454F"),
            wrap=True,
            align=Qt.AlignmentFlag.AlignHCenter,
        )
        page.card_layout.addWidget(hint_label)

    if serial:
        page.add_card_widget(_serial_pill(palette, serial))

    if error_text:
        banner = NoticeBanner(
            palette=palette,
            text=tr(error_text),
            icon="warning",
            background=palette.get("error_container", "#F9DEDC"),
            foreground=palette.get("on_error_container", "#410E0B"),
        )
        page.card_layout.addWidget(banner)

    if devices:
        prompt = make_label(
            tr("empty_devices_prompt", "Доступные устройства:"),
            size=12,
            weight=QFont.Weight.DemiBold,
            color=palette.get("on_surface_variant", "#49454F"),
            wrap=True,
            align=Qt.AlignmentFlag.AlignHCenter,
        )
        page.card_layout.addWidget(prompt)

        for device in devices:
            serial_value = str(device.get("serial", "")).strip()
            meta = " · ".join(
                str(part)
                for part in (
                    device.get("mode"),
                    device.get("state"),
                    device.get("model"),
                )
                if part
            )
            button = DeviceChoiceButton(
                serial_value or tr("status_no_serial", "без серийного номера"),
                meta or tr("status_usb_device", "USB-устройство"),
                palette=palette,
            )
            if on_select is not None and serial_value:
                button.clicked.connect(
                    lambda checked=False, value=serial_value: on_select(value)
                )
            else:
                button.setEnabled(False)
            page.card_layout.addWidget(button)

    retry = FilledButton(
        button_text,
        palette=palette,
        icon="refresh",
        parent=page,
    )
    retry.clicked.connect(on_retry)
    page.add_card_widget(retry)
    return page


def build_empty_page(
    palette: Mapping[str, str],
    info: Optional[PixelDeviceInfo],
    *,
    on_retry: Callable[[], None],
    on_select: Callable[[str], None],
) -> QWidget:
    error_text = info.error if info is not None else None
    devices = list(info.available_devices) if info is not None else []
    if devices:
        multiple = bool(
            error_text and ("несколько устройств" in error_text.lower() or "multiple devices" in error_text.lower())
        )
        if multiple:
            error_text = None
        elif error_text:
            translated = tr(error_text)
            compact = translated.split(". ", 1)[0].strip()
            if compact:
                error_text = compact if compact.endswith(".") else f"{compact}."
        return build_status_page(
            palette,
            icon="usb",
            icon_background="primary_container",
            icon_foreground="on_primary_container",
            title=tr("empty_multiple_title", "Выберите устройство"),
            description=tr(
                "empty_multiple_desc",
                "К компьютеру подключено несколько устройств. "
                "Выберите нужное по серийному номеру.",
            ),
            button_text=tr("empty_multiple_btn", "Обновить список"),
            on_retry=on_retry,
            error_text=error_text,
            devices=devices,
            on_select=on_select,
        )

    if error_text:
        translated = tr(error_text)
        compact = translated.split(". ", 1)[0].strip()
        if compact:
            error_text = compact if compact.endswith(".") else f"{compact}."

    return build_status_page(
        palette,
        icon="usb",
        icon_background="primary_container",
        icon_foreground="on_primary_container",
        title=tr("empty_none_title", "Устройство не обнаружено…"),
        description=tr(
            "empty_none_desc",
            "Подключите Google Pixel по USB и включите отладку по USB. "
            "Убедитесь, что ADB или Fastboot установлены и доступны в PATH.",
        ),
        button_text=tr("empty_none_btn", "Повторить поиск"),
        on_retry=on_retry,
        error_text=error_text,
    )


def build_permission_page(
    palette: Mapping[str, str],
    info: PixelDeviceInfo,
    *,
    on_retry: Callable[[], None],
) -> QWidget:
    return build_status_page(
        palette,
        icon="developer",
        icon_background="tertiary_container",
        icon_foreground="on_tertiary_container",
        title=tr("perm_title", "Нужно разрешение"),
        description=tr("perm_desc", UNAUTHORIZED_MESSAGE),
        hint=tr(
            "perm_hint",
            "Разблокируйте Pixel и подтвердите диалог «Разрешить отладку "
            "по USB». После этого нажмите «Проверить снова».",
        ),
        button_text=tr("perm_btn", "Проверить снова"),
        on_retry=on_retry,
        serial=info.serial,
    )


def build_issue_page(
    palette: Mapping[str, str],
    info: PixelDeviceInfo,
    *,
    on_retry: Callable[[], None],
) -> QWidget:
    state = (info.state or "").lower()
    if state == "offline":
        title = tr("issue_offline_title", "Устройство недоступно")
        message = tr("issue_offline_msg", "ADB видит устройство, но не может с ним связаться.")
        hint = tr("issue_offline_hint", "Переподключите USB-кабель или перезагрузите Pixel.")
        icon = "link_off"
    elif state == "sideload":
        title = tr("issue_sideload_title", "Режим sideload")
        message = tr("issue_sideload_msg", "Устройство находится в режиме установки обновления.")
        hint = tr(
            "issue_sideload_hint",
            "Завершите установку или загрузите Android, затем повторите поиск.",
        )
        icon = "update"
    else:
        title = tr("issue_access_title", "Нет доступа к устройству")
        message = tr("issue_access_msg", "Устройство подключено, но ADB не может прочитать данные.")
        hint = tr("issue_access_hint", "Проверьте кабель, драйверы и разрешение на отладку по USB.")
        icon = "lock"

    return build_status_page(
        palette,
        icon=icon,
        icon_background="error_container",
        icon_foreground="on_error_container",
        title=title,
        description=message,
        hint=hint,
        button_text=tr("issue_btn", "Проверить снова"),
        on_retry=on_retry,
        serial=info.serial,
    )


# ---------------------------------------------------------------------------
# Device page
# ---------------------------------------------------------------------------


def _feature_row(
    palette: Mapping[str, str], text: str, accent: str
) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    layout.addWidget(IconWidget("check", accent, 16), 0, Qt.AlignmentFlag.AlignVCenter)
    label = make_label(
        text,
        size=12,
        weight=QFont.Weight.Normal,
        color=palette.get("on_surface_variant", "#49454F"),
    )
    layout.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
    layout.addStretch(1)
    return row


_PLACEHOLDERS = {
    1: {
        "icon": "update",
        "title": tr("nav_firmware", "Прошивка"),
        "description": tr(
            "placeholder_fw_desc",
            "Установка OTA-обновлений и полных заводских образов, проверка "
            "текущей сборки и переход в Fastboot.",
        ),
        "features": (
            tr("placeholder_f_ota", "OTA и factory images"),
            tr("placeholder_f_build", "Проверка сборки"),
            tr("placeholder_f_reboot", "Перезагрузка в режимы"),
        ),
        "icon_bg": "primary_container",
        "icon_fg": "on_primary_container",
        "accent": "primary",
    },
    2: {
        "icon": "apps",
        "title": tr("nav_apps", "Приложения"),
        "description": tr(
            "placeholder_apps_desc",
            "Просмотр установленных пакетов, установка APK и управление "
            "разрешениями приложений на устройстве.",
        ),
        "features": (
            tr("placeholder_f_apps_data", "Список пакетов"),
            tr("apps_install_apk", "Установка APK"),
            tr("apps_btn_permissions", "Разрешения"),
        ),
        "icon_bg": "secondary_container",
        "icon_fg": "on_secondary_container",
        "accent": "secondary",
    },
    3: {
        "icon": "backup",
        "title": tr("nav_backup", "Бэкап"),
        "description": tr(
            "placeholder_backup_desc",
            "Сохранение данных, контактов и медиа на компьютер с "
            "возможностью последующего восстановления.",
        ),
        "features": (
            tr("placeholder_f_apps_data", "Данные приложений"),
            tr("placeholder_f_contacts", "Контакты и медиа"),
            tr("placeholder_f_restore", "Восстановление"),
        ),
        "icon_bg": "tertiary_container",
        "icon_fg": "on_tertiary_container",
        "accent": "tertiary",
    },
}


def build_placeholder_page(palette: Mapping[str, str], index: int) -> QWidget:
    config = _PLACEHOLDERS.get(index, _PLACEHOLDERS[1])
    page = CenteredPage(palette=palette)

    badge = _badge(
        palette,
        str(config["icon"]),
        background=str(config["icon_bg"]),
        foreground=str(config["icon_fg"]),
    )
    page.add_card_widget(badge)

    title_key = {1: "nav_firmware", 2: "nav_apps", 3: "nav_backup"}.get(index, "tab_1_title")
    desc_key = {1: "placeholder_fw_desc", 2: "placeholder_apps_desc", 3: "placeholder_backup_desc"}.get(index, "placeholder_fw_desc")
    feature_keys = {
        1: ("placeholder_f_ota", "placeholder_f_build", "placeholder_f_reboot"),
        2: ("placeholder_f_apps_data", "apps_install_apk", "apps_btn_permissions"),
        3: ("placeholder_f_apps_data", "placeholder_f_contacts", "placeholder_f_restore"),
    }.get(index, ())

    title = make_label(
        tr(title_key, str(config["title"])),
        size=28,
        weight=QFont.Weight.Bold,
        color=palette.get("on_surface", "#1D1B20"),
        align=Qt.AlignmentFlag.AlignHCenter,
        display=True,
    )
    page.card_layout.addWidget(title)

    description = make_label(
        tr(desc_key, str(config["description"])),
        size=14,
        color=palette.get("on_surface_variant", "#49454F"),
        wrap=True,
        align=Qt.AlignmentFlag.AlignHCenter,
    )
    page.card_layout.addWidget(description)

    pill = Pill(
        tr("placeholder_in_dev", "В разработке"),
        background=palette.get(str(config["icon_bg"]), "#EADDFF"),
        foreground=palette.get(str(config["icon_fg"]), "#21005D"),
        height=32,
    )
    page.add_card_widget(pill)

    feature_box = QWidget()
    features = QVBoxLayout(feature_box)
    features.setContentsMargins(0, 0, 0, 0)
    features.setSpacing(8)
    accent = palette.get(str(config["accent"]), "#6750A4")
    for fk, default_text in zip(feature_keys, config["features"]):
        features.addWidget(_feature_row(palette, tr(fk, str(default_text)), accent))
    page.add_card_widget(feature_box)

    footer = make_label(
        tr("placeholder_footer", "Определение устройства уже доступно во вкладке «Инфо»."),
        size=11,
        color=palette.get("on_surface_variant", "#49454F"),
        wrap=True,
        align=Qt.AlignmentFlag.AlignHCenter,
    )
    page.card_layout.addWidget(footer)
    return page


def _android_card_data(info: PixelDeviceInfo) -> tuple[str, str]:
    if info.is_partial:
        return tr("dev_reading", "Считываем…"), tr("dev_android_data", "Данные Android")
    if info.android_version:
        value = f"Android {info.android_version}"
        subtitle = (
            f"API {info.android_api_level}"
            if info.android_api_level is not None
            else tr("dev_firmware_version", "Версия прошивки")
        )
        return value, subtitle
    if (info.mode or "").lower() == "fastboot":
        return tr("dev_unavailable", "Недоступно"), tr("dev_adb_after_boot", "Доступно после загрузки Android (ADB)")
    if (info.state or "").lower() == "recovery":
        return tr("dev_unavailable", "Недоступно"), tr("dev_boot_android", "Загрузите Android")
    return tr("dev_unavailable", "Недоступно"), tr("dev_read_failed", "Не удалось прочитать")


def _data_column(palette: Mapping[str, str], info: PixelDeviceInfo) -> QWidget:
    model_subtitle_parts: list[str] = []
    if info.model_name and info.codename:
        model_subtitle_parts.append(tr("dev_codename_prefix", "Кодовое имя: {name}", name=info.codename))
    if not info.is_pixel:
        model_subtitle_parts.append(tr("dev_not_pixel", "Нет в базе Pixel"))
    elif info.generation is not None and info.generation > 0:
        model_subtitle_parts.append(tr("dev_gen_prefix", "Поколение {gen}", gen=info.generation))

    android_value, android_subtitle = _android_card_data(info)
    serial_value = info.serial or "—"
    serial_subtitle = " · ".join(
        part for part in (mode_label(info), info.state) if part
    )

    cycle_raw = info.battery_cycles
    cycle_is_number = isinstance(cycle_raw, int)
    if cycle_is_number:
        cycle_value = str(cycle_raw)
        cycle_subtitle = tr("dev_read_from_device", "Считано с устройства")
        battery_bg = "primary_container"
        battery_fg = "on_primary_container"
    elif info.battery_cycle_status == "unsupported":
        cycle_value = tr("dev_cycles_unsupported", "Не поддерживается для этой модели")
        cycle_subtitle = None
        battery_bg = "tertiary_container"
        battery_fg = "on_tertiary_container"
    elif info.battery_cycle_status == "unavailable":
        cycle_value = tr("dev_cycles_unavailable", "Не удалось прочитать через ADB")
        cycle_subtitle = None
        battery_bg = "surface_container_highest"
        battery_fg = "on_surface_variant"
    elif info.battery_cycle_status == "fastboot":
        cycle_value = tr("dev_cycles_fastboot", "Недоступно в режиме Fastboot")
        cycle_subtitle = None
        battery_bg = "surface_container_highest"
        battery_fg = "on_surface_variant"
    elif info.battery_cycle_status == "recovery":
        cycle_value = tr("dev_cycles_recovery", "Недоступно в recovery. Загрузите Android")
        cycle_subtitle = None
        battery_bg = "surface_container_highest"
        battery_fg = "on_surface_variant"
    elif info.battery_cycle_status == "unauthorized":
        cycle_value = tr("dev_cycles_unauthorized", "Требуется авторизация USB")
        cycle_subtitle = None
        battery_bg = "surface_container_highest"
        battery_fg = "on_surface_variant"
    else:
        cycle_value = tr(str(cycle_raw), str(cycle_raw))
        cycle_subtitle = None
        battery_bg = "surface_container_highest"
        battery_fg = "on_surface_variant"

    column = QWidget()
    layout = QVBoxLayout(column)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)

    layout.addWidget(
        InfoCard(
            palette=palette,
            icon="phone",
            icon_background=palette.get("secondary_container", "#E8DEF8"),
            icon_foreground=palette.get("on_secondary_container", "#1D192B"),
            label=tr("dev_model", "Модель"),
            value=tr(info.display_name),
            subtitle=" · ".join(model_subtitle_parts) or None,
            value_size=value_size(tr(info.display_name)),
        )
    )
    layout.addWidget(
        InfoCard(
            palette=palette,
            icon="android",
            icon_background=palette.get("primary_container", "#EADDFF"),
            icon_foreground=palette.get("on_primary_container", "#21005D"),
            label=tr("dev_android_version", "Версия Android"),
            value=android_value,
            subtitle=android_subtitle,
            value_size=value_size(android_value),
        )
    )
    layout.addWidget(
        InfoCard(
            palette=palette,
            icon="tag",
            icon_background=palette.get("surface_container_highest", "#E6E0E9"),
            icon_foreground=palette.get("on_surface_variant", "#49454F"),
            label=tr("dev_serial", "Серийный номер"),
            value=serial_value,
            subtitle=serial_subtitle or None,
            value_size=value_size(serial_value),
            selectable=True,
        )
    )
    layout.addWidget(
        InfoCard(
            palette=palette,
            icon="battery",
            icon_background=palette.get(battery_bg, "#EADDFF"),
            icon_foreground=palette.get(battery_fg, "#21005D"),
            label=tr("dev_cycles", "Циклы батареи"),
            value=cycle_value,
            subtitle=cycle_subtitle,
            value_size=30 if cycle_is_number else value_size(cycle_value),
        )
    )
    layout.addStretch(1)
    return column


def _hardware_grid(palette: Mapping[str, str], info: PixelDeviceInfo) -> QWidget:
    container = QWidget()
    grid = QGridLayout(container)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(16)
    grid.setVerticalSpacing(12)

    # 1. SoC / Processor
    if info.is_partial:
        soc_val = tr("dev_reading", "Считываем…")
        soc_sub = tr("dev_android_data", "Данные Android")
    else:
        soc_val = info.chipset_name or tr("dev_unknown", "Не определен")
        soc_sub = (
            tr("dev_arch_prefix", "Архитектура: {abi}", abi=info.properties.get('ro.product.cpu.abi', 'arm64-v8a'))
            if info.mode == "adb"
            else tr("dev_hw_platform", "Аппаратная платформа")
        )
    grid.addWidget(
        InfoCard(
            palette=palette,
            icon="chip",
            icon_background=palette.get("secondary_container", "#E8DEF8"),
            icon_foreground=palette.get("on_secondary_container", "#1D192B"),
            label=tr("dev_processor", "Процессор / Платформа"),
            value=soc_val,
            subtitle=soc_sub,
            value_size=value_size(soc_val),
        ),
        0,
        0,
    )

    # 2. Storage & RAM
    if info.is_partial:
        storage_val = tr("dev_reading", "Считываем…")
        storage_sub = tr("dev_android_data", "Данные Android")
    elif info.storage_free_gb is not None and info.storage_total_gb is not None:
        storage_val = tr("dev_storage_free", "{free:g} ГБ свободно", free=info.storage_free_gb)
        storage_sub = tr("dev_storage_of", "из {total:g} ГБ (/data)", total=info.storage_total_gb) + (
            tr("dev_storage_ram", " · RAM: {ram:g} ГБ", ram=info.ram_total_gb) if info.ram_total_gb else ""
        )
    elif info.ram_total_gb is not None:
        storage_val = tr("dev_storage_ram_only", "{ram:g} ГБ RAM", ram=info.ram_total_gb)
        storage_sub = tr("dev_storage_ram_desc", "ОЗУ устройства")
    else:
        storage_val = tr("dev_no_data", "Нет данных")
        storage_sub = tr("dev_available_in_sys", "Доступно в режиме системы")

    grid.addWidget(
        InfoCard(
            palette=palette,
            icon="storage",
            icon_background=palette.get("tertiary_container", "#FFD8E4"),
            icon_foreground=palette.get("on_tertiary_container", "#31111D"),
            label=tr("dev_storage", "Память и накопитель"),
            value=storage_val,
            subtitle=storage_sub,
            value_size=value_size(storage_val),
        ),
        0,
        1,
    )

    # 3. Battery Health & Temperature
    if info.is_partial:
        bat_val = tr("dev_reading", "Считываем…")
        bat_sub = tr("dev_android_data", "Данные Android")
    elif info.battery_level is not None:
        bat_val = f"{info.battery_level}%" + (
            f" · {info.battery_temp_c}°C" if info.battery_temp_c is not None else ""
        )
        bat_sub = tr("dev_bat_health", "Состояние: {health}", health=tr(info.battery_health) if info.battery_health else tr("dev_bat_excellent", "Отличное"))
    else:
        bat_val = tr("dev_no_data", "Нет данных")
        bat_sub = tr("dev_adb_req", "Требуется подключение ADB")

    grid.addWidget(
        InfoCard(
            palette=palette,
            icon="battery",
            icon_background=palette.get("primary_container", "#EADDFF"),
            icon_foreground=palette.get("on_primary_container", "#21005D"),
            label=tr("dev_battery", "Аккумулятор и питание"),
            value=bat_val,
            subtitle=bat_sub,
            value_size=value_size(bat_val),
        ),
        1,
        0,
    )

    # 4. Slot & Bootloader
    slot_text = tr("dev_slot_num", "Слот {slot}", slot=info.current_slot) if info.current_slot else tr("dev_slot_na", "Слот N/A")
    if info.bootloader_locked is True:
        lock_text = tr("dev_locked", "Заблокирован")
        vb = (info.verified_boot_state or "").lower()
        if vb == "green":
            lock_sub = tr("bl_locked_green", "Заблокирован (Заводской ключ Google, Green)")
        else:
            lock_sub = tr("bl_locked_factory", "Заблокирован (Заводская защита)")
        lock_icon = "lock"
        lock_bg = "surface_container_highest"
        lock_fg = "on_surface_variant"
    elif info.bootloader_locked is False:
        lock_text = tr("dev_unlocked", "Разблокирован")
        vb = (info.verified_boot_state or "").lower()
        if vb == "orange":
            lock_sub = tr("bl_unlocked_orange", "Разблокирован (OEM Unlocked, Orange)")
        elif vb == "yellow":
            lock_sub = tr("bl_unlocked_yellow", "Разблокирован (Пользовательский ключ AVB, Yellow)")
        else:
            lock_sub = tr("bl_unlocked_oem", "Разблокирован (OEM Unlocked)")
        lock_icon = "key"
        lock_bg = "error_container"
        lock_fg = "on_error_container"
    else:
        lock_text = tr("dev_unknown", "Неизвестно")
        lock_sub = tr("bl_undefined", "Не определено")
        lock_icon = "lock"
        lock_bg = "surface_container_highest"
        lock_fg = "on_surface_variant"

    slot_val = f"{slot_text} · {lock_text}"
    if info.is_fastbootd:
        lock_sub = tr("dev_fastbootd_mode", "Режим fastbootd (userspace)")
    if info.is_partial:
        slot_val = tr("dev_reading", "Считываем…")
        lock_sub = tr("dev_bootloader_data", "Данные загрузчика")

    grid.addWidget(
        InfoCard(
            palette=palette,
            icon=lock_icon,
            icon_background=palette.get(lock_bg, "#E6E0E9"),
            icon_foreground=palette.get(lock_fg, "#49454F"),
            label=tr("dev_bootloader", "Загрузчик и слоты A/B"),
            value=slot_val,
            subtitle=lock_sub,
            value_size=value_size(slot_val),
        ),
        1,
        1,
    )

    return container


def build_device_page(
    palette: Mapping[str, str], info: PixelDeviceInfo
) -> QWidget:
    generation = generation_label(info)
    subtitle_parts: list[str] = []
    if info.codename:
        subtitle_parts.append(info.codename)
    if info.android_version:
        subtitle_parts.append(f"Android {info.android_version}")
    if not subtitle_parts:
        subtitle_parts.append(mode_label(info) or "USB")

    mode = mode_label(info) or "USB"
    state = info.state or "device"
    mode_dot = (
        palette.get("tertiary", "#7D5260")
        if state.lower() == "unauthorized"
        else palette.get("primary", "#6750A4")
    )

    page = QWidget()
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(0, 0, 0, 0)
    page_layout.setSpacing(0)

    scroll = QScrollArea(page)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.viewport().setAutoFillBackground(False)

    content = ResponsiveContent()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(32, 10, 32, 28)
    layout.setSpacing(18)

    top = QHBoxLayout()
    top.setContentsMargins(0, 0, 0, 0)
    top.setSpacing(20)

    phone = PhoneCard(
        palette=palette,
        title=tr(info.display_name),
        subtitle=" · ".join(subtitle_parts),
        generation=generation,
        mode_text=f"{mode} · {state}",
        mode_dot=mode_dot,
    )
    top.addWidget(phone, 5)
    top.addWidget(_data_column(palette, info), 7)
    layout.addLayout(top)

    hw_header = make_label(
        tr("dev_hw_header", "Аппаратные характеристики"),
        size=15,
        weight=QFont.Weight.Bold,
        color=palette.get("on_surface", "#1D1B20"),
        display=True,
    )
    layout.addWidget(hw_header)
    layout.addWidget(_hardware_grid(palette, info))

    messages = list(dict.fromkeys(info.warnings))
    if info.error and info.detected:
        messages.append(info.error)
    if messages:
        layout.addWidget(
            NoticeBanner(
                palette=palette,
                text="\n".join(tr(m, m) for m in messages),
                icon="info",
                background=palette.get("secondary_container", "#E8DEF8"),
                foreground=palette.get("on_secondary_container", "#1D192B"),
            )
        )

    footer = make_label(
        tr(
            "dev_footer_notice",
            "Данные получены через ADB/Fastboot. Доступность параметров зависит "
            "от модели, версии Android и состояния загрузки.",
        ),
        size=11,
        color=palette.get("on_surface_variant", "#49454F"),
        wrap=True,
    )
    layout.addWidget(footer)
    layout.addStretch(1)

    scroll.setWidget(content)
    page_layout.addWidget(scroll)
    return page


# ---------------------------------------------------------------------------
# Loading screen
# ---------------------------------------------------------------------------


def build_loading_page(palette: Mapping[str, str]) -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(32, 32, 32, 32)
    layout.setSpacing(16)
    layout.addStretch(1)

    spinner = Spinner(
        color=palette.get("primary", "#6750A4"),
        size=48,
        stroke=3.2,
        parent=page,
    )
    layout.addWidget(spinner, 0, Qt.AlignmentFlag.AlignHCenter)

    title = make_label(
        tr("loading_searching_title", "Ищем подключённое устройство…"),
        size=20,
        weight=QFont.Weight.Bold,
        color=palette.get("on_surface", "#1D1B20"),
        align=Qt.AlignmentFlag.AlignHCenter,
        display=True,
    )
    layout.addWidget(title)

    subtitle = make_label(
        tr("loading_checking_subtitle", "Проверяем ADB и Fastboot"),
        size=13,
        color=palette.get("on_surface_variant", "#49454F"),
        align=Qt.AlignmentFlag.AlignHCenter,
    )
    layout.addWidget(subtitle)
    layout.addStretch(1)
    return page


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------


def build_settings_page(
    palette: Mapping[str, str],
    *,
    current_appearance: str,
    current_language: str,
    auto_detect: bool,
    on_appearance_change: Callable[[str], None],
    on_language_change: Callable[[str], None],
    on_auto_detect_change: Callable[[bool], None],
    on_back: Optional[Callable[[], None]] = None,
) -> QWidget:
    """In-app settings page rendered in the main window body."""
    page = QWidget()
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(0, 0, 0, 0)
    page_layout.setSpacing(0)

    scroll = QScrollArea(page)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.viewport().setAutoFillBackground(False)
    page_layout.addWidget(scroll)

    content = ResponsiveContent()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(32, 12, 32, 28)
    layout.setSpacing(18)

    # 1. Header Row
    header_row = QHBoxLayout()
    header_row.setSpacing(14)
    badge = IconBadge(
        "settings",
        size=44,
        radius=14,
        background=palette.get("primary_container", "#EADDFF"),
        foreground=palette.get("on_primary_container", "#21005D"),
        icon_size=22,
    )
    header_row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)

    titles = QVBoxLayout()
    titles.setSpacing(2)
    titles.addWidget(
        make_label(
            tr("settings_title"),
            size=18,
            weight=QFont.Weight.Bold,
            color=palette.get("on_surface", "#1D1B20"),
            display=True,
        )
    )
    titles.addWidget(
        make_label(
            tr("settings_subtitle"),
            size=11.5,
            color=palette.get("on_surface_variant", "#49454F"),
        )
    )
    header_row.addLayout(titles, 1)

    layout.addLayout(header_row)

    # Section 1: Language
    layout.addWidget(
        make_label(
            tr("settings_section_lang"),
            size=10.5,
            weight=QFont.Weight.ExtraBold,
            color=palette.get("primary", "#6750A4"),
            letter_spacing=1.8,
            display=True,
        )
    )
    lang_card = CardSurface(palette=palette, radius=18, shadow=False)
    lang_layout = QVBoxLayout(lang_card)
    lang_layout.setContentsMargins(16, 14, 16, 14)
    lang_layout.setSpacing(12)

    lang_desc_row = QHBoxLayout()
    lang_desc_row.setSpacing(10)
    lang_desc_row.addWidget(
        IconWidget("language", palette.get("primary", "#6750A4"), 20),
        0,
        Qt.AlignmentFlag.AlignVCenter,
    )
    lang_desc_row.addWidget(
        make_label(
            tr("settings_lang_desc"),
            size=11.5,
            color=palette.get("on_surface_variant", "#49454F"),
        ),
        1,
    )
    lang_layout.addLayout(lang_desc_row)

    lang_btn_row = QHBoxLayout()
    lang_btn_row.setSpacing(10)
    for l_key, l_title in (("ru", tr("settings_lang_ru")), ("en", tr("settings_lang_en"))):
        btn = FilledButton(
            l_title,
            palette=palette,
            icon="check" if current_language == l_key else None,
            parent=lang_card,
        )
        btn.clicked.connect(lambda checked=False, k=l_key: on_language_change(k))
        lang_btn_row.addWidget(btn, 1)
    lang_layout.addLayout(lang_btn_row)
    layout.addWidget(lang_card)

    # Section 2: Appearance
    layout.addWidget(
        make_label(
            tr("settings_section_theme"),
            size=10.5,
            weight=QFont.Weight.ExtraBold,
            color=palette.get("primary", "#6750A4"),
            letter_spacing=1.8,
            display=True,
        )
    )
    theme_card = CardSurface(palette=palette, radius=18, shadow=False)
    theme_layout = QVBoxLayout(theme_card)
    theme_layout.setContentsMargins(16, 14, 16, 14)
    theme_layout.setSpacing(0)

    theme_btn_row = QHBoxLayout()
    theme_btn_row.setSpacing(8)
    for mode_key, mode_title in (
        ("system", tr("settings_theme_system")),
        ("light", tr("settings_theme_light")),
        ("dark", tr("settings_theme_dark")),
    ):
        btn = FilledButton(
            mode_title,
            palette=palette,
            icon="check" if current_appearance == mode_key else None,
            parent=theme_card,
        )
        btn.clicked.connect(lambda checked=False, m=mode_key: on_appearance_change(m))
        theme_btn_row.addWidget(btn, 1)
    theme_layout.addLayout(theme_btn_row)
    layout.addWidget(theme_card)

    # Section 3: Auto-detection
    layout.addWidget(
        make_label(
            tr("settings_section_auto"),
            size=10.5,
            weight=QFont.Weight.ExtraBold,
            color=palette.get("primary", "#6750A4"),
            letter_spacing=1.8,
            display=True,
        )
    )
    auto_card = CardSurface(palette=palette, radius=18, shadow=False)
    auto_layout = QHBoxLayout(auto_card)
    auto_layout.setContentsMargins(16, 14, 16, 14)
    auto_layout.setSpacing(12)

    auto_label_layout = QVBoxLayout()
    auto_label_layout.setSpacing(2)
    auto_label_layout.addWidget(
        make_label(
            tr("settings_auto_title"),
            size=12.5,
            weight=QFont.Weight.Bold,
            color=palette.get("on_surface", "#1D1B20"),
        )
    )
    auto_label_layout.addWidget(
        make_label(
            tr("settings_auto_desc"),
            size=11,
            color=palette.get("on_surface_variant", "#49454F"),
        )
    )
    auto_layout.addLayout(auto_label_layout, 1)

    auto_btn = FilledButton(
        tr("settings_auto_enabled") if auto_detect else tr("settings_auto_disabled"),
        palette=palette,
        icon="check" if auto_detect else "close",
        parent=auto_card,
    )
    auto_btn.clicked.connect(lambda: on_auto_detect_change(not auto_detect))
    auto_layout.addWidget(auto_btn)
    layout.addWidget(auto_card)

    # Section 4: Cache & data
    layout.addWidget(
        make_label(
            tr("settings_section_cache"),
            size=10.5,
            weight=QFont.Weight.ExtraBold,
            color=palette.get("primary", "#6750A4"),
            letter_spacing=1.8,
            display=True,
        )
    )
    cache_card = CardSurface(palette=palette, radius=18, shadow=False)
    cache_layout = QHBoxLayout(cache_card)
    cache_layout.setContentsMargins(16, 14, 16, 14)
    cache_layout.setSpacing(12)

    cache_dir = Path.home() / ".pixeltools" / "app_icons"
    files = list(cache_dir.glob("*")) if cache_dir.is_dir() else []
    count = len(files)
    total_bytes = sum(f.stat().st_size for f in files if f.is_file())
    if total_bytes < 1024:
        size_str = f"{total_bytes} B"
    elif total_bytes < 1024 * 1024:
        size_str = f"{total_bytes / 1024:.1f} KB"
    else:
        size_str = f"{total_bytes / (1024 * 1024):.1f} MB"

    cache_col = QVBoxLayout()
    cache_col.setSpacing(2)
    cache_status_lbl = make_label(
        tr("settings_cache_stats", count=count, size=size_str),
        size=12.5,
        weight=QFont.Weight.Bold,
        color=palette.get("on_surface", "#1D1B20"),
    )
    cache_col.addWidget(cache_status_lbl)
    cache_col.addWidget(
        make_label(
            tr("settings_cache_desc"),
            size=11,
            color=palette.get("on_surface_variant", "#49454F"),
        )
    )
    cache_layout.addLayout(cache_col, 1)

    clear_btn = FilledButton(
        tr("settings_cache_clear"),
        palette=palette,
        icon="trash",
        parent=cache_card,
    )

    def do_clear():
        if cache_dir.is_dir():
            for f in cache_dir.glob("*"):
                try:
                    if f.is_file():
                        f.unlink()
                except OSError:
                    pass
        cache_status_lbl.setText(tr("settings_cache_cleared"))
        clear_btn.setEnabled(False)

    clear_btn.clicked.connect(do_clear)
    cache_layout.addWidget(clear_btn)
    layout.addWidget(cache_card)

    # Section 6: About
    layout.addWidget(
        make_label(
            tr("settings_section_about"),
            size=10.5,
            weight=QFont.Weight.ExtraBold,
            color=palette.get("primary", "#6750A4"),
            letter_spacing=1.8,
            display=True,
        )
    )
    about_card = CardSurface(palette=palette, radius=18, shadow=False)
    about_layout = QVBoxLayout(about_card)
    about_layout.setContentsMargins(16, 14, 16, 14)
    about_layout.setSpacing(6)
    about_layout.addWidget(
        make_label(
            "Pixel Tools · Standalone v1.1",
            size=13,
            weight=QFont.Weight.Bold,
            color=palette.get("primary", "#6750A4"),
        )
    )
    about_layout.addWidget(
        make_label(
            tr("settings_about_desc"),
            size=11.5,
            color=palette.get("on_surface_variant", "#49454F"),
        )
    )
    layout.addWidget(about_card)

    layout.addStretch(1)
    scroll.setWidget(content)
    return page
