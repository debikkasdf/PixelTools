"""Reusable Material 3 Expressive widgets for Pixel Tools."""

from __future__ import annotations

from typing import Mapping, Optional

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
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
from locales import tr


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def make_label(
    text: str,
    *,
    size: float = 13,
    weight: QFont.Weight = QFont.Weight.Normal,
    color: str = "#000000",
    wrap: bool = False,
    align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft,
    display: bool = False,
    letter_spacing: float = 0.0,
    parent: QWidget | None = None,
) -> QLabel:
    label = QLabel(text, parent)
    label.setFont(
        ui_font(
            size,
            weight,
            display=display,
            letter_spacing=letter_spacing,
        )
    )
    label.setStyleSheet(f"color: {color}; background: transparent;")
    label.setWordWrap(wrap)
    label.setAlignment(align)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


class ElidedLabel(QWidget):
    """Single-line label that elides text instead of widening the layout."""

    def __init__(
        self,
        text: str,
        *,
        font: QFont,
        color: str | QColor,
        align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._text = text
        self._color = QColor(color)
        self._align = align
        self.setFont(font)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(QFontMetrics(font).height() + 2)
        if len(text) > 24:
            self.setToolTip(text)

    def setText(self, text: str) -> None:  # noqa: N802 (QLabel-like API)
        self._text = text
        if len(text) > 24:
            self.setToolTip(text)
        self.update()

    def text(self) -> str:
        return self._text

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, QFontMetrics(self.font()).height() + 2)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setFont(self.font())
        painter.setPen(self._color)
        text = QFontMetrics(self.font()).elidedText(
            self._text,
            Qt.TextElideMode.ElideRight,
            max(0, self.width() - 2),
        )
        painter.drawText(
            QRectF(0, 0, self.width(), self.height()),
            int(self._align | Qt.AlignmentFlag.AlignVCenter),
            text,
        )


