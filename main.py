"""Pixel Tools — native Material 3 Expressive desktop app for Google Pixel.

The interface is built with Qt Widgets and custom QPainter drawing; there is
no HTML, QML or embedded browser.  Device information is provided by the
fault-tolerant ``pixel_device_detector`` backend and the palette is loaded
from ``material-you-expressive.json`` with a built-in Material 3 fallback.

Run:
    python main.py
    python main.py --theme dark
    python main.py --light
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Optional

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QRectF,
    QTimer,
    QUrl,
    QVariantAnimation,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QDesktopServices,
    QFont,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

from pixel_device_detector import (
    PixelDeviceInfo,
    detect_pixel_device,
    resolve_tool_path,
)
from pixel_tools_ops import (
    clear_package_data,
    create_backup,
    disable_package,
    enable_package,
    fetch_app_icon,
    force_stop_package,
    get_cached_app_icon,
    install_apk,
    launch_package,
    list_packages,
    pull_apk,
    read_app_details,
    read_backup_info,
    read_build_info,
    reboot_device,
    restore_files,
    restore_system_package,
    send_device_key,
    set_active_slot,
    set_permission,
    sideload_ota,
    start_screen_mirror,
    take_device_screenshot,
    toggle_device_rotation,
    uninstall_package,
    uninstall_system_package,
    ScreenMirrorConfig,
    is_scrcpy_available,
)
from qt_feature_pages import (
    AppsState,
    BackupState,
    ConfirmDialog,
    FeatureController,
    FeatureMessage,
    FirmwareState,
    MirrorState,
    build_apps_page,
    build_backup_page,
    build_firmware_page,
    build_mirror_page,
)
from qt_material import IconBadge, build_app_icon, qss_for, ui_font, with_alpha
from qt_mirror_controls import MirrorControlPanel
from qt_pages import (
    build_device_page,
    build_empty_page,
    build_issue_page,
    build_loading_page,
    build_permission_page,
    build_settings_page,
    build_status_page,
)
from qt_widgets import (
    CardSurface,
    FilledButton,
    Header,
    NavigationRail,
    PageHost,
    make_label,
)
from theme_tokens import LoadedTheme, load_theme
from locales import (
    tr,
    get_current_language,
    set_current_language,
    load_app_config,
    save_app_config,
)

# Automatically prioritize bundled 'tools' directory for standalone ADB/Fastboot
_TOOLS_DIR = Path(__file__).resolve().parent / "tools"
if _TOOLS_DIR.is_dir():
    os.environ["PATH"] = str(_TOOLS_DIR) + os.pathsep + os.environ.get("PATH", "")

LOGGER = logging.getLogger("pixel_tools")

TAB_TITLES = (
    (
        tr("tab_0_title", "Информация об устройстве"),
        tr("tab_0_subtitle", "Данные считываются напрямую через ADB или Fastboot"),
    ),
    (tr("tab_1_title", "Прошивка"), tr("tab_1_subtitle", "Сборка, OTA sideload и режимы загрузки")),
    (tr("tab_2_title", "Приложения"), tr("tab_2_subtitle", "Установленные пакеты, APK и разрешения")),
    (tr("tab_3_title", "Бэкап"), tr("tab_3_subtitle", "Резервные копии данных и медиа")),
    (tr("tab_4_title", "Трансляция"), tr("tab_4_subtitle", "Трансляция экрана Pixel и быстрое управление в реальном времени")),
    (tr("tab_5_title", "Настройки"), tr("tab_5_subtitle", "Параметры приложения, язык интерфейса и внешний вид")),
)


def get_tab_titles() -> tuple[tuple[str, str], ...]:
    return (
        (tr("tab_0_title"), tr("tab_0_subtitle")),
        (tr("tab_1_title"), tr("tab_1_subtitle")),
        (tr("tab_2_title"), tr("tab_2_subtitle")),
        (tr("tab_3_title"), tr("tab_3_subtitle")),
        (tr("tab_4_title"), tr("tab_4_subtitle")),
        (tr("tab_5_title"), tr("tab_5_subtitle")),
    )


VALID_APPEARANCES = ("system", "light", "dark")


@dataclass
class _TaskRequest:
    name: str
    tab: int
    work: Callable[[Callable[[str], None]], object]
    done: Callable[[object], None]


def system_appearance() -> str:
    """Return the current Windows/Qt light or dark preference."""
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
        if scheme == Qt.ColorScheme.Light:
            return "light"
    except (AttributeError, RuntimeError):
        pass
    window = QGuiApplication.palette().color(QPalette.ColorRole.Window)
    return "dark" if window.lightness() < 128 else "light"


def resolve_appearance(mode: str) -> str:
    if mode in ("light", "dark"):
        return mode
    return system_appearance()


class SettingsDialog(QDialog):
    """Material 3 Expressive settings dialog."""

    def __init__(
        self,
        parent: "PixelToolsWindow",
        palette: Mapping[str, str],
        *,
        current_appearance: str,
        auto_detect: bool,
        on_appearance_change: Callable[[str], None],
        on_auto_detect_change: Callable[[bool], None],
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self.setWindowTitle(tr("settings_title", "Настройки"))
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setStyleSheet(
            f"QDialog {{ background-color: {palette.get('surface', '#FEF7FF')}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        # Header with icon and title
        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        badge = IconBadge(
            "settings",
            size=40,
            radius=12,
            background=palette.get("primary_container", "#EADDFF"),
            foreground=palette.get("on_primary_container", "#21005D"),
            icon_size=20,
        )
        header_row.addWidget(badge)
        header_row.addWidget(
            make_label(
                tr("settings_title", "Настройки Pixel Tools"),
                size=18,
                weight=QFont.Weight.Bold,
                color=palette.get("on_surface", "#1D1B20"),
                display=True,
            )
        )
        header_row.addStretch(1)
        layout.addLayout(header_row)

        # Section 1: Appearance
        layout.addWidget(
            make_label(
                tr("settings_section_theme", "ТЕМА ОФОРМЛЕНИЯ"),
                size=10.5,
                weight=QFont.Weight.ExtraBold,
                color=palette.get("primary", "#6750A4"),
                letter_spacing=1.8,
                display=True,
            )
        )

        theme_card = CardSurface(palette=palette, radius=18, shadow=False)
        theme_layout = QHBoxLayout(theme_card)
        theme_layout.setContentsMargins(12, 10, 12, 10)
        theme_layout.setSpacing(8)

        for mode_key, mode_title in (
            ("system", tr("settings_theme_system", "Системная")),
            ("light", tr("settings_theme_light", "Светлая")),
            ("dark", tr("settings_theme_dark", "Тёмная")),
        ):
            btn = FilledButton(
                mode_title,
                palette=palette,
                parent=self,
            )
            btn.clicked.connect(
                lambda checked=False, m=mode_key: on_appearance_change(m)
            )
            theme_layout.addWidget(btn, 1)
        layout.addWidget(theme_card)

        # Section 2: Platform tools status
        layout.addWidget(
            make_label(
                tr("settings_section_tools", "ПЛАТФОРМА И ИНСТРУМЕНТЫ"),
                size=10.5,
                weight=QFont.Weight.ExtraBold,
                color=palette.get("primary", "#6750A4"),
                letter_spacing=1.8,
                display=True,
            )
        )

        tools_card = CardSurface(palette=palette, radius=18, shadow=False)
        tools_layout = QVBoxLayout(tools_card)
        tools_layout.setContentsMargins(16, 12, 16, 12)
        tools_layout.setSpacing(6)

        adb_path = resolve_tool_path("adb")
        is_bundled = "tools" in adb_path.lower()
        tools_layout.addWidget(
            make_label(
                "ADB: "
                + (
                    tr("settings_adb_bundled", "Встроенный (папка tools/)")
                    if is_bundled
                    else tr("settings_adb_system", "Системный PATH")
                ),
                size=12,
                weight=QFont.Weight.Medium,
                color=palette.get("on_surface", "#1D1B20"),
            )
        )
        tools_layout.addWidget(
            make_label(
                adb_path,
                size=10.5,
                color=palette.get("on_surface_variant", "#49454F"),
            )
        )
        layout.addWidget(tools_card)

        # Section 3: Auto-detection
        layout.addWidget(
            make_label(
                tr("settings_section_auto", "АВТОМАТИЧЕСКИЙ ОПРОС"),
                size=10.5,
                weight=QFont.Weight.ExtraBold,
                color=palette.get("primary", "#6750A4"),
                letter_spacing=1.8,
                display=True,
            )
        )

        auto_card = CardSurface(palette=palette, radius=18, shadow=False)
        auto_layout = QHBoxLayout(auto_card)
        auto_layout.setContentsMargins(16, 12, 16, 12)
        auto_layout.setSpacing(12)

        auto_label_layout = QVBoxLayout()
        auto_label_layout.setSpacing(2)
        auto_label_layout.addWidget(
            make_label(
                tr("settings_auto_title", "Автопроверка подключения"),
                size=12.5,
                weight=QFont.Weight.Bold,
                color=palette.get("on_surface", "#1D1B20"),
            )
        )
        auto_label_layout.addWidget(
            make_label(
                tr("settings_auto_desc", "Периодический опрос USB (каждые 1 сек)"),
                size=11,
                color=palette.get("on_surface_variant", "#49454F"),
            )
        )
        auto_layout.addLayout(auto_label_layout, 1)

        auto_btn = FilledButton(
            tr("settings_auto_enabled", "Включено") if auto_detect else tr("settings_auto_disabled", "Отключено"),
            palette=palette,
            icon="check" if auto_detect else "close",
            parent=self,
        )

        def toggle_auto() -> None:
            new_val = not auto_detect
            on_auto_detect_change(new_val)
            self.accept()

        auto_btn.clicked.connect(toggle_auto)
        auto_layout.addWidget(auto_btn)
        layout.addWidget(auto_card)

        # Section 4: About info
        about_card = CardSurface(palette=palette, radius=18, shadow=False)
        about_layout = QVBoxLayout(about_card)
        about_layout.setContentsMargins(16, 12, 16, 12)
        about_layout.setSpacing(4)
        about_layout.addWidget(
            make_label(
                "Pixel Tools · Standalone v1.1",
                size=12,
                weight=QFont.Weight.Bold,
                color=palette.get("primary", "#6750A4"),
            )
        )
        about_layout.addWidget(
            make_label(
                tr("settings_about_desc", "Интерфейс: Material 3 Expressive (PySide6 / QPainter)\nПоддерживаемые устройства: Google Pixel 1-9, Pro, Fold, Tablet"),
                size=11,
                color=palette.get("on_surface_variant", "#49454F"),
            )
        )
        layout.addWidget(about_card)

        # Close button
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = FilledButton(tr("hdr_close", "Закрыть"), palette=palette, parent=self)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)


class RoundedWindowRoot(QWidget):
    def __init__(
        self, palette: Mapping[str, str], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        win = self.window()
        is_max = win is not None and win.isMaximized()
        r = 0.0 if is_max else 20.0
        painter.setBrush(QColor(self._palette.get("surface", "#FEF7FF")))
        painter.setPen(
            QPen(
                with_alpha(self._palette.get("outline_variant", "#CAC4D0"), 140),
                1.0,
            )
        )
        painter.drawRoundedRect(rect, r, r)


class CornerHighlightOverlay(QWidget):
    """Compact corner overlay painting subtle glow and accent arc when hovering window corners."""

    CORNER_SIZE = 60

    def __init__(
        self, palette: Mapping[str, str], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self._target_corner: Optional[str] = None
        self._active_corner: Optional[str] = None
        self._opacity: float = 0.0

        self.setFixedSize(self.CORNER_SIZE, self.CORNER_SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.hide()

        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._on_anim_value)
        self._anim.finished.connect(self._on_anim_finished)

    def reposition(self, corner: str, win_width: int, win_height: int) -> None:
        cs = self.CORNER_SIZE
        if corner == "top_left":
            self.move(0, 0)
        elif corner == "top_right":
            self.move(win_width - cs, 0)
        elif corner == "bottom_right":
            self.move(win_width - cs, win_height - cs)
        elif corner == "bottom_left":
            self.move(0, win_height - cs)

    def hide_immediately(self) -> None:
        self._anim.stop()
        self._target_corner = None
        self._active_corner = None
        self._opacity = 0.0
        self.hide()

    def set_palette(self, palette: Mapping[str, str]) -> None:
        self._palette = palette
        if self._opacity > 0.001 and self.isVisible():
            self.update()

    def set_active_corner(self, corner: Optional[str]) -> None:
        if corner == self._target_corner:
            return
        self._target_corner = corner

        if corner is not None:
            # Appearing smoothly and slightly slowly
            self._active_corner = corner
            win = self.window()
            if win is not None:
                self.reposition(corner, win.width(), win.height())
            self.show()
            self._anim.stop()
            self._anim.setDuration(360)
            self._anim.setStartValue(self._opacity)
            self._anim.setEndValue(1.0)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.start()
        else:
            # Disappearing smoothly when cursor is moved away
            self._anim.stop()
            if self._opacity <= 0.001:
                self._active_corner = None
                self._opacity = 0.0
                self.hide()
            else:
                self._anim.setDuration(260)
                self._anim.setStartValue(self._opacity)
                self._anim.setEndValue(0.0)
                self._anim.setEasingCurve(QEasingCurve.Type.OutQuad)
                self._anim.start()

    def _on_anim_value(self, value: object) -> None:
        self._opacity = float(value)  # type: ignore[arg-type]
        if self._opacity <= 0.001 and self._target_corner is None:
            self.hide()
        elif not self.isVisible() and self._opacity > 0.001:
            self.show()
        if self.isVisible():
            self.update()

    def _on_anim_finished(self) -> None:
        if self._target_corner is None and self._opacity <= 0.001:
            self._active_corner = None
            self._opacity = 0.0
            self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._active_corner or self._opacity <= 0.001:
            return
        win = self.window()
        if win is not None and win.isMaximized():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        cw = float(self.CORNER_SIZE)
        ch = float(self.CORNER_SIZE)
        win_w = float(win.width()) if win is not None else cw
        win_h = float(win.height()) if win is not None else ch
        primary = self._palette.get("primary", "#6750A4")
        op = max(0.0, min(1.0, self._opacity))

        clip_path = QPainterPath()
        if self._active_corner == "top_left":
            win_rect = QRectF(0.5, 0.5, win_w - 1.0, win_h - 1.0)
        elif self._active_corner == "top_right":
            win_rect = QRectF(cw - win_w + 0.5, 0.5, win_w - 1.0, win_h - 1.0)
        elif self._active_corner == "bottom_right":
            win_rect = QRectF(cw - win_w + 0.5, ch - win_h + 0.5, win_w - 1.0, win_h - 1.0)
        elif self._active_corner == "bottom_left":
            win_rect = QRectF(0.5, ch - win_h + 0.5, win_w - 1.0, win_h - 1.0)
        else:
            win_rect = QRectF(0.5, 0.5, cw - 1.0, ch - 1.0)

        clip_path.addRoundedRect(win_rect, 20.0, 20.0)
        painter.setClipPath(clip_path)

        path = QPainterPath()
        grad: Optional[QRadialGradient] = None

        if self._active_corner == "top_left":
            path.moveTo(0.5, 36.0)
            path.lineTo(0.5, 20.5)
            path.arcTo(QRectF(0.5, 0.5, 40.0, 40.0), 180.0, -90.0)
            path.lineTo(36.0, 0.5)
            grad = QRadialGradient(0.0, 0.0, 56.0)
        elif self._active_corner == "top_right":
            path.moveTo(cw - 36.0, 0.5)
            path.lineTo(cw - 20.5, 0.5)
            path.arcTo(QRectF(cw - 40.5, 0.5, 40.0, 40.0), 90.0, -90.0)
            path.lineTo(cw - 0.5, 36.0)
            grad = QRadialGradient(cw, 0.0, 56.0)
        elif self._active_corner == "bottom_right":
            path.moveTo(cw - 0.5, ch - 36.0)
            path.lineTo(cw - 0.5, ch - 20.5)
            path.arcTo(QRectF(cw - 40.5, ch - 40.5, 40.0, 40.0), 0.0, -90.0)
            path.lineTo(cw - 36.0, ch - 0.5)
            grad = QRadialGradient(cw, ch, 56.0)
        elif self._active_corner == "bottom_left":
            path.moveTo(36.0, ch - 0.5)
            path.lineTo(20.5, ch - 0.5)
            path.arcTo(QRectF(0.5, ch - 40.5, 40.0, 40.0), 270.0, -90.0)
            path.lineTo(0.5, ch - 36.0)
            grad = QRadialGradient(0.0, ch, 56.0)

        if grad is not None:
            grad.setColorAt(0.0, QColor(with_alpha(primary, int(75 * op))))
            grad.setColorAt(0.5, QColor(with_alpha(primary, int(26 * op))))
            grad.setColorAt(1.0, QColor(with_alpha(primary, 0)))
            painter.fillPath(clip_path, grad)

        # Soft outer glow
        painter.setPen(
            QPen(
                QColor(with_alpha(primary, int(95 * op))),
                4.0,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        painter.drawPath(path)

        # Crisp inner highlight arc
        painter.setPen(
            QPen(
                QColor(with_alpha(primary, int(230 * op))),
                2.0,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        painter.drawPath(path)


class PixelToolsWindow(QMainWindow):
    """Main window, detection controller and responsive shell."""

    app_icon_ready = Signal(str, str)

    def __init__(
        self,
        theme: LoadedTheme,
        *,
        appearance: str = "system",
        auto_refresh: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._appearance_mode = (
            appearance if appearance in VALID_APPEARANCES else "system"
        )
        self._effective_appearance = resolve_appearance(self._appearance_mode)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

        config = load_app_config()
        saved_lang = config.get("language", "en")
        self._language = saved_lang if saved_lang in ("ru", "en") else "en"
        set_current_language(self._language)
        if "appearance" in config and config["appearance"] in VALID_APPEARANCES and appearance == "system":
            self._appearance_mode = config["appearance"]
            self._effective_appearance = resolve_appearance(self._appearance_mode)
        self._previous_tab_index = 0

        self._info: Optional[PixelDeviceInfo] = None
        self._busy = False
        self._tab_index = 0
        self._selected_serial: Optional[str] = None
        self._last_updated: Optional[str] = None

        # App icon caching and async worker queue
        self._icon_badges: dict[str, list[object]] = {}
        self._icon_queue: queue.Queue[str] = queue.Queue()
        self._icon_queued_set: set[str] = set()
        self._icon_workers_started = False
        self.app_icon_ready.connect(self._on_app_icon_ready)

        # Feature-tab state survives theme rebuilds and async reloads.
        self._firmware_state = FirmwareState()
        self._apps_state = AppsState()
        self._backup_state = BackupState()
        self._mirror_state = MirrorState(scrcpy_ready=is_scrcpy_available())
        self._mirror_process: Optional[subprocess.Popen] = None
        self._mirror_controls: Optional[MirrorControlPanel] = None
        self._mirror_title = ""
        self._mirror_monitor_timer = QTimer(self)
        self._mirror_monitor_timer.setInterval(1000)
        self._mirror_monitor_timer.timeout.connect(self._check_mirror_process)
        self._feature_serial: Optional[str] = None

        # One long ADB/Fastboot operation at a time, executed off the GUI thread.
        self._task: Optional[_TaskRequest] = None
        self._task_token = 0
        self._task_message = ""
        self._task_events: queue.Queue[tuple[int, str, object]] = queue.Queue()
        self._task_timer = QTimer(self)
        self._task_timer.setInterval(90)
        self._task_timer.timeout.connect(self._poll_tasks)

        self._auto_refresh = auto_refresh
        self._auto_started = False
        self._results: queue.Queue[tuple[int, PixelDeviceInfo, bool]] = queue.Queue()
        self._detection_token = 0
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(120)
        self._poll_timer.timeout.connect(self._poll_detection)

        self._auto_detect_timer = QTimer(self)
        self._auto_detect_timer.setInterval(3500)
        self._auto_detect_timer.timeout.connect(self._auto_detect_tick)
        self._auto_detect_enabled = config.get("auto_detect", True) if isinstance(config.get("auto_detect"), bool) else True
        self._detect_thread: Optional[threading.Thread] = None

        self._is_resizing: bool = False
        self._active_corner: Optional[str] = None
        self._corner_watch_timer = QTimer(self)
        self._corner_watch_timer.setInterval(50)
        self._corner_watch_timer.timeout.connect(self._check_corner_hover)
        self._corner_overlay = CornerHighlightOverlay(self._palette(), self)

        self.setWindowTitle("Pixel Tools")
        self.setMinimumSize(780, 520)
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = max(780, min(1240, available.width() - 80))
            height = max(520, min(860, available.height() - 80))
            self.resize(width, height)
            self.move(
                available.center().x() - width // 2,
                available.center().y() - height // 2,
            )
        else:
            self.resize(1240, 860)

        self._build_ui()
        self._update_header()

        if auto_refresh:
            self._body_host.set_page(
                build_loading_page(self._palette()), animate=False
            )
        else:
            self._render_body(animate=False)

        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(
                self._system_scheme_changed
            )
        except (AttributeError, RuntimeError):
            pass

    # ------------------------------------------------------------------
    # Theme and shell
    # ------------------------------------------------------------------

    def _palette(self) -> dict[str, str]:
        if self._effective_appearance == "dark":
            return self._theme.dark
        return self._theme.light

    def _is_dark(self) -> bool:
        return self._effective_appearance == "dark"

    def _source_caption(self) -> str:
        appearance = tr("appearance_dark", "тёмная") if self._is_dark() else tr("appearance_light", "светлая")
        return f"{self._theme.source_label} · {appearance}"

    def _theme_tooltip(self) -> str:
        parts = [
            tr("theme_source_label", "Палитра: {label}", label=self._theme.source_label),
            tr("hdr_theme_dark_mode", "Тёмная тема") if self._is_dark() else tr("hdr_theme_light_mode", "Светлая тема"),
            tr("nav_settings_tooltip", "Настройки и параметры"),
        ]
        if self._theme.warning:
            parts.append(tr(self._theme.warning, self._theme.warning))
        return "\n".join(parts)

    def _source_tooltip(self) -> str:
        if self._theme.warning:
            return tr(self._theme.warning, self._theme.warning)
        return tr("window_appearance_label", "Источник палитры: {label}", label=self._theme.source_label)

    def _build_ui(self) -> None:
        palette = self._palette()
        self.setStyleSheet(qss_for(palette))

        old = self.takeCentralWidget()
        if old is not None:
            old.deleteLater()

        root = RoundedWindowRoot(palette=palette)
        root.setObjectName("Root")
        root.setMouseTracking(True)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._rail = NavigationRail(palette=palette, parent=root)
        self._rail.tab_selected.connect(self._on_tab_selected)
        self._rail.settings_clicked.connect(self._open_settings_dialog)
        self._rail.set_selected(self._tab_index)
        root_layout.addWidget(self._rail)

        content = QWidget()
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self._header = Header(palette=palette, parent=content)
        self._header.refresh_requested.connect(self._on_refresh_clicked)
        self._header.minimize_clicked.connect(self.showMinimized)
        self._header.maximize_clicked.connect(self._toggle_maximize)
        self._header.close_clicked.connect(self.close)
        content_layout.addWidget(self._header)

        self._body_host = PageHost(parent=content)
        content_layout.addWidget(self._body_host, 1)
        root_layout.addWidget(content, 1)

        self._rail.set_theme_tooltip(tr("nav_settings_tooltip"))
        self.setCentralWidget(root)
        self.setWindowIcon(build_app_icon(palette))
        if hasattr(self, "_corner_overlay"):
            self._corner_overlay.set_palette(palette)
            self._corner_overlay.raise_()
        self._update_responsive()

    def _toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        if hasattr(self, "_header"):
            self._header.update_window_state(self.isMaximized())
        if self.centralWidget() is not None:
            self.centralWidget().update()

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == event.Type.WindowStateChange:
            if hasattr(self, "_header"):
                self._header.update_window_state(self.isMaximized())
            if self.centralWidget() is not None:
                self.centralWidget().update()
            if hasattr(self, "_corner_overlay"):
                if self.isMaximized():
                    self._corner_overlay.hide_immediately()
                    self._active_corner = None
                elif self._active_corner:
                    self._corner_overlay.reposition(
                        self._active_corner, self.width(), self.height()
                    )
        super().changeEvent(event)

    def nativeEvent(self, event_type, message):
        if sys.platform == "win32":
            try:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0231:  # WM_ENTERSIZEMOVE
                    self._is_resizing = True
                    self._cancel_corner_highlight()
                elif msg.message == 0x0232:  # WM_EXITSIZEMOVE
                    self._is_resizing = False
                    QTimer.singleShot(80, self._check_corner_hover)
                elif msg.message == 0x00A1:  # WM_NCLBUTTONDOWN
                    # 10..17 are HTLEFT..HTBOTTOMRIGHT resize handles
                    if 10 <= msg.wParam <= 17:
                        self._is_resizing = True
                        self._cancel_corner_highlight()
                elif msg.message in (0x02A2, 0x02A3):  # WM_NCMOUSELEAVE, WM_MOUSELEAVE
                    if not self._is_resizing:
                        self._set_active_corner(None)
                elif msg.message == 0x0084:  # WM_NCHITTEST
                    if not self.isMaximized():
                        x = ctypes.c_short(msg.lParam & 0xFFFF).value
                        y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                        pos = self.mapFromGlobal(QPoint(x, y))
                        corner = self._detect_corner(pos)
                        if not self._is_resizing:
                            self._set_active_corner(corner)

                        if corner == "top_left":
                            return True, 13  # HTTOPLEFT
                        if corner == "top_right":
                            return True, 14  # HTTOPRIGHT
                        if corner == "bottom_left":
                            return True, 16  # HTBOTTOMLEFT
                        if corner == "bottom_right":
                            return True, 17  # HTBOTTOMRIGHT

                        px, py = pos.x(), pos.y()
                        w, h = self.width(), self.height()
                        margin = 8
                        left = px <= margin
                        right = px >= w - margin
                        top = py <= margin
                        bottom = py >= h - margin

                        if left:
                            return True, 10  # HTLEFT
                        if right:
                            return True, 11  # HTRIGHT
                        if top:
                            return True, 12  # HTTOP
                        if bottom:
                            return True, 15  # HTBOTTOM
                    else:
                        if not self._is_resizing:
                            self._set_active_corner(None)
            except Exception:
                pass
        try:
            return super().nativeEvent(event_type, message)
        except Exception:
            return False, 0

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and not self.isMaximized():
            pos = event.position().toPoint()
            margin = 8
            w, h = self.width(), self.height()
            edges = Qt.Edge(0)
            if pos.x() <= margin:
                edges |= Qt.Edge.LeftEdge
            elif pos.x() >= w - margin:
                edges |= Qt.Edge.RightEdge
            if pos.y() <= margin:
                edges |= Qt.Edge.TopEdge
            elif pos.y() >= h - margin:
                edges |= Qt.Edge.BottomEdge
            if edges:
                self._is_resizing = True
                self._cancel_corner_highlight()
                wh = self.windowHandle()
                if wh is not None:
                    wh.startSystemResize(edges)
                    return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._is_resizing:
            self._is_resizing = False
            QTimer.singleShot(80, self._check_corner_hover)
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._is_resizing:
            super().mouseMoveEvent(event)
            return
        if not self.isMaximized():
            pos = event.position().toPoint()
            corner = self._detect_corner(pos)
            self._set_active_corner(corner)

            if corner in ("top_left", "bottom_right"):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif corner in ("top_right", "bottom_left"):
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            else:
                margin = 8
                w, h = self.width(), self.height()
                left = pos.x() <= margin
                right = pos.x() >= w - margin
                top = pos.y() <= margin
                bottom = pos.y() >= h - margin
                if left or right:
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                elif top or bottom:
                    self.setCursor(Qt.CursorShape.SizeVerCursor)
                else:
                    self.unsetCursor()
        else:
            self._set_active_corner(None)
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if not self._is_resizing:
            self._set_active_corner(None)
        super().leaveEvent(event)

    def _detect_corner(self, pos: QPoint) -> Optional[str]:
        if self.isMaximized():
            return None
        px, py = pos.x(), pos.y()
        w, h = self.width(), self.height()
        if px < 0 or py < 0 or px > w or py > h:
            return None
        corner_margin = 22
        c_left = px <= corner_margin
        c_right = px >= w - corner_margin
        c_top = py <= corner_margin
        c_bottom = py >= h - corner_margin

        if c_left and c_top:
            return "top_left"
        if c_right and c_top:
            return "top_right"
        if c_left and c_bottom:
            return "bottom_left"
        if c_right and c_bottom:
            return "bottom_right"
        return None

    def _check_corner_hover(self) -> None:
        if self._is_resizing or self.isMaximized() or not self.isActiveWindow():
            if not self._is_resizing:
                self._set_active_corner(None)
            return
        cur_pos = self.mapFromGlobal(QCursor.pos())
        corner = self._detect_corner(cur_pos)
        if corner != self._active_corner:
            self._set_active_corner(corner)

    def _set_active_corner(self, corner: Optional[str]) -> None:
        if self._is_resizing:
            corner = None
        if self._active_corner == corner:
            return
        self._active_corner = corner
        if hasattr(self, "_corner_overlay"):
            if corner is not None and not self._is_resizing:
                self._corner_overlay.reposition(corner, self.width(), self.height())
            self._corner_overlay.set_active_corner(corner)
        if corner is not None and not self._is_resizing:
            if hasattr(self, "_corner_watch_timer") and not self._corner_watch_timer.isActive():
                self._corner_watch_timer.start()
        else:
            if hasattr(self, "_corner_watch_timer") and self._corner_watch_timer.isActive():
                self._corner_watch_timer.stop()

    def _cancel_corner_highlight(self) -> None:
        self._active_corner = None
        if hasattr(self, "_corner_watch_timer"):
            self._corner_watch_timer.stop()
        if hasattr(self, "_corner_overlay"):
            self._corner_overlay.hide_immediately()

    def _open_settings_dialog(self) -> None:
        self._on_tab_selected(5)

    def set_language(self, lang: str) -> None:
        """Switch language (ru/en), persist preference, and rebuild interface."""
        if lang not in ("ru", "en"):
            lang = "en"
        if lang == self._language:
            return
        self._language = lang
        set_current_language(lang)
        save_app_config({
            "language": self._language,
            "appearance": self._appearance_mode,
            "auto_detect": self._auto_detect_enabled,
        })
        self._rebuild_ui()

    def _set_auto_detect(self, enabled: bool) -> None:
        self._auto_detect_enabled = enabled
        if enabled:
            self._auto_detect_timer.start()
        else:
            self._auto_detect_timer.stop()
        save_app_config({
            "language": self._language,
            "appearance": self._appearance_mode,
            "auto_detect": self._auto_detect_enabled,
        })
        if self._tab_index == 5:
            self._render_body(animate=False)

    def _rebuild_ui(self) -> None:
        self._build_ui()
        self._rail.set_selected(self._tab_index)
        self._update_header()
        self._refresh_mirror_controls_theme()
        if self._busy and self._info is None:
            self._body_host.set_page(
                build_loading_page(self._palette()), animate=False
            )
        else:
            self._render_body(animate=False)

    def set_appearance_mode(self, mode: str) -> None:
        """Switch system/light/dark and rebuild the painted interface."""
        mode = mode if mode in VALID_APPEARANCES else "system"
        effective = resolve_appearance(mode)
        mode_changed = mode != self._appearance_mode
        appearance_changed = effective != self._effective_appearance
        self._appearance_mode = mode
        self._effective_appearance = effective
        save_app_config({
            "language": self._language,
            "appearance": self._appearance_mode,
            "auto_detect": self._auto_detect_enabled,
        })
        if appearance_changed:
            self._rebuild_ui()
        elif mode_changed:
            self._header.set_source(self._source_caption(), self._source_tooltip())
            self._rail.set_theme_tooltip(self._theme_tooltip())
            if self._tab_index == 5:
                self._render_body(animate=False)

    def _toggle_theme(self) -> None:
        self.set_appearance_mode("light" if self._is_dark() else "dark")

    def _system_scheme_changed(self, scheme=None) -> None:
        if self._appearance_mode != "system":
            return
        effective = resolve_appearance("system")
        if effective != self._effective_appearance:
            self._effective_appearance = effective
            self._rebuild_ui()

    # ------------------------------------------------------------------
    # Detection lifecycle
    # ------------------------------------------------------------------

    def refresh(self, *, preserve_selection: bool = True) -> None:
        if self._busy:
            return
        if not preserve_selection:
            self._selected_serial = None

        while True:
            try:
                self._results.get_nowait()
            except queue.Empty:
                break

        self._busy = True
        if self._info is None:
            self._body_host.set_page(
                build_loading_page(self._palette()), animate=False
            )
        self._update_header()

        self._detection_token += 1
        token = self._detection_token
        serial = self._selected_serial
        threading.Thread(
            target=self._detection_job,
            args=(serial, token),
            name="pixel-tools-detect",
            daemon=True,
        ).start()
        self._poll_timer.start()

    def _detection_job(self, serial: Optional[str], token: int) -> None:
        def progress(preview: PixelDeviceInfo) -> None:
            # Предварительный результат: телефон уже найден, детали ещё
            # читаются. UI покажет его на ближайшем тике QTimer.
            self._results.put((token, preview, False))

        try:
            info = detect_pixel_device(
                serial, timeout=10.0, on_progress=progress
            )
        except Exception as exc:  # the backend should never raise; UI stays alive
            LOGGER.exception("Failed to detect device")
            info = PixelDeviceInfo(
                error=f"Device detection failed: {exc}"
            )
        self._results.put((token, info, True))

    def _poll_detection(self) -> None:
        try:
            token, info, final = self._results.get_nowait()
        except queue.Empty:
            return
        if token != self._detection_token:
            # Результат устаревшей проверки; актуальная продолжает работать.
            return
        if final:
            self._poll_timer.stop()

        was_busy = self._busy
        self._busy = not final

        old_info = self._info
        self._info = info
        self._sync_feature_serial(info)
        if final:
            self._last_updated = datetime.now().strftime("%H:%M:%S")

        status_changed = (
            old_info is None
            or old_info.detected != info.detected
            or old_info.serial != info.serial
            or old_info.state != info.state
            or old_info.mode != info.mode
            or old_info.battery_cycles != info.battery_cycles
            or old_info.battery_level != info.battery_level
        )

        self._update_header()

        if was_busy or status_changed or self._tab_index == 0:
            animate = (
                was_busy
                or (old_info is None)
                or (old_info.detected != info.detected)
                or (old_info.is_partial != info.is_partial)
            )
            self._render_body(animate=animate)

    def _auto_detect_tick(self) -> None:
        if not self._auto_detect_enabled:
            return
        if self._busy or self._task is not None:
            return
        if self._detect_thread is not None and self._detect_thread.is_alive():
            return

        serial = self._selected_serial
        self._detection_token += 1
        token = self._detection_token

        def job() -> None:
            try:
                info = detect_pixel_device(serial, timeout=1)
            except Exception:
                return
            self._results.put((token, info, True))

        self._detect_thread = threading.Thread(
            target=job, name="pixel-tools-auto-probe", daemon=True
        )
        self._detect_thread.start()
        if not self._poll_timer.isActive():
            self._poll_timer.start()

    def _on_refresh_clicked(self) -> None:
        self.refresh(preserve_selection=True)

    def _on_retry_clicked(self) -> None:
        self.refresh(preserve_selection=False)

    def _on_select_device(self, serial: str) -> None:
        if self._busy:
            return
        self._selected_serial = serial
        self.refresh(preserve_selection=True)

    # ------------------------------------------------------------------
    # Header state
    # ------------------------------------------------------------------

    def _update_header(self) -> None:
        if not hasattr(self, "_header"):
            return
        titles = get_tab_titles()
        title, subtitle = titles[self._tab_index]
        info = self._info

        if self._tab_index == 0:
            if self._busy:
                if info is not None and info.detected:
                    parts = [tr(info.display_name)]
                    mode = self._mode_label(info)
                    if mode:
                        parts.append(mode)
                    if info.state:
                        parts.append(str(info.state))
                    parts.append(tr("hdr_reading"))
                    subtitle = " · ".join(parts)
                else:
                    subtitle = tr("hdr_checking")
            elif info is not None and info.detected:
                parts = [tr(info.display_name)]
                mode = self._mode_label(info)
                if mode:
                    parts.append(mode)
                if info.state:
                    parts.append(str(info.state))
                if self._last_updated:
                    parts.append(tr("hdr_updated", time=self._last_updated))
                subtitle = " · ".join(parts)
            elif info is not None and info.error:
                if info.available_devices:
                    subtitle = tr("hdr_multiple_devices")
                else:
                    subtitle = tr("hdr_no_device")
            elif info is None:
                subtitle = tr("hdr_checking")
        elif self._tab_index == 5:
            subtitle = tr("tab_5_subtitle")
        else:
            parts = []
            if info is not None and info.detected:
                parts.append(tr(info.display_name))
                mode = self._mode_label(info)
                if mode:
                    parts.append(mode)
                if info.state:
                    parts.append(str(info.state))
            task = self._task
            if task is not None and task.tab == self._tab_index:
                parts.append(f"{task.name.lower()}…")
                if self._task_message:
                    parts.append(self._task_message)
            if parts:
                subtitle = " · ".join(parts)
            else:
                subtitle = titles[self._tab_index][1]

        self._header.set_title(title)
        self._header.set_subtitle(subtitle)
        self._header.set_source(self._source_caption(), self._source_tooltip())
        self._header.set_busy(self._busy or self._task is not None)
        self._header.set_refresh_visible(self._tab_index == 0)
        self._header.set_refresh_enabled(not self._busy and self._task is None)
        self._update_status_chip()
        self._update_responsive()

    def _update_status_chip(self) -> None:
        palette = self._palette()
        info = self._info
        if self._busy and info is None:
            self._header.set_status(
                tr("status_searching", "Поиск устройства"),
                background=palette.get("surface_container", "#F3EDF7"),
                foreground=palette.get("on_surface_variant", "#49454F"),
                dot=palette.get("primary", "#6750A4"),
            )
        elif info is None:
            self._header.set_status(
                tr("status_waiting", "Ожидание"),
                background=palette.get("surface_container", "#F3EDF7"),
                foreground=palette.get("on_surface_variant", "#49454F"),
                dot=palette.get("outline", "#79747E"),
            )
        elif not info.detected:
            if info.available_devices:
                self._header.set_status(
                    tr("status_select", "Выберите устройство"),
                    background=palette.get("secondary_container", "#E8DEF8"),
                    foreground=palette.get("on_secondary_container", "#1D192B"),
                    dot=palette.get("primary", "#6750A4"),
                )
            else:
                self._header.set_status(
                    tr("status_not_connected", "Не подключено"),
                    background=palette.get("error_container", "#F9DEDC"),
                    foreground=palette.get("on_error_container", "#410E0B"),
                    dot=palette.get("error", "#B3261E"),
                )
        elif (info.state or "").lower() == "unauthorized":
            self._header.set_status(
                tr("status_unauthorized", "Ожидает разрешения"),
                background=palette.get("tertiary_container", "#FFD8E4"),
                foreground=palette.get("on_tertiary_container", "#31111D"),
                dot=palette.get("tertiary", "#7D5260"),
            )
        else:
            mode = self._mode_label(info) or "USB"
            state = info.state or "device"
            self._header.set_status(
                f"{mode} · {state}",
                background=palette.get("primary_container", "#EADDFF"),
                foreground=palette.get("on_primary_container", "#21005D"),
                dot=palette.get("primary", "#6750A4"),
            )

    def _update_responsive(self) -> None:
        if not hasattr(self, "_header"):
            return
        width = self.width()
        self._header.adapt_to_width(width)
        if hasattr(self, "_rail") and hasattr(self._rail, "adapt_to_height"):
            self._rail.adapt_to_height(self.height())

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
    # Body rendering
    # ------------------------------------------------------------------

    def _render_body(self, *, animate: bool = True) -> None:
        if not hasattr(self, "_body_host"):
            return
        palette = self._palette()

        if self._tab_index == 5:
            page = self._build_settings_page()
        elif self._tab_index != 0:
            page = self._build_feature_page()
        else:
            info = self._info
            if self._busy and info is None:
                page = build_loading_page(palette)
            elif info is None:
                page = build_empty_page(
                    palette,
                    None,
                    on_retry=self._on_retry_clicked,
                    on_select=self._on_select_device,
                )
            elif not info.detected:
                page = build_empty_page(
                    palette,
                    info,
                    on_retry=self._on_retry_clicked,
                    on_select=self._on_select_device,
                )
            else:
                state = (info.state or "").lower()
                if state == "unauthorized":
                    page = build_permission_page(
                        palette, info, on_retry=self._on_retry_clicked
                    )
                elif state in {
                    "offline",
                    "no permissions",
                    "authorizing",
                    "sideload",
                }:
                    page = build_issue_page(
                        palette, info, on_retry=self._on_retry_clicked
                    )
                else:
                    page = build_device_page(palette, info)

        self._body_host.set_page(page, animate=animate)
        self._current_page = page
        if self._tab_index != 0 and self._tab_index != 5:
            self._maybe_auto_load()

    def _build_settings_page(self) -> QWidget:
        return build_settings_page(
            self._palette(),
            current_appearance=self._appearance_mode,
            current_language=self._language,
            auto_detect=self._auto_detect_enabled,
            on_appearance_change=self.set_appearance_mode,
            on_language_change=self.set_language,
            on_auto_detect_change=self._set_auto_detect,
        )

    # ------------------------------------------------------------------
    # Feature tabs
    # ------------------------------------------------------------------

    @staticmethod
    def _adb_ready(info: Optional[PixelDeviceInfo]) -> bool:
        return bool(
            info is not None
            and info.detected
            and (info.mode or "").lower() == "adb"
            and (info.state or "").lower() == "device"
        )

    @staticmethod
    def _firmware_ready(info: Optional[PixelDeviceInfo]) -> bool:
        if info is None or not info.detected:
            return False
        mode = (info.mode or "").lower()
        state = (info.state or "").lower()
        if mode == "fastboot":
            return True
        return mode == "adb" and state in {"device", "sideload", "recovery"}

    def _build_feature_page(self) -> QWidget:
        palette = self._palette()
        info = self._info
        controller = self._feature_controller()

        if self._tab_index == 1 and self._firmware_ready(info):
            return build_firmware_page(
                palette, info, self._firmware_state, controller
            )
        if self._tab_index == 2 and self._adb_ready(info):
            return build_apps_page(palette, info, self._apps_state, controller)
        if self._tab_index == 3 and self._adb_ready(info):
            if not self._backup_state.destination:
                self._backup_state.destination = self._suggest_backup_dir()
            return build_backup_page(
                palette, info, self._backup_state, controller
            )
        if self._tab_index == 4 and self._adb_ready(info):
            self._mirror_state.scrcpy_ready = is_scrcpy_available()
            return build_mirror_page(
                palette, info, self._mirror_state, controller
            )
        return self._feature_gate_page(self._tab_index)

    def _feature_gate_page(self, tab: int) -> QWidget:
        palette = self._palette()
        info = self._info
        titles = {
            1: tr("nav_firmware", "Прошивка"),
            2: tr("nav_apps", "Приложения"),
            3: tr("nav_backup", "Бэкап"),
            4: tr("nav_screen", "Трансляция"),
        }
        title = titles.get(tab, tr("nav_info", "Информация"))
        if info is None or not info.detected:
            return build_status_page(
                palette,
                icon="usb",
                icon_background="primary_container",
                icon_foreground="on_primary_container",
                title=tr("gate_no_device_title", "Устройство не подключено"),
                description=tr(
                    "gate_no_device_desc",
                    "Для этой вкладки нужен Google Pixel с включённой "
                    "отладкой по USB. Подключите устройство и повторите поиск.",
                ),
                button_text=tr("dev_check_again", "Проверить снова"),
                on_retry=self._on_retry_clicked,
                error_text=tr(info.error) if (info is not None and info.error) else None,
            )

        state = (info.state or "").lower()
        mode = (info.mode or "").lower()
        if state == "unauthorized":
            return build_status_page(
                palette,
                icon="developer",
                icon_background="tertiary_container",
                icon_foreground="on_tertiary_container",
                title=tr("gate_unauthorized_title", "Нужно разрешение"),
                description=tr(
                    "gate_unauthorized_desc",
                    "Подтвердите диалог «Разрешить отладку по USB» на Pixel, "
                    "чтобы открыть вкладку.",
                ),
                button_text=tr("dev_check_again", "Проверить снова"),
                on_retry=self._on_retry_clicked,
                serial=info.serial,
            )
        if mode == "fastboot":
            description = tr(
                "gate_fastboot_desc",
                "Устройство находится в Fastboot. Загрузите Android, чтобы "
                "работать с приложениями и резервными копиями.",
            )
        elif state == "sideload":
            description = tr(
                "gate_sideload_desc",
                "Устройство устанавливает обновление. Завершите установку и "
                "загрузите Android, чтобы открыть вкладку.",
            )
        elif state == "recovery":
            description = tr(
                "gate_recovery_desc",
                "Устройство в режиме Recovery. Загрузите Android, чтобы "
                "открыть вкладку.",
            )
        elif state == "offline":
            description = tr(
                "gate_offline_desc",
                "ADB видит устройство, но не может с ним связаться. "
                "Переподключите кабель и повторите поиск.",
            )
        else:
            description = tr(
                "gate_adb_required_desc",
                "Вкладка «{title}» требует доступного устройства по ADB. "
                "Проверьте подключение и разрешение на отладку.",
                title=title,
            )
        return build_status_page(
            palette,
            icon="lock",
            icon_background="error_container",
            icon_foreground="on_error_container",
            title=tr("gate_tab_unavailable_title", "{title} недоступна", title=title),
            description=description,
            button_text=tr("dev_check_again", "Проверить снова"),
            on_retry=self._on_retry_clicked,
            serial=info.serial,
        )

    def _feature_controller(self) -> FeatureController:
        task = self._task
        return FeatureController(
            palette=self._palette(),
            info=self._info,
            busy=task is not None,
            task_tab=task.tab if task is not None else None,
            task_label=task.name if task is not None else "",
            task_message=self._task_message,
            load_build=self._load_build_info,
            reboot=self._request_reboot,
            choose_ota=self._choose_ota,
            install_ota=self._request_ota_install,
            set_active_slot=self._request_set_active_slot,
            open_browser_url=self._open_browser_url,
            load_packages=self._load_packages,
            open_package=self._open_package,
            install_apk=self._request_install_apk,
            uninstall=self._request_uninstall,
            uninstall_system=self._request_uninstall_system,
            restore_system=self._request_restore_system,
            freeze_package=self._request_freeze_package,
            force_stop_package=self._request_force_stop,
            clear_package_data=self._request_clear_package_data,
            launch_package=self._request_launch_package,
            export_apk=self._request_export_apk,
            set_permission=self._request_permission,
            choose_backup_dir=self._choose_backup_dir,
            create_backup=self._request_create_backup,
            choose_restore_dir=self._choose_restore_dir,
            restore_backup=self._request_restore,
            open_folder=self._open_folder,
            confirm=self._confirm,
            start_mirror=self._start_screen_mirror,
            stop_mirror=self._stop_screen_mirror,
            toggle_mirror_option=self._toggle_mirror_option,
            take_screenshot=self._request_screenshot,
            send_key=self._request_send_key,
            install_scrcpy=self._request_install_scrcpy,
            register_icon_badge=self._register_icon_badge,
        )

    def _maybe_auto_load(self) -> None:
        if not self._auto_refresh or self._task is not None:
            return
        info = self._info
        if self._tab_index == 1:
            if self._adb_ready(info) and self._firmware_state.build is None:
                if not self._firmware_state.build_loading:
                    QTimer.singleShot(0, self._load_build_info)
        elif self._tab_index == 2:
            if self._adb_ready(info) and self._apps_state.packages is None:
                if not self._apps_state.loading:
                    QTimer.singleShot(0, self._load_packages)

    def _sync_feature_serial(self, info: Optional[PixelDeviceInfo]) -> None:
        serial = info.serial if info is not None and info.detected else None
        if serial == self._feature_serial:
            return
        self._feature_serial = serial
        self._firmware_state = FirmwareState()
        self._apps_state = AppsState()
        self._backup_state = BackupState()
        self._stop_screen_mirror()
        self._mirror_state = MirrorState(scrcpy_ready=is_scrcpy_available())
        if self._task is not None:
            self._task_token += 1
            self._task = None
            self._task_message = ""
            self._task_timer.stop()

    def _suggest_backup_dir(self) -> str:
        info = self._info
        name = (info.display_name if info is not None else "Pixel") or "Pixel"
        safe = "".join(
            character if character.isalnum() or character in "-_" else "-"
            for character in name.replace(" ", "-")
        )
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        base = Path.home() / "Documents" / "PixelTools"
        return str(base / f"{safe}_{stamp}")

    def _on_tab_selected(self, index: int) -> None:
        index = max(0, min(5, int(index)))
        if index == self._tab_index:
            return
        if self._tab_index != 5:
            self._previous_tab_index = self._tab_index
        self._tab_index = index
        self._rail.set_selected(index)
        self._update_header()
        self._render_body(animate=True)

    # ------------------------------------------------------------------
    # Async feature operations
    # ------------------------------------------------------------------

    def _start_task(
        self,
        name: str,
        work: Callable[[Callable[[str], None]], object],
        done: Callable[[object], None],
        *,
        tab: int,
    ) -> bool:
        if self._task is not None:
            self._set_feature_message(
                tab,
                "warning",
                tr("task_wait_finish", "Дождитесь завершения текущей операции."),
            )
            QTimer.singleShot(0, lambda: self._render_body(animate=False))
            return False

        self._task_token += 1
        task = _TaskRequest(name=name, tab=tab, work=work, done=done)
        self._task = task
        self._task_message = ""
        token = self._task_token
        threading.Thread(
            target=self._task_worker,
            args=(token, task),
            name="pixel-tools-task",
            daemon=True,
        ).start()
        self._task_timer.start()
        self._update_header()
        # Destroying the clicked button from inside its own signal is unsafe,
        # so the busy page is installed on the next event-loop turn.
        QTimer.singleShot(0, lambda: self._render_body(animate=False))
        return True

    def _task_worker(self, token: int, task: _TaskRequest) -> None:
        def report(text: str) -> None:
            self._task_events.put((token, "progress", str(text)))

        try:
            result = task.work(report)
        except Exception as exc:  # a backend failure must never kill the UI
            LOGGER.exception("Failed to execute operation: %s", task.name)
            self._task_events.put(
                (
                    token,
                    "error",
                    tr("task_exec_failed", "Не удалось выполнить операцию «{name}»: {exc}", name=task.name, exc=exc),
                )
            )
        else:
            self._task_events.put((token, "done", result))

    def _poll_tasks(self) -> None:
        finished: Optional[tuple[str, object]] = None
        while True:
            try:
                token, kind, payload = self._task_events.get_nowait()
            except queue.Empty:
                break
            if self._task is None or token != self._task_token:
                continue
            if kind == "progress":
                self._task_message = str(payload)
                page = getattr(self, "_current_page", None)
                if (
                    page is not None
                    and self._task is not None
                    and self._tab_index == self._task.tab
                    and hasattr(page, "set_task_message")
                ):
                    page.set_task_message(self._task_message)
            else:
                finished = (kind, payload)

        if finished is None:
            return
        task = self._task
        if task is None:
            return
        kind, payload = finished
        self._task = None
        self._task_message = ""
        self._task_timer.stop()

        if kind == "error":
            self._set_feature_message(task.tab, "error", str(payload))
            self._reset_feature_loading(task.tab)
        else:
            try:
                task.done(payload)
            except Exception as exc:
                LOGGER.exception("Failed to process operation result")
                self._set_feature_message(
                    task.tab,
                    "error",
                    tr("task_handle_result_failed", "Не удалось обработать результат: {exc}", exc=exc),
                )
                self._reset_feature_loading(task.tab)

        self._update_header()
        if self._tab_index == task.tab:
            self._render_body(animate=False)

    def _set_feature_message(self, tab: int, kind: str, text: str) -> None:
        message = FeatureMessage(text=text, kind=kind)
        if tab == 1:
            self._firmware_state.message = message
        elif tab == 2:
            self._apps_state.message = message
        elif tab == 3:
            self._backup_state.message = message

    def _reset_feature_loading(self, tab: int) -> None:
        if tab == 1:
            self._firmware_state.build_loading = False
        elif tab == 2:
            self._apps_state.loading = False
            self._apps_state.details_loading = False

    # ------------------------------------------------------------------
    # Firmware actions
    # ------------------------------------------------------------------

    def _load_build_info(self) -> None:
        info = self._info
        if not self._adb_ready(info):
            return
        state = self._firmware_state
        if state.build_loading or self._task is not None:
            return
        serial = info.serial
        state.build_loading = True
        state.message = None

        def work(report: Callable[[str], None]) -> object:
            report(tr("task_reading_build_props", "Читаем свойства сборки Android…"))
            return read_build_info(serial)

        def done(result: object) -> None:
            state.build_loading = False
            build = result
            state.build = build  # type: ignore[assignment]
            if getattr(build, "error", None):
                state.message = FeatureMessage(str(build.error), "error")
            elif getattr(build, "warnings", None):
                state.message = FeatureMessage(
                    "\n".join(build.warnings), "warning"
                )

        if not self._start_task(tr("task_check_build", "Проверка сборки"), work, done, tab=1):
            state.build_loading = False

    def _request_reboot(self, target: str) -> None:
        info = self._info
        if info is None or not info.detected or self._task is not None:
            return
        serial = info.serial
        mode = (info.mode or "adb").lower()
        descriptions = {
            "system": tr("fw_target_system", "систему"),
            "recovery": "Recovery",
            "bootloader": "Fastboot",
            "fastboot": "Fastbootd (userspace)",
            "sideload": "Sideload",
        }
        label = descriptions.get(target, target)
        state = self._firmware_state
        state.message = None

        def work(report: Callable[[str], None]) -> object:
            report(tr("task_rebooting_to", "Перезагружаем Pixel в {target}…", target=label))
            return reboot_device(serial, target, mode=mode)

        def done(result: object) -> None:
            if getattr(result, "ok", False):
                state.message = FeatureMessage(
                    tr(
                        "fw_msg_reboot_sent",
                        "Команда перезагрузки в {target} отправлена. "
                        "Ждём, пока устройство снова появится в ADB или Fastboot.",
                        target=label,
                    ),
                    "info",
                )
                QTimer.singleShot(1600, self._refresh_after_reboot)
            else:
                state.message = FeatureMessage(
                    tr(
                        "fw_msg_reboot_failed",
                        "Не удалось перезагрузить устройство: {msg}",
                        msg=getattr(result, 'message', tr("unknown_error", "неизвестная ошибка")),
                    ),
                    "error",
                )

        self._start_task(tr("task_reboot", "Перезагрузка"), work, done, tab=1)

    def _request_set_active_slot(self, slot: str) -> None:
        info = self._info
        if info is None or not info.detected or self._task is not None:
            return
        serial = info.serial
        state = self._firmware_state
        state.message = None
        slot_clean = slot.strip().lower().lstrip("_")

        def work(report: Callable[[str], None]) -> object:
            report(tr("task_switching_slot", "Переключаем загрузочный слот на {slot}…", slot=slot_clean.upper()))
            return set_active_slot(serial, slot_clean)

        def done(result: object) -> None:
            if getattr(result, "ok", False):
                state.message = FeatureMessage(
                    tr("fw_msg_slot_switched", "Активный загрузочный слот успешно переключён на Слот {slot}.", slot=slot_clean.upper()),
                    "info",
                )
                self.refresh(preserve_selection=True)
            else:
                state.message = FeatureMessage(
                    tr("fw_msg_slot_failed", "Не удалось переключить слот: {msg}", msg=getattr(result, "message", tr("error", "ошибка"))),
                    "error",
                )

        self._start_task(tr("task_switch_slot", "Смена слота"), work, done, tab=1)

    def _open_browser_url(self, url: str) -> None:
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _refresh_after_reboot(self) -> None:
        if self.isVisible() and not self._busy:
            self.refresh(preserve_selection=True)

    def _choose_ota(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("fw_dialog_choose_ota", "Выберите OTA-обновление"),
            "",
            tr("file_filter_zip", "OTA update (*.zip);;Все файлы (*.*)"),
        )
        if not path:
            return
        state = self._firmware_state
        state.zip_path = path
        state.message = None
        self._render_body(animate=False)

    def _request_ota_install(self) -> None:
        state = self._firmware_state
        info = self._info
        if not state.zip_path or info is None or self._task is not None:
            return
        path = state.zip_path
        self._confirm(
            tr("fw_dialog_install_ota_title", "Установить OTA?"),
            tr(
                "fw_dialog_install_ota_msg",
                "Устройство должно находиться в режиме sideload. Установка "
                "займёт несколько минут — не отключайте USB-кабель и не "
                "выключайте Pixel.",
            ),
            tr("fw_btn_install", "Установить"),
            lambda: self._do_ota_install(path),
        )

    def _do_ota_install(self, path: str) -> None:
        info = self._info
        if info is None or not info.detected:
            return
        serial = info.serial
        state = self._firmware_state
        state.message = None

        def work(report: Callable[[str], None]) -> object:
            report(tr("task_sending_ota", "Передаём OTA-образ на устройство…"))
            return sideload_ota(serial, path)

        def done(result: object) -> None:
            if getattr(result, "ok", False):
                state.message = FeatureMessage(
                    tr(
                        "fw_msg_ota_sent",
                        "OTA-образ передан. Устройство завершает установку и "
                        "перезагрузится автоматически.",
                    ),
                    "success",
                )
                QTimer.singleShot(2500, self._refresh_after_reboot)
            else:
                state.message = FeatureMessage(
                    tr(
                        "fw_msg_ota_failed",
                        "Не удалось установить OTA: {msg}",
                        msg=getattr(result, 'message', tr("unknown_error", "неизвестная ошибка")),
                    ),
                    "error",
                )

        self._start_task(tr("task_install_ota", "Установка OTA"), work, done, tab=1)

    # ------------------------------------------------------------------
    # Apps actions
    # ------------------------------------------------------------------

    def _load_packages(self) -> None:
        info = self._info
        if not self._adb_ready(info):
            return
        state = self._apps_state
        if state.loading or self._task is not None:
            return
        serial = info.serial
        include_system = state.include_system
        state.loading = True
        state.message = None

        def work(report: Callable[[str], None]) -> object:
            report(tr("task_reading_packages", "Читаем список пакетов…"))
            return list_packages(serial, include_system=include_system)

        def done(result: object) -> None:
            state.loading = False
            if getattr(result, "error", None):
                state.message = FeatureMessage(str(result.error), "error")
                return
            apps = list(getattr(result, "apps", []))
            state.packages = apps
            for entry in apps:
                self._queue_icon_fetch(entry.package)
            if state.selected and not any(
                entry.package == state.selected for entry in apps
            ):
                state.selected = None
                state.details = None
            warnings = list(getattr(result, "warnings", []))
            if warnings:
                state.message = FeatureMessage("\n".join(warnings), "warning")

        if not self._start_task(tr("task_packages_list", "Список приложений"), work, done, tab=2):
            state.loading = False

    def _open_package(self, package: Optional[str]) -> None:
        state = self._apps_state
        if package is None:
            state.selected = None
            state.details = None
            state.details_loading = False
            state.message = None
            self._render_body(animate=True)
            return
        if self._task is not None:
            self._set_feature_message(
                2, "warning", tr("task_wait_finish", "Дождитесь завершения текущей операции.")
            )
            self._render_body(animate=False)
            return
        if not self._adb_ready(self._info):
            return
        state.selected = package
        state.details = None
        state.details_loading = False
        state.message = None
        self._load_app_details(package)

    def _load_app_details(self, package: str) -> None:
        info = self._info
        if not self._adb_ready(info):
            return
        state = self._apps_state
        serial = info.serial
        state.details_loading = True

        def work(report: Callable[[str], None]) -> object:
            report(tr("task_reading_app_details", "Читаем сведения о {pkg}…", pkg=package))
            return read_app_details(serial, package)

        def done(result: object) -> None:
            state.details_loading = False
            state.details = result  # type: ignore[assignment]
            if getattr(result, "error", None):
                state.message = FeatureMessage(str(result.error), "error")

        if not self._start_task(tr("task_app_details", "Сведения: {pkg}", pkg=package), work, done, tab=2):
            state.details_loading = False

    def _request_install_apk(self) -> None:
        if self._task is not None or not self._adb_ready(self._info):
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("apps_dialog_choose_apk", "Выберите APK"),
            "",
            tr("file_filter_apk", "Android package (*.apk);;Все файлы (*.*)"),
        )
        if path:
            self._do_install_apk(path)

    def _do_install_apk(self, path: str) -> None:
        info = self._info
        if info is None or not info.detected:
            return
        serial = info.serial
        state = self._apps_state
        state.message = None

        def work(report: Callable[[str], None]) -> object:
            report(tr("task_installing_apk", "Устанавливаем APK на устройство…"))
            return install_apk(serial, path)

        def done(result: object) -> None:
            if getattr(result, "ok", False):
                state.message = FeatureMessage(
                    tr("apps_msg_apk_installed", "APK установлен: {name}", name=Path(path).name), "success"
                )
                self._load_packages()
            else:
                state.message = FeatureMessage(
                    tr(
                        "apps_msg_apk_failed",
                        "Не удалось установить APK: {msg}",
                        msg=getattr(result, 'message', tr("unknown_error", "неизвестная ошибка")),
                    ),
                    "error",
                )

        self._start_task(tr("task_install_apk", "Установка APK"), work, done, tab=2)

    def _request_uninstall(self, package: str) -> None:
        if self._task is not None:
            return
        self._confirm(
            tr("apps_dialog_uninstall_title", "Удалить приложение?"),
            tr(
                "apps_dialog_uninstall_msg",
                "Пакет {pkg} и его данные будут удалены с устройства. "
                "Действие нельзя отменить.",
                pkg=package,
            ),
            tr("apps_btn_uninstall", "Удалить"),
            lambda: self._do_uninstall(package),
            danger=True,
        )

    def _do_uninstall(self, package: str) -> None:
        info = self._info
        if info is None or not info.detected:
            return
        serial = info.serial
        state = self._apps_state
        state.message = None

        def work(report: Callable[[str], None]) -> object:
            report(tr("task_uninstalling_pkg", "Удаляем {pkg}…", pkg=package))
            return uninstall_package(serial, package)

        def done(result: object) -> None:
            if getattr(result, "ok", False):
                state.selected = None
                state.details = None
                state.message = FeatureMessage(
                    tr("apps_msg_pkg_uninstalled", "Пакет {pkg} удалён.", pkg=package), "success"
                )
                self._load_packages()
            else:
                state.message = FeatureMessage(
                    tr(
                        "apps_msg_pkg_uninstall_failed",
                        "Не удалось удалить приложение: {msg}",
                        msg=getattr(result, 'message', tr("unknown_error", "неизвестная ошибка")),
                    ),
                    "error",
                )

        self._start_task(tr("task_uninstall_app", "Удаление приложения"), work, done, tab=2)

    def _request_uninstall_system(self, package: str) -> None:
        if self._task is not None:
            return
        self._confirm(
            tr("apps_dialog_uninstall_sys_title", "Удалить системный пакет для пользователя?"),
            tr(
                "apps_dialog_uninstall_sys_msg",
                "Пакет {pkg} будет удалён для текущего пользователя (user 0).\n"
                "Приложение перестанет отображаться и работать, но сам APK "
                "сохранится в системном разделе и может быть восстановлен в любой момент.",
                pkg=package,
            ),
            tr("apps_btn_uninstall", "Удалить"),
            lambda: self._do_uninstall_system(package),
            danger=True,
        )

    def _do_uninstall_system(self, package: str) -> None:
        info = self._info
        if info is None or not info.detected:
            return
        serial = info.serial
        state = self._apps_state
        state.message = None

        def work(report: Callable[[str], None]) -> object:
            report(tr("task_uninstalling_sys_pkg", "Удаляем системный пакет {pkg} для user 0…", pkg=package))
            return uninstall_system_package(serial, package)

        def done(result: object) -> None:
            if getattr(result, "ok", False):
                state.message = FeatureMessage(
                    tr("apps_msg_sys_pkg_uninstalled", "Системный пакет {pkg} удалён для пользователя.", pkg=package), "success"
                )
                self._load_packages()
                self._load_app_details(package)
            else:
                state.message = FeatureMessage(
                    tr(
                        "apps_msg_sys_pkg_uninstall_failed",
                        "Не удалось удалить системный пакет: {msg}",
                        msg=getattr(result, 'message', tr("unknown_error", "неизвестная ошибка")),
                    ),
                    "error",
                )

        self._start_task(tr("task_debloat_pkg", "Деблоатинг пакета"), work, done, tab=2)

    def _request_restore_system(self, package: str) -> None:
        if self._task is not None:
            return
        info = self._info
        if info is None or not info.detected:
            return
        serial = info.serial
        state = self._apps_state
        state.message = None

        def work(report: Callable[[str], None]) -> object:
            report(tr("task_restoring_sys_pkg", "Восстанавливаем системный пакет {pkg}…", pkg=package))
            return restore_system_package(serial, package)

        def done(result: object) -> None:
            if getattr(result, "ok", False):
                state.message = FeatureMessage(
                    tr("apps_msg_sys_pkg_restored", "Системный пакет {pkg} успешно восстановлен.", pkg=package), "success"
                )
                self._load_packages()
                self._load_app_details(package)
            else:
                state.message = FeatureMessage(
                    tr(
                        "apps_msg_sys_pkg_restore_failed",
                        "Не удалось восстановить системный пакет: {msg}",
                        msg=getattr(result, 'message', tr("unknown_error", "неизвестная ошибка")),
                    ),
                    "error",
                )

        self._start_task(tr("task_restore_pkg", "Восстановление пакета"), work, done, tab=2)

    def _request_permission(
        self, package: str, permission: str, granted: bool
    ) -> None:
        if self._task is not None or not self._adb_ready(self._info):
            return
        serial = self._info.serial
        state = self._apps_state
        state.message = None
        action = tr("action_granting", "Выдаём") if granted else tr("action_revoking", "Отзываем")

        def work(report: Callable[[str], None]) -> object:
            report(tr("task_permission_action", "{action} разрешение…", action=action))
            return set_permission(serial, package, permission, granted)

        def done(result: object) -> None:
            if getattr(result, "ok", False):
                state.message = FeatureMessage(
                    tr("apps_msg_perm_granted", "Разрешение выдано.") if granted else tr("apps_msg_perm_revoked", "Разрешение отозвано."),
                    "success",
                )
                self._load_app_details(package)
            else:
                state.message = FeatureMessage(
                    tr(
                        "apps_msg_perm_failed",
                        "Не удалось изменить разрешение: {msg}",
                        msg=getattr(result, 'message', tr("unknown_error", "неизвестная ошибка")),
                    ),
                    "error",
                )

        self._start_task(tr("task_permission", "Разрешение"), work, done, tab=2)

    def _request_freeze_package(self, package: str, should_freeze: bool) -> None:
        if self._task is not None or not self._adb_ready(self._info):
            return
        serial = self._info.serial
        state = self._apps_state
        state.message = None
        action_verb = tr("action_freezing", "Замораживаем") if should_freeze else tr("action_unfreezing", "Размораживаем")

        def work(report: Callable[[str], None]) -> object:
            report(tr("task_freeze_action", "{action} {pkg}…", action=action_verb, pkg=package))
            if should_freeze:
                return disable_package(serial, package)
            return enable_package(serial, package)

        def done(result: object) -> None:
            if getattr(result, "ok", False):
                state.message = FeatureMessage(
                    tr("apps_msg_frozen", "Приложение {pkg} успешно отключено (заморожено).", pkg=package)
                    if should_freeze
                    else tr("apps_msg_unfrozen", "Приложение {pkg} успешно включено (разморожено).", pkg=package),
                    "success",
                )
                self._load_app_details(package)
            else:
                state.message = FeatureMessage(
                    tr(
                        "apps_msg_freeze_failed",
                        "Не удалось изменить состояние приложения: {msg}",
                        msg=getattr(result, 'message', tr("error", "ошибка")),
                    ),
                    "error",
                )

        self._start_task(
            tr("task_freeze", "Заморозка") if should_freeze else tr("task_unfreeze", "Разморозка"),
            work,
            done,
            tab=2,
        )

    def _request_force_stop(self, package: str) -> None:
        if self._task is not None or not self._adb_ready(self._info):
            return
        serial = self._info.serial
        state = self._apps_state
        state.message = None

        def work(report: Callable[[str], None]) -> object:
            report(tr("task_stopping_pkg", "Останавливаем процесс {pkg}…", pkg=package))
            return force_stop_package(serial, package)

        def done(result: object) -> None:
            if getattr(result, "ok", False):
                state.message = FeatureMessage(
                    tr("apps_msg_force_stopped", "Процесс {pkg} принудительно остановлен.", pkg=package),
                    "success",
                )
            else:
                state.message = FeatureMessage(
                    tr(
                        "apps_msg_force_stop_failed",
                        "Не удалось остановить процесс: {msg}",
                        msg=getattr(result, 'message', tr("error", "ошибка")),
                    ),
                    "error",
                )

        self._start_task(tr("task_stop_process", "Остановка процесса"), work, done, tab=2)

    def _request_clear_package_data(self, package: str) -> None:
        if self._task is not None or not self._adb_ready(self._info):
            return
        serial = self._info.serial
        state = self._apps_state
        state.message = None

        def work(report: Callable[[str], None]) -> object:
            report(tr("task_clearing_pkg_data", "Очищаем данные {pkg}…", pkg=package))
            return clear_package_data(serial, package)

        def done(result: object) -> None:
            if getattr(result, "ok", False):
                state.message = FeatureMessage(
                    tr("apps_msg_data_cleared", "Все данные и кэш приложения {pkg} успешно очищены.", pkg=package),
                    "success",
                )
                self._load_app_details(package)
            else:
                state.message = FeatureMessage(
                    tr(
                        "apps_msg_clear_data_failed",
                        "Не удалось очистить данные: {msg}",
                        msg=getattr(result, 'message', tr("error", "ошибка")),
                    ),
                    "error",
                )

        self._start_task(tr("task_clear_data", "Очистка данных"), work, done, tab=2)

    def _request_launch_package(self, package: str) -> None:
        if self._task is not None or not self._adb_ready(self._info):
            return
        serial = self._info.serial
        state = self._apps_state
        state.message = None

        def work(report: Callable[[str], None]) -> object:
            report(tr("task_launching_pkg", "Запускаем {pkg}…", pkg=package))
            return launch_package(serial, package)

        def done(result: object) -> None:
            if getattr(result, "ok", False):
                state.message = FeatureMessage(
                    tr("apps_msg_launched", "Приложение {pkg} запущено на устройстве.", pkg=package),
                    "success",
                )
            else:
                state.message = FeatureMessage(
                    tr(
                        "apps_msg_launch_failed",
                        "Не удалось запустить приложение: {msg}",
                        msg=getattr(result, 'message', tr("error", "ошибка")),
                    ),
                    "error",
                )

        self._start_task(tr("task_launch_app", "Запуск приложения"), work, done, tab=2)

    def _request_export_apk(self, package: str) -> None:
        if self._task is not None or not self._adb_ready(self._info):
            return
        serial = self._info.serial
        state = self._apps_state
        default_name = f"{package}.apk"
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            tr("apps_dialog_save_apk", "Сохранить APK-файл"),
            str(Path.home() / "Downloads" / default_name),
            tr("file_filter_apk", "APK-файлы (*.apk);;Все файлы (*.*)"),
        )
        if not save_path:
            return
        state.message = None

        def work(report: Callable[[str], None]) -> object:
            report(tr("task_pulling_apk", "Выгружаем APK {pkg}…", pkg=package))
            return pull_apk(serial, package, save_path)

        def done(result: object) -> None:
            if getattr(result, "ok", False):
                state.message = FeatureMessage(
                    tr("apps_msg_apk_saved", "APK-файл успешно сохранён: {path}", path=save_path),
                    "success",
                )
            else:
                state.message = FeatureMessage(
                    tr(
                        "apps_msg_apk_save_failed",
                        "Не удалось выгрузить APK: {msg}",
                        msg=getattr(result, 'message', tr("error", "ошибка")),
                    ),
                    "error",
                )

        self._start_task(tr("task_export_apk", "Экспорт APK"), work, done, tab=2)

    # ------------------------------------------------------------------
    # App icon fetching & dispatch
    # ------------------------------------------------------------------

    def _register_icon_badge(self, package: str, badge: object) -> None:
        if not package:
            return
        badges = self._icon_badges.setdefault(package, [])
        clean = []
        for b in badges:
            try:
                _ = b.rect()
                clean.append(b)
            except RuntimeError:
                pass
        clean.append(badge)
        self._icon_badges[package] = clean

        cached = get_cached_app_icon(package)
        if cached:
            try:
                badge.set_icon_path(cached)
            except Exception:
                pass
        else:
            self._queue_icon_fetch(package)

    def _queue_icon_fetch(self, package: str) -> None:
        if not package or package in self._icon_queued_set:
            return
        if get_cached_app_icon(package):
            return
        self._icon_queued_set.add(package)
        self._ensure_icon_workers()
        self._icon_queue.put(package)

    def _ensure_icon_workers(self) -> None:
        if self._icon_workers_started:
            return
        self._icon_workers_started = True
        for _ in range(2):
            t = threading.Thread(target=self._icon_worker_loop, daemon=True)
            t.start()

    def _icon_worker_loop(self) -> None:
        while True:
            try:
                pkg = self._icon_queue.get()
                if not pkg:
                    continue
                cached = get_cached_app_icon(pkg)
                if cached:
                    self.app_icon_ready.emit(pkg, cached)
                    continue

                info = self._info
                serial = info.serial if (info and info.detected) else None
                icon_path = fetch_app_icon(serial, pkg)
                if icon_path:
                    self.app_icon_ready.emit(pkg, icon_path)
            except Exception as e:
                LOGGER.debug("App icon worker error for %s: %s", pkg, e)
            finally:
                self._icon_queue.task_done()

    def _on_app_icon_ready(self, package: str, icon_path: str) -> None:
        badges = self._icon_badges.get(package, [])
        alive = []
        for badge in badges:
            try:
                badge.set_icon_path(icon_path)
                alive.append(badge)
            except (RuntimeError, ReferenceError):
                pass
        self._icon_badges[package] = alive

    # ------------------------------------------------------------------
    # Backup actions
    # ------------------------------------------------------------------

    def _choose_backup_dir(self) -> None:
        state = self._backup_state
        start = state.destination or str(Path.home() / "Documents")
        chosen = QFileDialog.getExistingDirectory(
            self, tr("backup_dialog_choose_dir", "Куда сохранить резервную копию"), start
        )
        if not chosen:
            return
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        state.destination = str(Path(chosen) / f"PixelTools-{stamp}")
        state.result = None
        state.message = None
        self._render_body(animate=False)

    def _request_create_backup(self) -> None:
        state = self._backup_state
        info = self._info
        if not self._adb_ready(info) or self._task is not None:
            return
        if not state.selected:
            state.message = FeatureMessage(
                tr("backup_msg_choose_section", "Выберите хотя бы один раздел для резервной копии."), "warning"
            )
            self._render_body(animate=False)
            return
        if not state.destination:
            state.destination = self._suggest_backup_dir()
        keys = sorted(state.selected)
        destination = state.destination
        serial = info.serial
        model = info.display_name
        state.result = None
        state.restore_result = None
        state.message = None

        def work(report: Callable[[str], None]) -> object:
            return create_backup(
                serial,
                keys,
                destination,
                model=model,
                progress=report,
            )

        def done(result: object) -> None:
            state.result = result  # type: ignore[assignment]
            if getattr(result, "error", None):
                state.message = FeatureMessage(str(result.error), "error")
            elif getattr(result, "warnings", None):
                state.message = FeatureMessage(
                    tr(
                        "backup_msg_warnings",
                        "Резервная копия создана, но часть данных не сохранена. "
                        "Подробности ниже.",
                    ),
                    "warning",
                )
            else:
                state.message = FeatureMessage(tr("backup_msg_success", "Резервная копия создана."), "success")

        self._start_task(tr("task_backup", "Резервная копия"), work, done, tab=3)

    def _choose_restore_dir(self) -> None:
        state = self._backup_state
        start = state.restore_dir or str(Path.home() / "Documents" / "PixelTools")
        chosen = QFileDialog.getExistingDirectory(
            self, tr("backup_dialog_choose_restore", "Выберите папку резервной копии"), start
        )
        if not chosen:
            return
        state.restore_dir = chosen
        state.restore_info = read_backup_info(chosen)
        state.restore_result = None
        if state.restore_info is None:
            state.message = FeatureMessage(
                tr(
                    "backup_restore_missing_info",
                    "В выбранной папке нет backup-info.json от Pixel Tools. "
                    "Выберите папку, созданную этим приложением.",
                ),
                "error",
            )
        else:
            state.message = FeatureMessage(
                tr(
                    "backup_restore_found",
                    "Резервная копия найдена. Проверьте данные и запустите "
                    "восстановление.",
                ),
                "info",
            )
        self._render_body(animate=False)

    def _request_restore(self) -> None:
        state = self._backup_state
        if (
            not self._adb_ready(self._info)
            or self._task is not None
            or state.restore_info is None
            or not state.restore_dir
        ):
            return
        self._confirm(
            tr("backup_dialog_restore_title", "Восстановить файлы?"),
            tr(
                "backup_dialog_restore_msg",
                "Медиа будет скопировано в /sdcard, contacts.vcf — в папку "
                "Download. Файлы с совпадающими именами могут быть "
                "перезаписаны.",
            ),
            tr("backup_btn_restore", "Восстановить"),
            self._do_restore,
        )

    def _do_restore(self) -> None:
        info = self._info
        state = self._backup_state
        if info is None or not info.detected:
            return
        serial = info.serial
        source = state.restore_dir
        state.restore_result = None
        state.message = None

        def work(report: Callable[[str], None]) -> object:
            return restore_files(serial, source, progress=report)

        def done(result: object) -> None:
            state.restore_result = result  # type: ignore[assignment]
            if getattr(result, "error", None):
                state.message = FeatureMessage(str(result.error), "error")
            elif getattr(result, "warnings", None):
                state.message = FeatureMessage(
                    tr(
                        "backup_restore_msg_warnings",
                        "Файлы восстановлены, но часть данных не скопирована. "
                        "Подробности ниже.",
                    ),
                    "warning",
                )
            else:
                state.message = FeatureMessage(tr("backup_restore_msg_success", "Файлы восстановлены на устройство."), "success")

        self._start_task(tr("task_restore", "Восстановление"), work, done, tab=3)

    def _open_folder(self, path: str) -> None:
        if not path:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _confirm(
        self,
        title: str,
        message: str,
        action_label: str,
        on_confirm: Callable[[], None],
        *,
        danger: bool = False,
    ) -> None:
        dialog = ConfirmDialog(
            self,
            self._palette(),
            title=title,
            message=message,
            action_label=action_label,
            danger=danger,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            on_confirm()

    # ------------------------------------------------------------------
    # Screen Mirroring operations
    # ------------------------------------------------------------------

    def _check_mirror_process(self) -> None:
        if self._mirror_process is not None:
            if self._mirror_process.poll() is not None:
                self._mirror_process = None
                self._mirror_state.is_running = False
                self._mirror_monitor_timer.stop()
                self._dispose_mirror_controls()
                if self._tab_index == 4:
                    self._render_body(animate=False)

    def _start_screen_mirror(self) -> None:
        info = self._info
        if not self._adb_ready(info):
            self._mirror_state.message = FeatureMessage(
                tr("mirror_not_ready_msg", "Устройство не готово к трансляции. Включите отладку по USB."),
                "error",
            )
            if self._tab_index == 4:
                self._render_body(animate=False)
            return

        cfg = ScreenMirrorConfig(
            stay_awake=self._mirror_state.stay_awake,
            turn_screen_off=self._mirror_state.turn_screen_off,
            always_on_top=self._mirror_state.always_on_top,
            max_size=self._mirror_state.max_size,
            bit_rate_mbps=self._mirror_state.bit_rate_mbps,
            max_fps=self._mirror_state.max_fps,
            audio=self._mirror_state.audio,
            read_only=self._mirror_state.read_only,
            fullscreen=self._mirror_state.fullscreen,
            borderless=self._mirror_state.borderless,
        )
        serial = info.serial
        title = f"{tr(info.display_name)} — " + tr("mirror_window_title_suffix", "Экран Pixel")
        ok, msg, proc = start_screen_mirror(serial, cfg, custom_title=title)
        if ok and proc is not None:
            self._mirror_process = proc
            self._mirror_title = title
            self._mirror_state.is_running = True
            # Успешный запуск виден по окну scrcpy и статусу «Трансляция
            # активна»; отдельное сообщение только засоряет страницу.
            self._mirror_state.message = None
            self._mirror_monitor_timer.start()
            self._start_mirror_controls()
        else:
            self._mirror_state.message = FeatureMessage(msg, "error")

        if self._tab_index == 4:
            self._render_body(animate=False)

    def _stop_screen_mirror(self) -> None:
        self._dispose_mirror_controls()
        if self._mirror_process is not None:
            try:
                self._mirror_process.terminate()
            except Exception:
                pass
            self._mirror_process = None
        self._mirror_title = ""
        self._mirror_state.is_running = False
        self._mirror_monitor_timer.stop()
        if self._tab_index == 4:
            self._render_body(animate=False)

    def _start_mirror_controls(self) -> None:
        """Open the side panel that follows the scrcpy window."""
        if self._mirror_process is None:
            return
        try:
            ontop = bool(self._mirror_state.always_on_top)
            if (
                self._mirror_controls is None
                or getattr(self._mirror_controls, "always_on_top", False) != ontop
            ):
                if self._mirror_controls is not None:
                    self._mirror_controls.shutdown()
                self._mirror_controls = MirrorControlPanel(
                    self._palette(),
                    on_key=self._request_send_key,
                    on_screenshot=self._request_screenshot,
                    on_rotate=self._request_toggle_rotation,
                    on_close=self._stop_screen_mirror,
                    always_on_top=ontop,
                )
            else:
                self._mirror_controls.set_palette(self._palette())
            self._mirror_controls.attach(
                self._mirror_process, self._mirror_title
            )
        except Exception:
            LOGGER.exception("Failed to open mirror control panel")
            self._mirror_controls = None

    def _refresh_mirror_controls_theme(self) -> None:
        if self._mirror_controls is not None:
            self._mirror_controls.set_palette(self._palette())

    def _dispose_mirror_controls(self) -> None:
        panel = self._mirror_controls
        self._mirror_controls = None
        if panel is not None:
            panel.shutdown()

    def _toggle_mirror_option(self, option_name: str, value: object) -> None:
        if hasattr(self._mirror_state, option_name):
            setattr(self._mirror_state, option_name, value)

    def _request_toggle_rotation(self) -> None:
        info = self._info
        if not self._adb_ready(info):
            return
        serial = info.serial
        threading.Thread(
            target=lambda: toggle_device_rotation(serial),
            daemon=True,
            name="pixel-rotate",
        ).start()

    def _request_send_key(self, keycode: int) -> None:
        info = self._info
        if not self._adb_ready(info):
            return
        serial = info.serial
        threading.Thread(
            target=lambda: send_device_key(serial, keycode),
            daemon=True,
            name="pixel-keyevent",
        ).start()

    def _request_screenshot(self) -> None:
        info = self._info
        if not self._adb_ready(info):
            return
        serial = info.serial
        state = self._mirror_state
        state.message = None

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_folder = Path.home() / "Documents" / "PixelTools" / "Screenshots"
        dest_path = str(dest_folder / f"Pixel_{stamp}.png")

        def work(report: Callable[[str], None]) -> object:
            report(tr("task_taking_screenshot", "Создаём снимок экрана…"))
            return take_device_screenshot(serial, dest_path)

        def done(result: object) -> None:
            if getattr(result, "ok", False):
                state.last_screenshot_path = dest_path
                state.message = FeatureMessage(
                    tr("mirror_msg_screenshot_saved", "Снимок сохранён: {name}", name=Path(dest_path).name), "success"
                )
            else:
                state.message = FeatureMessage(
                    tr(
                        "mirror_msg_screenshot_failed",
                        "Не удалось сделать снимок: {msg}",
                        msg=getattr(result, 'error', tr("capture_error", "ошибка захвата")),
                    ),
                    "error",
                )
            if self._tab_index == 4:
                self._render_body(animate=False)

        self._start_task(tr("task_screenshot", "Снимок экрана"), work, done, tab=4)

    def _request_install_scrcpy(self) -> None:
        if is_scrcpy_available():
            self._mirror_state.scrcpy_ready = True
            self._mirror_state.message = FeatureMessage(
                tr("mirror_scrcpy_ready_msg", "Движок scrcpy готов к использованию."), "success"
            )
            if self._tab_index == 4:
                self._render_body(animate=False)
            return

    # ------------------------------------------------------------------
    # Public helpers (also used by the screenshot/test harness)
    # ------------------------------------------------------------------

    def set_device_info(
        self, info: PixelDeviceInfo, *, timestamp: bool = True
    ) -> None:
        self._info = info
        self._busy = False
        self._poll_timer.stop()
        self._sync_feature_serial(info)
        if timestamp:
            self._last_updated = datetime.now().strftime("%H:%M:%S")
        self._update_header()
        self._render_body(animate=False)

    def set_tab(self, index: int) -> None:
        index = max(0, min(5, int(index)))
        if self._tab_index != 5 and index == 5:
            self._previous_tab_index = self._tab_index
        self._tab_index = index
        self._rail.set_selected(index)
        self._update_header()
        self._render_body(animate=False)

    @property
    def device_info(self) -> Optional[PixelDeviceInfo]:
        return self._info

    # ------------------------------------------------------------------
    # Qt events
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._auto_refresh and not self._auto_started:
            self._auto_started = True
            QTimer.singleShot(140, self._auto_refresh_once)
            self._auto_detect_timer.start()

    def _auto_refresh_once(self) -> None:
        if not self._busy and self._info is None:
            self.refresh(preserve_selection=True)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_corner_overlay"):
            if self._is_resizing:
                self._cancel_corner_highlight()
            elif self._active_corner:
                self._corner_overlay.reposition(
                    self._active_corner, self.width(), self.height()
                )
        self._update_responsive()
        if self.centralWidget() is not None:
            self.centralWidget().update()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_screen_mirror()
        self._auto_detect_timer.stop()
        self._poll_timer.stop()
        self._task_timer.stop()
        self._corner_watch_timer.stop()
        if self._task is not None:
            self._task_token += 1
            self._task = None
        super().closeEvent(event)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pixel Tools — Google Pixel detection & management via ADB/Fastboot."
    )
    parser.add_argument(
        "--theme",
        choices=VALID_APPEARANCES,
        default=None,
        help="Appearance theme: system, light, or dark.",
    )
    parser.add_argument(
        "--light",
        action="store_true",
        help="Shorthand for --theme light.",
    )
    parser.add_argument(
        "--dark",
        action="store_true",
        help="Shorthand for --theme dark.",
    )
    return parser.parse_known_args(argv)[0]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    args = _parse_args(argv[1:])

    level_name = os.environ.get("PIXELTOOLS_LOG_LEVEL", "WARNING").upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except (AttributeError, RuntimeError):
        pass

    app = QApplication([argv[0], *argv[1:]])
    app.setApplicationName("Pixel Tools")
    app.setApplicationDisplayName("Pixel Tools")
    app.setOrganizationName("Pixel Tools")
    app.setStyle("Fusion")
    app.setFont(ui_font(13))

    theme = load_theme()
    if theme.warning:
        LOGGER.warning(theme.warning)

    mode = args.theme or os.environ.get("PIXELTOOLS_APPEARANCE", "system")
    mode = str(mode).strip().lower()
    if mode not in VALID_APPEARANCES:
        LOGGER.warning(
            "Unknown theme '%s', using system setting.", mode
        )
        mode = "system"
    if args.light:
        mode = "light"
    elif args.dark:
        mode = "dark"

    window = PixelToolsWindow(theme, appearance=mode)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
