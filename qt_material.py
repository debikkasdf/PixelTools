"""Material 3 Expressive painting helpers for the native Pixel Tools UI.

The module contains palette utilities, font builders, procedural vector icons
and small icon widgets.  Everything is painted with QPainter; no QML, web
content or external image assets are required.
"""

from __future__ import annotations

import math
from typing import Mapping

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget

DISPLAY_FAMILIES = [
    "Segoe UI Variable Display",
    "Segoe UI",
    "Inter",
    "Arial",
]
TEXT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Inter", "Arial"]


# ---------------------------------------------------------------------------
# Colour and font helpers
# ---------------------------------------------------------------------------


def qcolor(value: str | QColor) -> QColor:
    return QColor(value)


def with_alpha(value: str | QColor, alpha: int) -> QColor:
    result = QColor(value)
    result.setAlpha(max(0, min(255, int(alpha))))
    return result


def blend(first: str | QColor, second: str | QColor, amount: float) -> QColor:
    """Mix two colours; amount 0 returns first, 1 returns second."""
    amount = max(0.0, min(1.0, float(amount)))
    a = QColor(first)
    b = QColor(second)
    return QColor(
        round(a.red() + (b.red() - a.red()) * amount),
        round(a.green() + (b.green() - a.green()) * amount),
        round(a.blue() + (b.blue() - a.blue()) * amount),
        round(a.alpha() + (b.alpha() - a.alpha()) * amount),
    )


def ui_font(
    size: float,
    weight: QFont.Weight = QFont.Weight.Normal,
    *,
    display: bool = False,
    letter_spacing: float = 0.0,
) -> QFont:
    font = QFont()
    font.setFamilies(DISPLAY_FAMILIES if display else TEXT_FAMILIES)
    font.setPixelSize(max(1, int(round(size))))
    font.setWeight(weight)
    if letter_spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_spacing)
    return font


def qss_for(palette: Mapping[str, str]) -> str:
    """Global stylesheet for stock Qt widgets (tooltips, scrollbars)."""

    def c(name: str) -> str:
        return palette.get(name, "#808080")

    return f"""
QWidget {{
    color: {c('on_surface')};
}}
QMainWindow, QWidget#Root {{
    background-color: {c('surface')};
}}
QToolTip {{
    background-color: {c('surface_container_highest')};
    color: {c('on_surface')};
    border: 1px solid {c('outline_variant')};
    border-radius: 10px;
    padding: 8px 12px;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 4px 2px 4px 2px;
}}
QScrollBar::handle:vertical {{
    background: {c('outline_variant')};
    min-height: 42px;
    border-radius: 6px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c('outline')};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QScrollBar:horizontal {{
    height: 0;
}}
"""


def soft_shadow(
    widget: QWidget,
    *,
    blur: int = 40,
    dy: int = 10,
    alpha: int = 24,
) -> QGraphicsDropShadowEffect:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)
    return effect


# ---------------------------------------------------------------------------
# Procedural Material-style icons (24x24 design grid)
# ---------------------------------------------------------------------------


def _arrow_head(
    painter: QPainter,
    tip: QPointF,
    direction: tuple[float, float],
    size: float,
    fill: QColor,
) -> None:
    dx, dy = direction
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    base = QPointF(tip.x() - ux * size, tip.y() - uy * size)
    px, py = -uy, ux
    polygon = QPolygonF(
        [
            tip,
            QPointF(base.x() + px * size * 0.60, base.y() + py * size * 0.60),
            QPointF(base.x() - px * size * 0.60, base.y() - py * size * 0.60),
        ]
    )
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    painter.drawPolygon(polygon)


