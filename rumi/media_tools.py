"""재생 엔진 필터 문자열과 ffmpeg 명령 만들기 (GUI 비의존, 테스트 대상)."""

from __future__ import annotations

from dataclasses import dataclass

EQ_PRESETS = {
    "기본": (0, 0),
    "저음 강조": (6, 0),
    "고음 강조": (0, 5),
    "음성 강조": (-3, 4),
}


@dataclass
class AudioSettings:
    normalize: bool = False   # 소리 크기 자동 맞춤 (작은 소리는 키우고 큰 소리는 줄임)
    bass: int = 0             # 저음 dB (-12 ~ 12)
    treble: int = 0           # 고음 dB (-12 ~ 12)

    def is_default(self) -> bool:
        return not self.normalize and self.bass == 0 and self.treble == 0


def build_audio_filter(s: AudioSettings) -> str:
    """mpv 'af' 속성 값. 아무 효과도 없으면 빈 문자열."""
    parts = []
    if s.bass:
        parts.append(f"lavfi=[bass=g={max(min(s.bass, 12), -12)}:f=110]")
    if s.treble:
        parts.append(f"lavfi=[treble=g={max(min(s.treble, 12), -12)}:f=4000]")
    if s.normalize:
        parts.append("lavfi=[dynaudnorm=f=250:g=15:p=0.9]")
    return ",".join(parts)


def build_thumbnail_args(ffmpeg: str, path: str, t: float, width: int = 240) -> list[str]:
    """탐색 바 미리보기: t 초 지점의 한 장면을 PNG 로 표준출력에 쓴다 (빠른 키프레임 탐색)."""
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{max(t, 0):.2f}", "-i", path,
            "-frames:v", "1", "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "-"]


def contact_sheet_times(duration: float, count: int) -> list[float]:
    """장면 모음에 쓸 시각: 처음과 끝을 피해 고르게 나눈 count 개 지점."""
    if duration <= 0 or count <= 0:
        raise ValueError("영상 길이를 알 수 없습니다")
    step = duration / (count + 1)
    return [step * (i + 1) for i in range(count)]
