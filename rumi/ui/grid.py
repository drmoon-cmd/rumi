"""화면 분할 보기: CCTV 관제 화면처럼 여러 영상을 격자로 나눠 동시에 보여 준다."""

from __future__ import annotations

import os

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QMenu, QStackedLayout, QToolButton, QWidget,
)

from ..library.scanner import VIDEO_EXTENSIONS
from .controls import SeekSlider
from .fullscreen import enter_fullscreen, is_fullscreen, leave_fullscreen
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
MAX_CELLS = 16
FG = "#e6e7e9"


def layout_for(count: int, current: str | None = None) -> str:
    """영상 count 개가 모두 들어가는 분할. 지금 분할로 충분하면 그대로 둔다."""
    if current in LAYOUTS and LAYOUTS[current][0] * LAYOUTS[current][1] >= count:
        return current
    for name, (cols, rows) in LAYOUTS.items():
        if cols * rows >= count:
            return name
    return list(LAYOUTS)[-1]


def video_filter() -> str:
    exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))
    return f"동영상 ({exts});;모든 파일 (*)"


def _button(icon: str | None, tip: str, slot, text: str = "") -> QToolButton:
    b = QToolButton()
    if icon:
        b.setIcon(make_icon(icon, FG))
    if text:
        b.setText(text)
    b.setToolTip(tip)
    b.setAutoRaise(True)
    b.setFocusPolicy(Qt.NoFocus)
    b.clicked.connect(slot)
    return b


class _EmptyLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class CellBar(QWidget):
    """칸 위쪽에 마우스를 올리면 나타나는 칸별 조작 줄."""

    def __init__(self, cell: "GridCell"):
        super().__init__(cell)
        self.setObjectName("cellBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("#cellBar { background: rgba(20, 21, 24, 215); }"
                           f"#cellBar QLabel, #cellBar QToolButton {{ color: {FG}; font-size: 11px; }}")
        self.name = QLabel()
        self.name.setMinimumWidth(40)
        self.play = _button("pause", "이 화면 재생/일시정지", cell.toggle_pause)
        self.seek = SeekSlider()
        self.seek.setFocusPolicy(Qt.NoFocus)
        self.seek.seek_requested.connect(cell.seek_ratio)
        self.time = QLabel("--:--")
        self.sound = _button("mute", "이 화면 소리 켜기/끄기", cell.toggle_sound)
        self.open = _button(None, "이 칸에 다른 파일 열기", cell.choose_file, "열기")
        self.solo = _button("fullscreen", "이 화면만 크게 / 되돌리기 (더블클릭)", cell.toggle_solo)
        self.clear = _button("close", "이 칸 비우기", lambda: cell.load(None))
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 1, 2, 1)
        row.setSpacing(2)
        row.addWidget(self.play)
        row.addWidget(self.name, 2)
        row.addWidget(self.seek, 3)
        row.addWidget(self.time)
        row.addWidget(self.sound)
        row.addWidget(self.open)
        row.addWidget(self.solo)
        row.addWidget(self.clear)
        self.hide()


