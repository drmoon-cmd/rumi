"""같은 동영상 찾기.

내용이 완전히 같은 파일: 크기 → 부분 해시(앞/중간/끝 1MB) → 전체 해시 순으로 좁혀 나가므로,
같은 크기의 파일이 있을 때만 파일 전체를 읽는다.
이름·화질·형식이 다른 같은 영상: 스캔 때 만들어 둔 장면 지문으로 찾는다 (fingerprint.py).
"""

from __future__ import annotations

import hashlib
import math
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
    kind: str = "exact"       # "exact": 내용이 같은 파일 / "similar": 장면이 같은 영상 (화질·형식·이름이 다름)
    verified: bool = True     # exact 에서, 모든 사본을 파일 전체로 대조했는가 (연결 안 된 드라이브가 끼면 False)
    offline_ids: frozenset = frozenset()   # 지금 연결되지 않은 드라이브에 있는 파일

    @property
    def wasted_bytes(self) -> int:
        """하나만 남기면 비는 용량."""
        return sum(v.size for v in self.videos) - suggest_keep(self).size


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
    """그룹에서 남길 파일 추천.

    내용이 같은 파일: 표시 안 된 것 → 지금 연결된 것 → 가장 오래된 것 → 경로가 짧은 것.
    장면이 같은 영상: 표시 안 된 것 → 해상도가 높은 것 → 용량이 큰 것(화질이 좋을 가능성) → 연결된 것 → 오래된 것.
    (연결된 사본을 남기면 지우기 전에 그 사본을 실제로 확인할 수 있다)
    """
    away = group.offline_ids
    if group.kind == "similar":
        return min(group.videos, key=lambda v: (v.status != VideoStatus.ACTIVE, -(v.width or 0) * (v.height or 0),
                                                -v.size, v.id in away, v.mtime, v.path))
    return min(group.videos, key=lambda v: (v.status != VideoStatus.ACTIVE, v.id in away, v.mtime, len(v.path),
                                            v.path))


