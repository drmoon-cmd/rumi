import pytest

import math

from rumi.vr import FORMATS, clamp_view, detect_format, v_fov, view_params


@pytest.mark.parametrize("name,w,h,expected", [
    ("trailer_360.mp4", 3840, 1920, "360"),
    ("trip_360_TB.mp4", 4096, 4096, "360_tb"),
    ("Movie 360 3D.mp4", 4096, 4096, "360_tb"),      # 1:1 이면 위아래 3D
    ("360_SBS.mp4", None, None, "360_sbs"),
    ("VR180_clip.mp4", 5760, 2880, "180_sbs"),
    ("x_180_LR.mp4", None, None, "180_sbs"),
    ("cam_180.mp4", 2048, 2048, "180"),
    ("IMG_0360.mp4", 1920, 1080, None),               # 번호는 VR 표시가 아님
    ("video_360p.mp4", 640, 360, None),               # 해상도 표시도 아님
    ("normal.mp4", 3840, 1920, None),                 # 비율만으로는 켜지 않음
])
def test_detect_format(name, w, h, expected):
    assert detect_format(name, w, h) == expected


def test_detect_format_ignores_url_query():
    assert detect_format("https://cdn.example.com/promo_360.m3u8?token=abc") == "360"


def test_clamp_view_wraps_yaw_and_limits_pitch_fov():
    assert clamp_view(190, 120, 500) == (-170, 90, 150)
    assert clamp_view(-190, -120, 1) == (170, -90, 30)


def test_v_fov_matches_aspect():
    assert v_fov(90, 1000, 1000) == pytest.approx(90)
    assert v_fov(100, 1920, 1080) < 100


def test_view_params_for_renderer():
    v = view_params("360_tb", yaw=30, pitch=-10, fov=90)
    assert v["yaw"] == pytest.approx(math.radians(30)) and v["pitch"] == pytest.approx(math.radians(-10))
    assert v["tan_half"] == pytest.approx(1.0)            # tan(45°)
    assert v["span"] == pytest.approx(2 * math.pi) and v["stereo"] == 1
    v180 = view_params("180_sbs")
    assert v180["span"] == pytest.approx(math.pi) and v180["stereo"] == 2
    assert view_params("360", fov=999)["tan_half"] == pytest.approx(math.tan(math.radians(75)))  # 시야 상한 150°
    assert set(FORMATS) >= {"360", "360_tb", "360_sbs", "180", "180_sbs"}
