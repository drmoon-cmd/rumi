"""PIP(화면 속 화면): 메인 영상 위에 띄우는 작은 영상 창."""

from __future__ import annotations

import os

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QSizeGrip, QStyle, QToolButton, QVBoxLayout, QWidget,
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


class PipView(QFrame):
    closed = Signal(object)
    sync_toggled = Signal(object, bool)

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
        self.video.double_clicked.connect(lambda: self.player.cycle("pause") if not self.sync.isChecked() else None)
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

    def load(self, path: str) -> None:
        self.path = path
        self.label.setText(os.path.basename(path))
        self.label.setToolTip(path)
        self.video.load(path)

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
