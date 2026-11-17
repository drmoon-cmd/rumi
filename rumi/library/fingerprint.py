"""장면 지문: 이름·크기·화질·형식이 달라도 같은 영상인지 알아보기 위한 짧은 기록.

영상 길이의 일정 지점(5%, 15% … 95%)에서 장면을 한 장씩 뽑아 9×8 흑백으로 줄이고, 옆 칸보다
밝은지 어두운지를 64개의 0/1 로 적는다 (dHash). 해상도나 화질을 바꿔 다시 저장해도 이 값은 거의
그대로다. 길이가 거의 같고 장면 대부분의 값이 비슷하면 같은 영상으로 본다.

ffmpeg 만 쓴다 (설치판에 들어 있다). 길이·해상도를 읽고, 장면마다 그 지점으로 바로 건너뛰어 한 장씩 뽑는다
(파일 전체를 읽지 않는다).
"""

from __future__ import annotations

import math
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ..netguard import ffmpeg_input_guard
from ..tools import find_ffmpeg

VERSION = 1
FRAMES = 10
GRID_W, GRID_H = 9, 8
FLAT_RANGE = 12          # 가장 밝은 칸과 어두운 칸의 차이가 이보다 작으면 '빈 장면'(검은 화면 등)으로 보고 비교에서 뺀다
MAX_DISTANCE = 10        # 장면 하나가 같다고 보는 차이 (64개 중 다른 칸 수)
MIN_FRAMES = 4           # 비교할 수 있는 장면이 이보다 적으면 판단하지 않는다
MATCH_RATIO = 0.7        # 비교한 장면 중 이 비율 이상이 같아야 같은 영상
DURATION_TOLERANCE = 2.0  # 길이 차이 허용 (초)
TIMEOUT_S = 90
THUMB_FRAMES = (1, 4, 7)   # 그림으로도 남기는 장면 (길이의 15%, 45%, 75% 지점)
THUMB_FILTER = "scale=160:-2"
THUMB_CODEC = ["-c:v", "mjpeg", "-q:v", "7", "-pix_fmt", "yuvj420p"]
WORKERS = 4             # 한 파일에서 장면을 동시에 뽑는 수

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")
_VIDEO_RE = re.compile(r"Stream #\d+:\d+.*?: Video: .*?\b(\d{2,5})x(\d{2,5})\b")


@dataclass
class MediaInfo:
    duration: float = 0.0
    width: int = 0
    height: int = 0


def parse_info(text: str) -> MediaInfo:
    """`ffmpeg -i 파일` 이 알려 주는 글에서 길이와 해상도를 읽는다."""
    info = MediaInfo()
    if m := _DURATION_RE.search(text):
        info.duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    if m := _VIDEO_RE.search(text):
        info.width, info.height = int(m.group(1)), int(m.group(2))
    return info


def probe(path: str, ffmpeg: str | None = None) -> MediaInfo:
    ffmpeg = ffmpeg or find_ffmpeg()
    if not ffmpeg:
        return MediaInfo()
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", *ffmpeg_input_guard(), "-i", path], capture_output=True,
                           timeout=TIMEOUT_S, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return MediaInfo()
    return parse_info(r.stderr.decode("utf-8", "replace"))


def positions(duration: float) -> list[float]:
    return [duration * (i + 0.5) / FRAMES for i in range(FRAMES)]


def dhash(gray: bytes) -> int | None:
    """9×8 흑백 그림 → 64비트 값. 거의 단색이면 None."""
    if len(gray) != GRID_W * GRID_H or max(gray) - min(gray) < FLAT_RANGE:
        return None
    value = 0
    for y in range(GRID_H):
        row = y * GRID_W
        for x in range(GRID_W - 1):
            value = (value << 1) | (gray[row + x] > gray[row + x + 1])
    return value


