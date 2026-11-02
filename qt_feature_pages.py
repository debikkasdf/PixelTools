"""Native Material 3 pages for the firmware, apps and backup tabs.

The pages are stateless: everything the user typed or selected lives in the
``*State`` objects owned by :class:`main.PixelToolsWindow`.  Rebuilding a page
after a theme switch or an async result therefore never loses the search text,
the selected app or the backup choices.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import ctypes
from ctypes import wintypes
import subprocess
import threading
from typing import Callable, Dict, Mapping, Optional, Sequence

from PySide6.QtCore import QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from locales import tr, get_permission_label
from pixel_device_detector import PixelDeviceInfo
from pixel_tools_ops import (
    AppDetails,
    AppEntry,
    BACKUP_OPTIONS,
    BACKUP_OPTIONS_BY_KEY,
    BackupResult,
    BuildInfo,
    RestoreResult,
    get_cached_app_icon,
    send_device_key,
    toggle_device_rotation,
)
from qt_material import (
    IconBadge,
    IconWidget,
    blend,
    draw_icon,
    soft_shadow,
    ui_font,
    with_alpha,
)
from qt_widgets import (
    CardSurface,
    ElidedLabel,
    NoticeBanner,
    Pill,
    ResponsiveContent,
    Spinner,
    _PressFeedbackMixin,
    make_label,
)


# ---------------------------------------------------------------------------
# Persistent state and controller
# ---------------------------------------------------------------------------


@dataclass
class FeatureMessage:
    text: str
    kind: str = "info"  # info | success | warning | error


@dataclass
class FirmwareState:
    build: Optional[BuildInfo] = None
    build_loading: bool = False
    zip_path: str = ""
    message: Optional[FeatureMessage] = None


@dataclass
class AppsState:
    packages: Optional[list[AppEntry]] = None
    loading: bool = False
    include_system: bool = False
    query: str = ""
    selected: Optional[str] = None
    details: Optional[AppDetails] = None
    details_loading: bool = False
    message: Optional[FeatureMessage] = None


@dataclass
class BackupState:
    selected: set[str] = field(default_factory=lambda: {"photos", "contacts"})
    destination: str = ""
    restore_dir: str = ""
    restore_info: Optional[Dict[str, object]] = None
    result: Optional[BackupResult] = None
    restore_result: Optional[RestoreResult] = None
    message: Optional[FeatureMessage] = None


@dataclass
class MirrorState:
    is_running: bool = False
    scrcpy_ready: bool = False
    stay_awake: bool = True
    turn_screen_off: bool = False
    always_on_top: bool = False
    max_size: int = 0  # 0 = Original
    bit_rate_mbps: int = 8
    max_fps: int = 60
    audio: bool = True
    read_only: bool = False
    borderless: bool = False
    fullscreen: bool = False
    last_screenshot_path: Optional[str] = None
    message: Optional[FeatureMessage] = None


@dataclass
class FeatureController:
    """Callbacks implemented by the main window."""

    palette: Mapping[str, str]
    info: PixelDeviceInfo
    busy: bool
    task_tab: Optional[int]
    task_label: str
    task_message: str
    load_build: Callable[[], None]
    reboot: Callable[[str], None]
    choose_ota: Callable[[], None]
    install_ota: Callable[[], None]
    set_active_slot: Callable[[str], None]
    open_browser_url: Callable[[str], None]
    load_packages: Callable[[], None]
    open_package: Callable[[Optional[str]], None]
    install_apk: Callable[[], None]
    uninstall: Callable[[str], None]
    uninstall_system: Callable[[str], None]
    restore_system: Callable[[str], None]
    freeze_package: Callable[[str, bool], None]
    force_stop_package: Callable[[str], None]
    clear_package_data: Callable[[str], None]
    launch_package: Callable[[str], None]
    export_apk: Callable[[str], None]
    set_permission: Callable[[str, str, bool], None]
    choose_backup_dir: Callable[[], None]
    create_backup: Callable[[], None]
    choose_restore_dir: Callable[[], None]
    restore_backup: Callable[[], None]
    open_folder: Callable[[str], None]
    confirm: Callable[..., None]
    start_mirror: Callable[[], None]
    stop_mirror: Callable[[], None]
    toggle_mirror_option: Callable[[str, object], None]
    take_screenshot: Callable[[], None]
    send_key: Callable[[int], None]
    install_scrcpy: Callable[[], None]
    register_icon_badge: Optional[Callable[[str, object], None]] = None


# ---------------------------------------------------------------------------
# Buttons and controls
# ---------------------------------------------------------------------------


class ActionButton(_PressFeedbackMixin, QPushButton):
    """Pill button with Material filled, tonal, outlined and text variants."""

    def __init__(
        self,
        text: str,
        *,
        palette: Mapping[str, str],
        icon: Optional[str] = None,
        kind: str = "filled",
        height: int = 40,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._text = text
        self._icon = icon
        self._palette = palette
        self._kind = kind
        self._height = height
        self.setAccessibleName(text)
        self.setFixedHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._init_press_feedback()
        self._recalculate_width()

    def _c(self, name: str) -> QColor:
        return QColor(self._palette.get(name, "#6750A4"))

    def _font(self) -> QFont:
        return ui_font(12 if self._height >= 38 else 11, QFont.Weight.Bold)

    def _recalculate_width(self) -> None:
        metrics = QFontMetrics(self._font())
        text_width = metrics.horizontalAdvance(self._text)
        icon_width = 17 if self._icon else 0
        gap = 8 if self._icon and self._text else 0
        padding = 20 if self._height >= 38 else 15
        self.setFixedWidth(max(self._height, padding * 2 + icon_width + gap + text_width))

    def _colors(self) -> tuple[QColor, QColor, Optional[QColor]]:
        enabled = self.isEnabled()
        kind = self._kind
        if not enabled:
            background = (
                self._c("surface_container_high")
                if kind in {"filled", "tonal", "danger"}
                else QColor(0, 0, 0, 0)
            )
            foreground = blend(self._c("surface"), self._c("on_surface"), 0.38)
            border = self._c("outline_variant") if kind == "outlined" else None
            return background, foreground, border
        if kind == "tonal":
            return self._c("secondary_container"), self._c("on_secondary_container"), None
        if kind == "outlined":
            return QColor(0, 0, 0, 0), self._c("primary"), self._c("outline")
        if kind == "text":
            return QColor(0, 0, 0, 0), self._c("primary"), None
        if kind == "danger":
            return self._c("error"), self._c("on_error"), None
        return self._c("primary"), self._c("on_primary"), None

    def set_text(self, text: str) -> None:
        self._text = text
        self.setAccessibleName(text)
        self._recalculate_width()
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._apply_press_transform(painter)

        background, foreground, border = self._colors()
        if self.underMouse() and self.isEnabled():
            if self._kind in {"outlined", "text"}:
                background = with_alpha(foreground, 20)
            elif self._kind == "tonal":
                background = blend(background, foreground, 0.08)
            else:
                background = blend(background, foreground, 0.08)

        radius = self._height / 2.0
        rect = QRectF(self.rect())
        painter.setPen(Qt.PenStyle.NoPen)
        if background.alpha() > 0:
            painter.setBrush(background)
            painter.drawRoundedRect(rect, radius, radius)
        if border is not None:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(border, 1))
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        if self.isEnabled():
            self._paint_press_feedback(
                painter,
                rect,
                radius,
                foreground,
                state_alpha=22,
                ripple_alpha=42,
            )

        font = self._font()
        metrics = QFontMetrics(font)
        icon_size = 17.0 if self._height >= 38 else 15.0
        text_width = metrics.horizontalAdvance(self._text)
        content_width = (icon_size if self._icon else 0) + (
            8 if self._icon and self._text else 0
        ) + text_width
        start = (self.width() - content_width) / 2.0
        if self._icon:
            draw_icon(
                painter,
                self._icon,
                QRectF(start, (self.height() - icon_size) / 2.0, icon_size, icon_size),
                foreground,
                stroke=2.0,
            )
            start += icon_size + (8 if self._text else 0)
        painter.setPen(foreground)
        painter.setFont(font)
        painter.drawText(
            QRectF(start, 0, text_width + 1, self.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._text,
        )

        if self.hasFocus() and self._keyboard_focus:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(with_alpha(foreground, 210), 2))
            painter.drawRoundedRect(
                rect.adjusted(3, 3, -3, -3), radius - 2, radius - 2
            )


class ConfirmDialog(QDialog):
    """Compact Material confirmation dialog used before destructive actions."""

    def __init__(
        self,
        parent: QWidget,
        palette: Mapping[str, str],
        *,
        title: str,
        message: str,
        action_label: str,
        danger: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setStyleSheet(
            f"QDialog {{ background-color: {palette.get('surface', '#FEF7FF')}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(
            make_label(
                title,
                size=20,
                weight=QFont.Weight.Bold,
                color=palette.get("on_surface", "#1D1B20"),
                wrap=True,
                display=True,
            )
        )
        text = make_label(
            message,
            size=12.5,
            color=palette.get("on_surface_variant", "#49454F"),
            wrap=True,
        )
        layout.addWidget(text)
        layout.addSpacing(4)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(10)
        buttons.addStretch(1)
        cancel = ActionButton(
            tr("dialog_cancel", "Отмена"),
            palette=palette,
            kind="text",
            height=38,
        )
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        confirm = ActionButton(
            action_label,
            palette=palette,
            kind="danger" if danger else "filled",
            height=38,
        )
        confirm.clicked.connect(self.accept)
        buttons.addWidget(confirm)
        layout.addLayout(buttons)


class FilterChip(_PressFeedbackMixin, QAbstractButton):
    """Checkable filter pill used above the package list."""

    def __init__(
        self,
        text: str,
        *,
        palette: Mapping[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._text = text
        self._palette = palette
        self._font = ui_font(11, QFont.Weight.Bold)
        self.setCheckable(True)
        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(text)
        self._init_press_feedback()
        self._recalculate_width()

    def _c(self, name: str) -> QColor:
        return QColor(self._palette.get(name, "#6750A4"))

    def _recalculate_width(self) -> None:
        width = QFontMetrics(self._font).horizontalAdvance(self._text) + 38
        self.setFixedWidth(max(84, width))

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        checked = self.isChecked()
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2.0
        if checked:
            painter.setBrush(self._c("secondary_container"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, radius, radius)
        elif self.underMouse():
            painter.setBrush(self._c("surface_container_high"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, radius, radius)
        if not checked:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(self._c("outline"), 1))
            painter.drawRoundedRect(rect, radius, radius)
        elif self.isEnabled():
            self._paint_press_feedback(
                painter,
                rect,
                radius,
                self._c("on_secondary_container"),
                state_alpha=20,
                ripple_alpha=38,
            )
        elif self.isEnabled():
            self._paint_press_feedback(
                painter,
                rect,
                radius,
                self._c("on_surface"),
                state_alpha=20,
                ripple_alpha=38,
            )

        x = 15.0
        color = (
            self._c("on_secondary_container")
            if checked
            else self._c("on_surface_variant")
        )
        if checked:
            draw_icon(
                painter,
                "check",
                QRectF(x, (self.height() - 14) / 2.0, 14, 14),
                color,
                stroke=2.2,
            )
            x += 20.0
        painter.setPen(color)
        painter.setFont(self._font)
        painter.drawText(
            QRectF(x, 0, self.width() - x - 12, self.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._text,
        )

        if self.hasFocus() and self._keyboard_focus:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(self._c("primary"), 2))
            painter.drawRoundedRect(
                QRectF(self.rect()).adjusted(3, 3, -3, -3),
                radius - 2,
                radius - 2,
            )


class IconTextField(QWidget):
    """Rounded search field with a leading icon and Material focus ring."""

    textChanged = Signal(str)

    def __init__(
        self,
        *,
        palette: Mapping[str, str],
        placeholder: str = "",
        icon: str = "search",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self._focused = False
        self.setFixedHeight(48)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 12, 0)
        layout.setSpacing(8)
        self._icon = IconWidget(
            icon,
            palette.get("on_surface_variant", "#49454F"),
            18,
            parent=self,
        )
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)

        self._edit = QLineEdit(self)
        self._edit.setFrame(False)
        self._edit.setPlaceholderText(placeholder)
        self._edit.setFont(ui_font(12.5, QFont.Weight.Normal))
        self._edit.setStyleSheet(
            "QLineEdit {"
            f" background: transparent; border: none;"
            f" color: {palette.get('on_surface', '#1D1B20')};"
            f" selection-background-color: {palette.get('primary', '#6750A4')};"
            f" selection-color: {palette.get('on_primary', '#FFFFFF')};"
            "}"
        )
        text_palette = self._edit.palette()
        text_palette.setColor(
            text_palette.ColorRole.PlaceholderText,
            QColor(palette.get("on_surface_variant", "#49454F")),
        )
        self._edit.setPalette(text_palette)
        self._edit.installEventFilter(self)
        self._edit.textChanged.connect(self.textChanged.emit)
        layout.addWidget(self._edit, 1)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._edit:
            if event.type() == event.Type.FocusIn:
                self._focused = True
                self.update()
            elif event.type() == event.Type.FocusOut:
                self._focused = False
                self.update()
        return super().eventFilter(watched, event)

    def text(self) -> str:
        return self._edit.text()

    def setText(self, value: str) -> None:  # noqa: N802 (Qt-like API)
        self._edit.setText(value)

    def setFocus(self, reason=Qt.FocusReason.OtherFocusReason) -> None:  # noqa: N802
        self._edit.setFocus(reason)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(QColor(self._palette.get("surface_container_high", "#ECE6F0")))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 20, 20)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(
                QColor(self._palette.get("primary", "#6750A4"))
                if self._focused
                else QColor(self._palette.get("outline_variant", "#CAC4D0")),
                2 if self._focused else 1,
            )
        )
        painter.drawRoundedRect(rect, 20, 20)


class CheckOption(_PressFeedbackMixin, QAbstractButton):
    """Checkable list cell used for backup sections."""

    def __init__(
        self,
        title: str,
        description: str,
        *,
        palette: Mapping[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._description = description
        self._palette = palette
        self._title_font = ui_font(12, QFont.Weight.Bold)
        self._description_font = ui_font(10.5, QFont.Weight.Normal)
        self.setCheckable(True)
        self.setFixedHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"{title}. {description}")
        self._init_press_feedback()

    def _c(self, name: str) -> QColor:
        return QColor(self._palette.get(name, "#6750A4"))

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        checked = self.isChecked()
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if checked:
            painter.setBrush(self._c("secondary_container"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 20, 20)
        elif self.underMouse():
            painter.setBrush(with_alpha(self._c("on_surface"), 8))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 20, 20)
        if self.isEnabled():
            self._paint_press_feedback(
                painter,
                rect,
                20,
                self._c("primary"),
                state_alpha=20,
                ripple_alpha=38,
            )

        box = QRectF(14, (self.height() - 20) / 2.0, 20, 20)
        if checked:
            painter.setBrush(self._c("primary"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(box, 7, 7)
            draw_icon(
                painter,
                "check",
                box.adjusted(3, 3, -3, -3),
                self._c("on_primary"),
                stroke=2.2,
            )
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(self._c("outline"), 2))
            painter.drawRoundedRect(box, 7, 7)

        text_x = 46.0
        text_width = max(0.0, self.width() - text_x - 12)
        painter.setPen(
            self._c("on_secondary_container") if checked else self._c("on_surface")
        )
        painter.setFont(self._title_font)
        painter.drawText(
            QRectF(text_x, 13, text_width, 20),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(self._title_font).elidedText(
                self._title, Qt.TextElideMode.ElideRight, int(text_width)
            ),
        )
        painter.setPen(self._c("on_surface_variant"))
        painter.setFont(self._description_font)
        painter.drawText(
            QRectF(text_x, 33, text_width, 18),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            QFontMetrics(self._description_font).elidedText(
                self._description, Qt.TextElideMode.ElideRight, int(text_width)
            ),
        )

        if self.hasFocus() and self._keyboard_focus:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(self._c("primary"), 2))
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 17, 17)


class AppIconBadge(QWidget):
    """Squircle badge displaying the real app icon or a Material fallback."""

    def __init__(
        self,
        package: str,
        *,
        palette: Mapping[str, str],
        size: int = 40,
        radius: int = 12,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._package = package
        self._palette = palette
        self._size = size
        self._radius = radius
        self._pixmap: Optional[QPixmap] = None
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        cached_path = get_cached_app_icon(package)
        if cached_path:
            self._load_pixmap(cached_path)

    def _load_pixmap(self, path: str) -> None:
        try:
            pm = QPixmap(path)
            if not pm.isNull():
                dpr = self.devicePixelRatio() or 1.0
                target_px = int(self._size * dpr)
                self._pixmap = pm.scaled(
                    target_px,
                    target_px,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
        except Exception:
            self._pixmap = None

    def set_icon_path(self, path: str) -> None:
        self._load_pixmap(path)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = QRectF(self.rect())

        clip = QPainterPath()
        clip.addRoundedRect(rect, self._radius, self._radius)
        painter.setClipPath(clip)

        if self._pixmap is not None:
            painter.drawPixmap(rect.toRect(), self._pixmap)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._palette.get("secondary_container", "#E8DEF8")))
            painter.drawRect(rect)
            icon_sz = self._size * 0.5
            draw_icon(
                painter,
                "apps",
                QRectF(
                    (self.width() - icon_sz) / 2.0,
                    (self.height() - icon_sz) / 2.0,
                    icon_sz,
                    icon_sz,
                ),
                self._palette.get("on_secondary_container", "#1D192B"),
                stroke=1.8,
            )


class PackageRow(_PressFeedbackMixin, QAbstractButton):
    """Clickable package row with a leading real app icon or tonal badge."""

    def __init__(
        self,
        entry: AppEntry,
        *,
        palette: Mapping[str, str],
        controller: Optional[FeatureController] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.package = entry.package
        self._palette = palette
        self.setFixedHeight(60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(entry.package)
        self._init_press_feedback()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(12)

        self.badge = AppIconBadge(
            entry.package,
            palette=palette,
            size=40,
            radius=12,
            parent=self,
        )
        if controller and controller.register_icon_badge:
            controller.register_icon_badge(entry.package, self.badge)
        layout.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(1)
        name = ElidedLabel(
            entry.package,
            font=ui_font(12, QFont.Weight.DemiBold),
            color=palette.get("on_surface", "#1D1B20"),
            parent=self,
        )
        if entry.uninstalled:
            meta_text = tr("apps_meta_uninstalled_user", "Системное (удалено для пользователя)")
        elif entry.disabled:
            meta_text = tr("apps_meta_disabled", "Заморожено (отключено)")
        else:
            meta_text = tr("apps_system_app", "Системное приложение") if entry.system else tr("apps_user_app", "Пользовательское приложение")

        meta = ElidedLabel(
            meta_text,
            font=ui_font(10.5, QFont.Weight.Normal),
            color=palette.get("error" if (entry.disabled or entry.uninstalled) else "on_surface_variant", "#49454F"),
            parent=self,
        )
        name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        meta.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        column.addWidget(name)
        column.addWidget(meta)
        layout.addLayout(column, 1)

        if entry.uninstalled:
            layout.addWidget(
                Pill(
                    tr("apps_uninstalled", "Удалено"),
                    background=palette.get("error_container", "#F9DEDC"),
                    foreground=palette.get("on_error_container", "#410E0B"),
                    height=24,
                    parent=self,
                ),
                0,
                Qt.AlignmentFlag.AlignVCenter,
            )
        elif entry.disabled:
            layout.addWidget(
                Pill(
                    tr("apps_disabled", "Заморожено"),
                    background=palette.get("tertiary_container", "#FFD8E4"),
                    foreground=palette.get("on_tertiary_container", "#31111D"),
                    height=24,
                    parent=self,
                ),
                0,
                Qt.AlignmentFlag.AlignVCenter,
            )
        elif entry.system:
            layout.addWidget(
                Pill(
                    tr("apps_meta_system", "Системное"),
                    background=palette.get("surface_container_high", "#ECE6F0"),
                    foreground=palette.get("on_surface_variant", "#49454F"),
                    height=24,
                    parent=self,
                ),
                0,
                Qt.AlignmentFlag.AlignVCenter,
            )
        chevron = IconWidget(
            "chevron",
            palette.get("on_surface_variant", "#49454F"),
            18,
            parent=self,
        )
        layout.addWidget(chevron, 0, Qt.AlignmentFlag.AlignVCenter)

    def _c(self, name: str) -> QColor:
        return QColor(self._palette.get(name, "#6750A4"))

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        if self.underMouse():
            painter.setBrush(with_alpha(self._c("on_surface"), 8))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 18, 18)
        if self.isEnabled():
            self._paint_press_feedback(
                painter,
                rect,
                18,
                self._c("primary"),
                state_alpha=20,
                ripple_alpha=38,
            )

        painter.setPen(QPen(with_alpha(self._c("outline_variant"), 90), 1))
        painter.drawLine(
            QPointF(64, self.height() - 1),
            QPointF(self.width() - 14, self.height() - 1),
        )

        if self.hasFocus() and self._keyboard_focus:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(self._c("primary"), 2))
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 15, 15)


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------


class FeaturePage(QWidget):
    """Scrollable feature page with a task progress card at the top."""

    def __init__(
        self,
        palette: Mapping[str, str],
        *,
        tab: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self._tab = tab
        self._task_label: Optional[QLabel] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(scroll)

        content = ResponsiveContent()
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(32, 10, 32, 28)
        self._layout.setSpacing(18)
        self._layout.addStretch(1)
        scroll.setWidget(content)

    def add(self, widget: QWidget) -> QWidget:
        self._layout.insertWidget(self._layout.count() - 1, widget)
        return widget

    def add_task_card(self, controller: FeatureController) -> None:
        if not controller.busy or controller.task_tab != self._tab:
            return
        card = CardSurface(palette=self._palette, radius=24, shadow=False)
        row = QHBoxLayout(card)
        row.setContentsMargins(18, 14, 18, 14)
        row.setSpacing(14)
        row.addWidget(
            Spinner(
                color=self._palette.get("primary", "#6750A4"),
                size=22,
                stroke=2.2,
                parent=card,
            ),
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        title = make_label(
            f"{controller.task_label}…",
            size=12.5,
            weight=QFont.Weight.DemiBold,
            color=self._palette.get("on_surface", "#1D1B20"),
        )
        message = make_label(
            controller.task_message or tr("task_wait_finish", "Подождите, операция выполняется."),
            size=11,
            color=self._palette.get("on_surface_variant", "#49454F"),
        )
        message.setWordWrap(True)
        column.addWidget(title)
        column.addWidget(message)
        row.addLayout(column, 1)
        self._task_label = message
        self.add(card)

    def add_busy_elsewhere(self, controller: FeatureController) -> None:
        if not controller.busy or controller.task_tab in (None, self._tab):
            return
        self.add(
            NoticeBanner(
                palette=self._palette,
                text=tr(
                    "task_busy_elsewhere",
                    f"Сейчас выполняется операция «{controller.task_label}» "
                    "на другой вкладке. Действия на этой вкладке временно "
                    "недоступны.",
                    tab=controller.task_label,
                    name=controller.task_label,
                ),
                icon="info",
                background=self._palette.get("secondary_container", "#E8DEF8"),
                foreground=self._palette.get("on_secondary_container", "#1D192B"),
            )
        )

    def set_task_message(self, text: str) -> None:
        if self._task_label is not None:
            self._task_label.setText(text or tr("task_wait_finish", "Подождите, операция выполняется."))


def message_banner(
    palette: Mapping[str, str], message: Optional[FeatureMessage]
) -> Optional[NoticeBanner]:
    if message is None or not message.text:
        return None
    kind = message.kind
    if kind == "success":
        background = palette.get("primary_container", "#EADDFF")
        foreground = palette.get("on_primary_container", "#21005D")
        icon = "check"
    elif kind == "warning":
        background = palette.get("tertiary_container", "#FFD8E4")
        foreground = palette.get("on_tertiary_container", "#31111D")
        icon = "warning"
    elif kind == "error":
        background = palette.get("error_container", "#F9DEDC")
        foreground = palette.get("on_error_container", "#410E0B")
        icon = "warning"
    else:
        background = palette.get("secondary_container", "#E8DEF8")
        foreground = palette.get("on_secondary_container", "#1D192B")
        icon = "info"
    return NoticeBanner(
        palette=palette,
        text=message.text,
        icon=icon,
        background=background,
        foreground=foreground,
    )


def _section(
    palette: Mapping[str, str],
    icon: str,
    title: str,
    subtitle: Optional[str] = None,
    *,
    accent: str = "primary",
    trailing: Optional[QWidget] = None,
    custom_icon: Optional[QWidget] = None,
) -> tuple[CardSurface, QVBoxLayout]:
    card = CardSurface(palette=palette, radius=28, shadow=False)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(24, 20, 24, 22)
    layout.setSpacing(14)

    header = QHBoxLayout()
    header.setContentsMargins(0, 0, 0, 0)
    header.setSpacing(10)
    if custom_icon is not None:
        header.addWidget(custom_icon, 0, Qt.AlignmentFlag.AlignTop)
    else:
        header.addWidget(
            IconWidget(icon, palette.get(accent, "#6750A4"), 20),
            0,
            Qt.AlignmentFlag.AlignTop,
        )
    titles = QVBoxLayout()
    titles.setContentsMargins(0, 0, 0, 0)
    titles.setSpacing(1)
    titles.addWidget(
        make_label(
            title,
            size=14,
            weight=QFont.Weight.DemiBold,
            color=palette.get("on_surface", "#1D1B20"),
        )
    )
    if subtitle:
        subtitle_label = make_label(
            subtitle,
            size=10.5,
            color=palette.get("on_surface_variant", "#49454F"),
            wrap=True,
        )
        titles.addWidget(subtitle_label)
    header.addLayout(titles, 1)
    if trailing is not None:
        header.addWidget(trailing, 0, Qt.AlignmentFlag.AlignTop)
    layout.addLayout(header)

    body = QVBoxLayout()
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(10)
    layout.addLayout(body)
    return card, body


def _fact(
    palette: Mapping[str, str],
    label: str,
    value: str,
) -> QWidget:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(1)
    layout.addWidget(
        make_label(
            label,
            size=9.5,
            weight=QFont.Weight.DemiBold,
            color=palette.get("on_surface_variant", "#49454F"),
            letter_spacing=0.4,
        )
    )
    value_label = ElidedLabel(
        value or "—",
        font=ui_font(12.5, QFont.Weight.DemiBold),
        color=palette.get("on_surface", "#1D1B20"),
    )
    value_label.setToolTip(value or "—")
    layout.addWidget(value_label)
    return widget


def _fact_grid(
    palette: Mapping[str, str],
    facts: Sequence[tuple[str, str]],
    *,
    columns: int = 2,
) -> QWidget:
    widget = QWidget()
    grid = QGridLayout(widget)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(20)
    grid.setVerticalSpacing(12)
    for index, (label, value) in enumerate(facts):
        row, column = divmod(index, columns)
        grid.addWidget(_fact(palette, label, value), row, column)
    for column in range(columns):
        grid.setColumnStretch(column, 1)
    return widget


def _spinner_row(palette: Mapping[str, str], text: str) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 4, 0, 4)
    layout.setSpacing(10)
    layout.addWidget(
        Spinner(color=palette.get("primary", "#6750A4"), size=18, stroke=2.0, parent=row),
        0,
        Qt.AlignmentFlag.AlignVCenter,
    )
    layout.addWidget(
        make_label(
            text,
            size=12,
            color=palette.get("on_surface_variant", "#49454F"),
        ),
        0,
        Qt.AlignmentFlag.AlignVCenter,
    )
    layout.addStretch(1)
    return row


def _empty_row(
    palette: Mapping[str, str],
    icon: str,
    title: str,
    description: str,
) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 8, 0, 8)
    layout.setSpacing(14)
    badge = IconBadge(
        icon,
        size=44,
        radius=14,
        background=palette.get("surface_container_high", "#ECE6F0"),
        foreground=palette.get("on_surface_variant", "#49454F"),
        icon_size=22,
    )
    layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
    column = QVBoxLayout()
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(2)
    column.addWidget(
        make_label(
            title,
            size=12.5,
            weight=QFont.Weight.DemiBold,
            color=palette.get("on_surface", "#1D1B20"),
            wrap=True,
        )
    )
    column.addWidget(
        make_label(
            description,
            size=11,
            color=palette.get("on_surface_variant", "#49454F"),
            wrap=True,
        )
    )
    layout.addLayout(column, 1)
    return row


def _path_row(
    palette: Mapping[str, str],
    path: str,
    *,
    empty_text: str,
) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(14, 10, 10, 10)
    layout.setSpacing(12)
    layout.addWidget(
        IconWidget("folder", palette.get("primary", "#6750A4"), 22),
        0,
        Qt.AlignmentFlag.AlignVCenter,
    )
    column = QVBoxLayout()
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(1)
    if path:
        column.addWidget(
            ElidedLabel(
                Path(path).name or path,
                font=ui_font(12, QFont.Weight.DemiBold),
                color=palette.get("on_surface", "#1D1B20"),
            )
        )
        detail = ElidedLabel(
            path,
            font=ui_font(10.5, QFont.Weight.Normal),
            color=palette.get("on_surface_variant", "#49454F"),
        )
        detail.setToolTip(path)
        column.addWidget(detail)
    else:
        column.addWidget(
            make_label(
                empty_text,
                size=11.5,
                color=palette.get("on_surface_variant", "#49454F"),
            )
        )
    layout.addLayout(column, 1)
    return row


def _fact_section(
    palette: Mapping[str, str],
    icon: str,
    title: str,
    facts: Sequence[tuple[str, str]],
    *,
    subtitle: Optional[str] = None,
    trailing: Optional[QWidget] = None,
) -> CardSurface:
    card, body = _section(
        palette, icon, title, subtitle, trailing=trailing
    )
    body.addWidget(_fact_grid(palette, facts))
    return card


# ---------------------------------------------------------------------------
# Firmware tab
# ---------------------------------------------------------------------------


def build_firmware_page(
    palette: Mapping[str, str],
    info: PixelDeviceInfo,
    state: FirmwareState,
    controller: FeatureController,
) -> FeaturePage:
    page = FeaturePage(palette, tab=1)
    page.add_task_card(controller)
    page.add_busy_elsewhere(controller)
    banner = message_banner(palette, state.message)
    if banner is not None:
        page.add(banner)

    mode = (info.mode or "").lower()
    device_state = (info.state or "").lower()
    adb_device = mode == "adb" and device_state == "device"
    sideload = mode == "adb" and device_state == "sideload"
    fastboot = mode == "fastboot"
    busy = controller.busy

    if sideload:
        page.add(
            NoticeBanner(
                palette=palette,
                text=tr(
                    "fw_banner_sideload",
                    "Pixel находится в режиме sideload. Выберите OTA-файл и "
                    "запустите установку — после завершения устройство "
                    "перезагрузится само.",
                ),
                icon="update",
                background=palette.get("primary_container", "#EADDFF"),
                foreground=palette.get("on_primary_container", "#21005D"),
            )
        )
    elif fastboot:
        page.add(
            NoticeBanner(
                palette=palette,
                text=tr(
                    "fw_banner_fastboot",
                    "Pixel в режиме Fastboot: сведения о сборке недоступны, "
                    "но устройство можно перезагрузить в систему.",
                ),
                icon="terminal",
                background=palette.get("secondary_container", "#E8DEF8"),
                foreground=palette.get("on_secondary_container", "#1D192B"),
            )
        )

    if adb_device:
        refresh = ActionButton(
            tr("apps_refresh", "Обновить"),
            palette=palette,
            icon="refresh",
            kind="outlined",
            height=34,
        )
        refresh.setEnabled(not busy)
        refresh.clicked.connect(controller.load_build)
        card, body = _section(
            palette,
            "update",
            tr("fw_build_section", "Сборка Android"),
            tr("fw_build_desc", "Свойства прошивки, прочитанные через getprop"),
            trailing=refresh,
        )
        build = state.build
        if state.build_loading or (build is None and busy):
            body.addWidget(_spinner_row(palette, tr("fw_build_reading", "Читаем свойства сборки…")))
        elif build is not None and build.error:
            body.addWidget(
                NoticeBanner(
                    palette=palette,
                    text=build.error,
                    icon="warning",
                    background=palette.get("error_container", "#F9DEDC"),
                    foreground=palette.get("on_error_container", "#410E0B"),
                )
            )
            retry = ActionButton(
                tr("dev_check_again", "Повторить"),
                palette=palette,
                icon="refresh",
                kind="tonal",
                height=36,
            )
            retry.setEnabled(not busy)
            retry.clicked.connect(controller.load_build)
            body.addWidget(retry, 0, Qt.AlignmentFlag.AlignLeft)
        elif build is not None:
            facts = (
                ("Android", f"Android {build.android_version}" if build.android_version else "—"),
                (tr("fw_build_id", "Сборка"), build.build_id or "—"),
                (tr("fw_security_patch", "Патч безопасности"), build.security_patch or "—"),
                (tr("fw_build_date", "Дата сборки"), build.build_date or "—"),
                (tr("fw_build_type", "Тип сборки"), build.build_type or "—"),
                (tr("fw_device_target", "Устройство"), build.device or tr(info.display_name)),
            )
            body.addWidget(_fact_grid(palette, facts))
            if build.fingerprint:
                fingerprint = make_label(
                    build.fingerprint,
                    size=10,
                    color=palette.get("on_surface_variant", "#49454F"),
                    wrap=True,
                )
                fingerprint.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                fingerprint.setToolTip(build.fingerprint)
                body.addWidget(fingerprint)
            if build.warnings:
                body.addWidget(
                    NoticeBanner(
                        palette=palette,
                        text="\n".join(build.warnings),
                        icon="info",
                        background=palette.get("tertiary_container", "#FFD8E4"),
                        foreground=palette.get("on_tertiary_container", "#31111D"),
                    )
                )
        else:
            body.addWidget(
                make_label(
                    tr("fw_build_no_data_desc", "Нажмите «Обновить», чтобы прочитать свойства сборки."),
                    size=11.5,
                    color=palette.get("on_surface_variant", "#49454F"),
                    wrap=True,
                )
            )
        page.add(card)

    # Reboot modes ---------------------------------------------------------
    card, body = _section(
        palette,
        "terminal",
        tr("fw_reboot_section", "Режимы загрузки"),
        tr("fw_reboot_desc", "Команда выполняется через ADB или Fastboot"),
        accent="tertiary",
    )
    body.addWidget(
        make_label(
            tr(
                "fw_reboot_fastboot_hint",
                "Перезагрузка прервёт текущие операции на устройстве. "
                "Не отключайте кабель во время перезагрузки.",
            ),
            size=11,
            color=palette.get("on_surface_variant", "#49454F"),
            wrap=True,
        )
    )
    buttons = QHBoxLayout()
    buttons.setContentsMargins(0, 2, 0, 0)
    buttons.setSpacing(8)

    def reboot_target(target: str, label: str, description: str) -> ActionButton:
        button = ActionButton(
            label,
            palette=palette,
            icon="refresh" if target == "system" else None,
            kind="tonal" if target == "sideload" else "outlined",
            height=38,
        )
        button.setEnabled(not busy)

        def clicked() -> None:
            if target == "system":
                controller.reboot(target)
            else:
                controller.confirm(
                    tr("fw_reboot_confirm_title", f"Перезагрузить в {description}?", desc=description),
                    tr(
                        "fw_reboot_confirm_msg",
                        f"Pixel отправит команду перезагрузки в режим "
                        f"{description}. Текущая работа на устройстве будет "
                        "прервана.",
                        desc=description,
                    ),
                    tr("fw_reboot_confirm_btn", "Перезагрузить"),
                    lambda: controller.reboot(target),
                    danger=False,
                )

        button.clicked.connect(clicked)
        return button

    system_button = reboot_target("system", tr("fw_reboot_btn_system", "Система"), tr("fw_target_system", "систему"))
    recovery_button = reboot_target("recovery", tr("fw_reboot_btn_recovery", "Recovery"), tr("fw_target_recovery", "Recovery"))
    bootloader_button = reboot_target("bootloader", tr("fw_reboot_btn_bootloader", "Fastboot"), tr("fw_target_bootloader", "Fastboot"))
    fastbootd_button = reboot_target("fastboot", tr("fw_reboot_btn_fastbootd", "Fastbootd"), tr("fw_target_fastbootd", "fastbootd (userspace)"))
    sideload_button = reboot_target("sideload", tr("fw_reboot_btn_sideload", "Sideload"), tr("fw_target_sideload", "Sideload"))

    fastbootd_button.setToolTip(tr("fw_tip_reboot_fastbootd", "Режим fastbootd для динамических разделов (Android 10+)"))

    if fastboot:
        recovery_button.setEnabled(False)
        sideload_button.setEnabled(False)
        fastbootd_button.setEnabled(not busy)
        recovery_button.setToolTip(tr("fw_tip_reboot_recovery", "Доступно после загрузки Android через ADB"))
        sideload_button.setToolTip(tr("fw_tip_reboot_sideload", "Доступно после загрузки Android через ADB"))
    elif sideload:
        recovery_button.setEnabled(False)
        sideload_button.setEnabled(False)
        fastbootd_button.setEnabled(False)
        system_button.setEnabled(not busy)
        recovery_button.setToolTip(tr("fw_tip_fastboot_recovery", "Сначала завершите установку обновления"))
        sideload_button.setToolTip(tr("fw_tip_fastboot_sideload", "Устройство уже в режиме sideload"))
    elif not adb_device:
        system_button.setEnabled(False)
        recovery_button.setEnabled(False)
        bootloader_button.setEnabled(False)
        fastbootd_button.setEnabled(False)
        sideload_button.setEnabled(False)
        system_button.setToolTip(tr("fw_tip_reboot_system", "Дождитесь загрузки Android"))

    for button in (
        system_button,
        recovery_button,
        bootloader_button,
        fastbootd_button,
        sideload_button,
    ):
        buttons.addWidget(button)
    buttons.addStretch(1)
    body.addLayout(buttons)
    page.add(card)

    # Slots & Bootloader ----------------------------------------------------
    slot_card, slot_body = _section(
        palette,
        "lock",
        tr("fw_slots_section", "Слоты A/B и статус загрузчика"),
        tr("fw_slots_desc", "Активный раздел загрузки и состояние OEM-блокировки"),
        accent="primary",
    )
    slot_row = QHBoxLayout()
    slot_row.setContentsMargins(0, 0, 0, 0)
    slot_row.setSpacing(10)

    cur_slot = (info.current_slot or "").upper()
    slot_row.addWidget(
        Pill(
            tr("fw_slot_current", f"Активный слот: {cur_slot or 'N/A'}", slot=cur_slot or "N/A"),
            background=palette.get("primary_container", "#EADDFF"),
            foreground=palette.get("on_primary_container", "#21005D"),
            height=30,
        )
    )

    if info.bootloader_locked is True:
        lock_label = tr("fw_slot_locked", "Загрузчик заблокирован")
        lock_bg = "surface_container_high"
        lock_fg = "on_surface_variant"
    elif info.bootloader_locked is False:
        lock_label = tr("fw_slot_unlocked", "Загрузчик разблокирован")
        lock_bg = "error_container"
        lock_fg = "on_error_container"
    else:
        lock_label = tr("fw_slot_status_unknown", "Загрузчик: нет данных")
        lock_bg = "surface_container_high"
        lock_fg = "on_surface_variant"

    slot_row.addWidget(
        Pill(
            lock_label,
            background=palette.get(lock_bg, "#ECE6F0"),
            foreground=palette.get(lock_fg, "#49454F"),
            height=30,
        )
    )

    if info.is_fastbootd:
        slot_row.addWidget(
            Pill(
                tr("fw_btn_fastbootd", "Режим fastbootd"),
                background=palette.get("tertiary_container", "#FFD8E4"),
                foreground=palette.get("on_tertiary_container", "#31111D"),
                height=30,
            )
        )

    slot_row.addStretch(1)

    if fastboot:
        next_slot = "b" if cur_slot == "A" else "a"
        switch_btn = ActionButton(
            tr("fw_btn_switch_slot", f"Переключить на Слот {next_slot.upper()}", slot=next_slot.upper()),
            palette=palette,
            icon="refresh",
            kind="tonal",
            height=36,
        )
        switch_btn.setEnabled(not busy)
        switch_btn.setToolTip(tr("fw_tip_switch_slot", f"Выполнить fastboot --set-active={next_slot}", slot=next_slot))
        switch_btn.clicked.connect(
            lambda: controller.confirm(
                tr("fw_confirm_slot_title", f"Переключить активный слот на {next_slot.upper()}?", slot=next_slot.upper()),
                tr(
                    "fw_confirm_slot_msg",
                    f"Pixel переключит активный загрузочный слот на {next_slot.upper()}. При следующем запуске устройство загрузится из этого слота.",
                    slot=next_slot.upper(),
                ),
                tr("fw_btn_switch_confirm", "Переключить"),
                lambda: controller.set_active_slot(next_slot),
                danger=False,
            )
        )
        slot_row.addWidget(switch_btn)

    slot_body.addLayout(slot_row)
    page.add(slot_card)

    # OTA ------------------------------------------------------------------
    card, body = _section(
        palette,
        "download",
        tr("fw_sideload_title", "OTA-обновление"),
        tr("fw_sideload_desc", "Установка официального OTA-образа через adb sideload"),
        accent="secondary",
    )
    body.addWidget(
        make_label(
            tr(
                "fw_sideload_guide",
                "Скачайте OTA-образ для своей модели и сборки. Установка идёт "
                "только в режиме sideload; не отключайте USB-кабель.",
            ),
            size=11,
            color=palette.get("on_surface_variant", "#49454F"),
            wrap=True,
        )
    )

    file_row = _path_row(
        palette,
        state.zip_path,
        empty_text=tr("fw_sideload_empty_file", "OTA-файл (.zip) не выбран"),
    )
    choose = ActionButton(
        tr("fw_sideload_choose_btn", "Выбрать ZIP"),
        palette=palette,
        icon="file",
        kind="outlined",
        height=36,
    )
    choose.setEnabled(not busy)
    choose.clicked.connect(controller.choose_ota)
    file_layout = file_row.layout()
    if isinstance(file_layout, QHBoxLayout):
        file_layout.addWidget(choose, 0, Qt.AlignmentFlag.AlignVCenter)
    body.addWidget(file_row)

    action_row = QHBoxLayout()
    action_row.setContentsMargins(0, 2, 0, 0)
    action_row.setSpacing(10)
    if sideload:
        install = ActionButton(
            tr("fw_sideload_install_btn", "Установить OTA"),
            palette=palette,
            icon="upload",
            kind="filled",
            height=40,
        )
        install.setEnabled(not busy and bool(state.zip_path))
        if not state.zip_path:
            install.setToolTip(tr("fw_tip_install_ota", "Сначала выберите OTA-файл"))
        install.clicked.connect(controller.install_ota)
        action_row.addWidget(install)
    else:
        go_sideload = ActionButton(
            tr("fw_sideload_reboot_btn", "Перезагрузить в sideload"),
            palette=palette,
            icon="update",
            kind="tonal",
            height=40,
        )
        go_sideload.setEnabled(not busy and mode == "adb" and not fastboot)
        go_sideload.clicked.connect(
            lambda: controller.confirm(
                tr("fw_confirm_sideload_title", "Перезагрузить в sideload?"),
                tr(
                    "fw_confirm_sideload_msg",
                    "Pixel перезагрузится в режим установки обновления. "
                    "После перезагрузки выберите OTA-файл и нажмите "
                    "«Установить OTA»."
                ),
                tr("fw_btn_reboot_action", "Перезагрузить"),
                lambda: controller.reboot("sideload"),
            )
        )
        action_row.addWidget(go_sideload)
    action_row.addStretch(1)
    body.addLayout(action_row)
    page.add(card)

    # Official Google Firmware links --------------------------------------
    links_card, links_body = _section(
        palette,
        "open_in_new",
        tr("fw_links_section", "Официальные ресурсы Google"),
        tr("fw_links_desc", "Заводские прошивки, Full OTA и WebUSB инструмент Google"),
        accent="secondary",
    )
    links_body.addWidget(
        make_label(
            tr(
                "fw_links_guide",
                "Google публикует официальные сборки для всех поколений Pixel. "
                "Вы можете загрузить их напрямую с официального сайта:",
            ),
            size=11,
            color=palette.get("on_surface_variant", "#49454F"),
            wrap=True,
        )
    )
    for link_title, link_desc, link_url in (
        (
            tr("fw_link_full_ota_title", "Full OTA Images"),
            tr("fw_link_full_ota_desc", "Официальные полные OTA-образы для установки через adb sideload без разблокировки загрузчика."),
            "https://developers.google.com/android/ota",
        ),
        (
            tr("fw_link_factory_title", "Factory Images"),
            tr("fw_link_factory_desc", "Полные заводские образы с таблицами разделов для чистого восстановления через Fastboot."),
            "https://developers.google.com/android/images",
        ),
        (
            tr("fw_link_webusb_title", "Android Flash Tool (WebUSB)"),
            tr("fw_link_webusb_desc", "Фирменный браузерный инструмент Google для прошивки устройства по WebUSB в один клик."),
            "https://flash.android.com",
        ),
    ):
        link_row = QWidget()
        lr_layout = QHBoxLayout(link_row)
        lr_layout.setContentsMargins(0, 4, 0, 4)
        lr_layout.setSpacing(10)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        col.addWidget(
            make_label(
                link_title,
                size=12,
                weight=QFont.Weight.DemiBold,
                color=palette.get("on_surface", "#1D1B20"),
            )
        )
        col.addWidget(
            make_label(
                link_desc,
                size=10.5,
                color=palette.get("on_surface_variant", "#49454F"),
                wrap=True,
            )
        )
        lr_layout.addLayout(col, 1)

        open_btn = ActionButton(
            tr("fw_link_btn_open", "Открыть сайт"),
            palette=palette,
            icon="open_in_new",
            kind="outlined",
            height=34,
        )
        open_btn.clicked.connect(
            lambda checked=False, target_url=link_url: controller.open_browser_url(
                target_url
            )
        )
        lr_layout.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        links_body.addWidget(link_row)

    page.add(links_card)

    page.add(
        NoticeBanner(
            palette=palette,
            text=tr(
                "fw_factory_hint",
                "Полные factory images не прошиваются: для них нужен "
                "разблокированный загрузчик и fastboot flash. Этот раздел "
                "безопасно работает с OTA sideload и режимами загрузки.",
            ),
            icon="shield",
            background=palette.get("surface_container_high", "#ECE6F0"),
            foreground=palette.get("on_surface_variant", "#49454F"),
        )
    )
    return page


# ---------------------------------------------------------------------------
# Apps tab
# ---------------------------------------------------------------------------




def _permission_label(permission: str) -> str:
    return get_permission_label(permission)


def _permission_row(
    palette: Mapping[str, str],
    package: str,
    permission: str,
    granted: bool,
    controller: FeatureController,
) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 4, 0, 4)
    layout.setSpacing(10)

    column = QVBoxLayout()
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(1)
    column.addWidget(
        make_label(
            _permission_label(permission),
            size=12,
            weight=QFont.Weight.DemiBold,
            color=palette.get("on_surface", "#1D1B20"),
        )
    )
    full = make_label(
        permission,
        size=9.5,
        color=palette.get("on_surface_variant", "#49454F"),
    )
    full.setToolTip(permission)
    column.addWidget(full)
    layout.addLayout(column, 1)

    layout.addWidget(
        Pill(
            tr("apps_perm_granted_pill", "Разрешено") if granted else tr("apps_perm_denied_pill", "Запрещено"),
            background=palette.get(
                "primary_container" if granted else "surface_container_high",
                "#EADDFF",
            ),
            foreground=palette.get(
                "on_primary_container" if granted else "on_surface_variant",
                "#21005D",
            ),
            height=26,
        ),
        0,
        Qt.AlignmentFlag.AlignVCenter,
    )

    action = ActionButton(
        tr("apps_perm_btn_revoke", "Отозвать") if granted else tr("apps_perm_btn_grant", "Выдать"),
        palette=palette,
        kind="outlined" if granted else "tonal",
        height=34,
    )
    action.setEnabled(not controller.busy)
    action.clicked.connect(
        lambda checked=False, value=granted: controller.set_permission(
            package, permission, not value
        )
    )
    layout.addWidget(action, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


def _build_apps_list(
    page: FeaturePage,
    palette: Mapping[str, str],
    state: AppsState,
    controller: FeatureController,
) -> None:
    busy = controller.busy

    toolbar, body = _section(
        palette,
        "search",
        tr("apps_search_section_title", "Поиск приложений"),
        tr("apps_search_section_desc", "Пакеты, установленные на устройстве через ADB"),
        accent="secondary",
    )
    search = IconTextField(
        palette=palette,
        placeholder=tr("apps_search_input_placeholder", "Имя пакета, например com.android.chrome"),
        icon="search",
    )
    search.setText(state.query)
    body.addWidget(search)

    controls = QHBoxLayout()
    controls.setContentsMargins(0, 0, 0, 0)
    controls.setSpacing(10)

    system_chip = FilterChip(tr("apps_btn_filter_system", "Системные приложения"), palette=palette)
    system_chip.setChecked(state.include_system)
    system_chip.setEnabled(not busy)

    def toggle_system(checked: bool) -> None:
        if checked == state.include_system:
            return
        state.include_system = checked
        controller.load_packages()

    system_chip.toggled.connect(toggle_system)
    controls.addWidget(system_chip)

    refresh = ActionButton(
        tr("apps_refresh", "Обновить"),
        palette=palette,
        icon="refresh",
        kind="text",
        height=34,
    )
    refresh.setEnabled(not busy)
    refresh.clicked.connect(controller.load_packages)
    controls.addWidget(refresh)
    controls.addStretch(1)

    install = ActionButton(
        tr("apps_btn_install_apk_action", "Установить APK"),
        palette=palette,
        icon="download",
        kind="filled",
        height=38,
    )
    install.setEnabled(not busy)
    install.clicked.connect(controller.install_apk)
    controls.addWidget(install)
    body.addLayout(controls)
    page.add(toolbar)

    count_pill = Pill(
        tr("apps_count_loading", "Загрузка…") if state.loading else f"{len(state.packages or [])}",
        background=palette.get("surface_container_high", "#ECE6F0"),
        foreground=palette.get("on_surface_variant", "#49454F"),
        height=26,
    )
    list_card, list_body = _section(
        palette,
        "apps",
        tr("apps_installed_packages", "Установленные пакеты"),
        None,
        trailing=count_pill,
    )

    if state.loading and state.packages is None:
        list_body.addWidget(_spinner_row(palette, tr("apps_msg_reading_pkgs", "Читаем список пакетов…")))
    elif state.packages is None:
        list_body.addWidget(
            _empty_row(
                palette,
                "apps",
                tr("apps_msg_list_not_loaded", "Список ещё не загружен"),
                tr("apps_msg_list_not_loaded_hint", "Нажмите «Обновить», чтобы прочитать пакеты с устройства."),
            )
        )
    elif not state.packages:
        list_body.addWidget(
            _empty_row(
                palette,
                "apps",
                tr("apps_msg_none_found", "Приложения не найдены"),
                tr("apps_msg_none_found_hint", "На устройстве нет пользовательских пакетов или ADB не вернул список."),
            )
        )
    else:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setFixedHeight(330)
        holder = QWidget()
        holder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 4, 0)
        holder_layout.setSpacing(0)

        rows: list[tuple[PackageRow, str]] = []
        for entry in state.packages:
            row = PackageRow(entry, palette=palette, controller=controller)
            row.setEnabled(not busy)
            row.clicked.connect(
                lambda checked=False, value=entry.package: controller.open_package(
                    value
                )
            )
            holder_layout.addWidget(row)
            rows.append((row, entry.package.casefold()))
        holder_layout.addStretch(1)
        scroll.setWidget(holder)
        list_body.addWidget(scroll)

        no_matches = make_label(
            tr("apps_msg_no_matches", "Ничего не найдено. Измените запрос."),
            size=11.5,
            color=palette.get("on_surface_variant", "#49454F"),
            wrap=True,
        )
        no_matches.setVisible(False)
        list_body.addWidget(no_matches)

        def apply_filter(text: str) -> None:
            state.query = text
            needle = text.strip().casefold()
            visible = 0
            for row, package in rows:
                match = not needle or needle in package
                row.setVisible(match)
                visible += 1 if match else 0
            no_matches.setVisible(visible == 0)
            count_pill.set_content(
                str(visible),
                background=palette.get("surface_container_high", "#ECE6F0"),
                foreground=palette.get("on_surface_variant", "#49454F"),
            )

        search.textChanged.connect(apply_filter)
        if state.query:
            apply_filter(state.query)
    page.add(list_card)


def _build_app_details(
    page: FeaturePage,
    palette: Mapping[str, str],
    state: AppsState,
    controller: FeatureController,
) -> None:
    package = state.selected or ""
    busy = controller.busy
    entry = next(
        (item for item in (state.packages or []) if item.package == package),
        None,
    )
    system_app = bool(entry and entry.system)
    is_uninstalled = bool(
        (entry and entry.uninstalled) or (state.details and state.details.uninstalled)
    )
    is_disabled = bool(
        (state.details and state.details.disabled) or (entry and entry.disabled)
    )

    top = QHBoxLayout()
    top.setContentsMargins(0, 0, 0, 0)
    top.setSpacing(10)
    back = ActionButton(
        tr("apps_btn_back", "Все приложения"),
        palette=palette,
        icon="back",
        kind="outlined",
        height=36,
    )
    back.clicked.connect(lambda: controller.open_package(None))
    top.addWidget(back)
    top.addStretch(1)
    refresh = ActionButton(
        tr("apps_btn_refresh_list", "Обновить сведения"),
        palette=palette,
        icon="refresh",
        kind="text",
        height=36,
    )
    refresh.setEnabled(not busy)
    refresh.clicked.connect(lambda: controller.open_package(package))
    top.addWidget(refresh)
    top_widget = QWidget()
    top_widget.setLayout(top)
    page.add(top_widget)

    app_icon = AppIconBadge(package, palette=palette, size=28, radius=8)
    if controller and controller.register_icon_badge:
        controller.register_icon_badge(package, app_icon)

    header, body = _section(
        palette,
        "apps",
        package,
        tr("apps_details_sub", "Сведения о пакете и разрешениях"),
        accent="secondary",
        custom_icon=app_icon,
    )
    details = state.details
    if state.details_loading or (details is None and busy):
        body.addWidget(_spinner_row(palette, tr("apps_details_reading", "Читаем сведения о приложении…")))
    elif details is not None and details.error:
        body.addWidget(
            NoticeBanner(
                palette=palette,
                text=details.error,
                icon="warning",
                background=palette.get("error_container", "#F9DEDC"),
                foreground=palette.get("on_error_container", "#410E0B"),
            )
        )
    elif details is not None:
        pills = QHBoxLayout()
        pills.setContentsMargins(0, 0, 0, 0)
        pills.setSpacing(8)
        if details.version_name:
            pills.addWidget(
                Pill(
                    tr("apps_version_prefix", "Версия {ver}", ver=details.version_name),
                    background=palette.get("primary_container", "#EADDFF"),
                    foreground=palette.get("on_primary_container", "#21005D"),
                    height=26,
                )
            )
        pills.addWidget(
            Pill(
                tr("apps_meta_system", "Системное") if system_app else tr("apps_meta_user", "Пользовательское"),
                background=palette.get("secondary_container", "#E8DEF8"),
                foreground=palette.get("on_secondary_container", "#1D192B"),
                height=26,
            )
        )
        if is_uninstalled:
            pills.addWidget(
                Pill(
                    tr("apps_uninstalled", "Удалено"),
                    background=palette.get("error_container", "#F9DEDC"),
                    foreground=palette.get("on_error_container", "#410E0B"),
                    height=26,
                )
            )
        elif is_disabled:
            pills.addWidget(
                Pill(
                    tr("apps_disabled", "Заморожено"),
                    background=palette.get("tertiary_container", "#FFD8E4"),
                    foreground=palette.get("on_tertiary_container", "#31111D"),
                    height=26,
                )
            )
        pills.addStretch(1)
        body.addLayout(pills)

        facts = (
            (tr("apps_details_col_version", "Версия"), details.version_name or "—"),
            (tr("apps_details_col_version_code", "Код версии"), details.version_code or "—"),
            (tr("apps_details_col_target_sdk", "Целевой SDK"), details.target_sdk or "—"),
            (tr("apps_details_col_min_sdk", "Минимальный SDK"), details.min_sdk or "—"),
            (tr("apps_details_col_installer", "Установщик"), details.installer or "—"),
            (tr("apps_details_col_package", "Пакет"), package),
        )
        body.addWidget(_fact_grid(palette, facts))
    else:
        body.addWidget(
            _empty_row(
                palette,
                "apps",
                tr("apps_no_details_title", "Нет сведений"),
                tr("apps_no_details_desc", "Нажмите «Обновить сведения», чтобы прочитать пакет."),
            )
        )
    page.add(header)

    # Quick Actions ---------------------------------------------------------
    if details is not None and not details.error:
        actions_card, actions_body = _section(
            palette,
            "play",
            tr("apps_actions_title", "Быстрые действия"),
            tr("apps_actions_desc", "Управление процессом, состоянием активности и APK"),
            accent="primary",
        )
        actions_row = QHBoxLayout()
        actions_row.setContentsMargins(0, 2, 0, 0)
        actions_row.setSpacing(10)

        # 1. Launch
        launch_btn = ActionButton(
            tr("apps_btn_launch", "Запустить"),
            palette=palette,
            icon="play",
            kind="filled",
            height=38,
        )
        launch_btn.setEnabled(not busy and not is_disabled)
        launch_btn.setToolTip(tr("apps_tip_launch", "Запустить приложение на устройстве"))
        launch_btn.clicked.connect(lambda: controller.launch_package(package))
        actions_row.addWidget(launch_btn)

        # 2. Stop
        stop_btn = ActionButton(
            tr("apps_btn_stop", "Остановить"),
            palette=palette,
            icon="stop",
            kind="outlined",
            height=38,
        )
        stop_btn.setEnabled(not busy)
        stop_btn.setToolTip(tr("apps_tip_stop", "Принудительно остановить процесс (am force-stop)"))
        stop_btn.clicked.connect(lambda: controller.force_stop_package(package))
        actions_row.addWidget(stop_btn)

        # 3. Freeze / Unfreeze
        if is_disabled:
            freeze_btn = ActionButton(
                tr("apps_btn_unfreeze", "Разморозить"),
                palette=palette,
                icon="lock",
                kind="tonal",
                height=38,
            )
            freeze_btn.setEnabled(not busy)
            freeze_btn.setToolTip(tr("apps_tip_unfreeze", "Включить приложение (pm enable)"))
            freeze_btn.clicked.connect(
                lambda: controller.freeze_package(package, False)
            )
        else:
            freeze_btn = ActionButton(
                tr("apps_btn_freeze", "Заморозить"),
                palette=palette,
                icon="lock",
                kind="outlined",
                height=38,
            )
            freeze_btn.setEnabled(not busy)
            freeze_btn.setToolTip(tr("apps_tip_freeze", "Отключить приложение для пользователя 0 (pm disable-user)"))

            def on_freeze_clicked() -> None:
                if system_app:
                    controller.confirm(
                        tr("apps_confirm_freeze_title", "Заморозить системное приложение?"),
                        tr(
                            "apps_confirm_freeze_sys_msg",
                            "Приложение {pkg} будет отключено для текущего пользователя "
                            "и скрыто из меню. Разморозить его можно в любой момент.",
                            pkg=package,
                        ),
                        tr("apps_btn_freeze_confirm", "Заморозить"),
                        lambda: controller.freeze_package(package, True),
                        danger=False,
                    )
                else:
                    controller.freeze_package(package, True)

            freeze_btn.clicked.connect(on_freeze_clicked)
        actions_row.addWidget(freeze_btn)

        # 4. Clear data
        clear_btn = ActionButton(
            tr("apps_btn_clear", "Очистить данные"),
            palette=palette,
            icon="trash",
            kind="outlined",
            height=38,
        )
        clear_btn.setEnabled(not busy)
        clear_btn.setToolTip(tr("apps_tip_clear", "Очистить все данные и кэш приложения (pm clear)"))
        clear_btn.clicked.connect(
            lambda: controller.confirm(
                tr("apps_confirm_clear_title", "Очистить данные приложения?"),
                tr(
                    "apps_confirm_clear_msg",
                    "Все данные, кэш и настройки приложения {package} будут безвозвратно удалены.",
                    package=package,
                ),
                tr("apps_btn_clear_confirm", "Очистить"),
                lambda: controller.clear_package_data(package),
                danger=True,
            )
        )
        actions_row.addWidget(clear_btn)

        # 5. Export APK
        export_btn = ActionButton(
            tr("apps_btn_export", "Извлечь APK"),
            palette=palette,
            icon="download",
            kind="tonal",
            height=38,
        )
        export_btn.setEnabled(not busy)
        export_btn.setToolTip(tr("apps_tip_export", "Сохранить APK-файл с устройства на локальный диск"))
        export_btn.clicked.connect(lambda: controller.export_apk(package))
        actions_row.addWidget(export_btn)

        actions_row.addStretch(1)
        actions_body.addLayout(actions_row)
        page.add(actions_card)

    if details is not None and not details.error:
        permissions_card, permissions_body = _section(
            palette,
            "shield",
            tr("apps_section_permissions", "Разрешения"),
            tr("apps_perm_section_desc", "Выдача и отзыв выполняются через pm grant / pm revoke"),
            accent="tertiary",
        )
        if details.runtime:
            for permission, granted in sorted(details.runtime.items()):
                permissions_body.addWidget(
                    _permission_row(
                        palette,
                        package,
                        permission,
                        granted,
                        controller,
                    )
                )
        else:
            permissions_body.addWidget(
                _empty_row(
                    palette,
                    "shield",
                    tr("apps_perm_empty_title", "Нет runtime-разрешений"),
                    tr(
                        "apps_perm_empty_desc",
                        "Приложение не запрашивает разрешения, которые можно "
                        "выдать или отозвать через ADB.",
                    ),
                )
            )
        if details.requested:
            permissions_body.addWidget(
                make_label(
                    tr(
                        "apps_perm_requested_lbl",
                        "Всего запрошено разрешений: {count}",
                        count=len(details.requested),
                    ),
                    size=10.5,
                    color=palette.get("on_surface_variant", "#49454F"),
                )
            )
        page.add(permissions_card)

        if is_uninstalled:
            restore_card, restore_body = _section(
                palette,
                "refresh",
                tr("apps_section_restore", "Восстановление приложения"),
                tr(
                    "apps_restore_sub",
                    "Пакет деблоатирован (удалён для пользователя 0), но доступен в прошивке",
                ),
                accent="primary",
            )
            restore_body.addWidget(
                make_label(
                    tr(
                        "apps_restore_desc",
                        "Это системное приложение было удалено для текущего пользователя. "
                        "Вы можете восстановить его обратно в систему без перепрошивки.",
                    ),
                    size=11.5,
                    color=palette.get("on_surface_variant", "#49454F"),
                    wrap=True,
                )
            )
            restore_btn = ActionButton(
                tr("apps_btn_restore", "Восстановить приложение"),
                palette=palette,
                icon="refresh",
                kind="filled",
                height=40,
            )
            restore_btn.setEnabled(not busy)
            restore_btn.clicked.connect(lambda: controller.restore_system(package))
            restore_body.addWidget(restore_btn, 0, Qt.AlignmentFlag.AlignLeft)
            page.add(restore_card)

        danger_card, danger_body = _section(
            palette,
            "trash",
            tr("apps_danger_section_title", "Удаление"),
            tr("apps_danger_section_desc", "Пакет будет удалён с устройства"),
            accent="error",
        )
        if system_app:
            danger_body.addWidget(
                make_label(
                    tr(
                        "apps_danger_uninstall_desc",
                        "Удаление системного приложения деблоатирует его для текущего "
                        "пользователя (pm uninstall --user 0). Вы сможете восстановить его "
                        "в любой момент через Pixel Tools.",
                    ),
                    size=11,
                    color=palette.get("on_surface_variant", "#49454F"),
                    wrap=True,
                )
            )
            uninstall_sys = ActionButton(
                tr("apps_btn_uninstall_user", "Удалить для пользователя"),
                palette=palette,
                icon="trash",
                kind="danger",
                height=38,
            )
            uninstall_sys.setEnabled(not busy and not is_uninstalled)
            uninstall_sys.clicked.connect(
                lambda: controller.confirm(
                    tr("apps_confirm_uninstall_sys_title", "Удалить системное приложение?"),
                    tr(
                        "apps_confirm_uninstall_sys_msg",
                        "Пакет {pkg} будет удалён для пользователя 0. "
                        "Вы сможете восстановить его в любой момент кнопкой «Восстановить».",
                        pkg=package,
                    ),
                    tr("apps_btn_uninstall_confirm", "Удалить"),
                    lambda: controller.uninstall_system(package),
                    danger=True,
                )
            )
            danger_body.addWidget(uninstall_sys, 0, Qt.AlignmentFlag.AlignLeft)
        else:
            danger_body.addWidget(
                make_label(
                    tr(
                        "apps_uninstall_user_warn",
                        "Данные приложения будут удалены. Действие нельзя "
                        "отменить.",
                    ),
                    size=11,
                    color=palette.get("on_surface_variant", "#49454F"),
                    wrap=True,
                )
            )
            uninstall = ActionButton(
                tr("apps_btn_uninstall_app", "Удалить приложение"),
                palette=palette,
                icon="trash",
                kind="danger",
                height=38,
            )
            uninstall.setEnabled(not busy)
            uninstall.clicked.connect(lambda: controller.uninstall(package))
            danger_body.addWidget(uninstall, 0, Qt.AlignmentFlag.AlignLeft)
        page.add(danger_card)


def build_apps_page(
    palette: Mapping[str, str],
    info: PixelDeviceInfo,
    state: AppsState,
    controller: FeatureController,
) -> FeaturePage:
    page = FeaturePage(palette, tab=2)
    page.add_task_card(controller)
    page.add_busy_elsewhere(controller)
    banner = message_banner(palette, state.message)
    if banner is not None:
        page.add(banner)

    if state.selected:
        _build_app_details(page, palette, state, controller)
    else:
        _build_apps_list(page, palette, state, controller)
    return page


# ---------------------------------------------------------------------------
# Backup tab
# ---------------------------------------------------------------------------


def _option_title(key: str) -> str:
    option = BACKUP_OPTIONS_BY_KEY.get(key)
    fallback = option.title if option is not None else key
    return tr(f"backup_opt_{key}_title", fallback)


def _backup_option_titles(keys: Sequence[object]) -> str:
    titles = [_option_title(str(key)) for key in keys]
    return ", ".join(titles) if titles else "—"


def _format_created(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    text = text.replace("T", " ")
    return text.split("+", 1)[0].split(".", 1)[0]


def _build_backup_result(
    page: FeaturePage,
    palette: Mapping[str, str],
    state: BackupState,
    controller: FeatureController,
) -> None:
    result = state.result
    if result is None:
        return
    if result.error:
        card, body = _section(
            palette,
            "warning",
            tr("backup_result_failed_title", "Резервная копия не создана"),
            None,
            accent="error",
        )
        body.addWidget(
            NoticeBanner(
                palette=palette,
                text=result.error,
                icon="warning",
                background=palette.get("error_container", "#F9DEDC"),
                foreground=palette.get("on_error_container", "#410E0B"),
            )
        )
        page.add(card)
        return

    title = (
        tr("backup_result_success_title", "Резервная копия создана")
        if not result.warnings
        else tr("backup_result_warn_title", "Копия создана с замечаниями")
    )
    card, body = _section(
        palette,
        "check",
        title,
        result.destination,
        accent="primary",
    )
    remote_titles = {
        option.remote_path: tr(f"backup_opt_{option.key}_title", option.title)
        for option in BACKUP_OPTIONS
        if option.remote_path
    }
    copied = [remote_titles.get(path, path) for path in result.copied]
    facts = (
        (tr("backup_result_col_copied", "Скопировано"), ", ".join(copied) if copied else "—"),
        (
            tr("backup_result_col_contacts", "Контакты"),
            tr("backup_contacts_count", "{count} записей", count=result.contacts_count)
            if result.contacts_count
            else "—",
        ),
        (
            tr("backup_result_col_packages", "Список приложений"),
            "packages.txt" if result.packages_written else tr("backup_not_saved", "не сохранён"),
        ),
        (tr("backup_result_col_folder", "Папка"), Path(result.destination).name or result.destination),
    )
    body.addWidget(_fact_grid(palette, facts))
    if result.warnings:
        body.addWidget(
            NoticeBanner(
                palette=palette,
                text="\n".join(result.warnings),
                icon="warning",
                background=palette.get("tertiary_container", "#FFD8E4"),
                foreground=palette.get("on_tertiary_container", "#31111D"),
            )
        )
    open_button = ActionButton(
        tr("backup_result_btn_open", "Открыть папку"),
        palette=palette,
        icon="folder",
        kind="tonal",
        height=36,
    )
    open_button.clicked.connect(
        lambda: controller.open_folder(result.destination)
    )
    body.addWidget(open_button, 0, Qt.AlignmentFlag.AlignLeft)
    page.add(card)


def _build_restore_result(
    page: FeaturePage,
    palette: Mapping[str, str],
    state: BackupState,
) -> None:
    result = state.restore_result
    if result is None:
        return
    if result.error:
        card, body = _section(
            palette,
            "warning",
            tr("backup_restore_failed_title", "Восстановление не выполнено"),
            None,
            accent="error",
        )
        body.addWidget(
            NoticeBanner(
                palette=palette,
                text=result.error,
                icon="warning",
                background=palette.get("error_container", "#F9DEDC"),
                foreground=palette.get("on_error_container", "#410E0B"),
            )
        )
        page.add(card)
        return
    card, body = _section(
        palette,
        "check",
        tr("backup_restore_success_title", "Файлы восстановлены"),
        result.source,
        accent="primary",
    )
    if result.pushed:
        body.addWidget(
            make_label(
                tr("backup_result_restore_pushed", "Скопировано: ") + ", ".join(result.pushed),
                size=11.5,
                color=palette.get("on_surface", "#1D1B20"),
                wrap=True,
            )
        )
    if result.warnings:
        body.addWidget(
            NoticeBanner(
                palette=palette,
                text="\n".join(result.warnings),
                icon="warning",
                background=palette.get("tertiary_container", "#FFD8E4"),
                foreground=palette.get("on_tertiary_container", "#31111D"),
            )
        )
    page.add(card)


def build_backup_page(
    palette: Mapping[str, str],
    info: PixelDeviceInfo,
    state: BackupState,
    controller: FeatureController,
) -> FeaturePage:
    page = FeaturePage(palette, tab=3)
    page.add_task_card(controller)
    page.add_busy_elsewhere(controller)
    banner = message_banner(palette, state.message)
    if banner is not None:
        page.add(banner)
    busy = controller.busy

    # Sections -------------------------------------------------------------
    options_card, options_body = _section(
        palette,
        "backup",
        tr("backup_section_options_title", "Что сохранить"),
        tr("backup_section_options_desc", "Медиа и документы копируются через adb pull"),
        accent="primary",
    )
    options_body.addWidget(
        make_label(
            tr(
                "backup_options_hint",
                "Выберите разделы. Список приложений сохраняется всегда, "
                "контакты — в формате contacts.vcf.",
            ),
            size=11,
            color=palette.get("on_surface_variant", "#49454F"),
            wrap=True,
        )
    )
    grid_holder = QWidget()
    grid = QGridLayout(grid_holder)
    grid.setContentsMargins(0, 2, 0, 0)
    grid.setHorizontalSpacing(10)
    grid.setVerticalSpacing(8)
    create_button: Optional[ActionButton] = None
    summary = make_label("", size=10.5, color=palette.get("on_surface_variant", "#49454F"))

    checklist: list[CheckOption] = []

    def refresh_summary() -> None:
        count = len(state.selected)
        summary.setText(
            tr(
                "backup_selected_count",
                "Выбрано разделов: {count} из {total}",
                count=count,
                total=len(BACKUP_OPTIONS),
            )
            if count
            else tr("backup_select_at_least_one", "Выберите хотя бы один раздел")
        )
        if create_button is not None:
            create_button.setEnabled(not busy and bool(state.selected))

    for index, option in enumerate(BACKUP_OPTIONS):
        check = CheckOption(
            tr(f"backup_opt_{option.key}_title", option.title),
            tr(f"backup_opt_{option.key}_desc", option.description),
            palette=palette,
        )
        check.setChecked(option.key in state.selected)
        check.setEnabled(not busy)

        def toggle(checked: bool, key: str = option.key) -> None:
            if checked:
                state.selected.add(key)
            else:
                state.selected.discard(key)
            refresh_summary()

        check.toggled.connect(toggle)
        checklist.append(check)
        row, column = divmod(index, 2)
        grid.addWidget(check, row, column)
    grid.setColumnStretch(0, 1)
    grid.setColumnStretch(1, 1)
    options_body.addWidget(grid_holder)
    options_body.addWidget(summary)
    page.add(options_card)

    # Destination ----------------------------------------------------------
    destination_card, destination_body = _section(
        palette,
        "folder",
        tr("backup_section_destination_title", "Куда сохранить"),
        tr("backup_section_destination_desc", "Новая папка для резервной копии на компьютере"),
        accent="secondary",
    )
    path_widget = _path_row(
        palette,
        state.destination,
        empty_text=tr("backup_destination_empty", "Папка назначения не выбрана"),
    )
    change = ActionButton(
        tr("backup_btn_change", "Изменить"),
        palette=palette,
        icon="folder",
        kind="outlined",
        height=34,
    )
    change.setEnabled(not busy)
    change.clicked.connect(controller.choose_backup_dir)
    path_layout = path_widget.layout()
    if isinstance(path_layout, QHBoxLayout):
        path_layout.addWidget(change, 0, Qt.AlignmentFlag.AlignVCenter)
    destination_body.addWidget(path_widget)
    destination_body.addWidget(
        make_label(
            tr(
                "backup_destination_hint",
                "Резервная копия размещается в отдельной папке и содержит "
                "backup-info.json — по нему работает восстановление.",
            ),
            size=10.5,
            color=palette.get("on_surface_variant", "#49454F"),
            wrap=True,
        )
    )

    create_row = QHBoxLayout()
    create_row.setContentsMargins(0, 2, 0, 0)
    create_row.setSpacing(10)
    create_button = ActionButton(
        tr("backup_btn_create", "Создать резервную копию"),
        palette=palette,
        icon="backup",
        kind="filled",
        height=40,
    )
    create_button.setEnabled(not busy and bool(state.selected))
    create_button.clicked.connect(controller.create_backup)
    create_row.addWidget(create_button)
    create_row.addStretch(1)
    destination_body.addLayout(create_row)
    page.add(destination_card)

    _build_backup_result(page, palette, state, controller)

    # Restore --------------------------------------------------------------
    restore_card, restore_body = _section(
        palette,
        "upload",
        tr("backup_restore_title", "Восстановление"),
        tr("backup_restore_hero_desc", "Файлы из резервной копии Pixel Tools"),
        accent="tertiary",
    )
    restore_body.addWidget(
        make_label(
            tr(
                "backup_restore_hero_hint",
                "Выберите папку, созданную этим приложением. Медиа будет "
                "скопировано обратно в /sdcard, contacts.vcf — в Download.",
            ),
            size=11,
            color=palette.get("on_surface_variant", "#49454F"),
            wrap=True,
        )
    )
    restore_path = _path_row(
        palette,
        state.restore_dir,
        empty_text=tr("backup_restore_empty_dir", "Папка резервной копии не выбрана"),
    )
    choose_restore = ActionButton(
        tr("backup_choose_dir", "Выбрать папку"),
        palette=palette,
        icon="folder",
        kind="outlined",
        height=34,
    )
    choose_restore.setEnabled(not busy)
    choose_restore.clicked.connect(controller.choose_restore_dir)
    restore_layout = restore_path.layout()
    if isinstance(restore_layout, QHBoxLayout):
        restore_layout.addWidget(choose_restore, 0, Qt.AlignmentFlag.AlignVCenter)
    restore_body.addWidget(restore_path)

    metadata = state.restore_info
    if metadata is not None:
        keys = metadata.get("options", [])
        if not isinstance(keys, list):
            keys = []
        facts = (
            (tr("dev_model", "Модель"), str(metadata.get("model") or "—")),
            (tr("dev_serial", "Серийный номер"), str(metadata.get("serial") or "—")),
            (tr("backup_meta_created", "Создано"), _format_created(metadata.get("created"))),
            (tr("backup_meta_sections", "Разделы"), _backup_option_titles(keys)),
        )
        restore_body.addWidget(_fact_grid(palette, facts))
        warnings = metadata.get("warnings", [])
        if isinstance(warnings, list) and warnings:
            restore_body.addWidget(
                NoticeBanner(
                    palette=palette,
                    text="\n".join(str(item) for item in warnings),
                    icon="warning",
                    background=palette.get("tertiary_container", "#FFD8E4"),
                    foreground=palette.get("on_tertiary_container", "#31111D"),
                )
            )
        restore_button = ActionButton(
            tr("backup_restore_start", "Восстановить файлы"),
            palette=palette,
            icon="upload",
            kind="tonal",
            height=40,
        )
        restore_button.setEnabled(not busy)
        restore_button.clicked.connect(controller.restore_backup)
        restore_body.addWidget(restore_button, 0, Qt.AlignmentFlag.AlignLeft)
    else:
        restore_body.addWidget(
            make_label(
                tr(
                    "backup_restore_missing_info_hint",
                    "В папке должен находиться файл backup-info.json, созданный "
                    "Pixel Tools. Без него восстановление недоступно.",
                ),
                size=10.5,
                color=palette.get("on_surface_variant", "#49454F"),
                wrap=True,
            )
        )
    page.add(restore_card)

    _build_restore_result(page, palette, state)

    refresh_summary()
    page.add(
        NoticeBanner(
            palette=palette,
            text=tr(
                "backup_contacts_notice",
                "Контакты восстанавливаются как contacts.vcf в папке "
                "Download: откройте «Контакты» на Pixel и импортируйте файл. "
                "Приложение не меняет системные настройки и не требует root.",
            ),
            icon="person",
            background=palette.get("surface_container_high", "#ECE6F0"),
            foreground=palette.get("on_surface_variant", "#49454F"),
        )
    )
    return page


# ---------------------------------------------------------------------------
# Mirror Page (Screen Mirroring & Control)
# ---------------------------------------------------------------------------


def build_mirror_page(
    palette: Mapping[str, str],
    info: PixelDeviceInfo,
    state: MirrorState,
    controller: FeatureController,
) -> FeaturePage:
    page = FeaturePage(palette, tab=4)
    page.add_task_card(controller)
    page.add_busy_elsewhere(controller)
    banner = message_banner(palette, state.message)
    if banner is not None:
        page.add(banner)
    busy = controller.busy

    # Hero / Launch Section
    hero_card, hero_body = _section(
        palette,
        "cast",
        tr("mirror_hero_title", "Трансляция экрана Pixel"),
        tr("mirror_hero_desc", "Передача видеопотока низкой задержки (60 FPS) с полным управлением"),
        accent="primary",
    )

    if not state.is_running:
        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        if state.scrcpy_ready:
            status_row.addWidget(
                Pill(
                    tr("mirror_ready", "scrcpy v5.0 готов к запуску"),
                    background=palette.get("surface_container_highest", "#E6E0E9"),
                    foreground=palette.get("on_surface", "#1D1B20"),
                    dot=palette.get("primary", "#6750A4"),
                )
            )
        else:
            status_row.addWidget(
                Pill(
                    tr("mirror_missing", "Требуется движок трансляции"),
                    background=palette.get("error_container", "#F9DEDC"),
                    foreground=palette.get("on_error_container", "#410E0B"),
                    dot=palette.get("error", "#B3261E"),
                )
            )
        status_row.addStretch(1)
        hero_body.addLayout(status_row)

    hero_body.addWidget(
        make_label(
            tr(
                "mirror_banner_warning",
                "Трансляция экрана позволяет взаимодействовать с Google Pixel с помощью "
                "мыши и клавиатуры, транслировать системный звук на ПК, быстро делать снимки "
                "и копировать буфер обмена.",
            ),
            size=11,
            color=palette.get("on_surface_variant", "#49454F"),
            wrap=True,
        )
    )

    cta_row = QHBoxLayout()
    cta_row.setSpacing(12)
    if state.is_running:
        stop_btn = ActionButton(
            tr("mirror_stop", "Остановить трансляцию"),
            palette=palette,
            icon="close",
            kind="tonal",
            height=44,
        )
        stop_btn.clicked.connect(controller.stop_mirror)
        cta_row.addWidget(stop_btn)
    elif state.scrcpy_ready:
        start_btn = ActionButton(
            tr("mirror_start", "Запустить трансляцию"),
            palette=palette,
            icon="cast",
            kind="filled",
            height=44,
        )
        start_btn.setEnabled(not busy)
        start_btn.clicked.connect(controller.start_mirror)
        cta_row.addWidget(start_btn)
    else:
        install_btn = ActionButton(
            tr("mirror_btn_download_engine", "Загрузить движок трансляции (scrcpy)"),
            palette=palette,
            icon="download",
            kind="filled",
            height=44,
        )
        install_btn.setEnabled(not busy)
        install_btn.clicked.connect(controller.install_scrcpy)
        cta_row.addWidget(install_btn)

    screenshot_btn = ActionButton(
        tr("mirror_screenshot", "Снимок экрана"),
        palette=palette,
        icon="screenshot",
        kind="tonal",
        height=44,
    )
    screenshot_btn.setEnabled(not busy)
    screenshot_btn.clicked.connect(controller.take_screenshot)
    cta_row.addWidget(screenshot_btn)
    cta_row.addStretch(1)
    hero_body.addLayout(cta_row)
    page.add(hero_card)

    # Mirroring Options
    opts_card, opts_body = _section(
        palette,
        "settings",
        tr("mirror_settings_title", "Параметры трансляции"),
        tr("mirror_settings_desc", "Настройки видеопотока, звука и поведения окна"),
        accent="tertiary",
    )

    grid_holder = QWidget()
    grid = QGridLayout(grid_holder)
    grid.setContentsMargins(0, 4, 0, 0)
    grid.setHorizontalSpacing(12)
    grid.setVerticalSpacing(8)

    check_off = CheckOption(
        tr("mirror_opt_turn_off", "Гасить дисплей телефона"),
        tr("mirror_opt_turn_off_desc", "Выключает экран Pixel во время трансляции (снижает нагрев и экономит заряд)"),
        palette=palette,
    )
    check_off.setChecked(state.turn_screen_off)
    check_off.toggled.connect(
        lambda c: controller.toggle_mirror_option("turn_screen_off", c)
    )
    grid.addWidget(check_off, 0, 0)

    check_ontop = CheckOption(
        tr("mirror_opt_top", "Поверх всех окон"),
        tr("mirror_opt_top_desc", "Окно трансляции закрепляется поверх других окон Windows"),
        palette=palette,
    )
    check_ontop.setChecked(state.always_on_top)
    check_ontop.toggled.connect(
        lambda c: controller.toggle_mirror_option("always_on_top", c)
    )
    grid.addWidget(check_ontop, 0, 1)

    check_awake = CheckOption(
        tr("mirror_opt_awake", "Не усыплять устройство"),
        tr("mirror_opt_awake_desc", "Предотвращает автоматический переход экрана в спящий режим"),
        palette=palette,
    )
    check_awake.setChecked(state.stay_awake)
    check_awake.toggled.connect(
        lambda c: controller.toggle_mirror_option("stay_awake", c)
    )
    grid.addWidget(check_awake, 1, 0)

    check_audio = CheckOption(
        tr("mirror_opt_audio", "Трансляция звука"),
        tr("mirror_opt_audio_desc", "Передавать аудиопоток с устройства на динамики компьютера"),
        palette=palette,
    )
    check_audio.setChecked(state.audio)
    check_audio.toggled.connect(
        lambda c: controller.toggle_mirror_option("audio", c)
    )
    grid.addWidget(check_audio, 1, 1)

    check_ro = CheckOption(
        tr("mirror_opt_readonly", "Режим «Только просмотр»"),
        tr("mirror_opt_readonly_desc", "Отключает управление с ПК (нажатия мыши и клавиатуры не передаются)"),
        palette=palette,
    )
    check_ro.setChecked(state.read_only)
    check_ro.toggled.connect(
        lambda c: controller.toggle_mirror_option("read_only", c)
    )
    grid.addWidget(check_ro, 2, 0)

    grid.setColumnStretch(0, 1)
    grid.setColumnStretch(1, 1)
    opts_body.addWidget(grid_holder)
    page.add(opts_card)

    page.add(
        NoticeBanner(
            palette=palette,
            text=tr(
                "mirror_hotkeys_hint",
                "Горячие клавиши в окне трансляции: Alt+F — полный экран; "
                "Alt+O — выключить экран устройства; Alt+P — питание; "
                "Alt+H — Домой; Alt+B — Назад; Alt+S — недавние приложения; "
                "перетаскивание APK в окно — быстрая установка.",
            ),
            icon="info",
            background=palette.get("surface_container_high", "#ECE6F0"),
            foreground=palette.get("on_surface_variant", "#49454F"),
        )
    )
    return page
