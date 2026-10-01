"""libmpv 렌더 API로 QOpenGLWidget 안에 영상을 그리는 위젯.

창 핸들(wid) 임베딩은 macOS에서 동작하지 않으므로 OpenGL 렌더 API를 사용한다.
mpv 이벤트는 mpv 스레드에서 들어오므로 모두 Qt 시그널로 넘겨 GUI 스레드에서 처리한다.
"""

from __future__ import annotations

import locale
import time
from collections import deque

from PySide6.QtCore import QByteArray, Qt, Signal
from PySide6.QtGui import QOpenGLFunctions
from PySide6.QtGui import QOpenGLContext
from PySide6.QtOpenGLWidgets import QOpenGLWidget

import mpv

# GPU 가속이 없는 환경(가상 머신, 원격 데스크톱, 드라이버 미설치)의 소프트웨어 렌더러.
# 기본 고품질 스케일러 셰이더가 너무 무거워 영상이 검게 나오므로 가벼운 설정으로 바꾼다.
SOFTWARE_RENDERERS = ("llvmpipe", "softpipe", "swrast", "gdi generic", "microsoft basic render")
GL_RENDERER = 0x1F01

# 하드웨어(GPU) 디코딩. 일부 그래픽 드라이버에서 탐색/이어보기 뒤 영상에 줄 노이즈가 생기므로
# mpv 와 같이 기본은 끄고(소프트웨어 디코딩), 필요한 사람만 켜도록 한다.
HWDEC_MODES = {
    "no": "끄기 (권장, 가장 안정적)",
    "auto-copy-safe": "켜기 - 복사 방식",
    "auto-safe": "켜기 - 빠름 (일부 PC에서 화면 깨짐)",
}
DEFAULT_HWDEC = "no"

# 'fast' 프로필이 바꾸는 렌더링 옵션. 호환 모드를 끌 때 원래 값으로 되돌리기 위해 기억해 둔다.
COMPAT_OPTIONS = ("scale", "dscale", "cscale", "dither", "correct-downscaling",
                  "linear-downscaling", "sigmoid-upscaling", "hdr-compute-peak",
                  "allow-delayed-peak-detect")


def _get_proc_address(_ctx, name: bytes) -> int:
    glctx = QOpenGLContext.currentContext()
    if glctx is None:
        return 0
    addr = glctx.getProcAddress(QByteArray(name))
    return int(addr) if addr else 0


