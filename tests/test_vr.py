import pytest

from rumi.vr import FORMATS, build_filter, clamp_view, detect_format, out_size, v_fov, view_commands


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


def test_out_size_keeps_aspect_under_cap_and_even():
    assert out_size(3840, 2160) == (1920, 1080)
    w, h = out_size(1001, 563)
    assert (w % 2, h % 2) == (0, 0) and w <= 1001


def test_v_fov_matches_aspect():
    assert v_fov(90, 1000, 1000) == pytest.approx(90)
    assert v_fov(100, 1920, 1080) < 100


def test_build_filter_and_commands():
    f = build_filter("360_tb", 1280, 720, yaw=30, pitch=-10, fov=90)
    assert f.startswith("@rumivr:lavfi=[v360=")
    assert "input=e" in f and "in_stereo=tb" in f and "out_stereo=2d" in f and "w=1280" in f
    assert "yaw=30.00" in f and "pitch=-10.00" in f
    assert "input=hequirect" in build_filter("180_sbs", 640, 360)
    names = [n for n, _ in view_commands(0, 0, 100, 1280, 720)]
    assert names == ["yaw", "pitch", "h_fov", "v_fov"]
    assert set(FORMATS) >= {"360", "360_tb", "360_sbs", "180", "180_sbs"}
