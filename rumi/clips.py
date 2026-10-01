"""구간(클립) 목록: 여러 동영상에서 잘라 낸 구간을 이어 재생하거나 하나의 파일로 내보낸다."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

CLIPLIST_SUFFIX = ".rumiclips"
MIN_CLIP_SECONDS = 0.1


@dataclass
class Clip:
    path: str
    start: float
    end: float
    # 내보내기에 필요한 원본 정보 (구간을 만들 때 재생 중인 영상에서 기록)
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = True

    @property
    def duration(self) -> float:
        return max(self.end - self.start, 0.0)

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


@dataclass
class ClipList:
    clips: list[Clip] = field(default_factory=list)

    @property
    def total_duration(self) -> float:
        return sum(c.duration for c in self.clips)

    def add(self, clip: Clip) -> None:
        if clip.end < clip.start:
            clip.start, clip.end = clip.end, clip.start
        if clip.duration < MIN_CLIP_SECONDS:
            raise ValueError("구간이 너무 짧습니다")
        self.clips.append(clip)

    def save(self, path: str | Path) -> None:
        data = {"version": 1, "clips": [asdict(c) for c in self.clips]}
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "ClipList":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        known = Clip.__dataclass_fields__
        return cls([Clip(**{k: v for k, v in c.items() if k in known}) for c in data.get("clips", [])])

    def missing_files(self) -> list[str]:
        return sorted({c.path for c in self.clips if not os.path.exists(c.path)})

    def uniform_format(self) -> bool:
        """모든 구간의 해상도가 같은지 (빠른 내보내기 가능 여부 판단용)."""
        sizes = {(c.width, c.height) for c in self.clips}
        return len(sizes) <= 1


# ---------------------------------------------------------------- 내보내기 명령
def _concat_escape(path: str) -> str:
    return path.replace("\\", "/").replace("'", r"'\''")


def build_copy_export(clips: list[Clip], list_file: str, output: str, ffmpeg: str = "ffmpeg") -> tuple[str, list[str]]:
    """빠른 내보내기: 다시 인코딩하지 않고 잘라 붙인다.

    매우 빠르고 화질 손실이 없지만, 구간 경계가 키프레임 단위로 조금 어긋날 수 있고
    모든 구간의 코덱/해상도가 같아야 한다. (concat 목록 파일 내용, ffmpeg 인자) 를 돌려준다.
    """
    lines = ["ffconcat version 1.0"]
    for c in clips:
        lines += [f"file '{_concat_escape(c.path)}'", f"inpoint {c.start:.3f}", f"outpoint {c.end:.3f}"]
    args = [ffmpeg, "-y", "-hide_banner", "-nostats", "-progress", "pipe:1",
            "-f", "concat", "-safe", "0", "-i", list_file,
            "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
            "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", output]
    return "\n".join(lines) + "\n", args


def build_reencode_export(clips: list[Clip], output: str, ffmpeg: str = "ffmpeg",
                          crf: int = 20, preset: str = "veryfast") -> list[str]:
    """정확한 내보내기: 구간을 프레임 단위로 정확히 자르고 H.264/AAC 로 다시 인코딩한다.

    해상도가 다른 영상은 첫 구간 크기에 맞춰 여백(레터박스)을 넣고, 소리가 없는 구간은 무음을 채운다.
    """
    if not clips:
        raise ValueError("내보낼 구간이 없습니다")
    w = clips[0].width or 1920
    h = clips[0].height or 1080
    w, h = w - w % 2, h - h % 2
    fps = next((c.fps for c in clips if c.fps > 0), 30.0)

    args = [ffmpeg, "-y", "-hide_banner", "-nostats", "-progress", "pipe:1"]
    for c in clips:
        args += ["-ss", f"{c.start:.3f}", "-t", f"{c.duration:.3f}", "-i", c.path]
    filters, concat_inputs = [], []
    for i, c in enumerate(clips):
        filters.append(
            f"[{i}:v:0]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps:g},format=yuv420p[v{i}]")
        if c.has_audio:
            filters.append(f"[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
        else:
            filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={c.duration:.3f}[a{i}]")
        concat_inputs.append(f"[v{i}][a{i}]")
    filters.append(f"{''.join(concat_inputs)}concat=n={len(clips)}:v=1:a=1[v][a]")
    args += ["-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
             "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", output]
    return args


def parse_progress_seconds(line: str) -> float | None:
    """ffmpeg -progress 출력에서 현재 처리 위치(초)를 읽는다."""
    if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
        try:
            return int(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return None
    return None
