"""Floating hardware-control panel docked beside the scrcpy window.

The panel is a separate always-on-top tool window, so the Pixel controls sit
next to the mirrored phone instead of taking space inside Pixel Tools.  On
Windows it tracks the scrcpy HWND in physical screen coordinates and uses
``WS_EX_NOACTIVATE`` so pressing a button never steals keyboard focus from
the mirror.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Callable, Mapping, Optional

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QVBoxLayout, QWidget

from qt_material import draw_icon, soft_shadow, with_alpha
from qt_widgets import _PressFeedbackMixin
from locales import tr


BUTTON_SIZE = 44
CARD_RADIUS = 24

# Android key codes used by the panel.
KEY_BACK = 4
KEY_HOME = 3
KEY_RECENTS = 187
KEY_POWER = 26
KEY_VOLUME_UP = 24
KEY_VOLUME_DOWN = 25
KEY_VOLUME_MUTE = 164


# ---------------------------------------------------------------------------
# Windows helpers
# ---------------------------------------------------------------------------


class _Rect:
    """Plain physical-pixel rectangle independent of ctypes structures."""

    __slots__ = ("left", "top", "right", "bottom")

    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


class _Win32:
    """Small, defensive wrapper around the Win32 calls used for docking."""

    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    SWP_FRAMECHANGED = 0x0020
    SWP_SHOWWINDOW = 0x0040
    SWP_NOOWNERZORDER = 0x0200

    GWL_EXSTYLE = -20
    GWLP_HWNDPARENT = -8
    GW_HWNDPREV = 3
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000

    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    MONITOR_DEFAULTTONEAREST = 2

    _initialised = False
    _user32 = None
    _dwmapi = None
    _monitor_info_type = None
    _get_long = None
    _set_long = None

    @classmethod
    def _init(cls) -> bool:
        if cls._initialised:
            return cls._user32 is not None
        cls._initialised = True
        if sys.platform != "win32":
            return False

        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD),
                ]

            WNDENUMPROC = ctypes.WINFUNCTYPE(
                wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
            )

            user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetWindow.restype = wintypes.HWND
            user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
            user32.EnumWindows.restype = wintypes.BOOL
            user32.IsWindowVisible.argtypes = [wintypes.HWND]
            user32.IsWindowVisible.restype = wintypes.BOOL
            user32.IsIconic.argtypes = [wintypes.HWND]
            user32.IsIconic.restype = wintypes.BOOL
            user32.GetWindowThreadProcessId.argtypes = [
                wintypes.HWND,
                ctypes.POINTER(wintypes.DWORD),
            ]
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
            user32.GetWindowTextLengthW.restype = ctypes.c_int
            user32.GetWindowTextW.argtypes = [
                wintypes.HWND,
                wintypes.LPWSTR,
                ctypes.c_int,
            ]
            user32.GetWindowTextW.restype = ctypes.c_int
            user32.GetWindowRect.argtypes = [
                wintypes.HWND,
                ctypes.POINTER(wintypes.RECT),
            ]
            user32.GetWindowRect.restype = wintypes.BOOL
            user32.SetWindowPos.argtypes = [
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            ]
            user32.SetWindowPos.restype = wintypes.BOOL
            user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
            user32.MonitorFromWindow.restype = wintypes.HMONITOR
            user32.GetMonitorInfoW.argtypes = [
                wintypes.HMONITOR,
                ctypes.POINTER(MONITORINFO),
            ]
            user32.GetMonitorInfoW.restype = wintypes.BOOL

            get_long = getattr(user32, "GetWindowLongPtrW", None)
            set_long = getattr(user32, "SetWindowLongPtrW", None)
            if get_long is None or set_long is None:
                get_long = user32.GetWindowLongW
                set_long = user32.SetWindowLongW
            get_long.argtypes = [wintypes.HWND, ctypes.c_int]
            get_long.restype = ctypes.c_ssize_t
            set_long.argtypes = [
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_ssize_t,
            ]
            set_long.restype = ctypes.c_ssize_t

            dwmapi.DwmGetWindowAttribute.argtypes = [
                wintypes.HWND,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

            cls._user32 = user32
            cls._dwmapi = dwmapi
            cls._monitor_info_type = MONITORINFO
            cls._get_long = get_long
            cls._set_long = set_long
            return True
        except Exception:
            cls._user32 = None
            cls._dwmapi = None
            cls._get_long = None
            cls._set_long = None
            return False

    @classmethod
    def available(cls) -> bool:
        return cls._init()

    @classmethod
    def find_window(cls, pid: int, title: str) -> Optional[int]:
        """Find the visible top-level window owned by *pid*."""
        if not cls._init() or pid <= 0:
            return None
        user32 = cls._user32
        matches: list[tuple[bool, int]] = []
        wanted = (title or "").strip()

        def callback(hwnd, _lparam) -> bool:
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                owner = wintypes.DWORD(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                if owner.value != pid:
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                text = buffer.value.strip()
                if not text:
                    return True
                matches.append((text == wanted, int(hwnd)))
            except Exception:
                return True
            return True

        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )
        try:
            user32.EnumWindows(callback_type(callback), 0)
        except Exception:
            return None
        if not matches:
            return None
        for exact, hwnd in matches:
            if exact:
                return hwnd
        return matches[0][1]

    @classmethod
    def is_minimized(cls, hwnd: int) -> bool:
        if not cls._init():
            return False
        try:
            return bool(cls._user32.IsIconic(hwnd))
        except Exception:
            return False

    @classmethod
    def frame_rect(cls, hwnd: int) -> Optional[_Rect]:
        """Return the visible window frame in physical screen pixels."""
        if not cls._init():
            return None
        rect = wintypes.RECT()
        try:
            result = cls._dwmapi.DwmGetWindowAttribute(
                hwnd,
                cls.DWMWA_EXTENDED_FRAME_BOUNDS,
                ctypes.byref(rect),
                ctypes.sizeof(rect),
            )
            if result != 0:
                if not cls._user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    return None
        except Exception:
            return None
        frame = _Rect(rect.left, rect.top, rect.right, rect.bottom)
        if frame.width < 80 or frame.height < 80:
            return None
        return frame

    @classmethod
    def work_area(cls, hwnd: int) -> tuple[Optional[_Rect], Optional[_Rect]]:
        """Return the monitor work area and full monitor rectangle."""
        if not cls._init():
            return None, None
        try:
            monitor = cls._user32.MonitorFromWindow(
                hwnd, cls.MONITOR_DEFAULTTONEAREST
            )
            if not monitor:
                return None, None
            info = cls._monitor_info_type()
            info.cbSize = ctypes.sizeof(info)
            if not cls._user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return None, None
            work = _Rect(
                info.rcWork.left,
                info.rcWork.top,
                info.rcWork.right,
                info.rcWork.bottom,
            )
            full = _Rect(
                info.rcMonitor.left,
                info.rcMonitor.top,
                info.rcMonitor.right,
                info.rcMonitor.bottom,
            )
            return work, full
        except Exception:
            return None, None

    @classmethod
    def dpi_scale(cls, hwnd: int) -> float:
        if not cls._init():
            return 1.0
        try:
            get_dpi = getattr(cls._user32, "GetDpiForWindow", None)
            if get_dpi is None:
                return 1.0
            get_dpi.argtypes = [wintypes.HWND]
            get_dpi.restype = wintypes.UINT
            dpi = int(get_dpi(hwnd))
            return dpi / 96.0 if dpi > 0 else 1.0
        except Exception:
            return 1.0

    @classmethod
    def apply_no_activate(cls, hwnd: int) -> None:
        """Keep the panel out of Alt+Tab and away from keyboard focus."""
        if not cls._init() or cls._get_long is None or cls._set_long is None:
            return
        try:
            style = cls._get_long(hwnd, cls.GWL_EXSTYLE)
            cls._set_long(
                hwnd,
                cls.GWL_EXSTYLE,
                style | cls.WS_EX_NOACTIVATE | cls.WS_EX_TOOLWINDOW,
            )
            cls._user32.SetWindowPos(
                hwnd,
                0,
                0,
                0,
                0,
                0,
                cls.SWP_NOMOVE
                | cls.SWP_NOSIZE
                | cls.SWP_NOZORDER
                | cls.SWP_NOACTIVATE
                | cls.SWP_FRAMECHANGED,
            )
        except Exception:
            pass

    @classmethod
    def set_owner(cls, hwnd: int, owner_hwnd: int) -> None:
        """Set *owner_hwnd* as the owner of *hwnd* so their Z-orders stay tied."""
        if not cls._init() or not hwnd or not owner_hwnd or cls._set_long is None:
            return
        try:
            cls._set_long(hwnd, cls.GWLP_HWNDPARENT, owner_hwnd)
        except Exception:
            pass

    @classmethod
    def is_window_covered(cls, hwnd: int, panel_hwnd: int = 0) -> bool:
        """Return True if *hwnd* is covered / obscured by any window above it in Z-order."""
        if not cls._init() or not hwnd:
            return False
        user32 = cls._user32
        if user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd):
            return True

        frame = cls.frame_rect(hwnd)
        if frame is None or frame.width <= 0 or frame.height <= 0:
            return True

        frame_area = frame.width * frame.height
        cx = frame.left + frame.width // 2
        cy = frame.top + frame.height // 2
        total_covered = 0

        curr = hwnd
        while curr:
            prev = user32.GetWindow(curr, cls.GW_HWNDPREV)
            if not prev:
                break
            curr = prev

            if panel_hwnd and int(curr) == panel_hwnd:
                continue
            if not user32.IsWindowVisible(curr) or user32.IsIconic(curr):
                continue

            rect = wintypes.RECT()
            if not user32.GetWindowRect(curr, ctypes.byref(rect)):
                continue

            ix1 = max(frame.left, rect.left)
            iy1 = max(frame.top, rect.top)
            ix2 = min(frame.right, rect.right)
            iy2 = min(frame.bottom, rect.bottom)
            if ix2 > ix1 and iy2 > iy1:
                total_covered += (ix2 - ix1) * (iy2 - iy1)
                # If window covers the center of the mirror
                if rect.left <= cx <= rect.right and rect.top <= cy <= rect.bottom:
                    return True
                # If more than 15% is covered
                if total_covered > frame_area * 0.15:
                    return True

        return False

    @classmethod
    def position_window(
        cls,
        hwnd: int,
        x: int,
        y: int,
        width: int,
        height: int,
        *,
        always_on_top: bool = False,
    ) -> None:
        if not cls._init():
            return
        try:
            z_target = (
                wintypes.HWND(-1) if always_on_top else wintypes.HWND(-2)
            )
            cls._user32.SetWindowPos(
                hwnd,
                z_target,
                int(x),
                int(y),
                int(width),
                int(height),
                cls.SWP_NOACTIVATE | cls.SWP_SHOWWINDOW,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Panel widgets
# ---------------------------------------------------------------------------


class _PanelSurface(QWidget):
    """Rounded Material surface behind the hardware buttons."""

    def __init__(self, palette: Mapping[str, str]) -> None:
        super().__init__()
        self._palette = palette
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def set_palette(self, palette: Mapping[str, str]) -> None:
        self._palette = palette
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(
            QColor(self._palette.get("surface_container_low", "#F7F2FA"))
        )
        painter.setPen(
            QPen(
                with_alpha(
                    self._palette.get("outline_variant", "#CAC4D0"), 150
                ),
                1.0,
            )
        )
        painter.drawRoundedRect(rect, CARD_RADIUS, CARD_RADIUS)


class _KeyButton(_PressFeedbackMixin, QAbstractButton):
    """Square icon button with Material hover, ripple and pressed state."""

    def __init__(
        self,
        icon: str,
        tooltip: str,
        *,
        palette: Mapping[str, str],
        accent: bool = False,
    ) -> None:
        super().__init__()
        self._icon = icon
        self._palette = palette
        self._accent = accent
        self.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        self._init_press_feedback()

    def set_palette(self, palette: Mapping[str, str]) -> None:
        self._palette = palette
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
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)

        if self._accent:
            background = QColor(
                self._palette.get("primary_container", "#EADDFF")
            )
            foreground = QColor(
                self._palette.get("on_primary_container", "#21005D")
            )
        else:
            background = QColor(
                self._palette.get("surface_container_high", "#ECE6F0")
            )
            foreground = QColor(
                self._palette.get("on_surface_variant", "#49454F")
            )
            if self.underMouse():
                background = QColor(
                    self._palette.get(
                        "surface_container_highest", "#E6E0E9"
                    )
                )

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 15.0, 15.0)

        if self.isEnabled():
            self._paint_press_feedback(
                painter,
                rect,
                15.0,
                QColor(self._palette.get("primary", "#6750A4")),
                state_alpha=24,
                ripple_alpha=42,
            )

        icon_size = 22.0
        draw_icon(
            painter,
            self._icon,
            QRectF(
                self.width() / 2 - icon_size / 2,
                self.height() / 2 - icon_size / 2,
                icon_size,
                icon_size,
            ),
            foreground,
            stroke=1.8,
        )


class _Divider(QWidget):
    def __init__(self, palette: Mapping[str, str]) -> None:
        super().__init__()
        self._palette = palette
        self.setFixedHeight(9)

    def set_palette(self, palette: Mapping[str, str]) -> None:
        self._palette = palette
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(
            QPen(
                with_alpha(
                    self._palette.get("outline_variant", "#CAC4D0"), 130
                ),
                1.0,
            )
        )
        painter.drawLine(6, self.height() // 2, self.width() - 6, self.height() // 2)


class MirrorControlPanel(QWidget):
    """Always-on-top control panel that follows the scrcpy window."""

    def __init__(
        self,
        palette: Mapping[str, str],
        *,
        on_key: Callable[[int], None],
        on_screenshot: Optional[Callable[[], None]] = None,
        on_rotate: Optional[Callable[[], None]] = None,
        on_close: Optional[Callable[[], None]] = None,
        always_on_top: bool = False,
    ) -> None:
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self._palette = palette
        self._on_key = on_key
        self._on_screenshot = on_screenshot
        self._on_rotate = on_rotate
        self._on_close = on_close
        self._always_on_top = bool(always_on_top)
        self._process = None
        self._window_title = ""
        self._last_geometry: Optional[tuple[int, int, int, int]] = None
        self._owner_hwnd: int = 0

        self.setWindowTitle(tr("ctrl_panel_title"))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(35)
        self._follow_timer.timeout.connect(self._follow_window)

        self._surface = _PanelSurface(palette)
        soft_shadow(self._surface, blur=32, dy=8, alpha=60)
        self._buttons: list[_KeyButton] = []
        self._dividers: list[_Divider] = []
        self._build_layout()
        self.adjustSize()
        self.setFixedSize(self.sizeHint())

    # -- construction ---------------------------------------------------

    def _build_layout(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(0)
        outer.addWidget(self._surface)

        body = QVBoxLayout(self._surface)
        body.setContentsMargins(10, 12, 10, 12)
        body.setSpacing(6)

        specs: list[Optional[tuple[str, str, object, bool]]] = [
            ("power", tr("ctrl_power"), KEY_POWER, True),
            ("volume_up", tr("ctrl_vol_up"), KEY_VOLUME_UP, False),
            ("volume_down", tr("ctrl_vol_down"), KEY_VOLUME_DOWN, False),
            ("volume_mute", tr("ctrl_vol_mute"), KEY_VOLUME_MUTE, False),
            None,
            ("rotate", tr("ctrl_rotate"), "rotate", False),
            ("screenshot", tr("ctrl_screenshot"), "screenshot", False),
            None,
            ("back", tr("ctrl_back"), KEY_BACK, False),
            ("home", tr("ctrl_home"), KEY_HOME, False),
            ("apps", tr("ctrl_recents"), KEY_RECENTS, False),
        ]
        if self._on_close is not None:
            specs.append(None)
            specs.append(("close", tr("mirror_stop"), "close", False))

        for spec in specs:
            if spec is None:
                divider = _Divider(self._palette)
                self._dividers.append(divider)
                body.addWidget(divider)
                continue

            icon, tooltip, action, accent = spec
            button = _KeyButton(
                icon,
                tooltip,
                palette=self._palette,
                accent=accent,
            )
            if action == "screenshot":
                callback = self._on_screenshot
                if callback is not None:
                    button.clicked.connect(
                        lambda checked=False, fn=callback: fn()
                    )
                else:
                    button.setEnabled(False)
            elif action == "rotate":
                callback = self._on_rotate
                if callback is not None:
                    button.clicked.connect(
                        lambda checked=False, fn=callback: fn()
                    )
                else:
                    button.setEnabled(False)
            elif action == "close":
                callback = self._on_close
                if callback is not None:
                    button.clicked.connect(
                        lambda checked=False, fn=callback: fn()
                    )
                else:
                    button.setEnabled(False)
            elif isinstance(action, int):
                button.clicked.connect(
                    lambda checked=False, code=action: self._on_key(code)
                )
            self._buttons.append(button)
            body.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)

        self.setFixedWidth(
            BUTTON_SIZE
            + body.contentsMargins().left()
            + body.contentsMargins().right()
            + outer.contentsMargins().left()
            + outer.contentsMargins().right()
        )

    # -- public API -----------------------------------------------------

    @property
    def always_on_top(self) -> bool:
        return self._always_on_top

    def set_palette(self, palette: Mapping[str, str]) -> None:
        self._palette = palette
        self._surface.set_palette(palette)
        for button in self._buttons:
            button.set_palette(palette)
        for divider in self._dividers:
            divider.set_palette(palette)

    def attach(self, process, window_title: str = "") -> None:
        """Start following the HWND owned by *process*."""
        self._process = process
        self._window_title = window_title or ""
        self._last_geometry = None
        self._owner_hwnd = 0
        self._follow_timer.start()
        self._follow_window()

    def detach(self) -> None:
        """Stop following and hide the panel."""
        self._follow_timer.stop()
        self._process = None
        self._window_title = ""
        self._last_geometry = None
        self._owner_hwnd = 0
        self.hide()

    def shutdown(self) -> None:
        self.detach()
        self.close()
        self.deleteLater()

    # -- docking --------------------------------------------------------

    def _follow_window(self) -> None:
        process = self._process
        if process is None or not _Win32.available():
            self.hide()
            return

        poll = getattr(process, "poll", None)
        pid = getattr(process, "pid", None)
        if pid is None:
            self._follow_timer.stop()
            self._last_geometry = None
            self.hide()
            return
        if callable(poll) and poll() is not None:
            self._follow_timer.stop()
            self._last_geometry = None
            self.hide()
            return

        hwnd = _Win32.find_window(int(pid), self._window_title)
        if not hwnd:
            self._hide_for_now()
            return

        if not self._is_usable_window(hwnd):
            self._hide_for_now()
            return

        # Hide the companion panel if the mirror window is covered by other windows
        hwnd_self = int(self.winId()) if self.isVisible() else 0
        if _Win32.is_window_covered(hwnd, panel_hwnd=hwnd_self):
            self._hide_for_now()
            return

        frame = _Win32.frame_rect(hwnd)
        work, monitor = _Win32.work_area(hwnd)
        if frame is None or work is None or monitor is None:
            self._hide_for_now()
            return

        # scrcpy in true fullscreen leaves no desktop strip beside the phone.
        if (
            frame.left <= monitor.left + 2
            and frame.top <= monitor.top + 2
            and frame.right >= monitor.right - 2
            and frame.bottom >= monitor.bottom - 2
        ):
            self._hide_for_now()
            return

        scale = _Win32.dpi_scale(hwnd)
        panel_width = max(1, round(self.width() * scale))
        panel_height = max(1, round(self.height() * scale))
        gap = max(4, round(10 * scale))

        x = frame.right + gap
        if x + panel_width > work.right:
            left_x = frame.left - gap - panel_width
            if left_x >= work.left:
                x = left_x
            else:
                x = max(work.left, work.right - panel_width - gap)

        y = frame.top + (frame.height - panel_height) // 2
        y = max(work.top + gap, min(y, work.bottom - panel_height - gap))
        geometry = (int(x), int(y), panel_width, panel_height)

        if not self.isVisible():
            self.setWindowOpacity(0.0)
            hwnd_self = int(self.winId())
            _Win32.apply_no_activate(hwnd_self)
            if self._owner_hwnd != hwnd:
                _Win32.set_owner(hwnd_self, hwnd)
                self._owner_hwnd = hwnd
            self.show()

        if geometry != self._last_geometry:
            _Win32.position_window(
                int(self.winId()),
                *geometry,
                always_on_top=self._always_on_top,
            )
            self._last_geometry = geometry
        self.setWindowOpacity(1.0)

    def _is_usable_window(self, hwnd: int) -> bool:
        if _Win32.is_minimized(hwnd):
            return False
        # A restored window is visible by definition; a hidden owner is not.
        frame = _Win32.frame_rect(hwnd)
        return frame is not None

    def _hide_for_now(self) -> None:
        if self.isVisible():
            self.hide()
        self._last_geometry = None