class _Sets:
    """서로 같은 것끼리 묶기 (union-find)."""

    def __init__(self):
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def find_duplicates(db: LibraryDB, progress: ProgressFn | None = None,
                    cancel: CancelFn | None = None, similar: bool = True) -> list[DuplicateGroup]:
    """중복 찾기. 지금 연결되지 않은 드라이브의 파일도 스캔 때 적어 둔 기록으로 함께 비교한다.

    1) 내용이 같은 파일: 크기 → 앞·중간·끝 요약 → (연결된 파일끼리는) 파일 전체 대조
    2) 장면이 같은 영상: 길이가 거의 같고 장면 지문이 같은 것 (similar=True 일 때)
    """
    from . import fingerprint as fp

    def check_cancel():
        if cancel and cancel():
            raise Cancelled

    db.refresh_volumes()
    volumes = db.volumes()

    def is_online(v: Video) -> bool:
        if v.path.startswith("<"):
            return False
        if v.volume_id is not None and not (volumes.get(v.volume_id) and volumes[v.volume_id].online):
            return False
        return os.path.exists(v.path)

    videos = db.videos([VideoStatus.ACTIVE, VideoStatus.MARKED])
    online = {v.id for v in videos if is_online(v)}
    # 연결되지 않은 파일은 스캔 때 적어 둔 기록이 있어야 비교할 수 있다
    videos = [v for v in videos if v.id in online or v.quick_hash or v.fingerprint]
    by_id = {v.id: v for v in videos}
    sets = _Sets()
    exact_of: dict[int, int] = {}       # 파일 → 내용이 같은 묶음 번호
    unverified: set[int] = set()        # 전체 대조를 하지 못한 묶음

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
            if v.id not in online:
                continue
            try:
                v.quick_hash = quick_hash(v.path)
            except OSError:
                continue
            db.set_hashes(v.id, quick_hash=v.quick_hash)
        by_quick[(v.size, v.quick_hash)].append(v)
    quick_groups = [g for g in by_quick.values() if len(g) > 1]

    # 2단계: 연결된 파일끼리는 전체 해시로 확정
    to_hash = [v for g in quick_groups if sum(m.id in online for m in g) > 1 for v in g if v.id in online]
    for i, v in enumerate(to_hash, 1):
        check_cancel()
        if progress:
            progress("정밀 비교", i, len(to_hash))
        if v.full_hash is None:
            try:
                v.full_hash = full_hash(v.path, cancel)
            except OSError:
                continue
            db.set_hashes(v.id, full_hash=v.full_hash)

    for g in quick_groups:
        on = [v for v in g if v.id in online]
        off = [v for v in g if v.id not in online]
        if len(on) > 1:
            by_full: dict[str, list[Video]] = defaultdict(list)
            for v in on:
                if v.full_hash:
                    by_full[v.full_hash].append(v)
            for v in off:  # 예전에 전체 해시까지 구해 둔 파일은 그 값으로, 아니면 하나뿐인 묶음에 넣는다
                if v.full_hash in by_full:
                    by_full[v.full_hash].append(v)
                elif not v.full_hash and len(by_full) == 1:
                    next(iter(by_full.values())).append(v)
            clusters = [c for c in by_full.values() if len(c) > 1]
        else:
            clusters = [g]
        for c in clusters:
            root = c[0].id
            for v in c:
                sets.union(root, v.id)
                exact_of[v.id] = root
            if any(v.id not in online for v in c):
                unverified.add(root)

    # 3단계: 장면 지문
    if similar:
        # 빠른 거르기: 장면 값 10개를 큰 수 하나로 이어 붙여 한 번에 비교한다. 같은 영상이면 다른 칸의 합이
        # 일정 수(limit)를 넘을 수 없으므로, 넘는 쌍은 장면별로 따져 볼 필요가 없다 (대부분이 여기서 걸러진다).
        need = math.ceil(fp.FRAMES * fp.MATCH_RATIO)
        limit = need * fp.MAX_DISTANCE + (fp.FRAMES - need) * 64
        marks = []
        for v in videos:
            hashes = fp.decode(v.fingerprint) if v.duration else None
            if hashes and sum(h is not None for h in hashes) >= fp.MIN_FRAMES:
                packed = 0
                for h in hashes:
                    packed = (packed << 64) | (h or 0)
                blank = sum(h is None for h in hashes)
                marks.append((v.duration, v.id, hashes, packed, limit + 64 * blank))
        marks.sort(key=lambda m: m[0])
        tolerance = fp.DURATION_TOLERANCE
        for i, (d1, id1, h1, p1, lim1) in enumerate(marks):
            if i % 200 == 0:
                check_cancel()
                if progress:
                    progress("장면 비교", i + 1, len(marks))
            for j in range(i + 1, len(marks)):
                d2, id2, h2, p2, lim2 = marks[j]
                if d2 - d1 > tolerance:
                    break
                if (p1 ^ p2).bit_count() > lim1 + lim2 - limit:
                    continue
                if sets.find(id1) != sets.find(id2) and fp.matches(h1, h2):
                    sets.union(id1, id2)

    members: dict[int, list[Video]] = defaultdict(list)
    for vid in sets.parent:
        members[sets.find(vid)].append(by_id[vid])
    groups = []
    for vs in members.values():
        if len(vs) < 2:
            continue
        vs.sort(key=lambda v: v.path)
        roots = {exact_of.get(v.id) for v in vs}
        away = frozenset(v.id for v in vs if v.id not in online)
        if len(roots) == 1 and None not in roots:
            groups.append(DuplicateGroup(vs[0].full_hash or "", vs[0].size, vs, "exact",
                                         verified=next(iter(roots)) not in unverified, offline_ids=away))
        else:
            groups.append(DuplicateGroup("", max(v.size for v in vs), vs, "similar", verified=False, offline_ids=away))
    groups.sort(key=lambda g: g.wasted_bytes, reverse=True)
    return groups
