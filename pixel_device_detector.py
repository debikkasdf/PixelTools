"""Определение подключённого Google Pixel через ADB или Fastboot.

Модуль не содержит UI и сторонних зависимостей. Все ожидаемые проблемы
(нет устройства, нет adb/fastboot, устройство не авторизовано, команда
не поддерживается) возвращаются в структуре PixelDeviceInfo, а не приводят
к необработанному исключению.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

LOGGER = logging.getLogger(__name__)

UNSUPPORTED_CYCLES_MESSAGE = "Не поддерживается для этой модели"
UNAVAILABLE_CYCLES_MESSAGE = "Не удалось получить количество циклов заряда"
FASTBOOT_CYCLES_MESSAGE = "Циклы заряда доступны только в режиме ADB"
RECOVERY_CYCLES_MESSAGE = "Циклы заряда недоступны в режиме recovery. Загрузите Android"
UNAUTHORIZED_MESSAGE = "Разрешите отладку по USB на экране устройства"


class ConnectionMode(str, Enum):
    ADB = "adb"
    FASTBOOT = "fastboot"


class BatteryCyclesStatus(str, Enum):
    OK = "ok"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    NOT_CHECKED = "not_checked"


@dataclass(frozen=True)
class PixelModel:
    """Коммерческое название и характеристики модели Pixel."""

    name: str
    generation: int
    supports_battery_cycles: bool


# Кодовые имена Google Pixel. Ключи — значения ro.product.device /
# fastboot-переменной product. База охватывает телефоны и планшеты,
# выпущенные с 2015 по 2026 год.
PIXEL_MODELS: Dict[str, PixelModel] = {
    # Pixel 1
    "sailfish": PixelModel("Pixel", 1, False),
    "marlin": PixelModel("Pixel XL", 1, False),
    # Pixel 2
    "walleye": PixelModel("Pixel 2", 2, False),
    "taimen": PixelModel("Pixel 2 XL", 2, False),
    # Pixel 3
    "blueline": PixelModel("Pixel 3", 3, False),
    "crosshatch": PixelModel("Pixel 3 XL", 3, False),
    # Pixel 3a
    "sargo": PixelModel("Pixel 3a", 3, False),
    "bonito": PixelModel("Pixel 3a XL", 3, False),
    # Pixel 4
    "flame": PixelModel("Pixel 4", 4, False),
    "coral": PixelModel("Pixel 4 XL", 4, False),
    # Pixel 4a
    "sunfish": PixelModel("Pixel 4a", 4, False),
    "bramble": PixelModel("Pixel 4a (5G)", 4, False),
    # Pixel 5
    "redfin": PixelModel("Pixel 5", 5, False),
    # Pixel 5a
    "barbet": PixelModel("Pixel 5a", 5, False),
    # Pixel 6
    "oriole": PixelModel("Pixel 6", 6, False),
    "raven": PixelModel("Pixel 6 Pro", 6, False),
    # Pixel 6a
    "bluejay": PixelModel("Pixel 6a", 6, False),
    # Pixel 7
    "panther": PixelModel("Pixel 7", 7, False),
    "cheetah": PixelModel("Pixel 7 Pro", 7, False),
    # Pixel 7a
    "lynx": PixelModel("Pixel 7a", 7, False),
    # Pixel Fold
    "felix": PixelModel("Pixel Fold", 7, False),
    # Pixel 8
    "shiba": PixelModel("Pixel 8", 8, True),
    "husky": PixelModel("Pixel 8 Pro", 8, True),
    # Pixel 8a
    "akita": PixelModel("Pixel 8a", 8, True),
    # Pixel 9
    "tokay": PixelModel("Pixel 9", 9, True),
    "caiman": PixelModel("Pixel 9 Pro", 9, True),
    "komodo": PixelModel("Pixel 9 Pro XL", 9, True),
    "comet": PixelModel("Pixel 9 Pro Fold", 9, True),
    # Pixel 9a
    "tegu": PixelModel("Pixel 9a", 9, True),
    # Pixel 10
    "frankel": PixelModel("Pixel 10", 10, True),
    "blazer": PixelModel("Pixel 10 Pro", 10, True),
    "mustang": PixelModel("Pixel 10 Pro XL", 10, True),
    "rango": PixelModel("Pixel 10 Pro Fold", 10, True),
    # Pixel 10a
    "stallion": PixelModel("Pixel 10a", 10, True),
    # Pixel 11
    "cubs": PixelModel("Pixel 11", 11, True),
    "grizzly": PixelModel("Pixel 11 Pro", 11, True),
    "kodiak": PixelModel("Pixel 11 Pro XL", 11, True),
    "yogi": PixelModel("Pixel 11 Pro Fold", 11, True),
    # Планшеты
    "ryu": PixelModel("Pixel C", 0, False),
    "tangorpro": PixelModel("Pixel Tablet", 0, False),
}

@dataclass(frozen=True)
class ConnectedDevice:
    serial: str
    mode: ConnectionMode
    state: str
    details: Dict[str, str] = field(default_factory=dict)


@dataclass
class PixelDeviceInfo:
    """Результат определения устройства. Все поля JSON-сериализуемы."""

    detected: bool = False
    serial: Optional[str] = None
    mode: Optional[str] = None
    state: Optional[str] = None
    codename: Optional[str] = None
    model_name: Optional[str] = None
    is_pixel: bool = False
    generation: Optional[int] = None
    supports_battery_cycles: Optional[bool] = None
    android_version: Optional[str] = None
    android_api_level: Optional[int] = None
    battery_cycle_count: Optional[int] = None
    battery_cycle_status: str = BatteryCyclesStatus.NOT_CHECKED.value
    battery_cycle_message: Optional[str] = None
    battery_level: Optional[int] = None
    battery_temp_c: Optional[float] = None
    battery_health: Optional[str] = None
    storage_total_gb: Optional[float] = None
    storage_free_gb: Optional[float] = None
    ram_total_gb: Optional[float] = None
    properties: Dict[str, str] = field(default_factory=dict)
    fastboot_variables: Dict[str, str] = field(default_factory=dict)
    available_devices: List[Dict[str, str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    # True для быстрого снимка: устройство найдено, детали ещё читаются.
    is_partial: bool = False

    @property
    def display_name(self) -> str:
        return self.model_name or self.codename or "Неизвестное устройство"

    @property
    def battery_cycles(self) -> Union[int, str]:
        """Число циклов либо готовое сообщение для отображения в UI."""
        if self.is_partial:
            return "Считываем…"
        if self.battery_cycle_count is not None:
            return self.battery_cycle_count
        return self.battery_cycle_message or UNAVAILABLE_CYCLES_MESSAGE

    @property
    def current_slot(self) -> Optional[str]:
        if self.mode == "adb":
            slot = self.properties.get("ro.boot.slot_suffix") or self.properties.get("ro.boot.slot")
            if slot:
                return slot.lstrip("_").upper()
        elif self.mode == "fastboot":
            slot = self.fastboot_variables.get("current-slot")
            if slot:
                return slot.upper()
        return None

    @property
    def bootloader_locked(self) -> Optional[bool]:
        if self.mode == "adb":
            vbmeta = (self.properties.get("ro.boot.vbmeta.device_state") or "").lower()
            if vbmeta == "locked":
                return True
            if vbmeta == "unlocked":
                return False
            locked = self.properties.get("ro.boot.flash.locked")
            if locked in ("1", "true", "yes"):
                return True
            if locked in ("0", "false", "no"):
                return False
            state = (self.properties.get("ro.boot.verifiedbootstate") or "").lower()
            if state == "green":
                return True
            if state in ("orange", "yellow", "red"):
                return False
        elif self.mode == "fastboot":
            unlocked = (self.fastboot_variables.get("unlocked") or "").lower()
            if unlocked == "yes":
                return False
            if unlocked == "no":
                return True
        return None

    @property
    def verified_boot_state(self) -> Optional[str]:
        if self.mode == "adb":
            return self.properties.get("ro.boot.verifiedbootstate")
        return None

    @property
    def bootloader_details(self) -> str:
        """Подробный статус загрузчика и верификации AVB."""
        is_locked = self.bootloader_locked
        if is_locked is True:
            vb = (self.verified_boot_state or "").lower()
            if vb == "green":
                return "Заблокирован (Заводской ключ Google, Green)"
            return "Заблокирован (Заводская защита)"
        elif is_locked is False:
            vb = (self.verified_boot_state or "").lower()
            if vb == "orange":
                return "Разблокирован (OEM Unlocked, Orange)"
            if vb == "yellow":
                return "Разблокирован (Пользовательский ключ AVB, Yellow)"
            return "Разблокирован (OEM Unlocked)"
        return "Не определено"

    @property
    def is_fastbootd(self) -> bool:
        if self.mode == "fastboot":
            return self.fastboot_variables.get("is-userspace") == "yes"
        return False

    @property
    def chipset_name(self) -> Optional[str]:
        code = (self.codename or "").lower()
        mapping = {
            "sailfish": "Qualcomm Snapdragon 821",
            "marlin": "Qualcomm Snapdragon 821",
            "walleye": "Qualcomm Snapdragon 835",
            "taimen": "Qualcomm Snapdragon 835",
            "blueline": "Qualcomm Snapdragon 845",
            "crosshatch": "Qualcomm Snapdragon 845",
            "sargo": "Qualcomm Snapdragon 670",
            "bonito": "Qualcomm Snapdragon 670",
            "flame": "Qualcomm Snapdragon 855",
            "coral": "Qualcomm Snapdragon 855",
            "sunfish": "Qualcomm Snapdragon 730G",
            "bramble": "Qualcomm Snapdragon 765G",
            "redfin": "Qualcomm Snapdragon 765G",
            "barbet": "Qualcomm Snapdragon 765G",
            "oriole": "Google Tensor (G1)",
            "raven": "Google Tensor (G1)",
            "bluejay": "Google Tensor (G1)",
            "panther": "Google Tensor G2",
            "cheetah": "Google Tensor G2",
            "lynx": "Google Tensor G2",
            "felix": "Google Tensor G2",
            "shiba": "Google Tensor G3",
            "husky": "Google Tensor G3",
            "akita": "Google Tensor G3",
            "tokay": "Google Tensor G4",
            "caiman": "Google Tensor G4",
            "komodo": "Google Tensor G4",
            "comet": "Google Tensor G4",
            "tegu": "Google Tensor G4",
            "frankel": "Google Tensor G5",
            "blazer": "Google Tensor G5",
            "mustang": "Google Tensor G5",
            "rango": "Google Tensor G5",
        }
        if code in mapping:
            return mapping[code]
        soc = (
            self.properties.get("ro.soc.model")
            or self.properties.get("ro.board.platform")
            or self.properties.get("ro.hardware")
            or self.fastboot_variables.get("cpu")
        )
        if soc:
            soc_lower = soc.lower()
            if "gs101" in soc_lower or "whitechapel" in soc_lower:
                return "Google Tensor (G1)"
            if "gs201" in soc_lower or "cloudripper" in soc_lower:
                return "Google Tensor G2"
            if "zuma" in soc_lower and "pro" not in soc_lower:
                return "Google Tensor G3"
            if "zumapro" in soc_lower or "zuma-pro" in soc_lower:
                return "Google Tensor G4"
            if "laguna" in soc_lower:
                return "Google Tensor G5"
            return soc.upper() if len(soc) <= 6 else soc.title()
        return None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["display_name"] = self.display_name
        data["battery_cycles"] = self.battery_cycles
        data["current_slot"] = self.current_slot
        data["bootloader_locked"] = self.bootloader_locked
        data["is_fastbootd"] = self.is_fastbootd
        data["chipset_name"] = self.chipset_name
        data["battery_level"] = self.battery_level
        data["battery_temp_c"] = self.battery_temp_c
        data["battery_health"] = self.battery_health
        data["storage_total_gb"] = self.storage_total_gb
        data["storage_free_gb"] = self.storage_free_gb
        data["ram_total_gb"] = self.ram_total_gb
        return data


def get_pixel_model(codename: Optional[str]) -> Optional[PixelModel]:
    """Вернуть модель Pixel по кодовому имени или None."""
    normalized = _normalize_codename(codename)
    if not normalized:
        return None
    return PIXEL_MODELS.get(normalized)


def resolve_tool_path(name: str) -> str:
    """Найти исполняемый файл утилиты, отдавая приоритет локальной папке tools."""
    exe_name = f"{name}.exe" if os.name == "nt" else name
    search_dirs: List[Path] = []

    try:
        search_dirs.append(Path(__file__).resolve().parent)
    except Exception:
        pass
    try:
        search_dirs.append(Path.cwd().resolve())
    except Exception:
        pass
    try:
        search_dirs.append(Path(sys.executable).resolve().parent)
    except Exception:
        pass

    seen = set()
    for base in search_dirs:
        if base in seen:
            continue
        seen.add(base)

        p = base / "tools" / exe_name
        if p.is_file():
            return str(p.resolve())

        p = base / "tools" / name
        if p.is_file():
            return str(p.resolve())

        p = base / "tools" / "platform-tools" / exe_name
        if p.is_file():
            return str(p.resolve())

        p = base / "platform-tools" / exe_name
        if p.is_file():
            return str(p.resolve())

    return name


def _preview_device_info(
    device: ConnectedDevice, available_devices: Sequence[Dict[str, str]]
) -> PixelDeviceInfo:
    """Быстрый снимок устройства сразу после перечисления ADB/Fastboot.

    Содержит только данные, которые уже есть в ``adb devices -l`` /
    ``fastboot devices -l``. Модель обычно определяется по ``product``,
    поэтому телефон появляется в UI до чтения getprop и батареи.
    """
    preview = PixelDeviceInfo(
        detected=True,
        serial=device.serial,
        mode=device.mode.value,
        state=device.state,
        available_devices=[dict(item) for item in available_devices],
        is_partial=True,
    )
    codename = _choose_codename(
        [device.details.get("product"), device.details.get("device")]
    )
    if codename:
        _apply_model(preview, codename)
    return preview


def detect_pixel_device(
    serial: Optional[str] = None,
    *,
    adb_path: str = "adb",
    fastboot_path: str = "fastboot",
    timeout: float = 15.0,
    on_progress: Optional[Callable[[PixelDeviceInfo], None]] = None,
) -> PixelDeviceInfo:
    """Найти Pixel в ADB/Fastboot, определить модель, Android и циклы батареи.

    Args:
        serial: серийный номер конкретного устройства. Если не задан и
            подключено несколько устройств, вернётся ошибка выбора.
        adb_path: имя команды adb или полный путь к исполняемому файлу.
        fastboot_path: имя команды fastboot или полный путь к файлу.
        timeout: таймаут одной внешней команды в секундах.
        on_progress: необязательный callback для промежуточного результата.
            Вызывается сразу после обнаружения устройства, пока свойства
            Android и батарея ещё читаются. Позволяет UI показать телефон
            без ожидания полного опроса.

    Returns:
        PixelDeviceInfo. Ожидаемые ошибки находятся в полях ``error`` и
        ``warnings``, функция не выбрасывает их наружу.
    """
    result = PixelDeviceInfo()
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = 15.0
    if timeout <= 0:
        timeout = 15.0
    if not adb_path or adb_path == "adb":
        adb_path = resolve_tool_path("adb")
    if not fastboot_path or fastboot_path == "fastboot":
        fastboot_path = resolve_tool_path("fastboot")
    adb_path = os.path.expanduser(str(adb_path))
    fastboot_path = os.path.expanduser(str(fastboot_path))

    try:
        devices, diagnostics = _enumerate_devices(adb_path, fastboot_path, timeout)
        result.available_devices = [_device_summary(device) for device in devices]

        if not devices:
            message = "Устройство не найдено. Подключите Pixel по USB."
            if diagnostics:
                message += " " + " ".join(diagnostics)
            result.error = message
            result.warnings.extend(diagnostics)
            return result

        selected, selection_error, selection_warnings = _select_device(devices, serial)
        result.warnings.extend(selection_warnings)
        if selected is None:
            result.error = selection_error
            return result

        result.detected = True
        result.serial = selected.serial
        result.mode = selected.mode.value
        result.state = selected.state

        # Отдать UI лёгкий результат до медленного чтения getprop/батареи.
        if on_progress is not None:
            state = (selected.state or "").lower()
            needs_details = selected.mode is ConnectionMode.FASTBOOT or state in {
                "device",
                "recovery",
            }
            if needs_details:
                try:
                    on_progress(
                        _preview_device_info(selected, result.available_devices)
                    )
                except Exception:  # callback из UI не должен ломать детектор
                    LOGGER.debug(
                        "Не удалось передать предварительный результат",
                        exc_info=True,
                    )

        if selected.mode is ConnectionMode.ADB:
            _fill_from_adb(result, selected, adb_path, timeout)
        else:
            _fill_from_fastboot(result, selected, fastboot_path, timeout)

        return result
    except Exception as exc:  # Последняя защита для backend/UI-потока.
        LOGGER.exception("Необработанная ошибка определения устройства")
        result.error = f"Внутренняя ошибка определения устройства: {exc}"
        return result


# ---------------------------------------------------------------------------
# Запуск внешних команд
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CommandResult:
    """Результат вызова adb/fastboot.

    ``error`` заполняется только тогда, когда команду не удалось запустить
    или она превысила таймаут. Ненулевой код возврата остаётся в
    ``returncode`` и разбирается вызывающим кодом.
    """

    command: Tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None

    @property
    def output(self) -> str:
        parts = [part for part in (self.stdout, self.stderr) if part]
        return "\n".join(parts)

    @property
    def ok(self) -> bool:
        return self.error is None and self.returncode == 0


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _command_label(command: Sequence[str]) -> str:
    return " ".join(str(part) for part in command)


def _run(command: Sequence[str], timeout: float) -> _CommandResult:
    """Выполнить команду без промежуточной оболочки.

    Команда передаётся списком аргументов, поэтому значения переменных
    не могут превратиться в дополнительные shell-команды. На Windows
    окно консоли скрывается.
    """
    command = tuple(str(part) for part in command)
    creationflags = 0
    if os.name == "nt":
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))

    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=creationflags,
        )
        return _CommandResult(
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
    except FileNotFoundError:
        return _CommandResult(
            command=command,
            returncode=-1,
            error=(
                f"Не найдена программа «{command[0]}». "
                "Проверьте PATH или укажите полный путь явно."
            ),
        )
    except subprocess.TimeoutExpired as exc:
        return _CommandResult(
            command=command,
            returncode=-1,
            stdout=_as_text(exc.stdout),
            stderr=_as_text(exc.stderr),
            error=f"Команда «{_command_label(command)}» не ответила за {timeout:g} с.",
        )
    except OSError as exc:
        return _CommandResult(
            command=command,
            returncode=-1,
            error=f"Не удалось запустить «{command[0]}»: {exc}",
        )


def _result_error_text(result: _CommandResult) -> str:
    """Короткое описание ошибки команды для warnings/error."""
    if result.error:
        return result.error
    text = (result.stderr or result.stdout).strip()
    if text:
        first_line = text.splitlines()[0].strip()
        if first_line:
            return first_line[:300]
    return (
        f"Команда «{_command_label(result.command)}» "
        f"завершилась с кодом {result.returncode}."
    )


# ---------------------------------------------------------------------------
# Поиск подключённых устройств
# ---------------------------------------------------------------------------

_ADB_STATES = frozenset(
    {
        "device",
        "offline",
        "unauthorized",
        "recovery",
        "sideload",
        "bootloader",
        "host",
        "no permissions",
        "authorizing",
        "connecting",
        "rescue",
    }
)
_FASTBOOT_STATES = frozenset({"fastboot", "fastbootd"})


def _parse_adb_devices(output: str) -> List[ConnectedDevice]:
    """Разобрать вывод ``adb devices -l``."""
    devices: List[ConnectedDevice] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("*") or line.startswith("List of devices"):
            continue

        parts = line.split()
        if len(parts) < 2:
            continue

        serial = parts[0]
        state = parts[1].lower()
        detail_start = 2

        # На Linux adb может вернуть состояние из двух слов.
        if state == "no" and len(parts) >= 3 and parts[2].lower() == "permissions":
            state = "no permissions"
            detail_start = 3

        if state not in _ADB_STATES:
            # Строки вида "adb server version ..." и прочий служебный вывод.
            continue

        details: Dict[str, str] = {}
        for token in parts[detail_start:]:
            key, separator, value = token.partition(":")
            if separator and key and value:
                details[key.lower()] = value

        devices.append(
            ConnectedDevice(
                serial=serial,
                mode=ConnectionMode.ADB,
                state=state,
                details=details,
            )
        )
    return devices


def _parse_fastboot_devices(output: str) -> List[ConnectedDevice]:
    """Разобрать вывод ``fastboot devices -l`` (может прийти в stderr)."""
    devices: List[ConnectedDevice] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        parts = line.split()
        if len(parts) < 2:
            continue

        serial = parts[0]
        state = parts[1].lower()
        if state not in _FASTBOOT_STATES:
            # "fastboot: error: no devices found" и другие служебные строки.
            continue

        details: Dict[str, str] = {}
        for token in parts[2:]:
            key, separator, value = token.partition(":")
            if separator and key and value:
                details[key.lower()] = value

        devices.append(
            ConnectedDevice(
                serial=serial,
                mode=ConnectionMode.FASTBOOT,
                state=state,
                details=details,
            )
        )
    return devices


def _device_priority(device: ConnectedDevice) -> int:
    """Чем выше число, тем более готовым считается устройство."""
    state = (device.state or "").lower()
    if device.mode is ConnectionMode.ADB:
        return {
            "device": 100,
            "recovery": 95,
            "sideload": 85,
            "unauthorized": 80,
            "no permissions": 30,
            "offline": 20,
            "bootloader": 15,
        }.get(state, 40)
    return {"fastboot": 90, "fastbootd": 90}.get(state, 35)


def _deduplicate_devices(devices: Sequence[ConnectedDevice]) -> List[ConnectedDevice]:
    """Оставить по одному (лучшему) состоянию на каждый serial."""
    by_serial: Dict[str, ConnectedDevice] = {}
    order: List[str] = []
    for device in devices:
        existing = by_serial.get(device.serial)
        if existing is None:
            by_serial[device.serial] = device
            order.append(device.serial)
        elif _device_priority(device) > _device_priority(existing):
            by_serial[device.serial] = device
    return [by_serial[serial] for serial in order]


def _enumerate_devices(
    adb_path: str, fastboot_path: str, timeout: float
) -> Tuple[List[ConnectedDevice], List[str]]:
    """Собрать ADB- и Fastboot-устройства и диагностические сообщения."""
    devices: List[ConnectedDevice] = []
    diagnostics: List[str] = []

    adb_result = _run([adb_path, "devices", "-l"], timeout)
    if adb_result.error:
        diagnostics.append(f"ADB: {adb_result.error}")
    else:
        if adb_result.returncode != 0:
            diagnostics.append(f"ADB: {_result_error_text(adb_result)}")
        devices.extend(_parse_adb_devices(adb_result.output))

    fastboot_result = _run([fastboot_path, "devices", "-l"], timeout)
    if fastboot_result.error:
        diagnostics.append(f"Fastboot: {fastboot_result.error}")
    else:
        fastboot_devices = _parse_fastboot_devices(fastboot_result.output)

        # Очень старые версии fastboot не знают флаг -l.
        if not fastboot_devices and fastboot_result.returncode != 0:
            output_lower = fastboot_result.output.lower()
            if (
                "unknown option" in output_lower
                or "unrecognized" in output_lower
                or "usage:" in output_lower
            ):
                fastboot_result = _run([fastboot_path, "devices"], timeout)
                if fastboot_result.error:
                    diagnostics.append(f"Fastboot: {fastboot_result.error}")
                else:
                    fastboot_devices = _parse_fastboot_devices(fastboot_result.output)
                    if fastboot_result.returncode != 0 and not fastboot_devices:
                        diagnostics.append(
                            f"Fastboot: {_result_error_text(fastboot_result)}"
                        )
            else:
                diagnostics.append(
                    f"Fastboot: {_result_error_text(fastboot_result)}"
                )

        devices.extend(fastboot_devices)

    return _deduplicate_devices(devices), diagnostics


def _device_summary(device: ConnectedDevice) -> Dict[str, str]:
    summary = {
        "serial": device.serial,
        "mode": device.mode.value,
        "state": device.state,
    }
    for key in ("product", "device", "model"):
        value = device.details.get(key)
        if value:
            summary[key] = value
    return summary


def _format_available_devices(devices: Sequence[ConnectedDevice]) -> str:
    if not devices:
        return "нет"
    return "; ".join(
        f"{device.serial} ({device.mode.value}, {device.state})"
        for device in devices
    )


def _select_device(
    devices: Sequence[ConnectedDevice], serial: Optional[str]
) -> Tuple[Optional[ConnectedDevice], Optional[str], List[str]]:
    """Выбрать устройство по serial или единственное подключённое."""
    warnings: List[str] = []

    if serial is not None:
        wanted = str(serial).strip()
        if not wanted:
            return None, "Передан пустой serial устройства.", warnings

        matches = [device for device in devices if device.serial == wanted]
        if not matches:
            # Serial обычно регистронезависим, но на всякий случай.
            matches = [
                device
                for device in devices
                if device.serial.lower() == wanted.lower()
            ]
        if not matches:
            return (
                None,
                (
                    f"Устройство с serial «{wanted}» не найдено. "
                    f"Доступные устройства: {_format_available_devices(devices)}."
                ),
                warnings,
            )

        matches.sort(key=_device_priority, reverse=True)
        if len(matches) > 1:
            warnings.append(
                f"Для serial «{wanted}» найдено несколько состояний; "
                f"выбрано {matches[0].mode.value}/{matches[0].state}."
            )
        return matches[0], None, warnings

    if len(devices) == 1:
        return devices[0], None, warnings

    return (
        None,
        (
            f"Найдено несколько устройств ({len(devices)}). "
            "Укажите serial. Доступные устройства: "
            f"{_format_available_devices(devices)}."
        ),
        warnings,
    )


# ---------------------------------------------------------------------------
# Свойства Android и модель устройства
# ---------------------------------------------------------------------------

_CODENAME_PROPERTIES = (
    "ro.product.device",
    "ro.product.vendor.device",
    "ro.product.system.device",
    "ro.product.odm.device",
    "ro.product.product.device",
    "ro.boot.product.hardware.sku",
)
_VERSION_PROPERTIES = (
    "ro.build.version.release",
    "ro.system.build.version.release",
    "ro.vendor.build.version.release",
    "ro.product.build.version.release",
)
_SDK_PROPERTIES = (
    "ro.build.version.sdk",
    "ro.system.build.version.sdk",
)

_GETPROP_RE = re.compile(r"^\s*\[([^\]]+)\]:\s*\[(.*)\]\s*$")


def _normalize_codename(value: Optional[str]) -> Optional[str]:
    """Привести кодовое имя к виду, в котором оно хранится в базе."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or text in {"unknown", "null", "none", "n/a"}:
        return None
    # Fastboot иногда возвращает значение с пояснением в скобках.
    text = re.split(r"[\s,;]+", text, maxsplit=1)[0]
    text = text.strip("[]()'\"")
    return text or None


