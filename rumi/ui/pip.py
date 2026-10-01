"""PIP(화면 속 화면): 메인 영상 위에 띄우는 작은 영상 창."""

from __future__ import annotations

import os

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QSizeGrip, QStyle, QToolButton,
    QVBoxLayout, QWidget,
)

from .mpv_widget import MpvWidget

MAX_PIPS = 4


class _TitleBar(QWidget):
    """끌어서 PIP 창을 옮기는 제목 줄."""

    def __init__(self, pip: "PipView"):
        super().__init__(pip)
        self._pip = pip
        self._drag: QPoint | None = None
        self.setCursor(Qt.SizeAllCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self._pip.pos()
            self._pip.raise_()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self._pip.move_within_parent(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def contextMenuEvent(self, e):
        self._pip.show_menu(e.globalPos())


class PipView(QFrame):
    closed = Signal(object)
    sync_toggled = Signal(object, bool)
    swap_requested = Signal(object)          # 메인 화면과 바꾸기
    main_pause_requested = Signal()          # 동기화 중 재생/일시정지 → 메인에 맡김

    SIZES = {"작게": 0.22, "보통": 0.32, "크게": 0.45}
    CORNERS = ("오른쪽 위", "왼쪽 위", "오른쪽 아래", "왼쪽 아래")

    def __init__(self, parent: QWidget, path: str, sync: bool, auto_partner: bool = False):
        super().__init__(parent)
        self.path = path
        self.auto_partner = auto_partner  # 뒤 카메라 자동 PIP 인지
        self.time = 0.0
        self.setObjectName("pip")
        self.setStyleSheet("#pip { background: black; border: 2px solid #4a90d9; }"
                           "#pipTitle { background: rgba(20,20,20,200); }"
                           "#pipTitle QLabel, #pipTitle QCheckBox { color: white; }")

        self.video = MpvWidget(self)
        self.player = self.video.player
        self.player.mute = True
        self.video.time_changed.connect(lambda t: setattr(self, "time", t))
        self.video.double_clicked.connect(self.toggle_pause)
        self.video.context_menu_requested.connect(self.show_menu)
        self.video.start_observing()

        title = _TitleBar(self)
        title.setObjectName("pipTitle")
        title.setAttribute(Qt.WA_StyledBackground, True)
        self.label = QLabel()
        self.sync = QCheckBox("동기화")
        self.sync.setToolTip("메인 영상과 같은 시각으로 재생·정지·탐색")
        self.sync.setChecked(sync)
        self.sync.setFocusPolicy(Qt.NoFocus)
        self.sync.toggled.connect(lambda on: self.sync_toggled.emit(self, on))
        st = self.style()
        self.btn_sound = QToolButton()
        self.btn_sound.setAutoRaise(True)
        self.btn_sound.setToolTip("PIP 소리 켜기/끄기")
        self.btn_sound.setFocusPolicy(Qt.NoFocus)
        self.btn_sound.clicked.connect(self.toggle_sound)
        close = QToolButton()
        close.setAutoRaise(True)
        close.setIcon(st.standardIcon(QStyle.SP_TitleBarCloseButton))
        close.setToolTip("PIP 닫기")
        close.setFocusPolicy(Qt.NoFocus)
        close.clicked.connect(self.close_pip)
        tl = QHBoxLayout(title)
        tl.setContentsMargins(6, 1, 2, 1)
        tl.addWidget(self.label, 1)
        tl.addWidget(self.sync)
        tl.addWidget(self.btn_sound)
        tl.addWidget(close)

        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 0, 0)
        grip_row.addStretch(1)
        grip_row.addWidget(QSizeGrip(self), 0, Qt.AlignBottom | Qt.AlignRight)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        lay.addWidget(title)
        lay.addWidget(self.video, 1)
        lay.addLayout(grip_row)
        self.video.setMinimumSize(160, 90)

        self._update_sound_icon()
        self.load(path)

    def load(self, path: str, **options) -> None:
        self.path = path
        self.label.setText(os.path.basename(path))
        self.label.setToolTip(path)
        self.video.load(path, **options)

    def toggle_pause(self) -> None:
        if self.sync.isChecked():
            self.main_pause_requested.emit()
        else:
            self.player.pause = not self.player.pause

    def show_menu(self, global_pos: QPoint | None = None) -> None:
        menu = QMenu(self)
        paused = bool(self.player.pause)
        menu.addAction("재생" if paused else "일시정지", self.toggle_pause)
        sync = menu.addAction("메인 영상과 동기화", lambda: self.sync.setChecked(not self.sync.isChecked()))
        sync.setCheckable(True)
        sync.setChecked(self.sync.isChecked())
        sound = menu.addAction("소리 켜기", self.toggle_sound)
        sound.setCheckable(True)
        sound.setChecked(not self.player.mute)
        menu.addSeparator()
        menu.addAction("메인 화면과 바꾸기", lambda: self.swap_requested.emit(self))
        menu.addAction("다른 파일 열기…", self._open_other)
        size = menu.addMenu("크기")
        for label, ratio in self.SIZES.items():
            size.addAction(label, lambda r=ratio: self.set_size_ratio(r))
        corner = menu.addMenu("위치")
        for label in self.CORNERS:
            corner.addAction(label, lambda c=label: self.move_to_corner(c))
        menu.addSeparator()
        menu.addAction("PIP 닫기", self.close_pip)
        menu.exec(global_pos or QCursor.pos())

    def _open_other(self) -> None:
        start = os.path.dirname(self.path) if self.path else ""
        f, _ = QFileDialog.getOpenFileName(self, "PIP로 열 동영상", start)
        if f:
            self.auto_partner = False  # 직접 고른 파일은 자동 짝 찾기에서 빼낸다
            self.load(f)

    def set_size_ratio(self, ratio: float) -> None:
        p = self.parentWidget()
        w = max(int(p.width() * ratio), 200)
        self.resize(w, int(w * 9 / 16) + 30)
        self.move_within_parent(self.pos())

    def move_to_corner(self, corner: str) -> None:
        p, m = self.parentWidget(), 12
        x = m if "왼쪽" in corner else p.width() - self.width() - m
        y = m if "위" in corner else p.height() - self.height() - m - 60  # 아래는 컨트롤 바 피하기
        self.move_within_parent(QPoint(x, y))

    def toggle_sound(self) -> None:
        self.player.mute = not self.player.mute
        self._update_sound_icon()

    def _update_sound_icon(self) -> None:
        icon = QStyle.SP_MediaVolumeMuted if self.player.mute else QStyle.SP_MediaVolume
        self.btn_sound.setIcon(self.style().standardIcon(icon))

    def move_within_parent(self, pos: QPoint) -> None:
        p = self.parentWidget()
        x = min(max(pos.x(), 0), max(p.width() - self.width(), 0))
        y = min(max(pos.y(), 0), max(p.height() - self.height(), 0))
        self.move(x, y)

    def close_pip(self) -> None:
        self.video.shutdown()
        self.closed.emit(self)
        self.deleteLater()
