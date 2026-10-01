"""테마 글자색으로 직접 그리는 단순한 아이콘 (기본 아이콘은 어두운 테마에서 잘 안 보인다)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

SIZE = 64  # 그린 뒤 버튼 크기로 줄여 쓴다


def _poly(p: QPainter, *pts) -> None:
    p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))


def make_icon(kind: str, color: str) -> QIcon:
    pm = QPixmap(SIZE, SIZE)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = QColor(color)
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    line = QPen(c, 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

    if kind == "play":
        _poly(p, (20, 12), (52, 32), (20, 52))
    elif kind == "pause":
        p.drawRoundedRect(QRectF(16, 12, 11, 40), 3, 3)
        p.drawRoundedRect(QRectF(37, 12, 11, 40), 3, 3)
    elif kind == "stop":
        p.drawRoundedRect(QRectF(16, 16, 32, 32), 4, 4)
    elif kind == "prev":
        p.drawRoundedRect(QRectF(12, 14, 7, 36), 2, 2)
        _poly(p, (52, 14), (22, 32), (52, 50))
    elif kind == "next":
        p.drawRoundedRect(QRectF(45, 14, 7, 36), 2, 2)
        _poly(p, (12, 14), (42, 32), (12, 50))
    elif kind in ("volume", "mute"):
        _poly(p, (8, 24), (20, 24), (34, 10), (34, 54), (20, 40), (8, 40))
        p.setBrush(Qt.NoBrush)
        p.setPen(line)
        if kind == "volume":
            path = QPainterPath()
            path.arcMoveTo(QRectF(26, 18, 18, 28), -50)
            path.arcTo(QRectF(26, 18, 18, 28), -50, 100)
            p.drawPath(path)
            path = QPainterPath()
            path.arcMoveTo(QRectF(26, 8, 30, 48), -50)
            path.arcTo(QRectF(26, 8, 30, 48), -50, 100)
            p.drawPath(path)
        else:
            p.drawLine(42, 22, 58, 42)
            p.drawLine(58, 22, 42, 42)
    elif kind == "list":
        for y in (16, 30, 44):
            p.drawRoundedRect(QRectF(10, y, 44, 6), 3, 3)
    elif kind == "fullscreen":
        p.setBrush(Qt.NoBrush)
        p.setPen(line)
        for x, y, dx, dy in ((10, 10, 1, 1), (54, 10, -1, 1), (10, 54, 1, -1), (54, 54, -1, -1)):
            p.drawLine(x, y, x + 14 * dx, y)
            p.drawLine(x, y, x, y + 14 * dy)
    elif kind == "close":
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(c, 6, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(18, 18, 46, 46)
        p.drawLine(46, 18, 18, 46)
    p.end()
    return QIcon(pm)
