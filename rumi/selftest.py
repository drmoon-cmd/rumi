"""설치 파일 점검용 자가 진단: `Rumi --selftest 결과파일`.

짧은 360° 시험 영상을 만들어 재생하고, VR 보기와 화질 설정을 한 번씩 거친 뒤 스스로 창을 닫는다.
빌드 과정에서 실제 설치판(exe)으로 돌려, 소스에서는 안 보이고 설치판에서만 나는 문제
(빠진 부품, 창 모드에서만 나는 오류, 종료가 안 되는 문제)를 걸러 낸다.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import traceback

from PySide6.QtCore import QTimer

TIMEOUT_MS = 60_000


class SelfTest:
    def __init__(self, win, out_path: str):
        self.win = win
        self.out_path = out_path
        self.lines: list[str] = []
        self.failures: list[str] = []
        self._tmp = tempfile.TemporaryDirectory(prefix="rumi_selftest_")
        self._old_hook = sys.excepthook
        sys.excepthook = self._on_exception
        self._deadline = QTimer(win, singleShot=True, interval=TIMEOUT_MS)
        self._deadline.timeout.connect(self._on_timeout)
        self._deadline.start()
        QTimer.singleShot(500, self._start)

    # ---- 기록 ----
    def note(self, text: str) -> None:
        self.lines.append(text)
        self._write()

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.lines.append(f"{'OK  ' if ok else 'FAIL'} {name}" + (f" ({detail})" if detail else ""))
        if not ok:
            self.failures.append(name)
        self._write()

    def _write(self, done: bool = False) -> None:
        try:
            with open(self.out_path, "w", encoding="utf-8") as f:
                f.write("\n".join(self.lines) + "\n")
                if done:
                    f.write("RESULT: " + ("PASS" if not self.failures else "FAIL " + ", ".join(self.failures)) + "\n")
        except OSError:
            pass

    def _on_exception(self, etype, value, tb) -> None:
        self.failures.append("exception")
        self.lines.append("EXCEPTION " + "".join(traceback.format_exception(etype, value, tb)).strip())
        self._write()

    def _on_timeout(self) -> None:
        self.failures.append("timeout")
        self.lines.append(f"FAIL {TIMEOUT_MS // 1000}초 안에 끝나지 않음")
        self._write(done=True)
        os._exit(4)

    def exit_code(self) -> int:
        self._write(done=True)
        return 0 if not self.failures else 3

    # ---- 단계 ----
    def _step(self, ms: int, fn) -> None:
        QTimer.singleShot(ms, fn)

    def _start(self) -> None:
        from . import __version__
        from .tools import find_ffmpeg
        w = self.win
        self.note(f"Rumi {__version__}  frozen={getattr(sys, 'frozen', False)}  stdout={'있음' if sys.stdout else '없음'}")
        self.gl = w.video.gl_renderer
        self.note(f"OpenGL: {self.gl or '(사용 불가)'}  소프트웨어={w.video.software_rendering}")
        ffmpeg = find_ffmpeg()
        self.check("ffmpeg 찾기", bool(ffmpeg), str(ffmpeg))
        self.video = os.path.join(self._tmp.name, "selftest_360.mp4")
        if ffmpeg:
            r = subprocess.run(
                [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x320:rate=25:duration=20",
                 "-c:v", "mpeg4", "-q:v", "5", self.video],
                capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.check("시험 영상 만들기", r.returncode == 0 and os.path.exists(self.video),
                       r.stderr.decode("utf-8", "replace")[-200:])
        if not ffmpeg or not os.path.exists(self.video):
            return self._finish()
        w.play(self.video)
        self._step(3500, self._check_vr)

    def _lightness(self) -> float:
        img = self.win.video.grabFramebuffer()
        vals = [img.pixelColor(x, y).lightness()
                for y in range(0, img.height(), max(img.height() // 12, 1))
                for x in range(0, img.width(), max(img.width() // 16, 1))]
        return sum(vals) / max(len(vals), 1)

    def _check_vr(self) -> None:
        w = self.win
        pos = w.player.time_pos or 0
        self.check("재생", pos > 0.5, f"위치 {pos:.1f}초")
        self.check("VR 영상 자동 인식", w.vr.fmt == "360", str(w.vr.fmt))
        if self.gl:
            renderer = w.video._vr_renderer
            self.check("VR 펼치기 (그래픽 카드 셰이더)", renderer is not None and not renderer.error,
                       getattr(renderer, "error", None) or "")
            light = self._lightness()
            self.check("VR 화면이 그려짐", light > 20, f"평균 밝기 {light:.0f}")
        else:
            self.note("SKIP VR 화면 점검 (이 PC 에서 OpenGL 을 쓸 수 없음)")
        w._on_drag(-0.1, 0.02)
        w.vr.toggle()
        w.set_picture_quality("sharp", announce=False)
        w.set_hdr_mode("dynamic", announce=False)
        w.a_smooth.setChecked(True)
        w.toggle_smooth(announce=False)
        self._pos = pos
        self._step(2500, self._check_picture)

    def _check_picture(self) -> None:
        w = self.win
        pos = w.player.time_pos or 0
        self.check("화질 설정을 바꾼 뒤에도 재생", pos > self._pos + 0.5, f"위치 {pos:.1f}초")
        if self.gl:
            light = self._lightness()
            self.check("일반 화면이 그려짐", light > 20, f"평균 밝기 {light:.0f}")
        errors = [line for line in w.video.log_lines if "[error]" in line or "[fatal]" in line]
        self.check("재생 엔진 오류 없음", not errors or w.video.software_rendering, " | ".join(errors[-2:]))
        w.reset_picture(announce=False)
        self._finish()

    def _finish(self) -> None:
        self._deadline.stop()
        sys.excepthook = self._old_hook
        self.note("창 닫기")
        self.win.close()
