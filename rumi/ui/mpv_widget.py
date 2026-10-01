"""libmpv 렌더 API로 QOpenGLWidget 안에 영상을 그리는 위젯.

창 핸들(wid) 임베딩은 macOS에서 동작하지 않으므로 OpenGL 렌더 API를 사용한다.
mpv 이벤트는 mpv 스레드에서 들어오므로 모두 Qt 시그널로 넘겨 GUI 스레드에서 처리한다.
"""

from __future__ import annotations

import locale

from PySide6.QtCore import QByteArray, Qt, Signal
from PySide6.QtGui import QOpenGLFunctions
from PySide6.QtGui import QOpenGLContext
from PySide6.QtOpenGLWidgets import QOpenGLWidget

import mpv

# GPU 가속이 없는 환경(가상 머신, 원격 데스크톱, 드라이버 미설치)의 소프트웨어 렌더러.
# 기본 고품질 스케일러 셰이더가 너무 무거워 영상이 검게 나오므로 가벼운 설정으로 바꾼다.
SOFTWARE_RENDERERS = ("llvmpipe", "softpipe", "swrast", "gdi generic", "microsoft basic render")
GL_RENDERER = 0x1F01


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

    mouse_moved = Signal()
    clicked = Signal()
    double_clicked = Signal()
    wheel_scrolled = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        # libmpv 는 C 로케일 숫자 형식을 요구한다
        locale.setlocale(locale.LC_NUMERIC, "C")
        self.player = mpv.MPV(
            vo="libmpv",
            hwdec="auto-safe",
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
        )
        self._render_ctx: mpv.MpvRenderContext | None = None
        self.software_rendering = False
        self._proc_fn = mpv.MpvGlGetProcAddressFn(_get_proc_address)
        self._frame_ready.connect(self.update, Qt.QueuedConnection)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(320, 180)
        self._observing = False

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
        renderer = (QOpenGLFunctions(QOpenGLContext.currentContext()).glGetString(GL_RENDERER) or "").lower()
        self.software_rendering = any(name in renderer for name in SOFTWARE_RENDERERS)
        if self.software_rendering:
            self.player.command("apply-profile", "fast")
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
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.double_clicked.emit()

    def wheelEvent(self, e):
        self.wheel_scrolled.emit(1 if e.angleDelta().y() > 0 else -1)
