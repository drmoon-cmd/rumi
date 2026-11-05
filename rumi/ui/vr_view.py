"""VR(360°/180°) 보기 상태: 형식, 보는 방향, 시야. 마우스로 끌면 둘러보고 Ctrl+휠로 시야를 바꾼다.

펼치기는 그래픽 카드가 한다 (rumi/ui/vr_renderer.py). 방향을 바꿀 때는 셰이더 값만 바꾸므로
CPU 를 거의 쓰지 않는다. VR 보기 중에는 mpv 의 화면 글자(OSD)가 함께 휘어지므로 탐색 막대는 끈다.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from .. import vr


class VrView(QObject):
    changed = Signal(object)  # 형식 이름 또는 None(끔)

    def __init__(self, player, video_widget, osd: Callable[[str], None], parent=None):
        super().__init__(parent)
        self.player, self.widget = player, video_widget
        self._osd = osd
        self.fmt: str | None = None
        self.auto = True
        self.yaw = self.pitch = 0.0
        self.fov = vr.DEFAULT_FOV
        self._saved_osd_bar = True
        self._view_timer = QTimer(self, singleShot=True, interval=16)  # 초당 약 60번까지 방향 반영
        self._view_timer.timeout.connect(self._apply_view)

    @property
    def active(self) -> bool:
        return self.fmt is not None

    # ---- 켜기/끄기 ----
    def set_format(self, fmt: str | None, announce: bool = True) -> None:
        if fmt == self.fmt:
            return
        was_active = self.active
        self.fmt = fmt
        if fmt is None:
            self.widget.set_vr(None)
            self._set("osd-bar", self._saved_osd_bar)
            if announce:
                self._osd("VR 보기 끔")
        else:
            if not was_active:
                self._saved_osd_bar = bool(self._get("osd-bar", True))
                self._set("osd-bar", False)
            self.yaw = self.pitch = 0.0
            self.fov = vr.DEFAULT_FOV
            self._apply_view()
            if announce:
                self._osd(f"VR 보기: {vr.FORMATS[fmt][2]}  (끌어서 둘러보기 · Ctrl+휠 시야 · Alt+V 끄기)")
        self.changed.emit(fmt)

    def toggle(self) -> None:
        """끄기 ↔ 켜기 (켤 때는 파일 이름으로 짐작한 형식, 모르면 일반 360°)."""
        if self.active:
            self.set_format(None)
        else:
            self.set_format(self._guess() or "360")

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
        w, h = max(self.widget.width(), 1), max(self.widget.height(), 1)
        self.yaw -= dx * self.fov
        self.pitch += dy * vr.v_fov(self.fov, w, h)
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
        pass  # 펼친 화면은 그릴 때마다 창 크기에 맞춘다

    # ---- mpv 반영 ----
    def _schedule(self) -> None:
        if self.active and not self._view_timer.isActive():
            self._view_timer.start()

    def _apply_view(self) -> None:
        if not self.active:
            return
        self.yaw, self.pitch, self.fov = vr.clamp_view(self.yaw, self.pitch, self.fov)
        self.widget.set_vr(vr.view_params(self.fmt, self.yaw, self.pitch, self.fov))

    def _get(self, name: str, default):
        try:
            value = self.player[name]
            return default if value in (None, "") else value
        except Exception:
            return default

    def _set(self, name: str, value) -> None:
        try:
            self.player[name] = value
        except Exception:
            pass
