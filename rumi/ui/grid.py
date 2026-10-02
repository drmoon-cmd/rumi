"""화면 분할 보기: CCTV 관제 화면처럼 여러 영상을 격자로 나눠 동시에 보여 준다."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QGridLayout, QLabel, QMenu, QStackedLayout, QWidget

from .mpv_widget import MpvWidget

LAYOUTS = {  # 이름: (열, 행)
    "2분할 (1×2)": (2, 1),
    "4분할 (2×2)": (2, 2),
    "9분할 (3×3)": (3, 3),
    "16분할 (4×4)": (4, 4),
}


class GridCell(QWidget):
    """칸 하나: 영상 또는 '비어 있음' 표시. 파일을 끌어다 놓으면 바뀐다."""

    def __init__(self, grid: "GridWindow", hwdec: str, compat: bool):
        super().__init__(grid)
        self.grid = grid
        self.path: str | None = None
        self.setAcceptDrops(True)
        self.setStyleSheet("background: black;")
        self.video = MpvWidget(self)
        p = self.video.player
        p.mute = True
        p.hwdec = hwdec
        p["loop-file"] = "inf"  # CCTV 처럼 끝나면 처음부터 반복
        self.video.set_compat_mode(compat)
        self.video.context_menu_requested.connect(grid.show_menu)
        self.video.double_clicked.connect(grid.toggle_fullscreen)
        self.video.start_observing()
        self.empty = QLabel("비어 있음\n(동영상 파일을 끌어다 놓으세요)")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet("color: #666; background: black;")
        self.stack = QStackedLayout(self)
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.video)

    def load(self, path: str | None) -> None:
        self.path = path
        if path:
            self.stack.setCurrentWidget(self.video)
            self.video.load(path)
            self.setToolTip(os.path.basename(path))
        else:
            self.stack.setCurrentWidget(self.empty)
            self.setToolTip("")

    def contextMenuEvent(self, e):
        self.grid.show_menu(e.globalPos())

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if files:
            self.load(files[0])

    def shutdown(self) -> None:
        self.video.shutdown()


class GridWindow(QWidget):
    def __init__(self, paths: list[str], layout_name: str, hwdec: str = "no", compat: bool = False):
        super().__init__(None, Qt.Window)
        self.setWindowTitle("화면 분할 보기 - Rumi")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setStyleSheet("background: #111;")
        self.paths = list(paths)
        self.hwdec, self.compat = hwdec, compat
        self.cells: list[GridCell] = []
        self.layout_name = ""
        self.paused = False
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(2)
        self.set_layout(layout_name)

        for key, slot in (("Esc", self.close), ("Space", self.toggle_pause),
                          ("F", self.toggle_fullscreen), ("Return", self.toggle_fullscreen)):
            a = QAction(self)
            a.setShortcut(QKeySequence(key))
            a.triggered.connect(slot)
            self.addAction(a)

    def set_layout(self, name: str) -> None:
        cols, rows = LAYOUTS[name]
        n = cols * rows
        # 칸에 있던 영상은 유지하고, 빈 자리는 재생목록 순서대로 채운다
        current = [c.path for c in self.cells]
        for c in self.cells:
            self.grid.removeWidget(c)
        while len(self.cells) > n:
            c = self.cells.pop()
            c.shutdown()
            c.deleteLater()
        while len(self.cells) < n:
            self.cells.append(GridCell(self, self.hwdec, self.compat))
        used = [p for p in current[:n] if p]
        queue = [p for p in self.paths if p not in used]
        for i, cell in enumerate(self.cells):
            if i < len(current) and current[i]:
                continue  # 이미 재생 중인 칸은 그대로
            cell.load(queue.pop(0) if queue else None)
        for i, cell in enumerate(self.cells):
            self.grid.addWidget(cell, i // cols, i % cols)
        for r in range(4):
            self.grid.setRowStretch(r, 1 if r < rows else 0)
        for c in range(4):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)
        self.layout_name = name

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        for c in self.cells:
            if c.path:
                c.video.player.pause = self.paused

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
            from .main_window import _windows_fullscreen_border
            _windows_fullscreen_border(self)

    def show_menu(self, pos) -> None:
        menu = QMenu(self)
        split = menu.addMenu("분할")
        for name in LAYOUTS:
            a = split.addAction(name, lambda n=name: self.set_layout(n))
            a.setCheckable(True)
            a.setChecked(name == self.layout_name)
        menu.addAction("모두 재생" if self.paused else "모두 일시정지", self.toggle_pause)
        menu.addAction("창 모드로" if self.isFullScreen() else "전체화면", self.toggle_fullscreen)
        menu.addSeparator()
        menu.addAction("닫기 (Esc)", self.close)
        menu.exec(pos)

    def closeEvent(self, e):
        for c in self.cells:
            c.shutdown()
        self.cells = []
        super().closeEvent(e)