class GridCell(QWidget):
    """칸 하나: 영상 또는 '비어 있음' 표시. 파일을 끌어다 놓거나 빈 칸을 눌러 열 수 있다."""

    def __init__(self, grid: "GridWindow", hwdec: str, compat: bool):
        super().__init__(grid)
        self.grid = grid
        self.path: str | None = None
        self.setAcceptDrops(True)
        self.setStyleSheet("background: black;")
        self.video = MpvWidget(self)
        self.video.setMinimumSize(80, 45)
        p = self.video.player
        p.mute = True
        p.hwdec = hwdec
        p["loop-file"] = "inf"  # CCTV 처럼 끝나면 처음부터 반복
        self.video.set_compat_mode(compat)
        self.video.context_menu_requested.connect(lambda pos: grid.show_menu(pos, self))
        self.video.double_clicked.connect(self.toggle_solo)
        self.video.pause_changed.connect(lambda paused: self.bar.play.setIcon(
            make_icon("play" if paused else "pause", FG)))
        self.video.mute_changed.connect(lambda m: self.bar.sound.setIcon(make_icon("mute" if m else "volume", FG)))
        self.video.start_observing()
        self.empty = _EmptyLabel("비어 있음\n\n눌러서 파일 열기\n(또는 동영상 파일을 끌어다 놓기)")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setCursor(Qt.PointingHandCursor)
        self.empty.setStyleSheet("color: #777; background: #0b0b0c;")
        self.empty.clicked.connect(self.choose_file)
        self.stack = QStackedLayout(self)
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.video)
        self.bar = CellBar(self)

    # ---- 파일 ----
    def load(self, path: str | None) -> None:
        self.path = path
        if path:
            self.stack.setCurrentWidget(self.video)
            self.video.load(path)
            self.video.player.pause = self.grid.paused
            self.bar.name.setText(os.path.basename(path))
            self.bar.name.setToolTip(path)
            self.setToolTip(os.path.basename(path))
        else:
            try:
                self.video.player.command("stop")
            except Exception:
                pass
            self.stack.setCurrentWidget(self.empty)
            self.bar.name.setText("")
            self.bar.hide()
            self.setToolTip("")
            if self.grid.solo_cell is self:
                self.grid.toggle_solo(self)

    def choose_file(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self.grid, "이 칸에 열 동영상", self.grid.last_dir(), video_filter())
        if f:
            self.grid.remember_dir(f)
            self.load(f)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if len(files) > 1:
            self.grid.set_files(files, start=self.grid.cells.index(self))
        elif files:
            self.load(files[0])

    def contextMenuEvent(self, e):
        self.grid.show_menu(e.globalPos(), self)

    # ---- 조작 ----
    def toggle_pause(self) -> None:
        if self.path:
            self.video.player.pause = not self.video.player.pause

    def toggle_sound(self) -> None:
        if self.path:
            self.video.player.mute = not self.video.player.mute

    def toggle_solo(self) -> None:
        self.grid.toggle_solo(self)

    def seek_ratio(self, ratio: float) -> None:
        try:
            d = float(self.video.player.duration or 0)
            if d > 0:
                self.video.player.seek(ratio * d, "absolute", "exact")
        except (SystemError, TypeError, ValueError):
            pass

    def update_bar(self) -> None:
        if not self.path or not self.bar.isVisible():
            return
        p = self.video.player
        try:
            t, d = float(p.time_pos or 0), float(p.duration or 0)
        except (TypeError, ValueError):
            return
        if d > 0:
            self.bar.seek.set_ratio(t / d)
        self.bar.time.setText(f"{fmt_time(t)} / {fmt_time(d or None)}")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 칸이 작으면 파일 이름·시간은 빼고 버튼과 탐색 바만 (이름은 마우스를 올리면 보임)
        self.bar.name.setVisible(self.width() >= 480)
        self.bar.time.setVisible(self.width() >= 300)
        self.bar.setGeometry(0, 0, self.width(), self.bar.sizeHint().height())
        self.bar.raise_()

    def shutdown(self) -> None:
        self.video.shutdown()


