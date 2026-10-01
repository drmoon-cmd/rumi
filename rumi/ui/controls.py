"""재생 컨트롤 바: 탐색 바, 재생 버튼, 볼륨, 배속."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QSlider, QToolButton, QVBoxLayout, QWidget,
)

from .icons import make_icon
from .util import fmt_time


class SeekSlider(QSlider):
    """클릭한 위치로 바로 이동하는 슬라이더."""

    seek_requested = Signal(float)  # 0.0 ~ 1.0
    hovered = Signal(float, QPoint)  # 마우스를 올린 위치 비율, 미리보기를 띄울 전역 좌표
    hover_left = Signal()

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setRange(0, 10000)
        self.setMouseTracking(True)
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

    def mouseMoveEvent(self, e):
        x = e.position().x()
        ratio = min(max(x / max(self.width(), 1), 0.0), 1.0)
        self.hovered.emit(ratio, self.mapToGlobal(QPoint(int(x), 0)))
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self.hover_left.emit()
        super().leaveEvent(e)

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
    seek_hover = Signal(float, QPoint)
    seek_hover_end = Signal()
    volume_set = Signal(int)
    mute_toggle = Signal()
    fullscreen_toggle = Signal()
    playlist_toggle = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("controlBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self._duration = 0.0

        self.seek = SeekSlider()
        self.seek.setObjectName("seekSlider")
        self.seek.seek_requested.connect(self.seek_ratio)
        self.seek.hovered.connect(self.seek_hover)
        self.seek.hover_left.connect(self.seek_hover_end)
        self.time_label = QLabel("--:-- / --:--")
        self.speed_label = QLabel("")

        self._paused, self._muted = True, False
        self._icons: dict[QToolButton, str] = {}

        def button(icon, tip, signal):
            b = QToolButton()
            self._icons[b] = icon
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(signal)
            return b

        self.btn_play = button("play", "재생/일시정지 (Space)", self.play_pause)
        btn_stop = button("stop", "정지", self.stop)
        btn_prev = button("prev", "이전 파일 (PgUp)", self.prev)
        btn_next = button("next", "다음 파일 (PgDn)", self.next)
        self.btn_mute = button("volume", "음소거 (M)", self.mute_toggle)
        btn_list = button("list", "재생목록 (F9)", self.playlist_toggle)
        btn_full = button("fullscreen", "전체화면 (Enter)", self.fullscreen_toggle)

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
        self.refresh_icons()

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
        self._paused = paused
        self._icons[self.btn_play] = "play" if paused else "pause"
        self.refresh_icons()

    def set_volume(self, v: float):
        self.volume.blockSignals(True)
        self.volume.setValue(int(round(v)))
        self.volume.blockSignals(False)
        self.volume_label.setText(f"{int(round(v))}%")

    def set_muted(self, muted: bool):
        self._muted = muted
        self._icons[self.btn_mute] = "mute" if muted else "volume"
        self.refresh_icons()

    def refresh_icons(self) -> None:
        """테마 글자색으로 아이콘을 다시 그린다 (테마가 바뀔 때도 호출)."""
        color = QApplication.palette().buttonText().color().name()
        for b, kind in self._icons.items():
            b.setIcon(make_icon(kind, color))

    def set_speed(self, s: float):
        self.speed_label.setText("" if abs(s - 1.0) < 1e-6 else f"{s:.2f}x")
