"""전체화면에서 마우스를 가장자리에 대면 조작 줄을 보이고, 가만히 있으면 숨긴다.

마우스 이동 이벤트에 기대지 않고 커서 위치를 주기적으로 확인한다. Windows 의 OpenGL 전체화면
에서는 영상 위젯이 마우스 이동 이벤트를 받지 못하는 경우가 있어, 이벤트 방식으로는 조작 줄이
나타나지 않을 수 있기 때문이다.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QWidget

POLL_MS = 150
HIDE_AFTER_MS = 2500


class HoverReveal(QObject):
    """area 위에서 커서가 zone(y, h) 안에 오면 해당 bar 를 보인다."""

    def __init__(self, area: QWidget, bars: list[tuple[QWidget, Callable[[int, int], bool]]],
                 cursor_widgets: list[QWidget] | None = None):
        super().__init__(area)
        self.area = area
        self.bars = bars
        self.cursor_widgets = cursor_widgets or []  # 숨김 상태일 때 커서를 감출 위젯들
        self._last = QPoint(-1, -1)
        self._idle_ms = 0
        self._timer = QTimer(self, interval=POLL_MS)
        self._timer.timeout.connect(self._poll)

    def start(self) -> None:
        self._last = QCursor.pos()
        self._idle_ms = 0
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        for w in self.cursor_widgets:
            w.unsetCursor()

    def show_all(self) -> None:
        for bar, _zone in self.bars:
            bar.show()
            bar.raise_()
        self._idle_ms = 0

    def _poll(self) -> None:
        pos = QCursor.pos()
        moved = pos != self._last
        self._last = pos
        local = self.area.mapFromGlobal(pos)
        inside = self.area.rect().contains(local)
        if moved and inside:
            self._idle_ms = 0
            for w in self.cursor_widgets:
                w.unsetCursor()
            for bar, zone in self.bars:
                if zone(local.y(), self.area.height()):
                    bar.show()
                    bar.raise_()
            return
        self._idle_ms += POLL_MS
        if self._idle_ms < HIDE_AFTER_MS:
            return
        # 조작 줄 위에 커서가 있거나 메뉴를 쓰는 중이면 숨기지 않는다
        if QApplication.activePopupWidget() or any(b.isVisible() and b.underMouse() for b, _ in self.bars):
            self._idle_ms = 0
            return
        if any(b.isVisible() for b, _ in self.bars):
            for b, _ in self.bars:
                b.hide()
            for w in self.cursor_widgets:
                w.setCursor(Qt.BlankCursor)


def top_zone(y: int, h: int) -> bool:
    return y <= max(90, h * 0.12)


def bottom_zone(y: int, h: int) -> bool:
    return y >= h - max(140, h * 0.22)