def _choose_codename(values: Sequence[Optional[str]]) -> Optional[str]:
    """Выбрать известное Pixel-имя, иначе первое непустое значение."""
    normalized: List[str] = []
    for value in values:
        codename = _normalize_codename(value)
        if codename and codename not in normalized:
            normalized.append(codename)
    for codename in normalized:
        if codename in PIXEL_MODELS:
            return codename
    return normalized[0] if normalized else None


def _parse_getprop(output: str) -> Dict[str, str]:
    """Разобрать вывод ``getprop`` формата ``[key]: [value]``."""
    properties: Dict[str, str] = {}
    for line in output.splitlines():
        match = _GETPROP_RE.match(line)
        if match:
            properties[match.group(1)] = match.group(2)
    return properties


def _first_property(properties: Dict[str, str], names: Sequence[str]) -> Optional[str]:
    for name in names:
        value = properties.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _resolve_codename(
    properties: Dict[str, str], device: ConnectedDevice
) -> Optional[str]:
    values: List[Optional[str]] = [
        properties.get(name) for name in _CODENAME_PROPERTIES
    ]
    # В recovery getprop может вернуть служебное имя, а adb devices -l —
    # настоящее кодовое имя. Известное Pixel-имя имеет приоритет.
    values.append(device.details.get("product"))
    values.append(device.details.get("device"))
    return _choose_codename(values)


