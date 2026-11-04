"""VR(360°/180°) 보기 상태: 형식, 보는 방향, 시야. 마우스로 끌면 둘러보고 Ctrl+휠로 시야를 바꾼다."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from .. import vr

HWDEC_COPY = "auto-copy-safe"  # 필터는 CPU 의 영상 프레임이 필요하다 (GPU 직접 방식은 필터를 쓸 수 없음)


class VrView(QObject):
    changed = Signal(object)  # 형식 이름 또는 None(끔)

    def __init__(self, player, video_widget, hwdec: Callable[[], str], osd: Callable[[str], None], parent=None):
        super().__init__(parent)
        self.player, self.widget = player, video_widget
        self._hwdec, self._osd = hwdec, osd
        self.fmt: str | None = None
        self.auto = True
        self.yaw = self.pitch = 0.0
        self.fov = vr.DEFAULT_FOV
        self._size = (0, 0)
        self._view_timer = QTimer(self, singleShot=True, interval=33)  # 초당 약 30번까지만 방향 반영
        self._view_timer.timeout.connect(self._apply_view)
        self._resize_timer = QTimer(self, singleShot=True, interval=300)
        self._resize_timer.timeout.connect(self._rebuild_if_resized)

    @property
    def active(self) -> bool:
        return self.fmt is not None

    # ---- 켜기/끄기 ----
    def set_format(self, fmt: str | None, announce: bool = True) -> None:
        if fmt == self.fmt:
            return
        self._remove()
        self.fmt = fmt
        self.sync_hwdec()
        if fmt is None:
            if announce:
                self._osd("VR 보기 끔")
        else:
            self.yaw = self.pitch = 0.0
            self.fov = vr.DEFAULT_FOV
            self._add()
            if announce:
                self._osd(f"VR 보기: {vr.FORMATS[fmt][2]}  (끌어서 둘러보기 · Ctrl+휠 시야 · Alt+V 끄기)")
        self.changed.emit(fmt)

    def toggle(self) -> None:
        """끄기 ↔ 켜기 (켤 때는 파일 이름으로 짐작한 형식, 모르면 일반 360°)."""
        if self.active:
            self.set_format(None)
        else:
            self.set_format(self._guess() or "360")

    def prepare(self, path: str) -> None:
        """파일을 열기 직전: 이름으로 VR 영상이 확실하면 디코딩 방식을 미리 맞춰 둔다.
        파일이 열리는 순간에 디코딩 방식을 바꾸면서 필터를 넣으면 재생이 멈출 수 있다."""
        self._apply_hwdec(self._wanted_hwdec(self.auto and vr.detect_format(path) is not None))

    def on_file_loaded(self, path: str) -> None:
        """새 파일: 자동 감지가 켜져 있으면 VR 영상인지 보고 켜거나 끈다."""
        fmt = self._guess(path) if self.auto else None
        if fmt != self.fmt:
            self.set_format(fmt, announce=fmt is not None)
        elif fmt is not None:
            self.reset_view(announce=False)

    def _guess(self, path: str | None = None) -> str | None:
        path = path or (self.player.path or "")
        w = h = None
        try:
            for t in self.player.track_list or []:
                if t.get("type") == "video" and t.get("selected"):
                    w, h = t.get("demux-w"), t.get("demux-h")
        except Exception:
            pass
        return vr.detect_format(path, w, h)

    # ---- 둘러보기 ----
    def rotate_by(self, dx: float, dy: float) -> None:
        """끈 거리(화면 크기 대비 비율)만큼 방향을 돌린다. 끄는 쪽으로 화면이 따라온다."""
        w, h = self._size or (16, 9)
        self.yaw -= dx * self.fov
        self.pitch += dy * vr.v_fov(self.fov, max(w, 1), max(h, 1))
        self._schedule()

    def change_fov(self, step: float) -> None:
        self.fov = vr.clamp_view(0, 0, self.fov + step)[2]
        self._schedule()
        self._osd(f"VR 시야 {self.fov:.0f}°")

    def turn(self, d_yaw: float = 0.0, d_pitch: float = 0.0) -> None:
        self.yaw += d_yaw
        self.pitch += d_pitch
        self._schedule()

    def reset_view(self, announce: bool = True) -> None:
        self.yaw = self.pitch = 0.0
        self.fov = vr.DEFAULT_FOV
        self._schedule()
        if announce:
            self._osd("VR: 정면으로")

    def on_resized(self) -> None:
        if self.active:
            self._resize_timer.start()

    # ---- mpv 반영 ----
    def _target_size(self) -> tuple[int, int]:
        ratio = self.widget.devicePixelRatioF()
        return vr.out_size(int(self.widget.width() * ratio), int(self.widget.height() * ratio))

    def _add(self) -> None:
        self._size = self._target_size()
        self.yaw, self.pitch, self.fov = vr.clamp_view(self.yaw, self.pitch, self.fov)
        try:
            self.player.command("vf", "add", vr.build_filter(self.fmt, *self._size, self.yaw, self.pitch, self.fov))
        except SystemError:
            self._osd("VR 보기를 켜지 못했습니다")
            self.fmt = None

    def _remove(self) -> None:
        if self.fmt is None:
            return
        try:
            self.player.command("vf", "remove", f"@{vr.LABEL}")
        except SystemError:
            pass

    def _rebuild_if_resized(self) -> None:
        if self.active and self._target_size() != self._size:
            self._remove()
            self._add()

    def _schedule(self) -> None:
        if self.active and not self._view_timer.isActive():
            self._view_timer.start()

    def _apply_view(self) -> None:
        if not self.active:
            return
        self.yaw, self.pitch, self.fov = vr.clamp_view(self.yaw, self.pitch, self.fov)
        for opt, value in vr.view_commands(self.yaw, self.pitch, self.fov, *self._size):
            try:
                self.player.command("vf-command", vr.LABEL, opt, value, "v360")
            except SystemError:
                pass

    def _wanted_hwdec(self, vr_on: bool) -> str:
        mode = self._hwdec()
        if vr_on and mode != "no" and "copy" not in mode:
            return HWDEC_COPY
        return mode

    def _apply_hwdec(self, mode: str) -> None:
        try:
            current = self.player.hwdec
            current = current[0] if isinstance(current, list) and current else current
            if current != mode:  # 같은 값이어도 다시 넣으면 디코더를 다시 시작하므로 바뀔 때만
                self.player.hwdec = mode
        except Exception:
            pass

    def sync_hwdec(self) -> None:
        """설정한 하드웨어 가속을 따르되, VR 보기 중에는 GPU 직접 방식 대신 복사 방식을 쓴다."""
        self._apply_hwdec(self._wanted_hwdec(self.active))
