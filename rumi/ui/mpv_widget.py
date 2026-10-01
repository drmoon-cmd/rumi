"""libmpv 렌더 API로 QOpenGLWidget 안에 영상을 그리는 위젯.

창 핸들(wid) 임베딩은 macOS에서 동작하지 않으므로 OpenGL 렌더 API를 사용한다.
mpv 이벤트는 mpv 스레드에서 들어오므로 모두 Qt 시그널로 넘겨 GUI 스레드에서 처리한다.
"""

from __future__ import annotations

import locale
import time
from collections import deque

from PySide6.QtCore import QByteArray, Qt, QTimer, Signal
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
    middle_clicked = Signal()
    wheel_scrolled = Signal(int, bool)   # 방향(+1/-1), Ctrl 눌림
    dragged = Signal(float, float)       # 왼쪽 버튼으로 끈 거리 (위젯 크기 대비 비율)

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
            # 파일 이름: Rumi_영상이름_00-01-23-456.png (Windows 에서 쓸 수 없는 ':' 대신 '-')
            screenshot_template="Rumi_%F_%wH-%wM-%wS-%wT",
            screenshot_format="png",
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
        # Windows 등에서 GL 컨텍스트가 다시 만들어지면 initializeGL 이 또 불린다.
        # 이전 렌더 컨텍스트는 쓸 수 없으므로 정리하고 새로 만든다.
        self._free_render_ctx()
        self._render_ctx = mpv.MpvRenderContext(
            self.player, "opengl",
            opengl_init_params={"get_proc_address": self._proc_fn},
        )
        self._render_ctx.update_cb = self._frame_ready.emit
        ctx = self.context()
        if ctx is not None:
            ctx.aboutToBeDestroyed.connect(self._on_context_destroyed)
        self.renderer_ready.emit()
        # 렌더 컨텍스트가 완전히 준비된 다음(이벤트 루프 다음 차례)에 미뤄 둔 파일을 연다.
        # 렌더 컨텍스트보다 먼저 열면 영상 출력 초기화가 실패해 검은 화면만 나온다.
        if self._pending_load is not None:
            QTimer.singleShot(0, self._flush_pending_load)

    def _flush_pending_load(self) -> None:
        if self._pending_load is not None and self._render_ctx is not None:
            path, options = self._pending_load
            self._pending_load = None
            self.player.loadfile(path, **options)

    def _free_render_ctx(self) -> None:
        if self._render_ctx is not None:
            try:
                self._render_ctx.free()
            except Exception:
                pass
            self._render_ctx = None

    def _on_context_destroyed(self) -> None:
        # Qt 문서: GL 자원은 컨텍스트가 사라지기 전에 컨텍스트를 활성화한 상태에서 해제해야 한다
        self.makeCurrent()
        self._free_render_ctx()
        self.doneCurrent()

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
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press_pos = e.position()
            self._last_drag_pos = e.position()
            self._dragging = False
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        self.mouse_moved.emit()
        press = getattr(self, "_press_pos", None)
        if press is not None and e.buttons() & Qt.LeftButton:
            if not self._dragging and (e.position() - press).manhattanLength() > 6:
                self._dragging = True
            if self._dragging:
                d = e.position() - self._last_drag_pos
                self._last_drag_pos = e.position()
                self.dragged.emit(d.x() / max(self.width(), 1), d.y() / max(self.height(), 1))
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            dragged = getattr(self, "_dragging", False)
            self._press_pos, self._dragging = None, False
            if not dragged:  # 끌어서 화면을 옮긴 경우는 클릭으로 치지 않음
                self.clicked.emit()
        elif e.button() == Qt.MiddleButton:
            self.middle_clicked.emit()
        elif e.button() == Qt.RightButton:
            self.context_menu_requested.emit(e.globalPosition().toPoint())
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.double_clicked.emit()

    def wheelEvent(self, e):
        if e.angleDelta().y():
            self.wheel_scrolled.emit(1 if e.angleDelta().y() > 0 else -1,
                                     bool(e.modifiers() & Qt.ControlModifier))
