"""동영상 합치기: 여러 파일(예: 나뉘어 저장된 .ts 조각)을 다시 인코딩하지 않고 하나로 잇는다."""

from __future__ import annotations

import os

from .clips import _concat_escape
from .library.scanner import is_video


def expand_paths(paths: list[str], sort_key) -> list[str]:
    """폴더는 그 안의 동영상(하위 폴더 제외)으로 펼치고, 동영상이 아닌 파일은 뺀다."""
    out: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            inner = [os.path.join(p, f) for f in os.listdir(p)]
            out += sorted((f for f in inner if os.path.isfile(f) and is_video(f)), key=sort_key)
        elif os.path.isfile(p) and is_video(p):
            out.append(p)
    seen: set[str] = set()
    return [p for p in out if not (p in seen or seen.add(p))]


def default_output(paths: list[str], ext: str = ".mp4") -> str:
    """첫 파일 이름에서 끝의 번호(_001, -12, 3 등)를 떼고 '_합침'을 붙인다."""
    first = paths[0]
    stem = os.path.splitext(os.path.basename(first))[0]
    base = stem.rstrip("0123456789").rstrip("_-. ") or stem
    return os.path.join(os.path.dirname(first), f"{base}_합침{ext}")


def build_merge(paths: list[str], list_file: str, output: str, ffmpeg: str = "ffmpeg") -> tuple[str, list[str]]:
    """(concat 목록 파일 내용, ffmpeg 인자). 모든 파일의 코덱·해상도가 같아야 한다."""
    lines = ["ffconcat version 1.0"] + [f"file '{_concat_escape(p)}'" for p in paths]
    args = [ffmpeg, "-y", "-hide_banner", "-nostats", "-loglevel", "error", "-progress", "pipe:1",
            "-f", "concat", "-safe", "0", "-i", list_file,
            "-map", "0:v?", "-map", "0:a?", "-c", "copy"]
    if output.lower().endswith((".mp4", ".m4v", ".mov")):
        args += ["-movflags", "+faststart"]
    args.append(output)
    return "\n".join(lines) + "\n", args