def _apply_model(result: PixelDeviceInfo, codename: Optional[str]) -> None:
    """Заполнить поля модели по кодовому имени."""
    result.codename = codename
    if not codename:
        result.warnings.append("Не удалось определить кодовое имя устройства.")
        return

    model = PIXEL_MODELS.get(codename)
    if model is None:
        result.is_pixel = False
        result.warnings.append(
            f"Кодовое имя «{codename}» отсутствует в базе моделей Pixel."
        )
        return

    result.is_pixel = True
    result.model_name = model.name
    result.generation = model.generation
    result.supports_battery_cycles = model.supports_battery_cycles


def _fill_android_version(
    result: PixelDeviceInfo, properties: Dict[str, str]
) -> None:
    release = _first_property(properties, _VERSION_PROPERTIES)
    if release:
        result.android_version = release
    else:
        result.warnings.append("Не удалось определить версию Android.")

    sdk = _first_property(properties, _SDK_PROPERTIES)
    if sdk:
        match = re.search(r"\d+", sdk)
        if match:
            try:
                result.android_api_level = int(match.group(0))
            except ValueError:
                pass


# ---------------------------------------------------------------------------
# Циклы заряда
# ---------------------------------------------------------------------------

_CYCLE_LABEL_RE = re.compile(
    r"cycle[ \t_.-]*count[ \t]*[:=][ \t]*(\d+)", re.IGNORECASE
)
_PLAIN_INT_RE = re.compile(r"^\s*(\d{1,6})\s*$")
_MAX_REASONABLE_CYCLES = 100_000

