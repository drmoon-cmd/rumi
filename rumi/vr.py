"""VR(360°/180°) 동영상을 일반 화면으로 보기: 보는 방향·시야를 정해 평면 화면으로 펼친다.

mpv 의 lavfi 필터 v360 을 쓴다. 방향(yaw/pitch)과 시야(fov)는 필터를 다시 만들지 않고
vf-command 로 바꿀 수 있어 마우스로 끌어 둘러볼 수 있다.
"""

from __future__ import annotations

import math
import os
import re

LABEL = "rumivr"

# 이름: (입력 투영, 입체 배치, 메뉴 이름)
FORMATS = {
    "360": ("e", "2d", "360° 영상"),
    "360_tb": ("e", "tb", "360° 3D (위아래)"),
    "360_sbs": ("e", "sbs", "360° 3D (좌우)"),
    "180_sbs": ("hequirect", "sbs", "180° 3D (좌우, VR180)"),
    "180_tb": ("hequirect", "tb", "180° 3D (위아래)"),
    "180": ("hequirect", "2d", "180° 영상"),
}

DEFAULT_FOV = 100.0
MIN_FOV, MAX_FOV = 30.0, 150.0
MAX_OUT_W, MAX_OUT_H = 1920, 1080  # 펼친 화면 크기 상한 (CPU 부담을 줄이기 위해)

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def detect_format(path: str, width: int | None = None, height: int | None = None) -> str | None:
    """파일 이름의 표시(360, 180, VR180, TB, SBS, LR …)와 화면 비율로 VR 영상인지 알아낸다.

    이름에 VR 표시가 없으면 None (일반 영상을 잘못 펼치지 않도록 비율만으로는 판단하지 않는다).
    """
    name = os.path.splitext(os.path.basename(path.split("?")[0]))[0].lower()
    tokens = set(_TOKEN_SPLIT.split(name))
    # 정확히 이 낱말일 때만 (IMG_0360 같은 번호나 360p 같은 해상도 표시는 제외)
    is180 = bool(tokens & {"180", "vr180", "180vr", "180deg", "180x180"})
    is360 = bool(tokens & {"360", "vr360", "360vr", "360deg", "360video", "equirect", "equirectangular"})
    if not (is180 or is360):
        return None
    tb = bool(tokens & {"tb", "ou", "overunder", "topbottom", "3dv", "stereo_tb"})
    sbs = bool(tokens & {"sbs", "lr", "3dh", "sidebyside", "stereo_sbs", "rl"})
    if not (tb or sbs) and width and height:
        ratio = width / height
        if is180:
            sbs = ratio > 1.5          # VR180 은 보통 좌우 2:1
        else:
            tb = 0.8 < ratio < 1.25    # 360° 위아래 3D 는 보통 1:1
            sbs = ratio > 3.0          # 360° 좌우 3D 는 4:1
    if is180:
        return "180_tb" if tb else "180_sbs" if sbs or not (width and height) else "180"
    return "360_tb" if tb else "360_sbs" if sbs else "360"


def clamp_view(yaw: float, pitch: float, fov: float) -> tuple[float, float, float]:
    yaw = (yaw + 180.0) % 360.0 - 180.0
    return yaw, max(-90.0, min(90.0, pitch)), max(MIN_FOV, min(MAX_FOV, fov))


def out_size(view_w: int, view_h: int) -> tuple[int, int]:
    """펼친 화면의 크기: 창 비율을 따르되 상한을 넘지 않게, 짝수로."""
    view_w, view_h = max(view_w, 16), max(view_h, 16)
    scale = min(1.0, MAX_OUT_W / view_w, MAX_OUT_H / view_h)
    return int(view_w * scale) // 2 * 2, int(view_h * scale) // 2 * 2


def v_fov(h_fov: float, w: int, h: int) -> float:
    """가로 시야와 화면 비율로 세로 시야를 구한다 (왜곡 없이 보이도록)."""
    return math.degrees(2 * math.atan(math.tan(math.radians(h_fov) / 2) * h / w))


def build_filter(fmt: str, w: int, h: int, yaw: float = 0.0, pitch: float = 0.0, fov: float = DEFAULT_FOV) -> str:
    projection, stereo, _ = FORMATS[fmt]
    yaw, pitch, fov = clamp_view(yaw, pitch, fov)
    opts = [f"input={projection}", "output=flat", f"in_stereo={stereo}", "out_stereo=2d",
            f"w={w}", f"h={h}", f"yaw={yaw:.2f}", f"pitch={pitch:.2f}",
            f"h_fov={fov:.2f}", f"v_fov={v_fov(fov, w, h):.2f}", "interp=linear"]
    return f"@{LABEL}:lavfi=[v360={':'.join(opts)}]"


def view_commands(yaw: float, pitch: float, fov: float, w: int, h: int) -> list[tuple[str, str]]:
    """필터를 다시 만들지 않고 방향·시야를 바꾸는 (옵션, 값) 목록 (vf-command 용)."""
    yaw, pitch, fov = clamp_view(yaw, pitch, fov)
    return [("yaw", f"{yaw:.2f}"), ("pitch", f"{pitch:.2f}"),
            ("h_fov", f"{fov:.2f}"), ("v_fov", f"{v_fov(fov, w, h):.2f}")]