class MpvWidget(QOpenGLWidget):
    # mpv 스레드 → GUI 스레드
    _frame_ready = Signal()
    time_changed = Signal(float)
    duration_changed = Signal(float)
    pause_changed = Signal(bool)
    volume_changed = Signal(float)
    mute_changed = Signal(bool)
    speed_changed = Signal(float)
    eof_reached = Signal()
    file_loaded = Signal()
    load_failed = Signal(str)
    tracks_changed = Signal(list)
    sub_text_changed = Signal(str)
    renderer_ready = Signal()

    mouse_moved = Signal()
    clicked = Signal()
    context_menu_requested = Signal(object)  # 전역 좌표 QPoint
    double_clicked = Signal()
    wheel_scrolled = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        # libmpv 는 C 로케일 숫자 형식을 요구한다
        locale.setlocale(locale.LC_NUMERIC, "C")
        self.log_lines: deque[str] = deque(maxlen=30)  # MPV 생성 중에도 로그가 올 수 있음
        self.player = mpv.MPV(
            vo="libmpv",
            hwdec=DEFAULT_HWDEC,
            keep_open="yes",
            idle="yes",
            osc="no",
            input_default_bindings="no",
            input_vo_keyboard="no",
            sub_auto="fuzzy",
            sub_codepage="auto",
            screenshot_directory="~~desktop/",
            osd_font_size=36,
            ytdl="no",
            log_handler=self._on_log,
            loglevel="warn",
        )
        self._render_ctx: mpv.MpvRenderContext | None = None
        self.software_rendering = False
        self._pending_load: tuple[str, dict] | None = None
        self.gl_renderer = ""
        self.compat_mode = False
        self._quality_defaults = {}
        for name in COMPAT_OPTIONS:
            try:
                self._quality_defaults[name] = self.player[name]
            except (AttributeError, KeyError, TypeError, SystemError):
                pass
        self._proc_fn = mpv.MpvGlGetProcAddressFn(_get_proc_address)
        self._frame_ready.connect(self.update, Qt.QueuedConnection)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(320, 180)
        self._observing = False

    def _on_log(self, level: str, component: str, message: str) -> None:
        # mpv 스레드에서 호출됨. 진단용으로 최근 경고/오류만 보관
        self.log_lines.append(f"{time.strftime('%H:%M:%S')} [{level}] {component}: {message.strip()}")

    def diagnostics(self) -> list[str]:
        """문제 신고용 상태 요약."""
        p = self.player

        def prop(name):
            try:
                return getattr(p, name.replace("-", "_"))
            except Exception:
                return None

        vp = prop("video-params") or {}
        return [
            f"파일: {prop('path')}",
            f"재생 위치: {prop('time-pos')} / {prop('duration')}  일시정지={prop('pause')} 탐색중={prop('seeking')}",
            f"영상 출력 준비: 렌더={'예' if self._render_ctx else '아니오'} vo-configured={prop('vo-configured')}",
            f"영상: {prop('video-codec')} {vp.get('w')}x{vp.get('h')} {vp.get('pixelformat')}"
            f"  하드웨어={prop('hwdec-current') or 'no'}",
            "최근 mpv 메시지:",
            *(f"  {line}" for line in list(self.log_lines)[-12:]),
        ]

    def start_observing(self) -> None:
        """시그널을 모두 연결한 뒤 호출. 등록 즉시 현재 값이 한 번씩 전달된다."""
        if self._observing:
            return
        self._observing = True
        p = self.player
        bind = {
            "time-pos": lambda v: v is not None and self.time_changed.emit(float(v)),
            "duration": lambda v: v is not None and self.duration_changed.emit(float(v)),
            "pause": lambda v: self.pause_changed.emit(bool(v)),
            "volume": lambda v: v is not None and self.volume_changed.emit(float(v)),
            "mute": lambda v: self.mute_changed.emit(bool(v)),
            "speed": lambda v: v is not None and self.speed_changed.emit(float(v)),
            "eof-reached": lambda v: v and self.eof_reached.emit(),
            "track-list": lambda v: self.tracks_changed.emit(list(v or [])),
            "sub-text": lambda v: self.sub_text_changed.emit(str(v or "")),
        }
        for prop, fn in bind.items():
            p.observe_property(prop, lambda _name, value, fn=fn: fn(value))

        @p.event_callback("file-loaded")
        def _loaded(_event):
            self.file_loaded.emit()

        @p.event_callback("end-file")
        def _ended(event):
            data = event.data
            if data is not None and data.reason == mpv.MpvEventEndFile.ERROR:
                self.load_failed.emit(f"mpv 오류 코드 {data.error}")

    # ---- OpenGL ----
    def initializeGL(self) -> None:
        self.gl_renderer = QOpenGLFunctions(QOpenGLContext.currentContext()).glGetString(GL_RENDERER) or ""
        self.software_rendering = any(name in self.gl_renderer.lower() for name in SOFTWARE_RENDERERS)
        if self.software_rendering:
            self.set_compat_mode(True)
        self.renderer_ready.emit()
        if self._pending_load is not None:
            path, options = self._pending_load
            self._pending_load = None
            self.player.loadfile(path, **options)
        self._render_ctx = mpv.MpvRenderContext(
            self.player, "opengl",
            opengl_init_params={"get_proc_address": self._proc_fn},
        )
        self._render_ctx.update_cb = self._frame_ready.emit

    def paintGL(self) -> None:
        if self._render_ctx is None:
            return
        ratio = self.devicePixelRatioF()
        self._render_ctx.render(
            flip_y=True,
            opengl_fbo={
                "w": int(self.width() * ratio),
                "h": int(self.height() * ratio),
                "fbo": self.defaultFramebufferObject(),
            },
        )

    def load(self, path: str, **options) -> None:
        """파일 열기. 렌더 컨텍스트가 만들어지기 전에 열면 영상 출력 초기화가 실패하므로,
        위젯이 처음 그려질 때까지 미뤘다가 연다."""
        if self._render_ctx is None:
            self._pending_load = (path, options)
        else:
            self.player.loadfile(path, **options)

    def set_compat_mode(self, on: bool) -> None:
        """호환 모드: 가벼운 스케일러로 그래픽 부담을 줄인다 (화면이 깨지거나 검게 나올 때)."""
        on = on or self.software_rendering
        self.compat_mode = on
        if on:
            self.player.command("apply-profile", "fast")
        else:
            for name, value in self._quality_defaults.items():
                try:
                    self.player[name] = value
                except (AttributeError, KeyError, TypeError, SystemError):
                    pass

    def shutdown(self) -> None:
        """창을 닫기 전에 호출. 렌더 컨텍스트는 GL 컨텍스트가 활성일 때 해제해야 한다."""
        if self._render_ctx is not None:
            self.makeCurrent()
            self._render_ctx.free()
            self._render_ctx = None
            self.doneCurrent()
        self.player.terminate()

    # ---- 마우스 ----
    def mouseMoveEvent(self, e):
        self.mouse_moved.emit()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        elif e.button() == Qt.RightButton:
            self.context_menu_requested.emit(e.globalPosition().toPoint())
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.double_clicked.emit()

    def wheelEvent(self, e):
        self.wheel_scrolled.emit(1 if e.angleDelta().y() > 0 else -1)
