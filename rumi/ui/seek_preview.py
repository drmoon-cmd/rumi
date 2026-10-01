"""탐색 바 미리보기: 마우스를 올린 시점의 장면을 작은 창으로 보여 준다.

ffmpeg 으로 한 장면을 뽑아 오며, 마지막 요청만 처리하고 결과는 잠시 기억해 둔다.
"""

from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QPoint, QProcess, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ..media_tools import build_thumbnail_args
from ..tools import find_ffmpeg
from .util import fmt_time

CACHE_SIZE = 120
DEBOUNCE_MS = 120
THUMB_WIDTH = 240


class SeekPreview(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setObjectName("seekPreview")
        self.setStyleSheet("#seekPreview { background: #111; border: 1px solid #3d8fe0; border-radius: 6px; }"
                           "QLabel { color: white; }")
        self.image = QLabel()
        self.image.setFixedSize(THUMB_WIDTH, THUMB_WIDTH * 9 // 16)
        self.image.setAlignment(Qt.AlignCenter)
        self.time = QLabel()
        self.time.setAlignment(Qt.AlignCenter)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)
        lay.addWidget(self.image)
        lay.addWidget(self.time)

        self._ffmpeg = find_ffmpeg()
        self._cache: OrderedDict[tuple[str, int], QPixmap] = OrderedDict()
        self._proc: QProcess | None = None
        self._busy_key: tuple[str, int] | None = None
        self._want: tuple[str, int] | None = None
        self._timer = QTimer(self, singleShot=True, interval=DEBOUNCE_MS)
        self._timer.timeout.connect(self._start_next)

    @property
    def available(self) -> bool:
        return self._ffmpeg is not None

    def show_at(self, path: str | None, seconds: float, global_pos: QPoint) -> None:
        self.time.setText(fmt_time(seconds))
        self.adjustSize()
        self.move(global_pos - QPoint(self.width() // 2, self.height() + 14))
        if not path or "://" in path or not self.available:
            self.image.hide()
        else:
            self.image.show()
            key = (path, int(seconds))
            if key in self._cache:
                self.image.setPixmap(self._cache[key])
            else:
                self._want = key
                self._timer.start()
        self.show()

    def hide_preview(self) -> None:
        self._want = None
        self._timer.stop()
        self.hide()

    def clear_cache(self) -> None:
        self._cache.clear()
        self.image.clear()

    def _start_next(self) -> None:
        if self._want is None or self._proc is not None:
            return  # 진행 중인 작업이 끝나면 다시 시도
        key = self._want
        self._want = None
        args = build_thumbnail_args(self._ffmpeg, key[0], float(key[1]), THUMB_WIDTH)
        self._busy_key = key
        self._proc = QProcess(self)
        self._proc.finished.connect(self._done)
        self._proc.start(args[0], args[1:])

    def _done(self, code: int, _status) -> None:
        proc, key = self._proc, self._busy_key
        self._proc, self._busy_key = None, None
        data = bytes(proc.readAllStandardOutput())
        proc.deleteLater()
        if code == 0 and data:
            pm = QPixmap()
            if pm.loadFromData(data, "PNG"):
                self._cache[key] = pm
                while len(self._cache) > CACHE_SIZE:
                    self._cache.popitem(last=False)
                if self.isVisible() and self._want is None:
                    self.image.setPixmap(pm)
        if self._want is not None:
            self._start_next()
