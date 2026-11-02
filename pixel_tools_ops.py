"""ADB/Fastboot operations behind the firmware, apps and backup tabs.

The module is GUI-free and fault-tolerant: expected failures are returned in
result objects and never raised into the UI. Commands are passed as argument
lists, so package names and paths cannot inject shell syntax.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from pixel_device_detector import resolve_tool_path

LOGGER = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None]

_PACKAGE_RE = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$")
_PERMISSION_RE = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$")
_PROPERTY_RE = re.compile(r"^\[([^\]]+)\]: \[(.*)\]\s*$")
_VERSION_RE = re.compile(r"\bversionName=([^\s]+)")
_VERSION_CODE_RE = re.compile(r"\bversionCode=(\d+)")
_TARGET_SDK_RE = re.compile(r"\btargetSdk=(\d+)")
_MIN_SDK_RE = re.compile(r"\bminSdk=(\d+)")
_INSTALLER_RE = re.compile(r"\binstallerPackageName=([^\s]+)")


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandResult:
    """Result of one adb/fastboot invocation."""

    command: Tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.returncode == 0

    @property
    def output(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part)

    @property
    def message(self) -> str:
        if self.error:
            return self.error
        text = (self.stderr or self.stdout).strip()
        if text:
            return text.splitlines()[0].strip()[:300]
        return f"Команда завершилась с кодом {self.returncode}."


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _run(program: str, args: Sequence[str], timeout: float) -> CommandResult:
    command = (str(program), *(str(arg) for arg in args))
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
        return CommandResult(
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
    except FileNotFoundError:
        return CommandResult(
            command=command,
            returncode=-1,
            error=(
                f"Не найдена программа «{program}». "
                "Проверьте PATH или укажите полный путь явно."
            ),
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command=command,
            returncode=-1,
            stdout=_as_text(exc.stdout),
            stderr=_as_text(exc.stderr),
            error=f"Команда не ответила за {timeout:g} с.",
        )
    except OSError as exc:
        return CommandResult(
            command=command,
            returncode=-1,
            error=f"Не удалось запустить «{program}»: {exc}",
        )


def _failed(program: str, args: Sequence[str], message: str) -> CommandResult:
    return CommandResult(
        command=(program, *(str(arg) for arg in args)),
        returncode=-1,
        error=message,
    )


def _adb(
    serial: str,
    args: Sequence[str],
    *,
    adb_path: str = "adb",
    timeout: float = 60.0,
) -> CommandResult:
    if not adb_path or adb_path == "adb":
        adb_path = resolve_tool_path("adb")
    return _run(
        os.path.expanduser(adb_path),
        ["-s", str(serial), *args],
        timeout,
    )


def _fastboot(
    serial: str,
    args: Sequence[str],
    *,
    fastboot_path: str = "fastboot",
    timeout: float = 60.0,
) -> CommandResult:
    if not fastboot_path or fastboot_path == "fastboot":
        fastboot_path = resolve_tool_path("fastboot")
    return _run(
        os.path.expanduser(fastboot_path),
        ["-s", str(serial), *args],
        timeout,
    )


def valid_package(package: str) -> bool:
    return bool(_PACKAGE_RE.match(str(package).strip()))


def valid_permission(permission: str) -> bool:
    return bool(_PERMISSION_RE.match(str(permission).strip()))


# ---------------------------------------------------------------------------
# Build information
# ---------------------------------------------------------------------------


@dataclass
class BuildInfo:
    build_id: str = ""
    android_version: str = ""
    security_patch: str = ""
    build_date: str = ""
    build_type: str = ""
    fingerprint: str = ""
    device: str = ""
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


def _parse_getprop(output: str) -> Dict[str, str]:
    properties: Dict[str, str] = {}
    for raw_line in output.splitlines():
        match = _PROPERTY_RE.match(raw_line.strip())
        if match:
            properties[match.group(1)] = match.group(2)
    return properties


def read_build_info(
    serial: str,
    *,
    adb_path: str = "adb",
    timeout: float = 20.0,
) -> BuildInfo:
    result = _adb(serial, ["shell", "getprop"], adb_path=adb_path, timeout=timeout)
    if result.error:
        return BuildInfo(error=result.error)
    if result.returncode != 0:
        return BuildInfo(error=f"ADB: {result.message}")

    props = _parse_getprop(result.output)
    if not props:
        return BuildInfo(error="Не удалось прочитать свойства сборки Android.")

    def pick(*names: str) -> str:
        for name in names:
            value = props.get(name, "").strip()
            if value:
                return value
        return ""

    info = BuildInfo(
        build_id=pick("ro.build.display.id", "ro.build.id"),
        android_version=pick(
            "ro.build.version.release",
            "ro.system.build.version.release",
        ),
        security_patch=pick(
            "ro.build.version.security_patch",
            "ro.vendor.build.security_patch",
        ),
        build_date=pick("ro.build.date", "ro.system.build.date"),
        build_type=pick("ro.build.type", "ro.system.build.type"),
        fingerprint=pick(
            "ro.build.fingerprint",
            "ro.system.build.fingerprint",
            "ro.vendor.build.fingerprint",
        ),
        device=pick("ro.product.device", "ro.product.vendor.device"),
    )
    if not info.build_id:
        info.warnings.append("Идентификатор сборки не найден в свойствах Android.")
    return info


# ---------------------------------------------------------------------------
# Packages and permissions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppEntry:
    package: str
    system: bool = False
    disabled: bool = False
    uninstalled: bool = False


@dataclass
class PackageList:
    apps: List[AppEntry] = field(default_factory=list)
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


def _parse_package_lines(output: str) -> List[str]:
    packages: List[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line.startswith("package:"):
            continue
        value = line.split(":", 1)[1].strip()
        if valid_package(value):
            packages.append(value)
    return packages


def list_packages(
    serial: str,
    *,
    include_system: bool = False,
    adb_path: str = "adb",
    timeout: float = 30.0,
) -> PackageList:
    result = PackageList()
    collected: Dict[str, AppEntry] = {}

    disabled_res = _adb(
        serial,
        ["shell", "pm", "list", "packages", "-d"],
        adb_path=adb_path,
        timeout=timeout,
    )
    disabled_set = (
        set(_parse_package_lines(disabled_res.output))
        if disabled_res.ok
        else set()
    )

    user = _adb(
        serial,
        ["shell", "pm", "list", "packages", "-3"],
        adb_path=adb_path,
        timeout=timeout,
    )
    if user.error:
        result.error = user.error
        return result
    if user.returncode != 0:
        result.error = f"ADB: {user.message}"
        return result
    for package in _parse_package_lines(user.output):
        collected[package] = AppEntry(
            package=package,
            system=False,
            disabled=package in disabled_set,
            uninstalled=False,
        )

    if include_system:
        system = _adb(
            serial,
            ["shell", "pm", "list", "packages", "-s"],
            adb_path=adb_path,
            timeout=timeout,
        )
        active_system: set[str] = set()
        if system.error:
            result.warnings.append(f"Системные приложения: {system.error}")
        elif system.returncode != 0:
            result.warnings.append(f"Системные приложения: {system.message}")
        else:
            for package in _parse_package_lines(system.output):
                active_system.add(package)
                existing = collected.get(package)
                collected[package] = AppEntry(
                    package=package,
                    system=True if existing is None else existing.system,
                    disabled=package in disabled_set,
                    uninstalled=False,
                )

        # Include uninstalled system packages that can be restored
        uninstalled_sys = _adb(
            serial,
            ["shell", "pm", "list", "packages", "-u", "-s"],
            adb_path=adb_path,
            timeout=timeout,
        )
        if uninstalled_sys.ok:
            for package in _parse_package_lines(uninstalled_sys.output):
                if package not in active_system and package not in collected:
                    collected[package] = AppEntry(
                        package=package,
                        system=True,
                        disabled=False,
                        uninstalled=True,
                    )

    result.apps = sorted(
        collected.values(),
        key=lambda item: item.package.casefold(),
    )
    return result


@dataclass
class AppDetails:
    package: str
    version_name: str = ""
    version_code: str = ""
    target_sdk: str = ""
    min_sdk: str = ""
    installer: str = ""
    disabled: bool = False
    uninstalled: bool = False
    requested: List[str] = field(default_factory=list)
    runtime: Dict[str, bool] = field(default_factory=dict)
    error: Optional[str] = None


def _parse_app_details(package: str, output: str) -> AppDetails:
    details = AppDetails(package=package)
    version = _VERSION_RE.search(output)
    if version:
        details.version_name = version.group(1)
    version_code = _VERSION_CODE_RE.search(output)
    if version_code:
        details.version_code = version_code.group(1)
    target = _TARGET_SDK_RE.search(output)
    if target:
        details.target_sdk = target.group(1)
    minimum = _MIN_SDK_RE.search(output)
    if minimum:
        details.min_sdk = minimum.group(1)
    installer = _INSTALLER_RE.search(output)
    if installer:
        details.installer = installer.group(1)

    section: Optional[str] = None
    requested: List[str] = []
    runtime: Dict[str, bool] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line == "requested permissions:":
            section = "requested"
            continue
        if line == "runtime permissions:":
            section = "runtime"
            continue
        if line in {"install permissions:", "granted permissions:"}:
            section = "granted"
            continue
        if line.endswith(":"):
            section = None
            continue
        if section == "requested" and line.startswith("android.permission."):
            permission = line.split()[0]
            if valid_permission(permission) and permission not in requested:
                requested.append(permission)
        elif section == "runtime" and line.startswith("android.permission."):
            permission, _, rest = line.partition(":")
            permission = permission.strip()
            if not valid_permission(permission):
                continue
            lowered = rest.lower()
            if "granted=true" in lowered:
                runtime[permission] = True
            elif "granted=false" in lowered:
                runtime[permission] = False

    details.requested = requested
    details.runtime = runtime

    lowered = output.lower()
    if (
        "enabled=2" in lowered
        or "enabled=3" in lowered
        or "enabled=4" in lowered
        or "disabled-user" in lowered
        or "disabled=true" in lowered
    ):
        details.disabled = True

    return details


def read_app_details(
    serial: str,
    package: str,
    *,
    adb_path: str = "adb",
    timeout: float = 30.0,
) -> AppDetails:
    if not valid_package(package):
        return AppDetails(package=package, error="Некорректное имя пакета.")
    result = _adb(
        serial,
        ["shell", "dumpsys", "package", package],
        adb_path=adb_path,
        timeout=timeout,
    )
    if result.error:
        return AppDetails(package=package, error=result.error)
    if result.returncode != 0:
        return AppDetails(package=package, error=f"ADB: {result.message}")
    details = _parse_app_details(package, result.output)
    if not details.version_name and not details.requested and not details.runtime:
        uninstalled_check = _adb(
            serial,
            ["shell", "pm", "list", "packages", "-u", "-s", package],
            adb_path=adb_path,
            timeout=10.0,
        )
        if uninstalled_check.ok and package in uninstalled_check.output:
            details.uninstalled = True
            details.error = None
        else:
            details.error = "Не удалось прочитать сведения о приложении."
    return details


def install_apk(
    serial: str,
    apk_path: str,
    *,
    reinstall: bool = True,
    adb_path: str = "adb",
    timeout: float = 900.0,
) -> CommandResult:
    if not adb_path or adb_path == "adb":
        adb_path = resolve_tool_path("adb")
    path = Path(apk_path).expanduser()
    if not path.is_file():
        return _failed(
            os.path.expanduser(adb_path),
            ["-s", str(serial), "install", str(path)],
            f"APK-файл не найден: {path}",
        )
    args = ["install"]
    if reinstall:
        args.append("-r")
    args.append(str(path))
    return _adb(serial, args, adb_path=adb_path, timeout=timeout)


def uninstall_package(
    serial: str,
    package: str,
    *,
    keep_data: bool = False,
    adb_path: str = "adb",
    timeout: float = 120.0,
) -> CommandResult:
    if not adb_path or adb_path == "adb":
        adb_path = resolve_tool_path("adb")
    if not valid_package(package):
        return _failed(
            os.path.expanduser(adb_path),
            ["-s", str(serial), "uninstall", package],
            "Некорректное имя пакета.",
        )
    args = ["uninstall"]
    if keep_data:
        args.append("-k")
    args.append(package)
    return _adb(serial, args, adb_path=adb_path, timeout=timeout)


def uninstall_system_package(
    serial: str,
    package: str,
    *,
    keep_data: bool = False,
    adb_path: str = "adb",
    timeout: float = 60.0,
) -> CommandResult:
    """Удалить системное приложение для пользователя 0 (деблоатинг)."""
    if not adb_path or adb_path == "adb":
        adb_path = resolve_tool_path("adb")
    if not valid_package(package):
        return _failed(
            os.path.expanduser(adb_path),
            ["-s", str(serial), "shell", "pm"],
            "Некорректное имя пакета.",
        )
    args = ["shell", "pm", "uninstall", "--user", "0"]
    if keep_data:
        args.append("-k")
    args.append(package)
    return _adb(serial, args, adb_path=adb_path, timeout=timeout)


def restore_system_package(
    serial: str,
    package: str,
    *,
    adb_path: str = "adb",
    timeout: float = 60.0,
) -> CommandResult:
    """Восстановить ранее удалённое системное приложение для пользователя 0."""
    if not adb_path or adb_path == "adb":
        adb_path = resolve_tool_path("adb")
    if not valid_package(package):
        return _failed(
            os.path.expanduser(adb_path),
            ["-s", str(serial), "cmd", "package"],
            "Некорректное имя пакета.",
        )
    return _adb(
        serial,
        ["shell", "cmd", "package", "install-existing", package],
        adb_path=adb_path,
        timeout=timeout,
    )


def set_permission(
    serial: str,
    package: str,
    permission: str,
    granted: bool,
    *,
    adb_path: str = "adb",
    timeout: float = 30.0,
) -> CommandResult:
    if not valid_package(package):
        return _failed(
            os.path.expanduser(adb_path),
            ["-s", str(serial), "shell", "pm"],
            "Некорректное имя пакета.",
        )
    if not valid_permission(permission):
        return _failed(
            os.path.expanduser(adb_path),
            ["-s", str(serial), "shell", "pm"],
            "Некорректное имя разрешения.",
        )
    action = "grant" if granted else "revoke"
    return _adb(
        serial,
        ["shell", "pm", action, package, permission],
        adb_path=adb_path,
        timeout=timeout,
    )


def disable_package(
    serial: str,
    package: str,
    *,
    adb_path: str = "adb",
    timeout: float = 30.0,
) -> CommandResult:
    """Заморозить (отключить) приложение для основного пользователя."""
    if not valid_package(package):
        return _failed(
            os.path.expanduser(adb_path),
            ["-s", str(serial), "shell", "pm"],
            "Некорректное имя пакета.",
        )
    return _adb(
        serial,
        ["shell", "pm", "disable-user", "--user", "0", package],
        adb_path=adb_path,
        timeout=timeout,
    )


def enable_package(
    serial: str,
    package: str,
    *,
    adb_path: str = "adb",
    timeout: float = 30.0,
) -> CommandResult:
    """Разморозить (включить) ранее отключенное приложение."""
    if not valid_package(package):
        return _failed(
            os.path.expanduser(adb_path),
            ["-s", str(serial), "shell", "pm"],
            "Некорректное имя пакета.",
        )
    return _adb(
        serial,
        ["shell", "pm", "enable", package],
        adb_path=adb_path,
        timeout=timeout,
    )


def force_stop_package(
    serial: str,
    package: str,
    *,
    adb_path: str = "adb",
    timeout: float = 20.0,
) -> CommandResult:
    """Принудительно остановить процесс приложения."""
    if not valid_package(package):
        return _failed(
            os.path.expanduser(adb_path),
            ["-s", str(serial), "shell", "am"],
            "Некорректное имя пакета.",
        )
    return _adb(
        serial,
        ["shell", "am", "force-stop", package],
        adb_path=adb_path,
        timeout=timeout,
    )


def clear_package_data(
    serial: str,
    package: str,
    *,
    adb_path: str = "adb",
    timeout: float = 30.0,
) -> CommandResult:
    """Очистить все данные и кэш приложения."""
    if not valid_package(package):
        return _failed(
            os.path.expanduser(adb_path),
            ["-s", str(serial), "shell", "pm"],
            "Некорректное имя пакета.",
        )
    return _adb(
        serial,
        ["shell", "pm", "clear", package],
        adb_path=adb_path,
        timeout=timeout,
    )


def launch_package(
    serial: str,
    package: str,
    *,
    adb_path: str = "adb",
    timeout: float = 20.0,
) -> CommandResult:
    """Запустить главное окно приложения на устройстве."""
    if not valid_package(package):
        return _failed(
            os.path.expanduser(adb_path),
            ["-s", str(serial), "shell", "monkey"],
            "Некорректное имя пакета.",
        )
    return _adb(
        serial,
        ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
        adb_path=adb_path,
        timeout=timeout,
    )


def pull_apk(
    serial: str,
    package: str,
    destination_file: str | Path,
    *,
    adb_path: str = "adb",
    timeout: float = 120.0,
) -> CommandResult:
    """Выгрузить APK-файл приложения с устройства на локальный диск."""
    if not adb_path or adb_path == "adb":
        adb_path = resolve_tool_path("adb")
    if not valid_package(package):
        return _failed(
            os.path.expanduser(adb_path),
            ["-s", str(serial), "pull"],
            "Некорректное имя пакета.",
        )
    path_res = _adb(
        serial,
        ["shell", "pm", "path", package],
        adb_path=adb_path,
        timeout=15.0,
    )
    if path_res.error:
        return path_res
    if path_res.returncode != 0 or not path_res.stdout.strip():
        return _failed(
            os.path.expanduser(adb_path),
            ["-s", str(serial), "pull"],
            f"Не удалось получить путь к APK: {path_res.message}",
        )
    remote_path = ""
    for line in path_res.stdout.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            candidate = line.split(":", 1)[1].strip()
            if candidate.endswith(".apk") or "/base.apk" in candidate:
                remote_path = candidate
                break
            if not remote_path:
                remote_path = candidate
    if not remote_path:
        return _failed(
            os.path.expanduser(adb_path),
            ["-s", str(serial), "pull"],
            "Путь к APK в файловой системе устройства не найден.",
        )
    dest = Path(destination_file).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    return _adb(
        serial,
        ["pull", remote_path, str(dest)],
        adb_path=adb_path,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Reboot, active slots and OTA sideload
# ---------------------------------------------------------------------------


def reboot_device(
    serial: str,
    target: str,
    *,
    mode: str = "adb",
    adb_path: str = "adb",
    fastboot_path: str = "fastboot",
    timeout: float = 30.0,
) -> CommandResult:
    if not adb_path or adb_path == "adb":
        adb_path = resolve_tool_path("adb")
    if not fastboot_path or fastboot_path == "fastboot":
        fastboot_path = resolve_tool_path("fastboot")
    target = str(target).strip().lower()
    allowed = {"system", "recovery", "bootloader", "sideload", "fastboot"}
    if target not in allowed:
        return _failed(
            os.path.expanduser(adb_path),
            ["-s", str(serial), "reboot"],
            f"Неизвестный режим перезагрузки: {target}",
        )

    if str(mode).lower() == "fastboot":
        if target in ("system", "recovery"):
            return _fastboot(
                serial,
                ["reboot"],
                fastboot_path=fastboot_path,
                timeout=timeout,
            )
        if target == "bootloader":
            return _fastboot(
                serial,
                ["reboot-bootloader"],
                fastboot_path=fastboot_path,
                timeout=timeout,
            )
        if target == "fastboot":
            return _fastboot(
                serial,
                ["reboot-fastboot"],
                fastboot_path=fastboot_path,
                timeout=timeout,
            )
        return _failed(
            os.path.expanduser(fastboot_path),
            ["-s", str(serial), "reboot"],
            f"В режиме Fastboot цель «{target}» не поддерживается.",
        )

    args = ["reboot"]
    if target != "system":
        args.append(target)
    return _adb(serial, args, adb_path=adb_path, timeout=timeout)


def set_active_slot(
    serial: str,
    slot: str,
    *,
    fastboot_path: str = "fastboot",
    timeout: float = 30.0,
) -> CommandResult:
    """Переключить активный загрузочный слот A/B в режиме Fastboot."""
    if not fastboot_path or fastboot_path == "fastboot":
        fastboot_path = resolve_tool_path("fastboot")
    slot_clean = slot.strip().lower().lstrip("_")
    if slot_clean not in ("a", "b"):
        return _failed(
            os.path.expanduser(fastboot_path),
            ["-s", str(serial), "--set-active"],
            f"Недопустимый слот: {slot}. Допустимы только 'a' или 'b'.",
        )
    return _fastboot(
        serial,
        [f"--set-active={slot_clean}"],
        fastboot_path=fastboot_path,
        timeout=timeout,
    )


def sideload_ota(
    serial: str,
    zip_path: str,
    *,
    adb_path: str = "adb",
    timeout: float = 1800.0,
) -> CommandResult:
    if not adb_path or adb_path == "adb":
        adb_path = resolve_tool_path("adb")
    path = Path(zip_path).expanduser()
    if not path.is_file():
        return _failed(
            os.path.expanduser(adb_path),
            ["-s", str(serial), "sideload", str(path)],
            f"Файл обновления не найден: {path}",
        )
    return _adb(
        serial,
        ["sideload", str(path)],
        adb_path=adb_path,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackupOption:
    key: str
    title: str
    description: str
    remote_path: Optional[str] = None


BACKUP_OPTIONS: Tuple[BackupOption, ...] = (
    BackupOption(
        "photos",
        "Фото и видео",
        "DCIM: камера, скриншоты и видео",
        "/sdcard/DCIM",
    ),
    BackupOption(
        "pictures",
        "Изображения",
        "Все изображения из Pictures",
        "/sdcard/Pictures",
    ),
    BackupOption(
        "movies",
        "Видео",
        "Видеофайлы из Movies",
        "/sdcard/Movies",
    ),
    BackupOption(
        "download",
        "Загрузки",
        "Файлы из папки Download",
        "/sdcard/Download",
    ),
    BackupOption(
        "documents",
        "Документы",
        "Файлы из папки Documents",
        "/sdcard/Documents",
    ),
    BackupOption(
        "contacts",
        "Контакты",
        "Файл contacts.vcf для импорта",
        None,
    ),
)

BACKUP_OPTIONS_BY_KEY = {option.key: option for option in BACKUP_OPTIONS}


@dataclass
class BackupResult:
    destination: str = ""
    copied: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    contacts_count: int = 0
    packages_written: bool = False


@dataclass
class RestoreResult:
    source: str = ""
    pushed: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None


def _vcard_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r", "")
        .replace("\n", "\\n")
    )


def _parse_contact_rows(output: str) -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line.startswith("Row:"):
            continue
        body = line
        marker = body.find("display_name=")
        if marker < 0:
            continue
        body = body[marker:]
        name_match = re.search(
            r"display_name=(.*?)(?:,\s*(?:number|display_name)=|$)",
            body,
        )
        number_match = re.search(
            r"number=(.*?)(?:,\s*(?:number|display_name)=|$)",
            body,
        )
        if not name_match or not number_match:
            continue
        name = name_match.group(1).strip()
        number = number_match.group(1).strip()
        if name or number:
            rows.append((name or "Без имени", number))
    return rows


def backup_contacts(
    serial: str,
    destination: Path,
    *,
    adb_path: str = "adb",
    timeout: float = 45.0,
) -> Tuple[Optional[Path], int, Optional[str]]:
    result = _adb(
        serial,
        [
            "shell",
            "content",
            "query",
            "--uri",
            "content://contacts/phones",
            "--projection",
            "display_name:number",
        ],
        adb_path=adb_path,
        timeout=timeout,
    )
    if result.error:
        return None, 0, result.error
    if result.returncode != 0:
        return None, 0, f"ADB: {result.message}"

    rows = _parse_contact_rows(result.output)
    if not rows:
        return None, 0, "Контакты не найдены или провайдер контактов недоступен."

    path = destination / "contacts.vcf"
    lines: List[str] = []
    for name, number in rows:
        lines.extend(
            [
                "BEGIN:VCARD",
                "VERSION:3.0",
                f"FN:{_vcard_escape(name)}",
                f"TEL;TYPE=CELL:{_vcard_escape(number)}",
                "END:VCARD",
            ]
        )
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        return None, 0, f"Не удалось записать contacts.vcf: {exc}"
    return path, len(rows), None


def _save_package_list(
    serial: str,
    destination: Path,
    *,
    adb_path: str,
    timeout: float,
    warnings: List[str],
) -> bool:
    result = _adb(
        serial,
        ["shell", "pm", "list", "packages", "-3"],
        adb_path=adb_path,
        timeout=timeout,
    )
    if result.error:
        warnings.append(f"Список приложений: {result.error}")
        return False
    if result.returncode != 0:
        warnings.append(f"Список приложений: {result.message}")
        return False
    packages = _parse_package_lines(result.output)
    try:
        (destination / "packages.txt").write_text(
            "\n".join(packages) + ("\n" if packages else ""),
            encoding="utf-8",
        )
    except OSError as exc:
        warnings.append(f"Не удалось записать packages.txt: {exc}")
        return False
    return True


def create_backup(
    serial: str,
    option_keys: Iterable[str],
    destination: str | Path,
    *,
    adb_path: str = "adb",
    model: str = "",
    progress: Optional[ProgressCallback] = None,
) -> BackupResult:
    keys = [str(key) for key in option_keys]
    unknown = [key for key in keys if key not in BACKUP_OPTIONS_BY_KEY]
    if unknown:
        return BackupResult(error=f"Неизвестные разделы резервной копии: {', '.join(unknown)}")
    if not keys:
        return BackupResult(error="Выберите хотя бы один раздел для резервной копии.")

    target = Path(destination).expanduser()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return BackupResult(error=f"Не удалось создать папку резервной копии: {exc}")
    if not target.is_dir():
        return BackupResult(error="Путь резервной копии не является папкой.")

    result = BackupResult(destination=str(target))
    for key in keys:
        option = BACKUP_OPTIONS_BY_KEY[key]
        if progress:
            progress(f"Копируем: {option.title}…")
        if option.remote_path:
            pulled = _adb(
                serial,
                ["pull", option.remote_path, str(target)],
                adb_path=adb_path,
                timeout=3600.0,
            )
            if pulled.error:
                result.warnings.append(f"{option.title}: {pulled.error}")
            elif pulled.returncode != 0:
                result.warnings.append(f"{option.title}: {pulled.message}")
            else:
                result.copied.append(option.remote_path)
        elif key == "contacts":
            path, count, error = backup_contacts(
                serial,
                target,
                adb_path=adb_path,
            )
            if path is not None:
                result.contacts_count = count
            if error:
                result.warnings.append(f"Контакты: {error}")

    if progress:
        progress("Сохраняем список приложений…")
    result.packages_written = _save_package_list(
        serial,
        target,
        adb_path=adb_path,
        timeout=45.0,
        warnings=result.warnings,
    )

    metadata = {
        "format": "pixel-tools-backup",
        "version": 1,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "serial": serial,
        "model": model,
        "options": keys,
        "copied": result.copied,
        "contacts_count": result.contacts_count,
        "warnings": result.warnings,
    }
    try:
        (target / "backup-info.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        result.warnings.append(f"Не удалось записать backup-info.json: {exc}")

    return result


def read_backup_info(directory: str | Path) -> Optional[Dict[str, object]]:
    path = Path(directory).expanduser() / "backup-info.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("format") != "pixel-tools-backup":
        return None
    return data


def restore_files(
    serial: str,
    source: str | Path,
    *,
    adb_path: str = "adb",
    progress: Optional[ProgressCallback] = None,
) -> RestoreResult:
    directory = Path(source).expanduser()
    if not directory.is_dir():
        return RestoreResult(error="Папка резервной копии не найдена.")
    metadata = read_backup_info(directory)
    if metadata is None:
        return RestoreResult(
            error="В выбранной папке нет backup-info.json от Pixel Tools."
        )

    result = RestoreResult(source=str(directory))
    keys = metadata.get("options", [])
    if not isinstance(keys, list):
        keys = []

    pushed_names: set[str] = set()
    for key in keys:
        option = BACKUP_OPTIONS_BY_KEY.get(str(key))
        if option is None or not option.remote_path:
            continue
        folder_name = option.remote_path.rstrip("/").rsplit("/", 1)[-1]
        local = directory / folder_name
        if not local.is_dir():
            continue
        if progress:
            progress(f"Восстанавливаем: {option.title}…")
        pushed = _adb(
            serial,
            ["push", str(local), "/sdcard/"],
            adb_path=adb_path,
            timeout=3600.0,
        )
        if pushed.error:
            result.warnings.append(f"{option.title}: {pushed.error}")
        elif pushed.returncode != 0:
            result.warnings.append(f"{option.title}: {pushed.message}")
        else:
            result.pushed.append(folder_name)
            pushed_names.add(folder_name)

    contacts = directory / "contacts.vcf"
    if contacts.is_file():
        if progress:
            progress("Копируем контакты в Download…")
        pushed = _adb(
            serial,
            ["push", str(contacts), "/sdcard/Download/PixelTools-contacts.vcf"],
            adb_path=adb_path,
            timeout=120.0,
        )
        if pushed.error:
            result.warnings.append(f"Контакты: {pushed.error}")
        elif pushed.returncode != 0:
            result.warnings.append(f"Контакты: {pushed.message}")
        else:
            result.pushed.append("contacts.vcf")

    if not result.pushed:
        result.error = (
            "В резервной копии не нашлось файлов для восстановления."
        )
    return result


# ---------------------------------------------------------------------------
# Screen mirroring and display control
# ---------------------------------------------------------------------------


@dataclass
class ScreenMirrorConfig:
    stay_awake: bool = True
    turn_screen_off: bool = False
    always_on_top: bool = False
    max_size: int = 0  # 0 means native device resolution
    bit_rate_mbps: int = 8
    max_fps: int = 60
    audio: bool = True
    read_only: bool = False
    borderless: bool = False
    fullscreen: bool = False


def find_scrcpy_binary() -> Optional[str]:
    """Search for scrcpy executable in tools/ or PATH."""
    return resolve_tool_path("scrcpy")


def is_scrcpy_available() -> bool:
    binary = find_scrcpy_binary()
    return bool(binary and (Path(binary).is_file() or shutil.which(binary)))


def start_screen_mirror(
    serial: Optional[str] = None,
    config: Optional[ScreenMirrorConfig] = None,
    *,
    custom_title: Optional[str] = None,
) -> tuple[bool, str, Optional[subprocess.Popen]]:
    """Launch scrcpy screen mirroring process."""
    binary = find_scrcpy_binary()
    if not binary:
        return False, "Исполняемый файл scrcpy не найден в папке tools/ или PATH.", None

    cfg = config or ScreenMirrorConfig()
    title = custom_title or "Pixel Tools — Трансляция экрана"
    args = [binary, "--window-title", title]

    if serial:
        args.extend(["-s", str(serial)])
    if cfg.stay_awake:
        args.append("--stay-awake")
    if cfg.turn_screen_off:
        args.append("--turn-screen-off")
    if cfg.always_on_top:
        args.append("--always-on-top")
    if cfg.max_size > 0:
        args.extend(["--max-size", str(cfg.max_size)])
    if cfg.bit_rate_mbps > 0:
        args.extend(["--video-bit-rate", f"{cfg.bit_rate_mbps}M"])
    if cfg.max_fps > 0:
        args.extend(["--max-fps", str(cfg.max_fps)])
    if not cfg.audio:
        args.append("--no-audio")
    if cfg.read_only:
        args.append("--no-control")
    if cfg.borderless:
        args.append("--window-borderless")
    if cfg.fullscreen:
        args.append("--fullscreen")

    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        return True, "", proc
    except Exception as exc:
        return False, f"Не удалось запустить трансляцию: {exc}", None


def send_device_key(
    serial: Optional[str],
    keycode: int,
    *,
    adb_path: str = "adb",
    timeout: float = 10.0,
) -> CommandResult:
    """Send Android keyevent (e.g. 3=Home, 4=Back, 187=AppSwitch, 26=Power)."""
    return _adb(
        serial,
        ["shell", "input", "keyevent", str(keycode)],
        adb_path=adb_path,
        timeout=timeout,
    )


def take_device_screenshot(
    serial: Optional[str],
    destination_file: str,
    *,
    adb_path: str = "adb",
    timeout: float = 20.0,
) -> CommandResult:
    """Capture device screenshot and save to local PNG file."""
    dest = Path(destination_file).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    real_adb = resolve_tool_path(adb_path)
    args = [real_adb]
    if serial:
        args.extend(["-s", str(serial)])
    args.extend(["exec-out", "screencap", "-p"])

    try:
        with open(dest, "wb") as f:
            proc = subprocess.run(
                args,
                stdout=f,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace").strip()
            return CommandResult(
                command=tuple(args),
                returncode=proc.returncode,
                stderr=err,
                error=err or "Ошибка захвата экрана через screencap",
            )
        if dest.stat().st_size < 100:
            return CommandResult(
                command=tuple(args),
                returncode=1,
                error="Файл скриншота пуст или поврежден",
            )
        return CommandResult(
            command=tuple(args),
            returncode=0,
            stdout=str(dest),
        )
    except Exception as exc:
        return CommandResult(
            command=tuple(args),
            returncode=1,
            error=f"Не удалось сделать снимок: {exc}",
        )


def toggle_device_rotation(
    serial: Optional[str],
    *,
    adb_path: str = "adb",
    timeout: float = 10.0,
) -> CommandResult:
    """Toggle device orientation between portrait and landscape."""
    res = _adb(
        serial,
        ["shell", "settings", "get", "system", "user_rotation"],
        adb_path=adb_path,
        timeout=timeout,
    )
    current = res.stdout.strip()
    new_rot = "1" if current == "0" else "0"
    _adb(
        serial,
        ["shell", "settings", "put", "system", "accelerometer_rotation", "0"],
        adb_path=adb_path,
        timeout=timeout,
    )
    return _adb(
        serial,
        ["shell", "settings", "put", "system", "user_rotation", new_rot],
        adb_path=adb_path,
        timeout=timeout,
    )


_ICON_PATTERNS = (
    re.compile(r"res/mipmap-xxxhdpi[^/]*/(?:ic_launcher|ic_logo|app_icon|icon)[^\.]*\.(?:png|webp)", re.I),
    re.compile(r"res/mipmap-xxhdpi[^/]*/(?:ic_launcher|ic_logo|app_icon|icon)[^\.]*\.(?:png|webp)", re.I),
    re.compile(r"res/mipmap-xhdpi[^/]*/(?:ic_launcher|ic_logo|app_icon|icon)[^\.]*\.(?:png|webp)", re.I),
    re.compile(r"res/drawable-xxxhdpi[^/]*/(?:ic_launcher|ic_logo|app_icon|icon)[^\.]*\.(?:png|webp)", re.I),
    re.compile(r"res/drawable-xxhdpi[^/]*/(?:ic_launcher|ic_logo|app_icon|icon)[^\.]*\.(?:png|webp)", re.I),
    re.compile(r"res/mipmap-[^/]*/.*(?:launcher|icon|logo)[^\.]*\.(?:png|webp)", re.I),
    re.compile(r"res/drawable-[^/]*/.*(?:launcher|icon|logo)[^\.]*\.(?:png|webp)", re.I),
    re.compile(r".*(?:ic_launcher|ic_logo|app_icon).*\.(?:png|webp)", re.I),
)


def get_cached_app_icon(package: str) -> Optional[str]:
    """Return local path to cached icon for *package* if present."""
    if not package:
        return None
    cache_dir = Path.home() / ".pixeltools" / "app_icons"
    if not cache_dir.exists():
        return None
    for ext in (".png", ".webp", ".jpg"):
        p = cache_dir / f"{package}{ext}"
        try:
            if p.exists() and p.stat().st_size > 100:
                return str(p)
        except OSError:
            pass
    return None


def fetch_app_icon(
    serial: Optional[str],
    package: str,
    *,
    adb_path: str = "adb",
    timeout: float = 6.0,
) -> Optional[str]:
    """Fetch app icon from device APK, Google Play Store, or RuStore, cached in ~/.pixeltools/app_icons."""
    if not package:
        return None

    cached = get_cached_app_icon(package)
    if cached:
        return cached

    cache_dir = Path.home() / ".pixeltools" / "app_icons"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    def _save_icon_bytes(data: bytes) -> Optional[str]:
        if len(data) < 100:
            return None
        # Detect extension
        if data[:4] == b"RIFF" and b"WEBP" in data[:12]:
            ext = ".webp"
        elif data[:4] == b"\x89PNG":
            ext = ".png"
        else:
            ext = ".png"
        dest = cache_dir / f"{package}{ext}"
        try:
            dest.write_bytes(data)
            return str(dest)
        except OSError:
            return None

    # 1. Try extracting from device APK
    if serial:
        real_adb = resolve_tool_path(adb_path)
        try:
            p_res = subprocess.run(
                [real_adb, "-s", str(serial), "shell", f"pm path {package}"],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            apk_paths = [
                line.replace("package:", "").strip()
                for line in p_res.stdout.splitlines()
                if line.startswith("package:")
            ]
            apk_paths.sort(key=lambda x: 0 if "base.apk" in x else 1)

            for apk in apk_paths:
                l_res = subprocess.run(
                    [real_adb, "-s", str(serial), "shell", f'unzip -l "{apk}"'],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                file_entries = []
                for line in l_res.stdout.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 4 and parts[0].isdigit():
                        file_entries.append((int(parts[0]), parts[-1]))

                files = [name for _, name in file_entries]

                # 1a. Try named icon patterns
                chosen = None
                for pat in _ICON_PATTERNS:
                    matched = [f for f in files if pat.match(f)]
                    if matched:
                        chosen = matched[0]
                        break

                if chosen:
                    raw = subprocess.run(
                        [real_adb, "-s", str(serial), "exec-out", f'unzip -p "{apk}" "{chosen}"'],
                        capture_output=True,
                        timeout=timeout,
                    )
                    saved = _save_icon_bytes(raw.stdout)
                    if saved:
                        return saved
        except Exception:
            pass

    # 2. Try RuStore web catalog (especially for Russian banking/fintech apps)
    try:
        import urllib.request
        import gzip
        import ssl
        ru_ctx = ssl.create_default_context()
        ru_ctx.check_hostname = False
        ru_ctx.verify_mode = ssl.CERT_NONE

        ru_url = f"https://www.rustore.ru/catalog/app/{package}"
        ru_req = urllib.request.Request(
            ru_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Encoding": "gzip",
            },
        )
        with urllib.request.urlopen(ru_req, context=ru_ctx, timeout=timeout) as ru_resp:
            data = ru_resp.read()
            if ru_resp.headers.get("Content-Encoding") == "gzip" or data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            ru_html = data.decode("utf-8", errors="ignore")
            # RuStore icon tag: <img data-testid="icon" ... srcSet="url ...">
            icon_tags = re.findall(r'<img[^>]+data-testid=["\']icon["\'][^>]+>', ru_html)
            if icon_tags:
                src_match = re.search(r'srcSet=["\']([^"\']+)["\']', icon_tags[0]) or re.search(r'src=["\']([^"\']+)["\']', icon_tags[0])
                if src_match:
                    ru_img_url = src_match.group(1).split()[0]
                    img_req = urllib.request.Request(ru_img_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(img_req, context=ru_ctx, timeout=timeout) as img_resp:
                        content = img_resp.read()
                        saved = _save_icon_bytes(content)
                        if saved:
                            return saved
    except Exception:
        pass

    # 3. Fallback to Google Play Store CDN
    try:
        import urllib.request
        url = f"https://play.google.com/store/apps/details?id={package}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            html = response.read().decode("utf-8", errors="ignore")
            # App icon in Google Play Store has alt="Icon image" or itemprop="image" or og:image
            m = re.search(r'<img[^>]+src=["\'](https://play-lh\.googleusercontent\.com/[^"\']+)["\'][^>]+alt=["\']Icon image["\']', html)
            if not m:
                m = re.search(r'<img[^>]+alt=["\']Icon image["\'][^>]+src=["\'](https://play-lh\.googleusercontent\.com/[^"\']+)["\']', html)
            if not m:
                m = re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\'](https://play-lh\.googleusercontent\.com/[^"\']+)["\']', html)

            if m:
                base_img = m.group(1)
                img_url = re.sub(r"=[^=]*$", "=s128", base_img)
                img_req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(img_req, timeout=timeout) as img_resp:
                    content = img_resp.read()
                    saved = _save_icon_bytes(content)
                    if saved:
                        return saved
    except Exception:
        pass

    # 4. Fallback heuristic for obfuscated APKs without named icons (if not found in official stores)
    if serial:
        real_adb = resolve_tool_path(adb_path)
        try:
            p_res = subprocess.run(
                [real_adb, "-s", str(serial), "shell", f"pm path {package}"],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            apk_paths = [
                line.replace("package:", "").strip()
                for line in p_res.stdout.splitlines()
                if line.startswith("package:")
            ]
            apk_paths.sort(key=lambda x: 0 if "base.apk" in x else 1)

            for apk in apk_paths:
                l_res = subprocess.run(
                    [real_adb, "-s", str(serial), "shell", f'unzip -l "{apk}"'],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                file_entries = []
                for line in l_res.stdout.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 4 and parts[0].isdigit():
                        file_entries.append((int(parts[0]), parts[-1]))

                candidates = []
                for sz, name in file_entries:
                    low = name.lower()
                    if not low.startswith("res/"):
                        continue
                    if not (low.endswith(".png") or low.endswith(".webp")):
                        continue
                    if ".9.png" in low or "/raw/" in low or "/font/" in low or "/emoji/" in low or "emoji" in low:
                        continue
                    candidates.append((sz, name))

                candidates.sort(key=lambda x: x[0], reverse=True)
                for sz, name in candidates[:15]:
                    raw = subprocess.run(
                        [real_adb, "-s", str(serial), "exec-out", f'unzip -p "{apk}" "{name}"'],
                        capture_output=True,
                        timeout=timeout,
                    )
                    data = raw.stdout
                    is_square = False
                    w, h = 0, 0
                    if len(data) > 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
                        import struct
                        w, h = struct.unpack(">II", data[16:24])
                        is_square = (w == h and w >= 48)
                    elif len(data) > 30 and data[:4] == b"RIFF" and b"WEBP" in data[:12]:
                        try:
                            from PIL import Image
                            import io
                            im = Image.open(io.BytesIO(data))
                            w, h = im.size
                            is_square = (w == h and w >= 48)
                        except Exception:
                            pass

                    if is_square:
                        saved = _save_icon_bytes(data)
                        if saved:
                            return saved
        except Exception:
            pass

    return None