# Статический скрипт: обходит все power_supply-устройства и печатает
# только существующие файлы с числом циклов. Пользовательских данных
# в команде нет, поэтому инъекция невозможна.
_SYSFS_SCAN_SCRIPT = (
    'for d in /sys/class/power_supply/*; do '
    'for f in "$d/cycle_count" "$d/battery_cycle_count" "$d/charge_cycle_count"; do '
    'if [ -r "$f" ]; then '
    'printf "%s:%s\\n" "$f" "$(cat "$f" 2>/dev/null)"; '
    "fi; done; done"
)


def _is_valid_cycle_count(value: int) -> bool:
    return 0 <= value <= _MAX_REASONABLE_CYCLES


def _parse_cycle_count(output: str, *, labeled_only: bool = False) -> Optional[int]:
    """Извлечь число циклов из вывода команды.

    ``labeled_only`` не даёт принять за циклы случайное число из
    многострочного ``dumpsys battery``.
    """
    if not output:
        return None

    for match in _CYCLE_LABEL_RE.finditer(output):
        value = int(match.group(1))
        if _is_valid_cycle_count(value):
            return value

    if not labeled_only:
        match = _PLAIN_INT_RE.match(output)
        if match:
            value = int(match.group(1))
            if _is_valid_cycle_count(value):
                return value
    return None