def draw_icon(
    painter: QPainter,
    name: str,
    rect: QRectF,
    color: str | QColor,
    stroke: float = 1.9,
) -> None:
    """Draw a 24x24 vector icon scaled into *rect*."""
    rect = QRectF(rect)
    side = min(rect.width(), rect.height())
    if side <= 0:
        return
    target = QRectF(
        rect.center().x() - side / 2.0,
        rect.center().y() - side / 2.0,
        side,
        side,
    )
    pen_color = QColor(color)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.translate(target.topLeft())
    painter.scale(side / 24.0, side / 24.0)

    pen = QPen(pen_color, stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    def fill_path(path: QPainterPath) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pen_color)
        painter.drawPath(path)

    def stroke_path(path: QPainterPath) -> None:
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    if name == "info":
        painter.drawEllipse(QRectF(3.6, 3.6, 16.8, 16.8))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pen_color)
        painter.drawEllipse(QRectF(10.9, 6.6, 2.2, 2.2))
        painter.setPen(pen)
        painter.drawLine(QPointF(12, 10.5), QPointF(12, 17.1))

    elif name == "update":
        path = QPainterPath()
        path.moveTo(12, 3.4)
        path.lineTo(12, 13.4)
        path.moveTo(7.8, 9.3)
        path.lineTo(12, 13.5)
        path.lineTo(16.2, 9.3)
        path.moveTo(4.6, 15.2)
        path.lineTo(4.6, 18.0)
        path.quadTo(4.6, 20.4, 7.0, 20.4)
        path.lineTo(17.0, 20.4)
        path.quadTo(19.4, 20.4, 19.4, 18.0)
        path.lineTo(19.4, 15.2)
        stroke_path(path)

    elif name == "apps":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pen_color)
        for x, y in ((4.4, 4.4), (13.3, 4.4), (4.4, 13.3), (13.3, 13.3)):
            painter.drawRoundedRect(QRectF(x, y, 6.3, 6.3), 1.7, 1.7)

    elif name == "backup":
        cloud = QPainterPath()
        cloud.moveTo(5.6, 18.2)
        cloud.lineTo(16.5, 18.2)
        cloud.cubicTo(19.0, 18.2, 21.0, 16.3, 21.0, 13.9)
        cloud.cubicTo(21.0, 11.6, 19.3, 9.8, 17.1, 9.5)
        cloud.cubicTo(16.5, 6.7, 14.1, 4.6, 11.2, 4.6)
        cloud.cubicTo(8.0, 4.6, 5.3, 7.1, 5.2, 10.4)
        cloud.cubicTo(3.3, 10.9, 2.0, 12.7, 2.0, 14.7)
        cloud.cubicTo(2.0, 16.6, 3.5, 18.2, 5.6, 18.2)
        cloud.closeSubpath()
        stroke_path(cloud)
        arrow = QPainterPath()
        arrow.moveTo(12, 15.4)
        arrow.lineTo(12, 10.0)
        arrow.moveTo(9.8, 12.2)
        arrow.lineTo(12, 10.0)
        arrow.lineTo(14.2, 12.2)
        stroke_path(arrow)

    elif name == "refresh":
        arc = QRectF(4.2, 4.2, 15.6, 15.6)
        path = QPainterPath()
        path.arcMoveTo(arc, 100)
        path.arcTo(arc, 100, -300)
        stroke_path(path)
        theta = math.radians(-200)
        radius = 7.8
        point = QPointF(
            12.0 + radius * math.cos(theta),
            12.0 - radius * math.sin(theta),
        )
        direction = (math.sin(theta), math.cos(theta))
        tip = QPointF(
            point.x() + direction[0] * 3.4,
            point.y() + direction[1] * 3.4,
        )
        _arrow_head(painter, tip, direction, 3.8, pen_color)

    elif name == "phone":
        painter.drawRoundedRect(QRectF(7.0, 2.5, 10.0, 19.0), 2.6, 2.6)
        painter.drawLine(QPointF(10.5, 5.2), QPointF(13.5, 5.2))
        painter.drawLine(QPointF(10.8, 19.0), QPointF(13.2, 19.0))

    elif name == "android":
        head = QPainterPath()
        head.moveTo(4.6, 14.4)
        head.cubicTo(4.6, 9.6, 7.9, 5.9, 12.0, 5.9)
        head.cubicTo(16.1, 5.9, 19.4, 9.6, 19.4, 14.4)
        head.closeSubpath()
        stroke_path(head)
        painter.drawLine(QPointF(7.8, 6.4), QPointF(6.3, 3.9))
        painter.drawLine(QPointF(16.2, 6.4), QPointF(17.7, 3.9))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pen_color)
        painter.drawEllipse(QRectF(8.5, 9.5, 2.3, 2.3))
        painter.drawEllipse(QRectF(13.2, 9.5, 2.3, 2.3))

    elif name == "tag":
        tag = QPainterPath()
        tag.moveTo(4.0, 10.8)
        tag.lineTo(4.0, 5.5)
        tag.quadTo(4.0, 4.0, 5.5, 4.0)
        tag.lineTo(10.8, 4.0)
        tag.lineTo(20.0, 13.2)
        tag.quadTo(20.9, 14.1, 20.0, 15.0)
        tag.lineTo(15.0, 20.0)
        tag.quadTo(14.1, 20.9, 13.2, 20.0)
        tag.closeSubpath()
        stroke_path(tag)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pen_color)
        painter.drawEllipse(QRectF(6.8, 6.8, 2.4, 2.4))

    elif name == "battery":
        painter.drawRoundedRect(QRectF(2.5, 7.0, 17.0, 10.0), 2.4, 2.4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pen_color)
        painter.drawRoundedRect(QRectF(20.4, 9.8, 1.6, 4.4), 0.8, 0.8)
        painter.drawRoundedRect(QRectF(5.0, 9.5, 7.0, 5.0), 1.0, 1.0)

    elif name == "usb":
        painter.drawLine(QPointF(12, 20.0), QPointF(12, 6.2))
        painter.drawLine(QPointF(12, 14.2), QPointF(8.4, 11.6))
        painter.drawLine(QPointF(8.4, 11.6), QPointF(8.4, 8.8))
        painter.drawLine(QPointF(12, 11.6), QPointF(15.6, 10.2))
        painter.drawLine(QPointF(15.6, 10.2), QPointF(15.6, 8.4))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pen_color)
        painter.drawEllipse(QRectF(10.7, 18.7, 2.6, 2.6))
        painter.drawEllipse(QRectF(7.2, 6.4, 2.4, 2.4))
        painter.drawRoundedRect(QRectF(14.2, 6.4, 2.8, 2.8), 0.5, 0.5)
        _arrow_head(painter, QPointF(12, 2.8), (0.0, -1.0), 3.2, pen_color)

    elif name == "warning":
        triangle = QPainterPath()
        triangle.moveTo(12.0, 3.3)
        triangle.lineTo(21.0, 19.2)
        triangle.quadTo(21.6, 20.4, 20.2, 20.4)
        triangle.lineTo(3.8, 20.4)
        triangle.quadTo(2.4, 20.4, 3.0, 19.2)
        triangle.closeSubpath()
        stroke_path(triangle)
        painter.drawLine(QPointF(12, 9.2), QPointF(12, 14.1))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pen_color)
        painter.drawEllipse(QRectF(10.9, 16.2, 2.2, 2.2))

    elif name == "palette":
        painter.drawEllipse(QRectF(3.4, 3.4, 17.2, 17.2))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pen_color)
        painter.drawEllipse(QRectF(8.2, 7.9, 2.5, 2.5))
        painter.drawEllipse(QRectF(13.5, 8.7, 2.5, 2.5))
        painter.drawEllipse(QRectF(9.2, 13.9, 2.5, 2.5))

    elif name == "check":
        painter.drawEllipse(QRectF(3.5, 3.5, 17.0, 17.0))
        path = QPainterPath()
        path.moveTo(7.7, 12.3)
        path.lineTo(10.7, 15.3)
        path.lineTo(16.6, 9.0)
        stroke_path(path)

    elif name == "lock":
        painter.drawRoundedRect(QRectF(5.0, 10.0, 14.0, 10.4), 2.4, 2.4)
        shackle = QPainterPath()
        shackle.moveTo(8.3, 10.0)
        shackle.lineTo(8.3, 7.7)
        shackle.cubicTo(8.3, 5.1, 10.0, 3.5, 12.0, 3.5)
        shackle.cubicTo(14.0, 3.5, 15.7, 5.1, 15.7, 7.7)
        shackle.lineTo(15.7, 10.0)
        stroke_path(shackle)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pen_color)
        painter.drawEllipse(QRectF(11.0, 14.0, 2.0, 2.0))
        painter.setPen(pen)
        painter.drawLine(QPointF(12, 16.0), QPointF(12, 17.5))

    elif name == "search":
        painter.drawEllipse(QRectF(3.5, 3.5, 13.0, 13.0))
        painter.drawLine(QPointF(15.3, 15.3), QPointF(20.3, 20.3))

    elif name == "developer":
        path = QPainterPath()
        path.moveTo(9.5, 7.5)
        path.lineTo(4.8, 12.0)
        path.lineTo(9.5, 16.5)
        path.moveTo(14.5, 7.5)
        path.lineTo(19.2, 12.0)
        path.lineTo(14.5, 16.5)
        stroke_path(path)

    elif name == "link_off":
        painter.save()
        painter.translate(12.0, 12.0)
        painter.rotate(-45.0)
        painter.drawRoundedRect(QRectF(-9.0, -2.3, 9.0, 4.6), 2.3, 2.3)
        painter.drawRoundedRect(QRectF(0.0, -2.3, 9.0, 4.6), 2.3, 2.3)
        painter.restore()
        painter.drawLine(QPointF(4.2, 4.2), QPointF(19.8, 19.8))

    elif name == "folder":
        folder = QPainterPath()
        folder.moveTo(3.2, 8.2)
        folder.lineTo(3.2, 18.2)
        folder.quadTo(3.2, 19.8, 4.8, 19.8)
        folder.lineTo(19.2, 19.8)
        folder.quadTo(20.8, 19.8, 20.8, 18.2)
        folder.lineTo(20.8, 9.6)
        folder.quadTo(20.8, 8.0, 19.2, 8.0)
        folder.lineTo(11.9, 8.0)
        folder.lineTo(9.9, 5.6)
        folder.lineTo(4.8, 5.6)
        folder.quadTo(3.2, 5.6, 3.2, 7.4)
        folder.closeSubpath()
        stroke_path(folder)

    elif name == "file":
        page = QPainterPath()
        page.moveTo(6.0, 3.2)
        page.lineTo(14.0, 3.2)
        page.lineTo(18.2, 7.4)
        page.lineTo(18.2, 20.8)
        page.lineTo(6.0, 20.8)
        page.closeSubpath()
        stroke_path(page)
        fold = QPainterPath()
        fold.moveTo(14.0, 3.2)
        fold.lineTo(14.0, 7.4)
        fold.lineTo(18.2, 7.4)
        stroke_path(fold)

    elif name == "trash":
        painter.drawLine(QPointF(4.0, 7.0), QPointF(20.0, 7.0))
        painter.drawLine(QPointF(9.2, 7.0), QPointF(9.2, 4.2))
        painter.drawLine(QPointF(9.2, 4.2), QPointF(14.8, 4.2))
        painter.drawLine(QPointF(14.8, 4.2), QPointF(14.8, 7.0))
        body = QPainterPath()
        body.moveTo(6.4, 7.0)
        body.lineTo(7.4, 19.2)
        body.quadTo(7.5, 20.8, 9.1, 20.8)
        body.lineTo(14.9, 20.8)
        body.quadTo(16.5, 20.8, 16.6, 19.2)
        body.lineTo(17.6, 7.0)
        stroke_path(body)
        painter.drawLine(QPointF(10.4, 10.8), QPointF(10.4, 17.0))
        painter.drawLine(QPointF(13.6, 10.8), QPointF(13.6, 17.0))

    elif name == "chevron":
        path = QPainterPath()
        path.moveTo(9.4, 6.0)
        path.lineTo(15.4, 12.0)
        path.lineTo(9.4, 18.0)
        stroke_path(path)

    elif name == "back":
        path = QPainterPath()
        path.moveTo(10.4, 5.2)
        path.lineTo(3.6, 12.0)
        path.lineTo(10.4, 18.8)
        stroke_path(path)
        painter.drawLine(QPointF(4.0, 12.0), QPointF(20.6, 12.0))

    elif name == "download":
        path = QPainterPath()
        path.moveTo(12.0, 3.8)
        path.lineTo(12.0, 14.2)
        path.moveTo(8.0, 10.4)
        path.lineTo(12.0, 14.4)
        path.lineTo(16.0, 10.4)
        path.moveTo(4.2, 16.4)
        path.lineTo(4.2, 18.6)
        path.quadTo(4.2, 20.2, 5.8, 20.2)
        path.lineTo(18.2, 20.2)
        path.quadTo(19.8, 20.2, 19.8, 18.6)
        path.lineTo(19.8, 16.4)
        stroke_path(path)

    elif name == "upload":
        path = QPainterPath()
        path.moveTo(12.0, 20.2)
        path.lineTo(12.0, 9.8)
        path.moveTo(8.0, 13.6)
        path.lineTo(12.0, 9.6)
        path.lineTo(16.0, 13.6)
        path.moveTo(4.2, 7.6)
        path.lineTo(4.2, 5.4)
        path.quadTo(4.2, 3.8, 5.8, 3.8)
        path.lineTo(18.2, 3.8)
        path.quadTo(19.8, 3.8, 19.8, 5.4)
        path.lineTo(19.8, 7.6)
        stroke_path(path)

    elif name == "key":
        painter.drawEllipse(QRectF(3.4, 3.4, 9.2, 9.2))
        key = QPainterPath()
        key.moveTo(10.9, 10.9)
        key.lineTo(20.4, 20.4)
        key.moveTo(16.4, 16.4)
        key.lineTo(18.6, 14.2)
        key.moveTo(18.6, 18.6)
        key.lineTo(20.4, 16.8)
        stroke_path(key)

    elif name == "clock":
        painter.drawEllipse(QRectF(3.4, 3.4, 17.2, 17.2))
        painter.drawLine(QPointF(12.0, 7.2), QPointF(12.0, 12.2))
        painter.drawLine(QPointF(12.0, 12.2), QPointF(15.6, 14.2))

    elif name == "shield":
        shield = QPainterPath()
        shield.moveTo(12.0, 3.0)
        shield.lineTo(19.4, 5.8)
        shield.lineTo(19.4, 11.6)
        shield.cubicTo(19.4, 16.2, 16.6, 19.7, 12.0, 21.2)
        shield.cubicTo(7.4, 19.7, 4.6, 16.2, 4.6, 11.6)
        shield.lineTo(4.6, 5.8)
        shield.closeSubpath()
        stroke_path(shield)
        check = QPainterPath()
        check.moveTo(8.4, 11.8)
        check.lineTo(11.0, 14.4)
        check.lineTo(15.7, 9.4)
        stroke_path(check)

    elif name == "terminal":
        screen = QPainterPath()
        screen.addRoundedRect(QRectF(3.2, 4.2, 17.6, 15.6), 2.6, 2.6)
        stroke_path(screen)
        prompt = QPainterPath()
        prompt.moveTo(7.2, 9.4)
        prompt.lineTo(10.2, 12.0)
        prompt.lineTo(7.2, 14.6)
        stroke_path(prompt)
        painter.drawLine(QPointF(13.0, 14.8), QPointF(17.0, 14.8))

    elif name == "add":
        painter.drawLine(QPointF(12.0, 4.6), QPointF(12.0, 19.4))
        painter.drawLine(QPointF(4.6, 12.0), QPointF(19.4, 12.0))

    elif name == "close":
        painter.drawLine(QPointF(5.8, 5.8), QPointF(18.2, 18.2))
        painter.drawLine(QPointF(18.2, 5.8), QPointF(5.8, 18.2))

    elif name in {"language", "translate"}:
        painter.drawEllipse(QRectF(3.0, 3.0, 18.0, 18.0))
        painter.drawEllipse(QRectF(7.5, 3.0, 9.0, 18.0))
        painter.drawLine(QPointF(3.0, 12.0), QPointF(21.0, 12.0))

    elif name == "person":
        painter.drawEllipse(QRectF(8.4, 3.6, 7.2, 7.2))
        shoulders = QPainterPath()
        shoulders.moveTo(4.6, 20.2)
        shoulders.cubicTo(4.6, 15.8, 7.9, 13.0, 12.0, 13.0)
        shoulders.cubicTo(16.1, 13.0, 19.4, 15.8, 19.4, 20.2)
        stroke_path(shoulders)

    elif name == "play":
        path = QPainterPath()
        path.moveTo(8.5, 5.5)
        path.lineTo(18.5, 12.0)
        path.lineTo(8.5, 18.5)
        path.closeSubpath()
        fill_path(path)

    elif name == "stop":
        path = QPainterPath()
        path.addRoundedRect(QRectF(6.0, 6.0, 12.0, 12.0), 2.2, 2.2)
        fill_path(path)

    elif name == "open_in_new":
        path = QPainterPath()
        path.moveTo(11.0, 5.0)
        path.lineTo(5.5, 5.0)
        path.quadTo(3.8, 5.0, 3.8, 6.7)
        path.lineTo(3.8, 18.3)
        path.quadTo(3.8, 20.0, 5.5, 20.0)
        path.lineTo(17.3, 20.0)
        path.quadTo(19.0, 20.0, 19.0, 18.3)
        path.lineTo(19.0, 13.0)
        stroke_path(path)
        arrow = QPainterPath()
        arrow.moveTo(13.0, 4.0)
        arrow.lineTo(20.0, 4.0)
        arrow.lineTo(20.0, 11.0)
        stroke_path(arrow)
        line = QPainterPath()
        line.moveTo(10.5, 13.5)
        line.lineTo(19.5, 4.5)
        stroke_path(line)

    elif name == "storage":
        path = QPainterPath()
        path.addRoundedRect(QRectF(4.0, 4.0, 16.0, 7.0), 2.0, 2.0)
        path.addRoundedRect(QRectF(4.0, 13.0, 16.0, 7.0), 2.0, 2.0)
        stroke_path(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pen_color)
        painter.drawEllipse(QRectF(16.0, 6.5, 2.0, 2.0))
        painter.drawEllipse(QRectF(16.0, 15.5, 2.0, 2.0))

    elif name in {"memory", "chip", "cpu"}:
        path = QPainterPath()
        path.addRoundedRect(QRectF(6.0, 6.0, 12.0, 12.0), 2.5, 2.5)
        stroke_path(path)
        for x in (9.0, 12.0, 15.0):
            painter.drawLine(QPointF(x, 2.5), QPointF(x, 6.0))
            painter.drawLine(QPointF(x, 18.0), QPointF(x, 21.5))
        for y in (9.0, 12.0, 15.0):
            painter.drawLine(QPointF(2.5, y), QPointF(6.0, y))
            painter.drawLine(QPointF(18.0, y), QPointF(21.5, y))
        painter.drawRoundedRect(QRectF(9.0, 9.0, 6.0, 6.0), 1.0, 1.0)

    elif name in {"settings", "gear"}:
        painter.drawEllipse(QRectF(8.5, 8.5, 7.0, 7.0))
        for angle in (0, 45, 90, 135, 180, 225, 270, 315):
            rad = math.radians(angle)
            c, s = math.cos(rad), math.sin(rad)
            painter.drawLine(
                QPointF(12.0 + 5.5 * c, 12.0 + 5.5 * s),
                QPointF(12.0 + 9.5 * c, 12.0 + 9.5 * s),
            )

    elif name == "minimize":
        painter.drawLine(QPointF(5.0, 13.0), QPointF(19.0, 13.0))

    elif name == "maximize":
        painter.drawRoundedRect(QRectF(5.5, 5.5, 13.0, 13.0), 2.0, 2.0)

    elif name == "restore_window":
        painter.drawRoundedRect(QRectF(5.0, 8.0, 11.0, 11.0), 1.5, 1.5)
        path = QPainterPath()
        path.moveTo(8.0, 5.0)
        path.lineTo(17.5, 5.0)
        path.lineTo(17.5, 14.5)
        stroke_path(path)

    elif name in {"cast", "screen_share", "mirror"}:
        screen = QPainterPath()
        screen.moveTo(4.0, 9.0)
        screen.lineTo(4.0, 6.0)
        screen.quadTo(4.0, 4.5, 5.5, 4.5)
        screen.lineTo(18.5, 4.5)
        screen.quadTo(20.0, 4.5, 20.0, 6.0)
        screen.lineTo(20.0, 17.0)
        screen.quadTo(20.0, 18.5, 18.5, 18.5)
        screen.lineTo(14.0, 18.5)
        stroke_path(screen)
        painter.drawArc(QRectF(1.0, 15.5, 6.0, 6.0), 0, 90 * 16)
        painter.drawArc(QRectF(-1.0, 13.5, 10.0, 10.0), 0, 90 * 16)
        painter.drawArc(QRectF(-3.0, 11.5, 14.0, 14.0), 0, 90 * 16)

    elif name in {"screenshot", "camera"}:
        box = QPainterPath()
        box.moveTo(4.5, 7.5)
        box.lineTo(7.5, 7.5)
        box.lineTo(9.0, 5.0)
        box.lineTo(15.0, 5.0)
        box.lineTo(16.5, 7.5)
        box.lineTo(19.5, 7.5)
        box.quadTo(20.5, 7.5, 20.5, 8.5)
        box.lineTo(20.5, 18.0)
        box.quadTo(20.5, 19.0, 19.5, 19.0)
        box.lineTo(4.5, 19.0)
        box.quadTo(3.5, 19.0, 3.5, 18.0)
        box.lineTo(3.5, 8.5)
        box.quadTo(3.5, 7.5, 4.5, 7.5)
        box.closeSubpath()
        stroke_path(box)
        painter.drawEllipse(QRectF(8.5, 9.5, 7.0, 7.0))

    elif name == "power":
        painter.drawArc(QRectF(4.5, 4.5, 15.0, 15.0), 45 * 16, 270 * 16)
        painter.drawLine(QPointF(12.0, 3.5), QPointF(12.0, 11.0))

    elif name in {"volume_up", "volume_down", "volume", "volume_mute"}:
        speaker = QPainterPath()
        speaker.moveTo(4.5, 9.0)
        speaker.lineTo(8.0, 9.0)
        speaker.lineTo(12.5, 5.0)
        speaker.lineTo(12.5, 19.0)
        speaker.lineTo(8.0, 15.0)
        speaker.lineTo(4.5, 15.0)
        speaker.closeSubpath()
        stroke_path(speaker)
        if name == "volume_up":
            painter.drawLine(QPointF(16.0, 12.0), QPointF(20.0, 12.0))
            painter.drawLine(QPointF(18.0, 10.0), QPointF(18.0, 14.0))
        elif name == "volume_down":
            painter.drawLine(QPointF(16.0, 12.0), QPointF(20.0, 12.0))
        elif name == "volume_mute":
            painter.drawLine(QPointF(16.0, 9.5), QPointF(20.0, 14.5))
            painter.drawLine(QPointF(20.0, 9.5), QPointF(16.0, 14.5))
        else:
            painter.drawArc(QRectF(11.0, 8.0, 8.0, 8.0), -60 * 16, 120 * 16)

    elif name in {"rotate", "screen_rotation", "refresh_cw"}:
        painter.drawArc(QRectF(4.5, 4.5, 15.0, 15.0), 30 * 16, 275 * 16)
        arrow = QPainterPath()
        arrow.moveTo(16.5, 4.0)
        arrow.lineTo(20.5, 8.0)
        arrow.lineTo(16.0, 10.5)
        arrow.closeSubpath()
        fill_path(arrow)

    elif name in {"home", "house"}:
        home = QPainterPath()
        home.moveTo(4.0, 11.5)
        home.lineTo(12.0, 4.5)
        home.lineTo(20.0, 11.5)
        home.lineTo(18.0, 11.5)
        home.lineTo(18.0, 19.5)
        home.lineTo(13.5, 19.5)
        home.lineTo(13.5, 14.0)
        home.lineTo(10.5, 14.0)
        home.lineTo(10.5, 19.5)
        home.lineTo(6.0, 19.5)
        home.lineTo(6.0, 11.5)
        home.closeSubpath()
        stroke_path(home)

    else:  # safe fallback: small ring
        painter.drawEllipse(QRectF(5.0, 5.0, 14.0, 14.0))

    painter.restore()


