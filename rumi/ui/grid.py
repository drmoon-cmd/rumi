"""화면 분할 보기: CCTV 관제 화면처럼 여러 영상을 격자로 나눠 동시에 보여 준다."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QLabel, QMenu, QStackedLayout, QToolButton, QWidget,
)

from .controls import SeekSlider
from .hover_reveal import HoverReveal, bottom_zone
from .icons import make_icon
from .mpv_widget import MpvWidget
from .util import fmt_time

SPEEDS = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 4.0)

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


class GridBar(QWidget):
    """분할 화면 아래 조작 줄: 모두 재생/일시정지, 탐색(모든 화면 같은 위치로), 배속, 창 모드, 닫기."""

    def __init__(self, grid: "GridWindow"):
        super().__init__(grid)
        self.setObjectName("gridBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("#gridBar { background: rgba(20, 21, 24, 230); border-top: 1px solid #3a3d43; }"
                           "#gridBar QLabel, #gridBar QToolButton { color: #e6e7e9; }")
        self.play = QToolButton()
        self.play.setAutoRaise(True)
        self.play.setFocusPolicy(Qt.NoFocus)
        self.play.clicked.connect(grid.toggle_pause)
        self.seek = SeekSlider()
        self.seek.setFocusPolicy(Qt.NoFocus)
        self.seek.seek_requested.connect(grid.seek_all)
        self.time = QLabel("--:-- / --:--")
        self.speed = QComboBox()
        for sp in SPEEDS:
            self.speed.addItem(f"{sp:g}x", sp)
        self.speed.setCurrentIndex(SPEEDS.index(1.0))
        self.speed.setFocusPolicy(Qt.NoFocus)
        self.speed.activated.connect(lambda i: grid.set_speed(SPEEDS[i]))
        self.full = QToolButton()
        self.full.setIcon(make_icon("fullscreen", "#e6e7e9"))
        self.full.setToolTip("전체화면 / 창 모드 (F)")
        self.full.setAutoRaise(True)
        self.full.setFocusPolicy(Qt.NoFocus)
        self.full.clicked.connect(grid.toggle_fullscreen)
        close = QToolButton()
        close.setIcon(make_icon("close", "#e6e7e9"))
        close.setToolTip("분할 화면 닫기 (Esc)")
        close.setAutoRaise(True)
        close.setFocusPolicy(Qt.NoFocus)
        close.clicked.connect(grid.close)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        row.addWidget(self.play)
        row.addWidget(self.seek, 1)
        row.addWidget(self.time)
        row.addWidget(self.speed)
        row.addWidget(self.full)
        row.addWidget(close)
        self.set_paused(False)

    def set_paused(self, paused: bool) -> None:
        self.play.setIcon(make_icon("play" if paused else "pause", "#e6e7e9"))


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

        self.bar = GridBar(self)
        self.reveal = HoverReveal(self, [(self.bar, bottom_zone)],
                                  cursor_widgets=[c.video for c in self.cells])
        self.reveal.start()
        self._tick = QTimer(self, interval=300)
        self._tick.timeout.connect(self._update_bar)
        self._tick.start()

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
        if hasattr(self, "reveal"):
            self.reveal.cursor_widgets = [c.video for c in self.cells]
            self.bar.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "bar"):
            h = self.bar.sizeHint().height()
            self.bar.setGeometry(0, self.height() - h, self.width(), h)
            self.bar.raise_()

    def _playing(self) -> list[GridCell]:
        return [c for c in self.cells if c.path]

    def _update_bar(self) -> None:
        cells = self._playing()
        if not cells:
            return
        p = cells[0].video.player
        try:
            t, d = float(p.time_pos or 0), float(p.duration or 0)
        except (TypeError, ValueError):
            return
        if d > 0:
            self.bar.seek.set_ratio(t / d)
        self.bar.time.setText(f"{fmt_time(t)} / {fmt_time(d or None)}")

    def seek_all(self, ratio: float) -> None:
        """모든 화면을 같은 위치(각 영상 길이의 같은 비율)로 옮긴다."""
        for c in self._playing():
            try:
                d = float(c.video.player.duration or 0)
                if d > 0:
                    c.video.player.seek(ratio * d, "absolute", "exact")
            except (SystemError, TypeError, ValueError):
                pass

    def set_speed(self, sp: float) -> None:
        for c in self._playing():
            c.video.player.speed = sp

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        for c in self.cells:
            if c.path:
                c.video.player.pause = self.paused
        if hasattr(self, "bar"):
            self.bar.set_paused(self.paused)

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
        self.reveal.stop()
        self._tick.stop()
        for c in self.cells:
            c.shutdown()
        self.cells = []
        super().closeEvent(e)