def _read_battery_cycles(
    serial: str, adb_path: str, timeout: float
) -> Optional[int]:
    """Попробовать несколько способов чтения циклов, вернуть первое число."""

    def shell(*args: str) -> _CommandResult:
        return _run([adb_path, "-s", serial, "shell", *args], timeout)

    # 1. Некоторые прошивки показывают счётчик в dumpsys battery.
    dumpsys = shell("dumpsys", "battery")
    if dumpsys.error:
        LOGGER.debug("dumpsys battery: %s", dumpsys.error)
    else:
        count = _parse_cycle_count(dumpsys.output, labeled_only=True)
        if count is not None:
            return count

    # 2. Прямая команда battery service (Android 14+).
    direct = shell("cmd", "battery", "get", "cycle_count")
    if direct.error:
        LOGGER.debug("cmd battery get cycle_count: %s", direct.error)
    else:
        count = _parse_cycle_count(direct.output)
        if count is not None:
            return count

    # 3. sysfs: battery/bms/maxfg и любые другие power_supply-узлы.
    sysfs = shell(_SYSFS_SCAN_SCRIPT)
    if sysfs.error:
        LOGGER.debug("sysfs scan: %s", sysfs.error)
    else:
        for line in sysfs.output.splitlines():
            path, separator, value = line.rpartition(":")
            if not separator or "cycle" not in path.lower():
                continue
            count = _parse_cycle_count(value)
            if count is not None:
                return count

    return None


