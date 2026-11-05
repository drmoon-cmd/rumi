"""화질 관련 재생 엔진(mpv) 설정: 화질 개선(확대), HDR 색 보정, 부드러운 움직임 (GUI 비의존, 테스트 대상)."""

from __future__ import annotations

import os

SHADER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shaders")

QUALITY_PRESETS = {
    "default": "기본",
    "hq": "고화질 (부드럽게 확대)",
    "sharp": "고화질 + 선명하게",
    "anime": "애니메이션 화질 개선 (Anime4K)",
}
HDR_PRESETS = {
    "auto": "자동 (기본)",
    "dynamic": "장면마다 밝기 맞춤",
}

# mpv 의 고화질 확대 설정 (high-quality 프로필과 같은 내용을 직접 지정)
HQ_OPTIONS = {
    "scale": "ewa_lanczossharp",
    "cscale": "ewa_lanczossharp",
    "dscale": "mitchell",
    "correct-downscaling": True,
    "linear-downscaling": True,
    "sigmoid-upscaling": True,
    "deband": True,
}
HDR_OPTIONS = {
    "auto": {},
    # 장면마다 가장 밝은 부분을 재서 맞춘다 (밝은 장면은 덜 날아가고 어두운 장면은 덜 칙칙하게)
    "dynamic": {"hdr-compute-peak": "yes", "tone-mapping": "bt.2390"},
}

# Anime4K 'Mode A (Fast)': 1080p 이하 애니메이션 권장 조합 (Anime4K 안내서 기준)
ANIME4K_CHAIN = (
    "Anime4K_Clamp_Highlights.glsl",
    "Anime4K_Restore_CNN_M.glsl",
    "Anime4K_Upscale_CNN_x2_M.glsl",
    "Anime4K_AutoDownscalePre_x2.glsl",
    "Anime4K_AutoDownscalePre_x4.glsl",
    "Anime4K_Upscale_CNN_x2_S.glsl",
)

# 렌더링 옵션 중 이 모듈과 호환 모드가 바꾸는 것 (원래 값으로 되돌리기 위해 기억해 둔다)
TOUCHED_OPTIONS = ("scale", "dscale", "cscale", "dither", "correct-downscaling", "linear-downscaling",
                   "sigmoid-upscaling", "deband", "hdr-compute-peak", "allow-delayed-peak-detect",
                   "tone-mapping")


def quality_options(preset: str) -> dict:
    return dict(HQ_OPTIONS) if preset in ("hq", "sharp") else {}


def shader_paths(preset: str, base: str = SHADER_DIR) -> list[str]:
    if preset == "sharp":
        return [os.path.join(base, "rumi_sharpen.glsl")]
    if preset == "anime":
        return [os.path.join(base, "anime4k", name) for name in ANIME4K_CHAIN]
    return []


def hdr_options(preset: str) -> dict:
    return dict(HDR_OPTIONS.get(preset, {}))


def smooth_options(on: bool, refresh_hz: float | None = None) -> dict:
    """부드러운 움직임: 화면 주사율에 맞춰 영상 속도를 아주 조금 조정하고 프레임 사이를 섞어 끊김을 줄인다."""
    if not on:
        return {"video-sync": "audio", "interpolation": False}
    opts = {"video-sync": "display-resample", "interpolation": True, "tscale": "oversample"}
    if refresh_hz and refresh_hz >= 23:
        opts["display-fps-override"] = round(float(refresh_hz), 3)
    return opts


def is_hdr(video_params: dict | None) -> bool:
    """영상이 HDR(PQ/HLG)인지."""
    return bool(video_params) and str(video_params.get("gamma", "")).lower() in ("pq", "hlg")


# ---- 과부하 막기 ----
ANIME4K_MAX_HEIGHT = 1080   # 이보다 큰 영상은 Anime4K 가 너무 무거워 '선명하게'로 대신한다
DROP_RATIO_LIMIT = 0.10     # 2초 동안 보여야 할 프레임 중 버린 비율이 이보다 크면 버거운 것
LAG_LIMIT_MS = 250          # 화면(조작)이 이만큼 넘게 멈추면 버거운 것


def effective_quality(preferred: str, video_height: int | None) -> str:
    """고른 화질 개선을 이 영상에 그대로 쓸지 (큰 영상에 Anime4K 는 너무 무겁다)."""
    if preferred == "anime" and video_height and video_height > ANIME4K_MAX_HEIGHT:
        return "sharp"
    return preferred


def is_overloaded(dropped: int, frames: int, lag_ms: float) -> bool:
    if lag_ms > LAG_LIMIT_MS:
        return True
    return frames >= 10 and dropped / frames > DROP_RATIO_LIMIT


def is_heavy(smooth: bool, quality: str, hdr: str) -> bool:
    return smooth or quality != "default" or hdr != "auto"


def step_down(smooth: bool, quality: str, hdr: str) -> tuple[tuple[bool, str, str], str] | None:
    """PC 가 버거울 때 한 단계 가볍게: (새 설정, 안내 문구). 더 내릴 것이 없으면 None.

    가장 부담이 큰 것부터: 부드러운 움직임(화면 주사율만큼 매번 다시 그림) → 셰이더 → 고화질 확대 → HDR 측정."""
    if smooth:
        return (False, quality, hdr), "부드러운 움직임을 껐습니다"
    if quality in ("anime", "sharp"):
        return (False, "hq", hdr), f"화질 개선을 '{QUALITY_PRESETS['hq']}'(으)로 낮췄습니다"
    if quality == "hq":
        return (False, "default", hdr), "화질 개선을 껐습니다"
    if hdr != "auto":
        return (False, quality, "auto"), "HDR 장면별 밝기 맞춤을 껐습니다"
    return None
