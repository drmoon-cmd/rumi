"""스트림 저장(녹화): ffmpeg 로 스트림을 다시 인코딩하지 않고 그대로 파일에 옮겨 담는다.

- 끝이 있는 영상(예고편 등)은 처음부터 끝까지 저장되면 저절로 끝난다.
- 생방송은 '중지'할 때까지 녹화한다.
- 암호화(DRM)된 스트림은 저장되지 않는다 (ffmpeg 가 읽지 못해 오류로 끝난다).
"""

from __future__ import annotations

import os

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

MIN_BYTES = 10_000


def record_args(url: str, out_path: str) -> list[str]:
    args = ["-hide_banner", "-loglevel", "error", "-y"]
    if url.lower().startswith("rtsp://"):
        args += ["-rtsp_transport", "tcp"]  # 방화벽·공유기 환경에서 UDP 보다 잘 된다
    args += ["-i", url,
             "-map", "0:v?", "-map", "0:a?",  # 영상·소리만 (데이터 트랙은 mkv 에 못 담는 경우가 있음)
             "-c", "copy", out_path]
    return args


class StreamRecorder(QObject):
    progress = Signal(int)              # 지금까지 저장한 바이트
    finished = Signal(str, bool, str)   # 경로, 성공 여부, 안내 문구

    def __init__(self, ffmpeg: str, parent=None):
        super().__init__(parent)
        self.ffmpeg = ffmpeg
        self.path: str | None = None
        self._stopping = False
        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.MergedChannels)
        self._proc.finished.connect(self._on_finished)
        self._proc.errorOccurred.connect(self._on_error)
        self._timer = QTimer(self, interval=1000)
        self._timer.timeout.connect(self._poll)

    def is_running(self) -> bool:
        return self._proc.state() != QProcess.NotRunning

    def start(self, url: str, out_path: str) -> None:
        self.path = out_path
        self._stopping = False
        self._proc.start(self.ffmpeg, record_args(url, out_path))
        self._timer.start()

    def stop(self) -> None:
        """ffmpeg 에 'q' 를 보내 파일을 제대로 마무리하게 한다. 응답이 없으면 강제로 끈다."""
        if not self.is_running():
            return
        self._stopping = True
        self._proc.write(b"q")
        QTimer.singleShot(5000, self._kill_if_running)

    def _kill_if_running(self) -> None:
        if self.is_running():
            self._proc.kill()

    def _size(self) -> int:
        try:
            return os.path.getsize(self.path) if self.path else 0
        except OSError:
            return 0

    def _poll(self) -> None:
        self.progress.emit(self._size())

    def _on_error(self, err) -> None:
        if err == QProcess.FailedToStart:
            self._timer.stop()
            self.finished.emit(self.path or "", False, "변환 도구(ffmpeg)를 실행하지 못했습니다.")

    def _on_finished(self, code: int, _status) -> None:
        self._timer.stop()
        size = self._size()
        log = bytes(self._proc.readAll()).decode("utf-8", "replace").strip()
        if size >= MIN_BYTES:
            done = "녹화를 멈췄습니다." if self._stopping else "영상 끝까지 저장했습니다."
            self.finished.emit(self.path, True, done)
            return
        if self.path and os.path.exists(self.path):
            os.remove(self.path)
        reason = "저장된 내용이 없습니다."
        if code != 0 and not self._stopping:
            reason = ("스트림을 읽지 못했습니다. 주소가 만료되었거나, 로그인이 필요하거나, "
                      "암호화(DRM)로 보호된 영상일 수 있습니다.")
        if log:
            reason += "\n\n" + "\n".join(log.splitlines()[-4:])
        self.finished.emit(self.path or "", False, reason)

    def shutdown(self) -> None:
        if self.is_running():
            self._stopping = True
            self._proc.write(b"q")
            if not self._proc.waitForFinished(3000):
                self._proc.kill()
                self._proc.waitForFinished(1000)
