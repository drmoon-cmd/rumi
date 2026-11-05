"""VR(360°/180°) 동영상을 일반 화면으로 보기: 보는 방향·시야를 정해 평면 화면으로 펼친다.

그래픽 카드 셰이더(rumi/ui/vr_renderer.py)로 펼친다. 방향·시야는 셰이더 값만 바꾸면 되므로
끌어서 둘러봐도 CPU 부담이 없다. (예전의 v360 필터는 CPU 에서 돌고, 방향을 바꿀 때마다
큰 계산표를 새로 만들어 PC 가 느려졌다.)
"""

from __future__ import annotations

import math
import os
import re

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


def v_fov(h_fov: float, w: int, h: int) -> float:
    """가로 시야와 화면 비율로 세로 시야를 구한다 (끌어서 둘러볼 때 위아래 이동량 계산용)."""
    return math.degrees(2 * math.atan(math.tan(math.radians(h_fov) / 2) * h / w))


def view_params(fmt: str, yaw: float = 0.0, pitch: float = 0.0, fov: float = DEFAULT_FOV) -> dict:
    """VR 펼치기 셰이더(rumi/ui/vr_renderer.py)에 넘길 값. 각도는 라디안."""
    projection, stereo, _ = FORMATS[fmt]
    yaw, pitch, fov = clamp_view(yaw, pitch, fov)
    return {
        "yaw": math.radians(yaw), "pitch": math.radians(pitch),
        "tan_half": math.tan(math.radians(fov) / 2),
        "span": math.radians(180 if projection == "hequirect" else 360),
        "stereo": {"2d": 0, "tb": 1, "sbs": 2}[stereo],
    }