def _fill_battery_cycles(
    result: PixelDeviceInfo, serial: str, adb_path: str, timeout: float
) -> None:
    """Заполнить циклы с корректным сообщением для старых моделей."""
    count = _read_battery_cycles(serial, adb_path, timeout)
    if count is not None:
        result.battery_cycle_count = count
        result.battery_cycle_status = BatteryCyclesStatus.OK.value
        result.battery_cycle_message = None
        return

    if result.supports_battery_cycles is False:
        # Требование: для моделей до Pixel 8 — строго этот текст.
        result.battery_cycle_status = BatteryCyclesStatus.UNSUPPORTED.value
        result.battery_cycle_message = UNSUPPORTED_CYCLES_MESSAGE
    else:
        result.battery_cycle_status = BatteryCyclesStatus.UNAVAILABLE.value
        result.battery_cycle_message = UNAVAILABLE_CYCLES_MESSAGE


_BATTERY_LEVEL_RE = re.compile(r"\blevel:\s*(\d+)", re.IGNORECASE)
_BATTERY_TEMP_RE = re.compile(r"\btemperature:\s*(\d+)", re.IGNORECASE)
_BATTERY_HEALTH_RE = re.compile(r"\bhealth:\s*(\w+)", re.IGNORECASE)
_MEM_TOTAL_RE = re.compile(r"^MemTotal:\s*(\d+)\s*kB", re.IGNORECASE | re.MULTILINE)

