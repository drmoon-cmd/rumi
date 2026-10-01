"""내용이 완전히 같은 동영상 찾기.

크기 → 부분 해시(앞/중간/끝 1MB) → 전체 해시 순으로 좁혀 나가므로,
같은 크기의 파일이 있을 때만 파일 전체를 읽는다.
"""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from .db import LibraryDB, Video, VideoStatus

CHUNK = 1024 * 1024

ProgressFn = Callable[[str, int, int], None]  # (단계 설명, 현재, 전체)
CancelFn = Callable[[], bool]


class Cancelled(Exception):
    pass


@dataclass
class DuplicateGroup:
    full_hash: str
    size: int
    videos: list[Video] = field(default_factory=list)

    @property
    def wasted_bytes(self) -> int:
        return self.size * (len(self.videos) - 1)


def quick_hash(path: str) -> str:
    size = os.path.getsize(path)
    h = hashlib.blake2b(digest_size=16)
    h.update(str(size).encode())
    with open(path, "rb") as f:
        offsets = [0] if size <= 3 * CHUNK else [0, size // 2 - CHUNK // 2, size - CHUNK]
        for off in offsets:
            f.seek(off)
            h.update(f.read(CHUNK))
    return h.hexdigest()


def full_hash(path: str, cancel: CancelFn | None = None) -> str:
    h = hashlib.blake2b(digest_size=32)
    with open(path, "rb") as f:
        while chunk := f.read(8 * CHUNK):
            if cancel and cancel():
                raise Cancelled
            h.update(chunk)
    return h.hexdigest()


def suggest_keep(group: DuplicateGroup) -> Video:
    """그룹에서 남길 파일 추천: 표시 안 된 것 → 가장 오래된 것 → 경로가 짧은 것."""
    return min(group.videos, key=lambda v: (v.status != VideoStatus.ACTIVE, v.mtime, len(v.path), v.path))


def find_duplicates(db: LibraryDB, progress: ProgressFn | None = None,
                    cancel: CancelFn | None = None) -> list[DuplicateGroup]:
    def check_cancel():
        if cancel and cancel():
            raise Cancelled

    videos = [v for v in db.videos([VideoStatus.ACTIVE, VideoStatus.MARKED])
              if os.path.exists(v.path)]

    by_size: dict[int, list[Video]] = defaultdict(list)
    for v in videos:
        if v.size > 0:
            by_size[v.size].append(v)
    candidates = [v for g in by_size.values() if len(g) > 1 for v in g]

    # 1단계: 부분 해시
    by_quick: dict[tuple[int, str], list[Video]] = defaultdict(list)
    for i, v in enumerate(candidates, 1):
        check_cancel()
        if progress:
            progress("빠른 비교", i, len(candidates))
        if v.quick_hash is None:
            try:
                v.quick_hash = quick_hash(v.path)
            except OSError:
                continue
            db.set_hashes(v.id, quick_hash=v.quick_hash)
        by_quick[(v.size, v.quick_hash)].append(v)
    candidates = [v for g in by_quick.values() if len(g) > 1 for v in g]

    # 2단계: 전체 해시
    by_full: dict[str, list[Video]] = defaultdict(list)
    for i, v in enumerate(candidates, 1):
        check_cancel()
        if progress:
            progress("정밀 비교", i, len(candidates))
        if v.full_hash is None:
            try:
                v.full_hash = full_hash(v.path, cancel)
            except OSError:
                continue
            db.set_hashes(v.id, full_hash=v.full_hash)
        by_full[v.full_hash].append(v)

    groups = [DuplicateGroup(h, vs[0].size, sorted(vs, key=lambda v: v.path))
              for h, vs in by_full.items() if len(vs) > 1]
    groups.sort(key=lambda g: g.wasted_bytes, reverse=True)
    return groups
