"""과부하 감시: 화질 설정 때문에 PC 가 버거우면 알려 준다 (MainWindow 가 한 단계씩 낮춘다).

두 가지를 본다.
- 버린 프레임: 2초 동안 보여야 할 프레임 중 재생 엔진이 제때 못 그려 버린 비율
- 화면 멈춤: 0.1초마다 도는 타이머가 얼마나 늦게 도는지 (그래픽 카드가 꽉 차면 창 조작이 굳는다)
두 번 연달아 버거우면 overloaded 를 보낸다. 설정을 바꾼 직후에는 셰이더를 새로 만드느라
잠깐 느릴 수 있어 몇 초 동안은 재지 않는다.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QElapsedTimer, QObject, QTimer, Signal

from .. import picture

SAMPLE_MS = 2000
TICK_MS = 100
GRACE_MS = 6000       # 설정을 바꾸거나 파일을 연 직후 재지 않는 시간
BAD_IN_A_ROW = 2


class LoadGuard(QObject):
    overloaded = Signal(str)  # 이유 (안내용)

    def __init__(self, player, active: Callable[[], bool], parent=None):
        super().__init__(parent)
        self.player = player
        self._active = active  # 무거운 설정이 하나라도 켜져 있는지
        self._clock = QElapsedTimer()
        self._last_tick = 0
        self._max_lag = 0.0
        self._base: tuple[int, int] | None = None
        self._bad = 0
        self._grace_until = 0
        self._tick = QTimer(self, interval=TICK_MS)
        self._tick.timeout.connect(self._on_tick)
        self._sample = QTimer(self, interval=SAMPLE_MS)
        self._sample.timeout.connect(self._on_sample)

    def start(self) -> None:
        self._clock.start()
        self._last_tick = self._clock.elapsed()
        self._tick.start()
        self._sample.start()
        self.reset()

    def stop(self) -> None:
        self._tick.stop()
        self._sample.stop()

    def reset(self) -> None:
        """설정을 바꿨거나 새 파일: 처음부터 다시 잰다."""
        self._base = None
        self._bad = 0
        self._max_lag = 0.0
        if self._clock.isValid():
            self._grace_until = self._clock.elapsed() + GRACE_MS

    def _on_tick(self) -> None:
        now = self._clock.elapsed()
        self._max_lag = max(self._max_lag, now - self._last_tick - TICK_MS)
        self._last_tick = now

    def _counters(self) -> tuple[int, int] | None:
        p = self.player
        try:
            if p.pause or p.seeking or p.idle_active or p.paused_for_cache:
                return None
            dropped = int(p.frame_drop_count or 0) + int(p.decoder_frame_drop_count or 0)
            frames = int(p.estimated_frame_number or 0)
            return dropped, frames
        except Exception:
            return None

    def _on_sample(self) -> None:
        lag, self._max_lag = self._max_lag, 0.0
        counters = self._counters()
        if counters is None or not self._active():
            self._base, self._bad = None, 0
            return
        base, self._base = self._base, counters
        if base is None or self._clock.elapsed() < self._grace_until:
            return
        dropped, frames = counters[0] - base[0], counters[1] - base[1]
        if frames < 0:  # 탐색으로 프레임 번호가 되돌아감
            return
        if picture.is_overloaded(max(dropped, 0), frames + max(dropped, 0), lag):
            self._bad += 1
            if self._bad >= BAD_IN_A_ROW:
                reason = f"화면이 {lag:.0f}ms 멈춤" if lag > picture.LAG_LIMIT_MS else \
                    f"2초에 프레임 {dropped}개를 제때 못 그림"
                self.reset()
                self.overloaded.emit(reason)
        else:
            self._bad = 0