_HEALTH_NAMES: Dict[str, str] = {
    "1": "Неизвестно",
    "2": "Отличное",
    "3": "Перегрев",
    "4": "Неисправна",
    "5": "Перенапряжение",
    "6": "Сбой",
    "7": "Холодная",
    "good": "Отличное",
    "overheat": "Перегрев",
    "dead": "Неисправна",
    "over_voltage": "Перенапряжение",
    "unspecified_failure": "Сбой",
    "cold": "Холодная",
}


def _fill_hardware_stats(
    result: PixelDeviceInfo, serial: str, adb_path: str, timeout: float
) -> None:
    """Прочитать уровень/температуру батареи, RAM и память /data."""
    cmd = [
        adb_path,
        "-s",
        serial,
        "shell",
        "dumpsys battery 2>/dev/null; echo ---MEM---; cat /proc/meminfo 2>/dev/null; echo ---DF---; df -k /data 2>/dev/null",
    ]
    res = _run(cmd, min(timeout, 6.0))
    if res.error or res.returncode != 0 or not res.stdout:
        return

    parts = res.stdout.split("---MEM---")
    battery_chunk = parts[0]
    mem_chunk = ""
    df_chunk = ""
    if len(parts) > 1:
        rest = parts[1].split("---DF---")
        mem_chunk = rest[0]
        if len(rest) > 1:
            df_chunk = rest[1]

    # Parse battery
    lvl = _BATTERY_LEVEL_RE.search(battery_chunk)
    if lvl:
        try:
            result.battery_level = int(lvl.group(1))
        except ValueError:
            pass

    temp = _BATTERY_TEMP_RE.search(battery_chunk)
    if temp:
        try:
            val = int(temp.group(1))
            result.battery_temp_c = round(val / 10.0, 1)
        except ValueError:
            pass

    hlth = _BATTERY_HEALTH_RE.search(battery_chunk)
    if hlth:
        raw_health = hlth.group(1).lower()
        result.battery_health = _HEALTH_NAMES.get(raw_health, raw_health.capitalize())

    # Parse RAM from meminfo
    mem = _MEM_TOTAL_RE.search(mem_chunk)
    if mem:
        try:
            kb = int(mem.group(1))
            gb = kb / (1024.0 * 1024.0)
            result.ram_total_gb = round(gb, 1)
        except ValueError:
            pass

    # Parse storage from df
    for line in df_chunk.splitlines():
        if "/data" in line:
            tokens = line.split()
            if len(tokens) >= 4:
                try:
                    total_kb = float(tokens[1])
                    free_kb = float(tokens[3])
                    result.storage_total_gb = round(total_kb / (1024.0 * 1024.0), 1)
                    result.storage_free_gb = round(free_kb / (1024.0 * 1024.0), 1)
                except (ValueError, IndexError):
                    pass
            break


# ---------------------------------------------------------------------------
# Заполнение результата из ADB или Fastboot
# ---------------------------------------------------------------------------

_FASTBOOT_VAR_RE = re.compile(
    r"^\s*(?:\((?:bootloader|fastbootd)\)\s*)?([^:]+?)\s*:\s*(.*?)\s*$",
    re.IGNORECASE,
)
_FASTBOOT_IGNORED_KEYS = frozenset(
    {"all", "finished", "total time", "fastboot", "failed", "error"}
)


def _parse_fastboot_variables(output: str) -> Dict[str, str]:
    """Разобрать ``fastboot getvar all`` (значения часто приходят в stderr)."""
    variables: Dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        match = _FASTBOOT_VAR_RE.match(line)
        if not match:
            continue

        key = match.group(1).strip()
        value = match.group(2).strip()
        if not key or not value:
            continue

        key_lower = key.lower()
        if (
            key_lower in _FASTBOOT_IGNORED_KEYS
            or "total time" in key_lower
            or key_lower.startswith(("finished", "failed", "sending ", "writing "))
        ):
            continue
        variables[key] = value
    return variables


