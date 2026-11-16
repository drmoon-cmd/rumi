"""마우스를 화면 오른쪽 끝에 대면 재생목록을 영상 위에 띄우고, 벗어나면 다시 숨긴다.

전체화면에서도, 창 모드에서 재생목록 패널을 닫아 둔 때에도 같은 방식으로 목록을 꺼내 쓸 수 있다.
전체화면 조작 줄(hover_reveal.py)과 마찬가지로 마우스 이동 이벤트 대신 커서 위치를 주기적으로 본다.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QWidget

POLL_MS = 100
EDGE_PX = 14          # 오른쪽 끝에서 이 안쪽으로 들어오면 '끝에 댄 것'으로 본다
SHOW_AFTER_MS = 200   # 끝에 이만큼 머물러야 띄운다 (지나가다 스친 것은 무시)
HIDE_AFTER_MS = 500   # 패널에서 벗어나 이만큼 지나면 숨긴다
KEEP_MARGIN = 48      # 패널 왼쪽으로 이만큼까지는 벗어난 것으로 치지 않는다


class EdgeReveal(QObject):
    def __init__(self, area: QWidget, panel: QWidget, can_show: Callable[[], bool],
                 show: Callable[[], None], hide: Callable[[], None]):
        super().__init__(area)
        self.area, self.panel = area, panel
        self._can_show, self._show, self._hide = can_show, show, hide
        self.pinned = False   # 단축키·버튼으로 연 경우: 마우스가 벗어나도 숨기지 않는다
        self._at_edge = 0
        self._away = 0
        self._timer = QTimer(self, interval=POLL_MS)
        self._timer.timeout.connect(self._poll)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _poll(self) -> None:
        local = self.area.mapFromGlobal(QCursor.pos())
        if not self.panel.isVisible():
            self._away = 0
            window = self.area.window()
            at_edge = (window.isActiveWindow() and not QApplication.activePopupWidget()
                       and QApplication.mouseButtons() == Qt.NoButton
                       and self.area.width() - EDGE_PX <= local.x() < self.area.width()
                       and 0 <= local.y() < self.panel.height() and self._can_show())
            self._at_edge = self._at_edge + POLL_MS if at_edge else 0
            if self._at_edge >= SHOW_AFTER_MS:
                self._at_edge = 0
                self._show()
            return
        if self.pinned:
            return
        keep = self.panel.geometry().adjusted(-KEEP_MARGIN, 0, 0, 0)
        # 메뉴·대화상자를 쓰는 중이거나 끌어다 놓는 중이면 숨기지 않는다
        busy = (QApplication.activePopupWidget() is not None or QApplication.activeModalWidget() is not None
                or QApplication.mouseButtons() != Qt.NoButton)
        if busy or keep.contains(local):
            self._away = 0
            return
        self._away += POLL_MS
        if self._away >= HIDE_AFTER_MS:
            self._away = 0
            self._hide()