class GridBar(QWidget):
    """분할 화면 아래 조작 줄: 모두 재생/일시정지, 탐색(모든 화면 같은 위치로), 배속, 파일 열기, 분할, 창 모드, 닫기."""

    def __init__(self, grid: "GridWindow"):
        super().__init__(grid)
        self.setObjectName("gridBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("#gridBar { background: rgba(20, 21, 24, 230); border-top: 1px solid #3a3d43; }"
                           f"#gridBar QLabel, #gridBar QToolButton {{ color: {FG}; }}")
        self.play = _button("pause", "모두 재생/일시정지 (Space)", grid.toggle_pause)
        self.seek = SeekSlider()
        self.seek.setFocusPolicy(Qt.NoFocus)
        self.seek.setToolTip("모든 화면을 같은 위치로")
        self.seek.seek_requested.connect(grid.seek_all)
        self.time = QLabel("--:-- / --:--")
        self.speed = QComboBox()
        for sp in SPEEDS:
            self.speed.addItem(f"{sp:g}x", sp)
        self.speed.setCurrentIndex(SPEEDS.index(1.0))
        self.speed.setFocusPolicy(Qt.NoFocus)
        self.speed.setToolTip("모든 화면 재생 속도")
        self.speed.activated.connect(lambda i: grid.set_speed(SPEEDS[i]))
        self.layout_box = QComboBox()
        self.layout_box.addItems(list(LAYOUTS))
        self.layout_box.setFocusPolicy(Qt.NoFocus)
        self.layout_box.setToolTip("화면 분할")
        self.layout_box.activated.connect(lambda i: grid.set_layout(list(LAYOUTS)[i]))
        files = _button(None, "여러 파일을 골라 칸에 차례로 넣기 (Ctrl+O)", grid.choose_files, "파일 열기")
        folder = _button(None, "폴더 안의 동영상으로 칸 채우기", grid.choose_folder, "폴더 열기")
        self.mute = _button("mute", "모든 화면 소리 끄기", grid.mute_all)
        self.full = _button("fullscreen", "전체화면 / 창 모드 (F)", grid.toggle_fullscreen)
        close = _button("close", "분할 화면 닫기 (Esc)", grid.close)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        row.addWidget(self.play)
        row.addWidget(self.seek, 1)
        row.addWidget(self.time)
        row.addWidget(self.speed)
        row.addSpacing(8)
        row.addWidget(files)
        row.addWidget(folder)
        row.addWidget(self.layout_box)
        row.addWidget(self.mute)
        row.addWidget(self.full)
        row.addWidget(close)

    def set_paused(self, paused: bool) -> None:
        self.play.setIcon(make_icon("play" if paused else "pause", FG))