class IconWidget(QWidget):
    """A lightweight static icon."""

    def __init__(
        self,
        name: str,
        color: str | QColor,
        size: int = 24,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._name = name
        self._color = QColor(color)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_color(self, color: str | QColor) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        draw_icon(painter, self._name, QRectF(self.rect()), self._color)


class IconBadge(QWidget):
    """Rounded tonal square with a centred icon."""

    def __init__(
        self,
        name: str,
        *,
        size: int,
        radius: int,
        background: str | QColor,
        foreground: str | QColor,
        icon_size: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._name = name
        self._background = QColor(background)
        self._foreground = QColor(foreground)
        self._icon_size = icon_size
        self.setFixedSize(size, size)
        self._radius = radius
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._background)
        painter.drawRoundedRect(
            QRectF(self.rect()), self._radius, self._radius
        )
        draw_icon(
            painter,
            self._name,
            QRectF(
                (self.width() - self._icon_size) / 2.0,
                (self.height() - self._icon_size) / 2.0,
                self._icon_size,
                self._icon_size,
            ),
            self._foreground,
            stroke=max(1.7, self._icon_size / 18.0),
        )


def build_app_icon(palette: Mapping[str, str]) -> QIcon:
    pixmap = QPixmap(128, 128)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    gradient = QLinearGradient(8, 8, 120, 120)
    gradient.setColorAt(0.0, QColor(palette.get("primary", "#6750A4")))
    gradient.setColorAt(1.0, QColor(palette.get("tertiary", "#7D5260")))
    path = QPainterPath()
    path.addRoundedRect(QRectF(8, 8, 112, 112), 34, 34)
    painter.fillPath(path, gradient)
    painter.setPen(QColor(palette.get("on_primary", "#FFFFFF")))
    painter.setFont(ui_font(46, QFont.Weight.ExtraBold, display=True))
    painter.drawText(QRectF(8, 4, 112, 116), int(Qt.AlignmentFlag.AlignCenter), "PX")
    painter.end()
    return QIcon(pixmap)