def _fastboot_value(
    variables: Dict[str, str], names: Sequence[str]
) -> Optional[str]:
    lowered = {key.lower(): value for key, value in variables.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value:
            return value
    return None


def _fastboot_codename(variables: Dict[str, str]) -> Optional[str]:
    values = [
        _fastboot_value(variables, (name,))
        for name in ("product", "ro.product.device", "device", "board")
    ]
    return _choose_codename(values)


def _adb_state_message(state: str) -> str:
    if state == "unauthorized":
        return UNAUTHORIZED_MESSAGE
    if state == "offline":
        return (
            "Устройство находится в состоянии offline. "
            "Переподключите USB-кабель и повторите попытку."
        )
    if state == "no permissions":
        return (
            "Нет прав доступа к USB-устройству. "
            "Проверьте правила udev для adb."
        )
    if state == "sideload":
        return "Устройство в режиме sideload: данные Android недоступны."
    if state == "bootloader":
        return "Устройство в режиме bootloader. Используйте Fastboot."
    return (
        f"Устройство в состоянии «{state or 'неизвестно'}»: "
        "данные Android недоступны."
    )


def _fill_from_adb(
    result: PixelDeviceInfo,
    device: ConnectedDevice,
    adb_path: str,
    timeout: float,
) -> None:
    """Прочитать свойства Android и циклы заряда через ADB."""
    state = (device.state or "").lower()

    # unauthorized/offline/sideload: shell-команды выполнить нельзя, но
    # устройство уже найдено, поэтому detected остаётся True.
    if state not in {"device", "recovery"}:
        codename = _resolve_codename({}, device)
        if codename:
            _apply_model(result, codename)
        result.battery_cycle_status = BatteryCyclesStatus.NOT_CHECKED.value
        message = _adb_state_message(state)
        result.battery_cycle_message = message
        result.warnings.append(message)
        return

    properties: Dict[str, str] = {}
    getprop = _run(
        [adb_path, "-s", device.serial, "shell", "getprop"], timeout
    )
    if getprop.error:
        result.warnings.append(f"ADB: {getprop.error}")
    elif getprop.returncode != 0:
        result.warnings.append(f"ADB: {_result_error_text(getprop)}")
    else:
        properties = _parse_getprop(getprop.output)

        # Редкий случай: полный getprop пуст, но отдельный ключ читается.
        if not properties:
            for prop_name in _CODENAME_PROPERTIES:
                single = _run(
                    [adb_path, "-s", device.serial, "shell", "getprop", prop_name],
                    timeout,
                )
                if single.error:
                    result.warnings.append(f"ADB: {single.error}")
                    break
                value = single.stdout.strip()
                if value:
                    properties[prop_name] = value
                    break

    result.properties = properties

    codename = _resolve_codename(properties, device)
    _apply_model(result, codename)

    if properties:
        _fill_android_version(result, properties)
    else:
        result.warnings.append("Не удалось прочитать свойства Android (getprop).")

    if state == "recovery":
        result.battery_cycle_status = BatteryCyclesStatus.NOT_CHECKED.value
        result.battery_cycle_message = RECOVERY_CYCLES_MESSAGE
        result.warnings.append(RECOVERY_CYCLES_MESSAGE)
        return

    _fill_battery_cycles(result, device.serial, adb_path, timeout)
    _fill_hardware_stats(result, device.serial, adb_path, timeout)


def _fill_from_fastboot(
    result: PixelDeviceInfo,
    device: ConnectedDevice,
    fastboot_path: str,
    timeout: float,
) -> None:
    """Прочитать переменные Fastboot и определить модель."""
    state = (device.state or "").lower()
    if state not in _FASTBOOT_STATES:
        result.warnings.append(
            f"Устройство в состоянии «{state or 'неизвестно'}»: "
            "переменные Fastboot могут быть недоступны."
        )

    variables: Dict[str, str] = {}
    getvar = _run(
        [fastboot_path, "-s", device.serial, "getvar", "all"], timeout
    )
    if getvar.error:
        result.warnings.append(f"Fastboot: {getvar.error}")
    else:
        variables = _parse_fastboot_variables(getvar.output)
        if not variables and getvar.returncode != 0:
            result.warnings.append(f"Fastboot: {_result_error_text(getvar)}")

    # Если product не пришёл в общем списке, запросим его отдельно.
    # После таймаута повторный запрос только увеличит задержку.
    if getvar.error is None and not _fastboot_value(
        variables, ("product", "ro.product.device", "device", "board")
    ):
        for name in ("product", "ro.product.device"):
            single = _run(
                [fastboot_path, "-s", device.serial, "getvar", name], timeout
            )
            if single.error:
                result.warnings.append(f"Fastboot: {single.error}")
                break
            parsed = _parse_fastboot_variables(single.output)
            value = _fastboot_value(parsed, (name,))
            if value:
                variables[name] = value
                break

    result.fastboot_variables = dict(variables)

    codename = _fastboot_codename(variables)
    if codename:
        _apply_model(result, codename)
    else:
        result.warnings.append(
            "Не удалось определить кодовое имя устройства в режиме Fastboot."
        )

    result.battery_cycle_status = BatteryCyclesStatus.NOT_CHECKED.value
    result.battery_cycle_message = FASTBOOT_CYCLES_MESSAGE
    result.warnings.append(
        "Версия Android и циклы заряда недоступны в режиме Fastboot. "
        "Загрузите Android и подключите устройство по ADB."
    )