class GridWindow(QWidget):
    def __init__(self, paths: list[str], layout_name: str, hwdec: str = "no", compat: bool = False):
        super().__init__(None, Qt.Window)
        self.setWindowTitle("화면 분할 보기 - Rumi")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setStyleSheet("background: #111;")
        self.settings = QSettings()
        self.hwdec, self.compat = hwdec, compat
        self.cells: list[GridCell] = []
        self.layout_name = ""
        self.paused = False
        self.solo_cell: GridCell | None = None
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(2)
        self.bar = GridBar(self)
        self.reveal = HoverReveal(self, [(self.bar, bottom_zone)])
        self.set_layout(layout_for(len(paths), layout_name))
        self.set_files(paths)
        self.reveal.start()
        self._tick = QTimer(self, interval=300)
        self._tick.timeout.connect(self._update_bars)
        self._tick.start()

        for key, slot in (("Esc", self._escape), ("Space", self.toggle_pause),
                          ("F", self.toggle_fullscreen), ("Return", self.toggle_fullscreen),
                          ("Ctrl+O", self.choose_files)):
            a = QAction(self)
            a.setShortcut(QKeySequence(key))
            a.triggered.connect(slot)
            self.addAction(a)

    # ---- 칸 배치 ----
    def set_layout(self, name: str) -> None:
        cols, rows = LAYOUTS[name]
        n = cols * rows
        self.solo_cell = None
        while len(self.cells) > n:
            c = self.cells.pop()
            self.grid.removeWidget(c)
            c.shutdown()
            c.deleteLater()
        while len(self.cells) < n:
            self.cells.append(GridCell(self, self.hwdec, self.compat))
        self.layout_name = name
        self.bar.layout_box.setCurrentText(name)
        self._arrange()

    def _arrange(self) -> None:
        cols, rows = LAYOUTS[self.layout_name]
        for c in self.cells:
            self.grid.removeWidget(c)
        if self.solo_cell is not None:
            for c in self.cells:
                c.setVisible(c is self.solo_cell)
            self.grid.addWidget(self.solo_cell, 0, 0)
            cols = rows = 1
        else:
            for i, c in enumerate(self.cells):
                self.grid.addWidget(c, i // cols, i % cols)
                c.show()
        for r in range(4):
            self.grid.setRowStretch(r, 1 if r < rows else 0)
        for c in range(4):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)
        self.reveal.cursor_widgets = [c.video for c in self.cells]
        self.reveal.follow = [(c.bar, lambda pos, _a, c=c: c.isVisible() and bool(c.path)
                               and c.geometry().contains(pos))
                              for c in self.cells]
        self.bar.raise_()

    def toggle_solo(self, cell: GridCell) -> None:
        """한 화면만 크게 보기 / 분할로 되돌리기."""
        self.solo_cell = None if self.solo_cell is cell else (cell if cell.path else None)
        self._arrange()

    def _escape(self) -> None:
        if self.solo_cell is not None:
            self.toggle_solo(self.solo_cell)
        else:
            self.close()

    # ---- 파일 ----
    def last_dir(self) -> str:
        return self.settings.value("gridDir", "") or self.settings.value("lastDir", "")

    def remember_dir(self, path: str) -> None:
        self.settings.setValue("gridDir", os.path.dirname(path))

    def set_files(self, paths: list[str], start: int = 0) -> None:
        """start 번째 칸부터 차례로 영상을 넣는다. start=0 이면 남는 칸은 비운다."""
        paths = [p for p in paths if p][:MAX_CELLS]
        need = layout_for(start + len(paths), self.layout_name)
        if need != self.layout_name:
            self.set_layout(need)
        for i, cell in enumerate(self.cells):
            if i < start:
                continue
            j = i - start
            if j < len(paths):
                cell.load(paths[j])
            elif start == 0 and cell.path:
                cell.load(None)

    def choose_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "분할 화면에 띄울 동영상 (여러 개 선택)",
                                                self.last_dir(), video_filter())
        if files:
            self.remember_dir(files[0])
            self.set_files(files)

    def choose_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "분할 화면에 띄울 동영상 폴더", self.last_dir())
        if not d:
            return
        files = sorted(os.path.join(d, f) for f in os.listdir(d)
                       if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS)
        if files:
            self.settings.setValue("gridDir", d)
            self.set_files(files)

    # ---- 조작 ----
    def resizeEvent(self, e):
        super().resizeEvent(e)
        h = self.bar.sizeHint().height()
        self.bar.setGeometry(0, self.height() - h, self.width(), h)
        self.bar.raise_()

    def _playing(self) -> list[GridCell]:
        return [c for c in self.cells if c.path]

    def _update_bars(self) -> None:
        cells = self._playing()
        for c in cells:
            c.update_bar()
        if not cells or not self.bar.isVisible():
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
            c.seek_ratio(ratio)

    def set_speed(self, sp: float) -> None:
        for c in self._playing():
            c.video.player.speed = sp

    def mute_all(self) -> None:
        for c in self._playing():
            c.video.player.mute = True

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        for c in self._playing():
            c.video.player.pause = self.paused
        self.bar.set_paused(self.paused)

    def toggle_fullscreen(self) -> None:
        if is_fullscreen(self):
            leave_fullscreen(self)
        else:
            enter_fullscreen(self)

    def show_menu(self, pos, cell: GridCell | None = None) -> None:
        menu = QMenu(self)
        if cell is not None:
            menu.addSection(os.path.basename(cell.path) if cell.path else "빈 칸")
            menu.addAction("이 칸에 파일 열기…", cell.choose_file)
            if cell.path:
                menu.addAction("이 화면 재생/일시정지", cell.toggle_pause)
                snd = menu.addAction("이 화면 소리", cell.toggle_sound)
                snd.setCheckable(True)
                snd.setChecked(not cell.video.player.mute)
                menu.addAction("분할로 되돌리기" if self.solo_cell is cell else "이 화면만 크게 보기",
                               cell.toggle_solo)
                menu.addAction("이 칸 비우기", lambda: cell.load(None))
            menu.addSeparator()
        menu.addAction("파일 열기 (여러 개)…", self.choose_files)
        menu.addAction("폴더 열기…", self.choose_folder)
        split = menu.addMenu("분할")
        for name in LAYOUTS:
            a = split.addAction(name, lambda n=name: self.set_layout(n))
            a.setCheckable(True)
            a.setChecked(name == self.layout_name)
        menu.addAction("모두 재생" if self.paused else "모두 일시정지", self.toggle_pause)
        menu.addAction("모두 소리 끄기", self.mute_all)
        menu.addAction("창 모드로" if is_fullscreen(self) else "전체화면", self.toggle_fullscreen)
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