def _grab_one(ffmpeg: str, path: str, t: float, thumb_path: str | None = None) -> bytes:
    """t 초 지점의 장면을 9×8 흑백으로. thumb_path 를 주면 같은 장면의 작은 그림(JPEG)도 그 파일로 쓴다
    (ffmpeg 를 한 번만 실행해 둘 다 얻는다)."""
    cmd = [ffmpeg, "-v", "error", "-nostdin", *ffmpeg_input_guard(), "-ss", f"{t:.3f}", "-i", path]
    small = f"scale={GRID_W}:{GRID_H}:flags=area,format=gray"
    if thumb_path:
        cmd += ["-filter_complex", f"[0:v:0]split=2[a][b];[a]{small}[h];[b]{THUMB_FILTER}[t]",
                "-map", "[h]", "-frames:v", "1", "-an", "-sn", "-dn", "-f", "rawvideo", "-",
                "-map", "[t]", "-frames:v", "1", "-an", "-sn", "-dn", *THUMB_CODEC, "-f", "image2", "-y", thumb_path]
    else:
        cmd += ["-map", "0:v:0", "-frames:v", "1", "-vf", small, "-an", "-sn", "-dn", "-f", "rawvideo", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_S, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return b""
    return r.stdout[:GRID_W * GRID_H]


def grab_thumb(ffmpeg: str, path: str, t: float) -> bytes:
    """t 초 지점의 작은 그림(JPEG)만. 지문은 이미 있고 그림만 없는 파일용."""
    cmd = [ffmpeg, "-v", "error", "-nostdin", *ffmpeg_input_guard(), "-ss", f"{t:.3f}", "-i", path, "-map", "0:v:0",
           "-frames:v", "1", "-vf", THUMB_FILTER, "-an", "-sn", "-dn", *THUMB_CODEC, "-f", "image2pipe", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_S, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return b""
    return r.stdout if r.stdout[:2] == b"\xff\xd8" else b""


def thumbs_only(path: str, duration: float, ffmpeg: str | None = None) -> list[bytes]:
    ffmpeg = ffmpeg or find_ffmpeg()
    if not ffmpeg or duration <= 0:
        return []
    times = positions(duration)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return list(pool.map(lambda i: grab_thumb(ffmpeg, path, times[i]), THUMB_FRAMES))


def encode(hashes: list[int | None]) -> str:
    return f"{VERSION}:" + ",".join("-" if h is None else f"{h:016x}" for h in hashes)


def decode(text: str | None) -> list[int | None] | None:
    """저장된 지문 → 장면 값 목록. 다른 판(version)이거나 깨졌으면 None."""
    if not text:
        return None
    version, _, body = text.partition(":")
    if version != str(VERSION):
        return None
    try:
        hashes = [None if part == "-" else int(part, 16) for part in body.split(",")]
    except ValueError:
        return None
    return hashes if len(hashes) == FRAMES else None


def usable(text: str | None) -> bool:
    hashes = decode(text)
    return hashes is not None and sum(h is not None for h in hashes) >= MIN_FRAMES


def fingerprint(path: str, duration: float, ffmpeg: str | None = None) -> str | None:
    """영상의 장면 지문. 만들 수 없으면 None (길이를 모르거나 영상이 아닌 파일)."""
    return fingerprint_with_thumbs(path, duration, ffmpeg, thumbs=False)[0]


def fingerprint_with_thumbs(path: str, duration: float, ffmpeg: str | None = None,
                            thumbs: bool = True) -> tuple[str | None, list[bytes]]:
    """(장면 지문, 작은 그림 3장). 그림은 지문을 뽑는 장면 중 3곳(THUMB_FRAMES)을 그대로 쓴다:
    뽑아 둔 드라이브의 영상도 지우기 전에 눈으로 비교할 수 있게 하기 위한 것."""
    ffmpeg = ffmpeg or find_ffmpeg()
    if not ffmpeg or duration <= 0:
        return None, []
    times = positions(duration)
    with tempfile.TemporaryDirectory(prefix="rumi_fp_") as tmp:
        targets = {i: os.path.join(tmp, f"{i}.jpg") for i in THUMB_FRAMES} if thumbs else {}
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            frames = list(pool.map(lambda i: _grab_one(ffmpeg, path, times[i], targets.get(i)), range(FRAMES)))
        pictures = []
        for i in THUMB_FRAMES if thumbs else ():
            try:
                with open(targets[i], "rb") as f:
                    data = f.read()
            except OSError:
                data = b""
            pictures.append(data if data[:2] == b"\xff\xd8" else b"")
    hashes = [dhash(f) for f in frames]
    if all(h is None for h in hashes):
        return None, pictures
    return encode(hashes), pictures


def matches(a: list[int | None], b: list[int | None]) -> bool:
    """두 지문이 같은 영상의 것인가.

    다른 영상은 대개 앞의 장면 몇 개에서 이미 어긋나므로, 더 볼 필요가 없어지면 바로 그만둔다
    (파일이 수만 개일 때 비교 횟수가 수천만 번이 된다)."""
    usable_count = 0
    for x, y in zip(a, b):
        if x is not None and y is not None:
            usable_count += 1
    if usable_count < MIN_FRAMES:
        return False
    need = math.ceil(usable_count * MATCH_RATIO)
    may_miss = usable_count - need
    for x, y in zip(a, b):
        if x is None or y is None:
            continue
        if (x ^ y).bit_count() > MAX_DISTANCE:
            may_miss -= 1
            if may_miss < 0:
                return False
    return True


def same_length(d1: float, d2: float) -> bool:
    return d1 > 0 and d2 > 0 and abs(d1 - d2) <= DURATION_TOLERANCE
