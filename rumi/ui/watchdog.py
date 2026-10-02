"""화면 멈춤 기록: 프로그램이 15초 넘게 응답하지 않으면 그 순간 무엇을 하고 있었는지 파일에 남긴다.

다른 PC 에서만 생기는 멈춤 문제를 고치기 위한 것으로, 진단 정보 복사에 마지막 기록이 같이 담긴다.
"""

from __future__ import annotations

import faulthandler
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

HANG_SECONDS = 15
MAX_LOG_BYTES = 200_000


class HangWatchdog(QObject):
    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = path
        self._file = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
                path.unlink()
            self._file = open(path, "a", encoding="utf-8")
            self._file.write(f"\n=== 시작 {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            self._file.flush()
        except OSError:
            return
        self._timer = QTimer(self, interval=2000)
        self._timer.timeout.connect(self._arm)
        self._timer.start()
        self._arm()

    def _arm(self) -> None:
        # 다시 부를 때마다 이전 예약이 취소된다. 화면이 멈춰 이 타이머가 못 돌면 기록이 남는다.
        faulthandler.dump_traceback_later(HANG_SECONDS, repeat=False, file=self._file)

    def stop(self) -> None:
        if self._file is not None:
            faulthandler.cancel_dump_traceback_later()
            self._timer.stop()

    def tail(self, lines: int = 40) -> str:
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        last = text.rsplit("=== 시작", 2)
        # 이번 실행과 바로 전 실행의 기록만 (멈춰서 강제로 끈 경우 전 실행에 남아 있다)
        recent = "=== 시작".join(last[-2:]) if len(last) > 2 else text
        return "\n".join(recent.strip().splitlines()[-lines:])