class Dot(QWidget):
    def __init__(self, color: str, size: int = 4, parent=None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(QRectF(self.rect()))


class _FocusRingMixin:
    """Show a focus ring only when the control was reached by keyboard."""

    _keyboard_focus = False

    def focusInEvent(self, event) -> None:  # noqa: N802
        self._keyboard_focus = event.reason() in (
            Qt.FocusReason.TabFocusReason,
            Qt.FocusReason.BacktabFocusReason,
            Qt.FocusReason.ShortcutFocusReason,
        )
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self._keyboard_focus = False
        self.update()
        super().focusOutEvent(event)


class _PressFeedbackMixin(_FocusRingMixin):
    """Material-style pressed state: animated state layer, ripple and scale.

    The mixin only paints feedback; subclasses decide where to clip it and
    which palette role to use. Animations are parented to the button, so a
    theme rebuild (which recreates all widgets) cleans them up safely.
    """

    _PRESS_IN_MS = 150
    _PRESS_OUT_MS = 280
    _RIPPLE_MS = 560
    _PRESS_SCALE = 0.020
    _RIPPLE_FADE_START = 0.25

    def _init_press_feedback(self) -> None:
        self._press_progress = 0.0
        self._ripple_progress = 1.0
        self._ripple_origin = QPointF(0.0, 0.0)
        self._keyboard_press = False

        self._press_animation = QVariantAnimation(self)
        self._press_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._press_animation.valueChanged.connect(self._on_press_value)

        self._ripple_animation = QVariantAnimation(self)
        self._ripple_animation.setDuration(self._RIPPLE_MS)
        self._ripple_animation.setEasingCurve(QEasingCurve.Type.Linear)
        self._ripple_animation.valueChanged.connect(self._on_ripple_value)

    def _on_press_value(self, value: object) -> None:
        self._press_progress = float(value)
        self.update()

    def _on_ripple_value(self, value: object) -> None:
        self._ripple_progress = float(value)
        self.update()

    def _start_press_feedback(self, origin: QPointF) -> None:
        if not self.isEnabled():
            return

        self._ripple_origin = QPointF(origin)
        self._ripple_progress = 0.0
        self._ripple_animation.stop()
        self._ripple_animation.setStartValue(0.0)
        self._ripple_animation.setEndValue(1.0)
        self._ripple_animation.start()

        self._press_animation.stop()
        self._press_animation.setDuration(self._PRESS_IN_MS)
        self._press_animation.setStartValue(self._press_progress)
        self._press_animation.setEndValue(1.0)
        self._press_animation.start()

    def _release_press_feedback(self) -> None:
        self._press_animation.stop()
        self._press_animation.setDuration(self._PRESS_OUT_MS)
        self._press_animation.setStartValue(self._press_progress)
        self._press_animation.setEndValue(0.0)
        self._press_animation.start()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._start_press_feedback(event.position())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._release_press_feedback()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        activation_keys = (
            Qt.Key.Key_Space,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        )
        if (
            event.key() in activation_keys
            and not event.isAutoRepeat()
            and not self._keyboard_press
        ):
            self._keyboard_press = True
            self._start_press_feedback(
                QPointF(self.width() / 2.0, self.height() / 2.0)
            )
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        activation_keys = (
            Qt.Key.Key_Space,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        )
        if (
            event.key() in activation_keys
            and not event.isAutoRepeat()
            and self._keyboard_press
        ):
            self._keyboard_press = False
            self._release_press_feedback()
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        if self._keyboard_press:
            self._keyboard_press = False
            self._release_press_feedback()
        super().focusOutEvent(event)

    @staticmethod
    def _smoothstep(value: float) -> float:
        value = max(0.0, min(1.0, value))
        return value * value * (3.0 - 2.0 * value)

    def _apply_press_transform(self, painter: QPainter) -> None:
        """Shrink the painted button slightly while it is held."""
        if self._press_progress <= 0.001:
            return
        scale = 1.0 - self._PRESS_SCALE * self._press_progress
        center_x = self.width() / 2.0
        center_y = self.height() / 2.0
        painter.translate(center_x, center_y)
        painter.scale(scale, scale)
        painter.translate(-center_x, -center_y)

    def _paint_press_feedback(
        self,
        painter: QPainter,
        rect: QRectF,
        radius: float,
        color: QColor,
        *,
        state_alpha: int = 20,
        ripple_alpha: int = 38,
    ) -> None:
        """Paint the tonal layer and the expanding ripple inside *rect*."""
        if self._press_progress <= 0.001 and self._ripple_progress >= 0.999:
            return

        shape = QPainterPath()
        shape.addRoundedRect(rect, radius, radius)
        painter.save()
        painter.setClipPath(shape)
        painter.setPen(Qt.PenStyle.NoPen)

        if self._press_progress > 0.001:
            painter.setBrush(
                with_alpha(color, round(state_alpha * self._press_progress))
            )
            painter.drawPath(shape)

        progress = self._ripple_progress
        if 0.0 <= progress < 1.0:
            origin = self._ripple_origin
            dx = max(origin.x() - rect.left(), rect.right() - origin.x())
            dy = max(origin.y() - rect.top(), rect.bottom() - origin.y())
            max_radius = (dx * dx + dy * dy) ** 0.5 + 2.0
            ripple_radius = max_radius * (1.0 - (1.0 - progress) ** 3.0)
            fade_progress = (progress - self._RIPPLE_FADE_START) / (
                1.0 - self._RIPPLE_FADE_START
            )
            fade = 1.0 - self._smoothstep(fade_progress)
            painter.setBrush(
                with_alpha(color, round(ripple_alpha * fade))
            )
            painter.drawEllipse(origin, ripple_radius, ripple_radius)

        painter.restore()


# ---------------------------------------------------------------------------
# Small controls
# ---------------------------------------------------------------------------


class Pill(QWidget):
    """Tonal rounded label with an optional status dot."""

    def __init__(
        self,
        text: str,
        *,
        background: str,
        foreground: str,
        dot: Optional[str] = None,
        height: int = 30,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._text = text
        self._background = QColor(background)
        self._foreground = QColor(foreground)
        self._dot = QColor(dot) if dot else None
        self._height = height
        self._font = ui_font(11.5, QFont.Weight.Bold)
        self.setFont(self._font)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._recalculate()

    def _recalculate(self) -> None:
        metrics = QFontMetrics(self._font)
        width = metrics.horizontalAdvance(self._text) + 26
        if self._dot is not None:
            width += 14
        self.setFixedWidth(max(48, width))
        self.updateGeometry()

    def set_content(
        self,
        text: str,
        *,
        background: str | QColor,
        foreground: str | QColor,
        dot: Optional[str | QColor] = None,
    ) -> None:
        self._text = text
        self._background = QColor(background)
        self._foreground = QColor(foreground)
        self._dot = QColor(dot) if dot is not None else None
        self._recalculate()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._background)
        radius = self.height() / 2.0
        painter.drawRoundedRect(QRectF(self.rect()), radius, radius)

        x = 12.0
        if self._dot is not None:
            painter.setBrush(self._dot)
            painter.drawEllipse(QRectF(x, self.height() / 2.0 - 3.5, 7, 7))
            x += 13.0

        painter.setPen(self._foreground)
        painter.setFont(self._font)
        painter.drawText(
            QRectF(x, 0, self.width() - x - 10, self.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._text,
        )


class Spinner(QWidget):
    """Indeterminate circular progress indicator."""

    def __init__(
        self,
        *,
        color: str,
        size: int = 18,
        stroke: float = 2.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self._angle = 0
        self._stroke = stroke
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.setInterval(28)
        self._timer.timeout.connect(self._advance)

    def _advance(self) -> None:
        self._angle = (self._angle + 13) % 360
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(self._color, self._stroke)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        inset = self._stroke / 2.0 + 1.0
        rect = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        painter.drawArc(rect, self._angle * 16, 270 * 16)


class FilledButton(_PressFeedbackMixin, QPushButton):
    """Filled pill button with an optional leading icon."""

    def __init__(
        self,
        text: str,
        *,
        palette: Mapping[str, str],
        icon: Optional[str] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._text = text
        self._icon = icon
        self._palette = palette
        self._compact = False
        self.setAccessibleName(text)
        self.setFixedHeight(44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._init_press_feedback()
        self._recalculate_width()

    def _c(self, name: str) -> QColor:
        return QColor(self._palette.get(name, "#6750A4"))

    def _metrics(self) -> QFontMetrics:
        return QFontMetrics(ui_font(13, QFont.Weight.Bold))

    def _recalculate_width(self) -> None:
        if self._compact:
            width = 44
        else:
            text_width = self._metrics().horizontalAdvance(self._text)
            icon_width = 18 if self._icon else 0
            gap = 8 if self._icon and self._text else 0
            width = max(64, 20 + 20 + icon_width + gap + text_width)
        self.setFixedWidth(width)

    def set_text(self, text: str) -> None:
        self._text = text
        self.setAccessibleName(text)
        self._recalculate_width()
        self.update()

    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self._recalculate_width()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.width(), 44)

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

        enabled = self.isEnabled()
        primary = self._c("primary")
        on_primary = self._c("on_primary")
        if not enabled:
            background = self._c("surface_container_highest")
            foreground = blend(
                self._c("surface_container_highest"),
                self._c("on_surface_variant"),
                0.48,
            )
        else:
            background = primary
            foreground = on_primary
            if self.underMouse():
                background = blend(primary, on_primary, 0.10)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        radius = self.height() / 2.0
        painter.drawRoundedRect(QRectF(self.rect()), radius, radius)
        if enabled:
            self._paint_press_feedback(
                painter,
                QRectF(self.rect()),
                radius,
                on_primary,
                state_alpha=24,
                ripple_alpha=46,
            )

        icon_size = 18.0
        font = ui_font(13, QFont.Weight.Bold)
        metrics = QFontMetrics(font)
        text_width = metrics.horizontalAdvance(self._text)
        content_width = (icon_size if self._icon else 0) + (
            8 if self._icon and self._text and not self._compact else 0
        ) + (0 if self._compact else text_width)
        start = (self.width() - content_width) / 2.0

        if self._icon:
            draw_icon(
                painter,
                self._icon,
                QRectF(start, (self.height() - icon_size) / 2.0, icon_size, icon_size),
                foreground,
                stroke=2.0,
            )
            start += icon_size + (8 if self._text and not self._compact else 0)

        if not self._compact:
            painter.setPen(foreground)
            painter.setFont(font)
            painter.drawText(
                QRectF(start, 0, text_width + 1, self.height()),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                self._text,
            )

        if self.hasFocus() and self._keyboard_focus:
            pen = QPen(with_alpha(foreground, 200), 2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                QRectF(self.rect()).adjusted(3, 3, -3, -3),
                radius - 2,
                radius - 2,
            )


class DeviceChoiceButton(_PressFeedbackMixin, QPushButton):
    """Outlined two-line button used to pick a device by serial."""

    def __init__(
        self,
        serial: str,
        meta: str,
        *,
        palette: Mapping[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._serial = serial
        self._meta = meta
        self._palette = palette
        self.setFixedHeight(58)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"{serial} — {meta}")
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
        self._apply_press_transform(painter)
        hover = self.underMouse()
        background = self._c("surface_container_high") if hover else self._c("surface_container_low")
        painter.setBrush(background)
        painter.setPen(QPen(with_alpha(self._c("outline_variant"), 140), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 20, 20)
        if self.isEnabled():
            self._paint_press_feedback(
                painter,
                QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                20,
                self._c("primary"),
            )

        available = max(0, self.width() - 28)
        serial_font = ui_font(12, QFont.Weight.DemiBold)
        meta_font = ui_font(10, QFont.Weight.Normal)
        serial = QFontMetrics(serial_font).elidedText(
            self._serial, Qt.TextElideMode.ElideRight, available
        )
        meta = QFontMetrics(meta_font).elidedText(
            self._meta, Qt.TextElideMode.ElideRight, available
        )
        painter.setPen(self._c("on_surface"))
        painter.setFont(serial_font)
        painter.drawText(
            QRectF(14, 9, available, 20),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            serial,
        )
        painter.setPen(self._c("on_surface_variant"))
        painter.setFont(meta_font)
        painter.drawText(
            QRectF(14, 29, available, 18),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            meta,
        )

        if self.hasFocus() and self._keyboard_focus:
            painter.setPen(QPen(self._c("primary"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                QRectF(self.rect()).adjusted(3, 3, -3, -3), 17, 17
            )


class NavButton(_PressFeedbackMixin, QAbstractButton):
    def __init__(
        self,
        label: str,
        icon: str,
        *,
        palette: Mapping[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._label = label
        self._icon = icon
        self._palette = palette
        self.setCheckable(True)
        self.setFixedSize(88, 74)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(label)
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
        self._apply_press_transform(painter)
        checked = self.isChecked()

        pill = QRectF((self.width() - 68) / 2.0, 6, 68, 34)
        if checked:
            painter.setBrush(self._c("secondary_container"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(pill, 17, 17)
        elif self.underMouse():
            painter.setBrush(with_alpha(self._c("on_surface"), 12))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(pill, 17, 17)

        if self.isEnabled():
            self._paint_press_feedback(
                painter,
                pill,
                17,
                self._c("on_secondary_container") if checked else self._c("on_surface"),
                state_alpha=20,
                ripple_alpha=38,
            )

        icon_color = (
            self._c("on_secondary_container")
            if checked
            else self._c("on_surface_variant")
        )
        draw_icon(
            painter,
            self._icon,
            QRectF(pill.center().x() - 11, pill.center().y() - 11, 22, 22),
            icon_color,
            stroke=2.1 if checked else 1.9,
        )

        label_font = ui_font(
            11,
            QFont.Weight.Bold if checked else QFont.Weight.Medium,
        )
        painter.setFont(label_font)
        painter.setPen(
            self._c("on_surface") if checked else self._c("on_surface_variant")
        )
        painter.drawText(
            QRectF(0, 46, self.width(), 22),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            self._label,
        )

        if self.hasFocus() and self._keyboard_focus:
            painter.setPen(QPen(self._c("primary"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                QRectF(self.rect()).adjusted(3, 1, -3, -3), 18, 18
            )


class CaptionButton(_PressFeedbackMixin, QAbstractButton):
    def __init__(
        self,
        icon: str,
        *,
        palette: Mapping[str, str],
        is_close: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon = icon
        self._palette = palette
        self._is_close = is_close
        self.setFixedSize(38, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._init_press_feedback()

    def set_icon_name(self, name: str) -> None:
        self._icon = name
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
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self.underMouse():
            painter.setPen(Qt.PenStyle.NoPen)
            if self._is_close:
                painter.setBrush(QColor(self._palette.get("error_container", "#F9DEDC")))
            else:
                painter.setBrush(
                    with_alpha(QColor(self._palette.get("on_surface", "#000000")), 22)
                )
            painter.drawRoundedRect(rect, 8, 8)
        if self.isEnabled():
            tint = QColor(
                self._palette.get("error" if self._is_close else "on_surface", "#000000")
            )
            self._paint_press_feedback(
                painter, rect, 8, tint, state_alpha=20, ripple_alpha=35
            )

        fg = QColor(
            self._palette.get(
                "on_error_container"
                if (self.underMouse() and self._is_close)
                else "on_surface_variant",
                "#49454F",
            )
        )
        draw_icon(
            painter,
            self._icon,
            QRectF(self.width() / 2 - 8, self.height() / 2 - 8, 16, 16),
            fg,
            stroke=1.8,
        )


class SettingsButton(_PressFeedbackMixin, QAbstractButton):
    def __init__(
        self,
        *,
        palette: Mapping[str, str],
        label: Optional[str] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self._label = label or tr("nav_settings")
        self.setCheckable(True)
        self.setFixedSize(68, 56)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(self._label)
        self.setToolTip(tr("nav_settings_tooltip"))
        self._init_press_feedback()

    def set_label(self, label: str) -> None:
        self._label = label
        self.setAccessibleName(label)
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
        checked = self.isChecked()
        pill = QRectF((self.width() - 56) / 2.0, 4, 56, 28)
        if checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._palette.get("secondary_container", "#E8DEF8")))
            painter.drawRoundedRect(pill, 14, 14)
        elif self.underMouse():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(
                with_alpha(QColor(self._palette.get("on_surface", "#000000")), 16)
            )
            painter.drawRoundedRect(pill, 14, 14)
        if self.isEnabled():
            self._paint_press_feedback(
                painter,
                pill,
                14,
                QColor(self._palette.get("on_secondary_container" if checked else "on_surface", "#000000")),
                state_alpha=20,
                ripple_alpha=38,
            )
        icon_color = QColor(
            self._palette.get("on_secondary_container", "#1D192B")
            if checked
            else self._palette.get("on_surface_variant", "#49454F")
        )
        draw_icon(
            painter,
            "settings",
            QRectF(self.width() / 2 - 11, 7, 22, 22),
            icon_color,
            stroke=2.1 if checked else 1.8,
        )
        font = ui_font(9.5, QFont.Weight.Bold if checked else QFont.Weight.Medium)
        painter.setFont(font)
        label_color = QColor(
            self._palette.get("on_surface", "#1D1B20")
            if checked
            else self._palette.get("on_surface_variant", "#49454F")
        )
        painter.setPen(label_color)
        painter.drawText(
            QRectF(0, 34, self.width(), 16),
            int(Qt.AlignmentFlag.AlignCenter),
            self._label,
        )


ThemeButton = SettingsButton


# ---------------------------------------------------------------------------
# Navigation rail
# ---------------------------------------------------------------------------


class LogoBadge(QWidget):
    """Gradient 'PX' app mark used at the top of the navigation rail."""

    def __init__(
        self,
        *,
        palette: Mapping[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self.setFixedSize(44, 44)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0.0, QColor(self._palette.get("primary", "#6750A4")))
        gradient.setColorAt(1.0, QColor(self._palette.get("tertiary", "#7D5260")))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(QRectF(self.rect()), 18, 18)
        painter.setPen(QColor(self._palette.get("on_primary", "#FFFFFF")))
        painter.setFont(ui_font(14, QFont.Weight.ExtraBold, display=True))
        painter.drawText(
            QRectF(self.rect()),
            int(Qt.AlignmentFlag.AlignCenter),
            "PX",
        )


class NavigationRail(QWidget):
    tab_selected = Signal(int)
    settings_clicked = Signal()
    theme_clicked = settings_clicked

    def __init__(
        self,
        *,
        palette: Mapping[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self.setFixedWidth(112)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 18, 0, 16)
        layout.setSpacing(0)

        logo = LogoBadge(palette=palette)
        layout.addWidget(logo, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(24)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[NavButton] = []
        entries = (
            (tr("nav_info"), "info"),
            (tr("nav_firmware"), "update"),
            (tr("nav_apps"), "apps"),
            (tr("nav_backup"), "backup"),
            (tr("nav_screen"), "cast"),
        )
        for index, (label, icon) in enumerate(entries):
            button = NavButton(label, icon, palette=palette, parent=self)
            self._group.addButton(button, index)
            button.clicked.connect(
                lambda checked=False, i=index: self.tab_selected.emit(i)
            )
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
            layout.addSpacing(4)
            self._buttons.append(button)

        layout.addStretch(1)
        self._settings_button = SettingsButton(palette=palette, parent=self)
        self._settings_button.clicked.connect(self.settings_clicked.emit)
        layout.addWidget(self._settings_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.set_selected(0)

    def set_selected(self, index: int) -> None:
        if index == 5:
            self._group.setExclusive(False)
            for button in self._buttons:
                button.setChecked(False)
            self._group.setExclusive(True)
            self._settings_button.setChecked(True)
        else:
            self._settings_button.setChecked(False)
            for position, button in enumerate(self._buttons):
                button.setChecked(position == index)

    def set_theme_tooltip(self, text: str) -> None:
        self._settings_button.setToolTip(text)

    def adapt_to_height(self, height: int) -> None:
        compact = height < 580
        layout = self.layout()
        if layout is not None:
            layout.setContentsMargins(0, 8 if compact else 18, 0, 8 if compact else 16)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        is_max = self.window() is not None and self.window().isMaximized()
        r = 0.0 if is_max else 20.0
        w = float(self.width())
        h = float(self.height())
        if r > 0:
            path = QPainterPath()
            path.moveTo(w, 0)
            path.lineTo(r, 0)
            path.quadTo(0, 0, 0, r)
            path.lineTo(0, h - r)
            path.quadTo(0, h, r, h)
            path.lineTo(w, h)
            path.closeSubpath()
            painter.fillPath(
                path, QColor(self._palette.get("surface_container_low", "#F7F2FA"))
            )
        else:
            painter.fillRect(
                self.rect(),
                QColor(self._palette.get("surface_container_low", "#F7F2FA")),
            )
        painter.setPen(
            QPen(with_alpha(self._palette.get("outline_variant", "#CAC4D0"), 110), 1)
        )
        painter.drawLine(int(w) - 1, 0, int(w) - 1, int(h))


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


class Header(QWidget):
    refresh_requested = Signal()
    minimize_clicked = Signal()
    maximize_clicked = Signal()
    close_clicked = Signal()

    def __init__(
        self,
        *,
        palette: Mapping[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self._status_text: str = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 16, 20, 16)
        layout.setSpacing(10)

        overline = QHBoxLayout()
        overline.setContentsMargins(0, 0, 0, 0)
        overline.setSpacing(8)
        overline.addWidget(
            make_label(
                "PIXEL TOOLS",
                size=10.5,
                weight=QFont.Weight.ExtraBold,
                color=palette.get("primary", "#6750A4"),
                letter_spacing=2.4,
                display=True,
            )
        )
        overline.addStretch(1)

        self.btn_min = CaptionButton("minimize", palette=palette, parent=self)
        self.btn_min.setToolTip(tr("hdr_minimize"))
        self.btn_min.clicked.connect(self.minimize_clicked.emit)

        self.btn_max = CaptionButton("maximize", palette=palette, parent=self)
        self.btn_max.setToolTip(tr("hdr_maximize"))
        self.btn_max.clicked.connect(self.maximize_clicked.emit)

        self.btn_close = CaptionButton(
            "close", palette=palette, is_close=True, parent=self
        )
        self.btn_close.setToolTip(tr("hdr_close"))
        self.btn_close.clicked.connect(self.close_clicked.emit)

        overline.addWidget(self.btn_min)
        overline.addWidget(self.btn_max)
        overline.addWidget(self.btn_close)
        layout.addLayout(overline)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)

        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(4)
        self.title_label = make_label(
            tr("tab_0_title"),
            size=29,
            weight=QFont.Weight.Bold,
            color=palette.get("on_surface", "#1D1B20"),
            display=True,
        )
        self.subtitle_label = make_label(
            tr("hdr_checking"),
            size=13,
            weight=QFont.Weight.Normal,
            color=palette.get("on_surface_variant", "#49454F"),
            wrap=True,
        )
        self.subtitle_label.setMaximumHeight(40)
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)
        row.addLayout(titles, 1)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(10)
        self.spinner = Spinner(
            color=palette.get("primary", "#6750A4"), size=18, stroke=2.0
        )
        self.spinner.setVisible(False)
        controls.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignVCenter)

        self.chip = Pill(
            "ADB · device",
            background=palette.get("surface_container", "#F3EDF7"),
            foreground=palette.get("on_surface_variant", "#49454F"),
            dot=palette.get("outline", "#79747E"),
            height=32,
        )
        controls.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignVCenter)

        self.refresh_button = FilledButton(
            tr("apps_refresh"),
            palette=palette,
            icon="refresh",
            parent=self,
        )
        self.refresh_button.setToolTip(tr("hdr_refresh"))
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        controls.addWidget(self.refresh_button, 0, Qt.AlignmentFlag.AlignVCenter)

        row.addLayout(controls, 0)
        layout.addLayout(row)

    def update_window_state(self, is_maximized: bool) -> None:
        self.btn_max.set_icon_name("restore_window" if is_maximized else "maximize")
        self.btn_max.setToolTip(tr("hdr_restore") if is_maximized else tr("hdr_maximize"))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            if pos.y() > 8:
                win = self.window()
                if win is not None and win.windowHandle() is not None:
                    win.windowHandle().startSystemMove()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.maximize_clicked.emit()
        super().mouseDoubleClickEvent(event)

    def set_source(self, text: str, tooltip: str = "") -> None:
        pass

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)
        self.subtitle_label.setToolTip(text if len(text) > 70 else "")

    def set_busy(self, busy: bool) -> None:
        self.spinner.setVisible(busy)

    def set_status(
        self,
        text: str,
        *,
        background: str,
        foreground: str,
        dot: Optional[str],
    ) -> None:
        self._status_text = text
        self.chip.set_content(
            text, background=background, foreground=foreground, dot=dot
        )

    def set_chip_visible(self, visible: bool) -> None:
        self.chip.setVisible(visible and bool(getattr(self, "_status_text", "")))

    def set_refresh_visible(self, visible: bool) -> None:
        self.refresh_button.setVisible(visible)

    def set_refresh_enabled(self, enabled: bool) -> None:
        self.refresh_button.setEnabled(enabled)

    def set_button_compact(self, compact: bool) -> None:
        self.refresh_button.set_compact(compact)

    def adapt_to_width(self, width: int) -> None:
        """Dynamically scale header typography, padding and controls based on width."""
        layout = self.layout()
        if width < 860:
            self.title_label.setFont(ui_font(21, QFont.Weight.Bold, display=True))
            self.subtitle_label.setFont(ui_font(11.5, QFont.Weight.Normal))
            if layout is not None:
                layout.setContentsMargins(18, 12, 16, 12)
            self.set_chip_visible(False)
            self.set_button_compact(True)
        elif width < 1020:
            self.title_label.setFont(ui_font(24, QFont.Weight.Bold, display=True))
            self.subtitle_label.setFont(ui_font(12.5, QFont.Weight.Normal))
            if layout is not None:
                layout.setContentsMargins(24, 14, 18, 14)
            self.set_chip_visible(width >= 920)
            self.set_button_compact(width < 980)
        else:
            self.title_label.setFont(ui_font(29, QFont.Weight.Bold, display=True))
            self.subtitle_label.setFont(ui_font(13, QFont.Weight.Normal))
            if layout is not None:
                layout.setContentsMargins(32, 16, 20, 16)
            self.set_chip_visible(True)
            self.set_button_compact(False)


# ---------------------------------------------------------------------------
# Info cards
# ---------------------------------------------------------------------------


class InfoCard(QWidget):
    def __init__(
        self,
        *,
        palette: Mapping[str, str],
        icon: str,
        icon_background: str,
        icon_foreground: str,
        label: str,
        value: str,
        subtitle: Optional[str] = None,
        value_size: float = 20,
        selectable: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self.setFixedHeight(116)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(20, 14, 20, 14)
        row.setSpacing(16)

        badge = IconBadge(
            icon,
            size=48,
            radius=16,
            background=icon_background,
            foreground=icon_foreground,
            icon_size=24,
        )
        row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        column.addStretch(1)

        column.addWidget(
            make_label(
                label,
                size=11,
                weight=QFont.Weight.DemiBold,
                color=palette.get("on_surface_variant", "#49454F"),
            )
        )

        if selectable:
            value_widget: QWidget = QLabel(value)
            value_widget.setFont(ui_font(value_size, QFont.Weight.Bold, display=True))
            value_widget.setStyleSheet(
                f"color: {palette.get('on_surface', '#1D1B20')}; background: transparent;"
            )
            value_widget.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            value_widget.setCursor(Qt.CursorShape.IBeamCursor)
            value_widget.setToolTip(value)
            value_widget.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
        else:
            value_widget = ElidedLabel(
                value,
                font=ui_font(value_size, QFont.Weight.Bold, display=True),
                color=palette.get("on_surface", "#1D1B20"),
            )
        column.addWidget(value_widget)

        if subtitle:
            column.addWidget(
                ElidedLabel(
                    subtitle,
                    font=ui_font(11, QFont.Weight.Normal),
                    color=palette.get("on_surface_variant", "#49454F"),
                )
            )
        column.addStretch(1)
        row.addLayout(column, 1)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QColor(self._palette.get("surface_container_low", "#F7F2FA")))
        painter.setPen(
            QPen(with_alpha(self._palette.get("outline_variant", "#CAC4D0"), 90), 1)
        )
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 24, 24
        )


# ---------------------------------------------------------------------------
# Phone showcase
# ---------------------------------------------------------------------------


class PhoneMock(QWidget):
    def __init__(
        self,
        *,
        palette: Mapping[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self.setFixedSize(178, 330)
        soft_shadow(self, blur=32, dy=12, alpha=50)

    def _c(self, name: str) -> QColor:
        return QColor(self._palette.get(name, "#6750A4"))

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(self._c("outline_variant"), 1))
        painter.setBrush(self._c("surface_container_highest"))
        painter.drawRoundedRect(body, 40, 40)

        screen = body.adjusted(10, 10, -10, -10)
        gradient = QLinearGradient(screen.topLeft(), screen.bottomRight())
        gradient.setColorAt(0.0, self._c("surface"))
        gradient.setColorAt(1.0, self._c("surface_container"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(screen, 32, 32)

        painter.setBrush(self._c("outline_variant"))
        painter.drawEllipse(QRectF(screen.center().x() - 5, screen.top() + 8, 10, 10))

        draw_icon(
            painter,
            "android",
            QRectF(screen.center().x() - 39, screen.center().y() - 55, 78, 78),
            self._c("primary"),
            stroke=2.0,
        )

        painter.setPen(self._c("on_surface_variant"))
        painter.setFont(ui_font(11, QFont.Weight.ExtraBold, letter_spacing=3.0))
        painter.drawText(
            QRectF(screen.left(), screen.bottom() - 30, screen.width(), 18),
            int(Qt.AlignmentFlag.AlignCenter),
            "PIXEL",
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._c("surface_container_highest"))
        painter.drawRoundedRect(
            QRectF(screen.center().x() - 17, screen.bottom() - 13, 34, 4), 2, 2
        )


class PhoneCard(QWidget):
    def __init__(
        self,
        *,
        palette: Mapping[str, str],
        title: str,
        subtitle: str,
        generation: str,
        mode_text: str,
        mode_dot: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self.setFixedHeight(500)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        soft_shadow(self, blur=42, dy=14, alpha=24)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 22)
        layout.setSpacing(12)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        top.addWidget(
            Pill(
                mode_text,
                background=palette.get("surface_container_lowest", "#FFFFFF"),
                foreground=palette.get("on_surface_variant", "#49454F"),
                dot=mode_dot,
                height=30,
            ),
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        top.addStretch(1)
        top.addWidget(
            Pill(
                generation,
                background=palette.get("surface_container_lowest", "#FFFFFF"),
                foreground=palette.get("on_surface_variant", "#49454F"),
                height=30,
            ),
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        layout.addLayout(top)

        layout.addStretch(1)
        mock = PhoneMock(palette=palette)
        layout.addWidget(mock, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)

        title_label = make_label(
            title,
            size=24,
            weight=QFont.Weight.Bold,
            color=palette.get("on_surface", "#1D1B20"),
            wrap=True,
            align=Qt.AlignmentFlag.AlignHCenter,
            display=True,
        )
        title_label.setMaximumHeight(64)
        layout.addWidget(title_label)

        subtitle_label = ElidedLabel(
            subtitle,
            font=ui_font(12, QFont.Weight.Normal),
            color=palette.get("on_surface_variant", "#49454F"),
            align=Qt.AlignmentFlag.AlignHCenter,
        )
        layout.addWidget(subtitle_label)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(
            0.0, QColor(self._palette.get("primary_container", "#EADDFF"))
        )
        gradient.setColorAt(
            1.0, QColor(self._palette.get("tertiary_container", "#FFD8E4"))
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(rect, 36, 36)

        painter.setBrush(QColor(255, 255, 255, 28))
        painter.drawEllipse(QRectF(-70, -100, 260, 260))
        painter.setBrush(
            with_alpha(self._palette.get("primary", "#6750A4"), 30)
        )
        painter.drawEllipse(
            QRectF(self.width() - 150, self.height() - 130, 240, 240)
        )


# ---------------------------------------------------------------------------
# Banners, cards and page scaffolding
# ---------------------------------------------------------------------------


class NoticeBanner(QWidget):
    def __init__(
        self,
        *,
        palette: Mapping[str, str],
        text: str,
        icon: str = "info",
        background: str,
        foreground: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self._background = QColor(background)
        self._foreground = QColor(foreground)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)
        badge = IconWidget(icon, self._foreground, 20)
        layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        label = make_label(
            text,
            size=12,
            weight=QFont.Weight.Normal,
            color=foreground,
            wrap=True,
        )
        label.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label, 1)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._background)
        painter.drawRoundedRect(QRectF(self.rect()), 20, 20)


class CardSurface(QWidget):
    def __init__(
        self,
        *,
        palette: Mapping[str, str],
        radius: int = 32,
        shadow: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self._radius = radius
        if shadow:
            soft_shadow(self, blur=40, dy=12, alpha=24)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QColor(self._palette.get("surface_container_low", "#F7F2FA")))
        painter.setPen(
            QPen(with_alpha(self._palette.get("outline_variant", "#CAC4D0"), 100), 1)
        )
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
            self._radius,
            self._radius,
        )


class ResponsiveContent(QWidget):
    """Container widget that auto-adjusts horizontal margins based on available width."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def resizeEvent(self, event) -> None:  # noqa: N802
        w = event.size().width()
        margin = 16 if w < 720 else (24 if w < 920 else 32)
        layout = self.layout()
        if layout is not None:
            layout.setContentsMargins(margin, 10, margin, 24)
        super().resizeEvent(event)


class CenteredPage(QWidget):
    """Scrollable page with a centered, responsive card."""

    def __init__(
        self,
        *,
        palette: Mapping[str, str],
        card_width: int = 760,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self._card_width = card_width

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll.viewport().setAutoFillBackground(False)

        body = QWidget()
        body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._body = body
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setContentsMargins(24, 24, 24, 24)
        self._body_layout.setSpacing(0)
        self._body_layout.addStretch(1)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addStretch(1)

        self.card = CardSurface(palette=palette)
        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(38, 36, 38, 36)
        self.card_layout.setSpacing(14)
        row.addWidget(self.card)
        row.addStretch(1)

        self._body_layout.addLayout(row)
        self._body_layout.addStretch(1)
        self.scroll.setWidget(body)
        layout.addWidget(self.scroll)

    def resizeEvent(self, event) -> None:  # noqa: N802
        available = max(360, self.width() - 76)
        self.card.setFixedWidth(min(self._card_width, available))
        super().resizeEvent(event)

    def add_card_widget(
        self,
        widget: QWidget,
        *,
        align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignHCenter,
        stretch: int = 0,
    ) -> None:
        self.card_layout.addWidget(widget, stretch, align)


# Fade helper lives here so both pages and the main window can import it.
from PySide6.QtWidgets import QGraphicsOpacityEffect  # noqa: E402
from PySide6.QtCore import QPropertyAnimation  # noqa: E402


class PageHost(QWidget):
    """Single-page host with a short fade-in transition."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current: Optional[QWidget] = None
        self._animation: Optional[QPropertyAnimation] = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._layout = layout

    def set_page(self, page: QWidget, *, animate: bool = True) -> None:
        if self._animation is not None:
            self._animation.stop()
            self._animation = None

        if self._current is not None:
            old = self._current
            self._current = None
            old.setGraphicsEffect(None)
            old.setParent(None)
            old.deleteLater()

        self._current = page
        self._layout.addWidget(page)
        page.show()

        if not animate:
            page.setGraphicsEffect(None)
            return

        effect = QGraphicsOpacityEffect(page)
        effect.setOpacity(0.0)
        page.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(260)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def finished() -> None:
            if self._current is page:
                page.setGraphicsEffect(None)
            self._animation = None

        animation.finished.connect(finished)
        self._animation = animation
        animation.start()
