"""재생 컨트롤 바: 탐색 바, 재생 버튼, 볼륨, 배속."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QSlider, QStyle, QToolButton, QVBoxLayout, QWidget,
)

from .util import fmt_time


class SeekSlider(QSlider):
    """클릭한 위치로 바로 이동하는 슬라이더."""

    seek_requested = Signal(float)  # 0.0 ~ 1.0

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setRange(0, 10000)
        self._dragging = False
        self.sliderPressed.connect(lambda: setattr(self, "_dragging", True))
        self.sliderReleased.connect(self._released)
        self.sliderMoved.connect(lambda v: self.seek_requested.emit(v / 10000))

    def _released(self):
        self._dragging = False
        self.seek_requested.emit(self.value() / 10000)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            ratio = min(max(e.position().x() / max(self.width(), 1), 0.0), 1.0)
            self.setValue(int(ratio * 10000))
            self.seek_requested.emit(ratio)
        super().mousePressEvent(e)

    def set_ratio(self, ratio: float):
        if not self._dragging:
            self.blockSignals(True)
            self.setValue(int(ratio * 10000))
            self.blockSignals(False)


class ControlBar(QWidget):
    play_pause = Signal()
    stop = Signal()
    prev = Signal()
    next = Signal()
    seek_ratio = Signal(float)
    volume_set = Signal(int)
    mute_toggle = Signal()
    fullscreen_toggle = Signal()
    playlist_toggle = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAutoFillBackground(True)
        self._duration = 0.0

        self.seek = SeekSlider()
        self.seek.seek_requested.connect(self.seek_ratio)
        self.time_label = QLabel("--:-- / --:--")
        self.speed_label = QLabel("")

        st = self.style()

        def button(icon, tip, signal):
            b = QToolButton()
            b.setIcon(st.standardIcon(icon))
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(signal)
            return b

        self.btn_play = button(QStyle.SP_MediaPlay, "재생/일시정지 (Space)", self.play_pause)
        btn_stop = button(QStyle.SP_MediaStop, "정지", self.stop)
        btn_prev = button(QStyle.SP_MediaSkipBackward, "이전 파일 (PgUp)", self.prev)
        btn_next = button(QStyle.SP_MediaSkipForward, "다음 파일 (PgDn)", self.next)
        self.btn_mute = button(QStyle.SP_MediaVolume, "음소거 (M)", self.mute_toggle)
        btn_list = button(QStyle.SP_FileDialogListView, "재생목록 (F9)", self.playlist_toggle)
        btn_full = button(QStyle.SP_TitleBarMaxButton, "전체화면 (Enter)", self.fullscreen_toggle)

        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 130)
        self.volume.setFixedWidth(100)
        self.volume.setFocusPolicy(Qt.NoFocus)
        self.volume.valueChanged.connect(self.volume_set)
        self.volume_label = QLabel("100%")
        self.volume_label.setMinimumWidth(36)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        for w in (self.btn_play, btn_stop, btn_prev, btn_next):
            row.addWidget(w)
        row.addSpacing(8)
        row.addWidget(self.time_label)
        row.addSpacing(8)
        row.addWidget(self.speed_label)
        row.addStretch(1)
        row.addWidget(self.btn_mute)
        row.addWidget(self.volume)
        row.addWidget(self.volume_label)
        row.addWidget(btn_list)
        row.addWidget(btn_full)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(2)
        lay.addWidget(self.seek)
        lay.addLayout(row)

        self.seek.setFocusPolicy(Qt.NoFocus)

    # ---- 상태 반영 ----
    def set_duration(self, d: float):
        self._duration = d
        self._time = getattr(self, "_time", 0.0)
        self._update_time()

    def set_time(self, t: float):
        self._time = t
        if self._duration > 0:
            self.seek.set_ratio(t / self._duration)
        self._update_time()

    def _update_time(self):
        self.time_label.setText(f"{fmt_time(getattr(self, '_time', None))} / {fmt_time(self._duration or None)}")

    def reset(self):
        self._duration, self._time = 0.0, 0.0
        self.seek.set_ratio(0)
        self.time_label.setText("--:-- / --:--")

    def set_paused(self, paused: bool):
        icon = QStyle.SP_MediaPlay if paused else QStyle.SP_MediaPause
        self.btn_play.setIcon(self.style().standardIcon(icon))

    def set_volume(self, v: float):
        self.volume.blockSignals(True)
        self.volume.setValue(int(round(v)))
        self.volume.blockSignals(False)
        self.volume_label.setText(f"{int(round(v))}%")

    def set_muted(self, muted: bool):
        icon = QStyle.SP_MediaVolumeMuted if muted else QStyle.SP_MediaVolume
        self.btn_mute.setIcon(self.style().standardIcon(icon))

    def set_speed(self, s: float):
        self.speed_label.setText("" if abs(s - 1.0) < 1e-6 else f"{s:.2f}x")
