"""중복 파일 정리: 표시 → 격리 폴더 이동 → (확인 후) 영구 삭제.

어떤 단계에서도 '마지막 남은 사본'은 지워지지 않도록 삭제 직전에
남겨 둘 사본의 존재와 내용 일치를 다시 확인한다.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .db import LibraryDB, Video, VideoStatus
from .duplicates import Cancelled, full_hash
from .scanner import QUARANTINE_DIRNAME

CancelFn = Callable[[], bool]
ProgressFn = Callable[[int, int], None]


@dataclass
class OpResult:
    done: list[int] = field(default_factory=list)
    errors: list[tuple[Video, str]] = field(default_factory=list)


@dataclass
class PurgeCheck:
    video: Video
    ok: bool
    reason: str = ""
    kept_copy: str | None = None


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(1, 10_000):
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"사용 가능한 파일 이름을 찾지 못했습니다: {path}")


class Organizer:
    def __init__(self, db: LibraryDB):
        self.db = db

    def quarantine_dir_for(self, video: Video) -> Path:
        root = self.db.folder_path(video.folder_id) if video.folder_id else None
        if root and Path(video.path).is_relative_to(root):
            return Path(root) / QUARANTINE_DIRNAME
        return Path(video.path).parent / QUARANTINE_DIRNAME

    # ---- 표시 ----
    def mark(self, ids: list[int]) -> OpResult:
        res = OpResult()
        for vid in ids:
            v = self.db.get(vid)
            if v is None:
                continue
            if v.status != VideoStatus.ACTIVE:
                res.errors.append((v, "이미 정리 대상입니다"))
                continue
            self.db.set_status(vid, VideoStatus.MARKED)
            res.done.append(vid)
        return res

    def unmark(self, ids: list[int]) -> OpResult:
        res = OpResult()
        for vid in ids:
            v = self.db.get(vid)
            if v is None:
                continue
            if v.status == VideoStatus.QUARANTINED:
                res.errors.append((v, "격리된 파일은 '복원'을 사용하세요"))
                continue
            self.db.set_status(vid, VideoStatus.ACTIVE)
            res.done.append(vid)
        return res

    # ---- 격리 ----
    def quarantine(self, ids: list[int]) -> OpResult:
        res = OpResult()
        for vid in ids:
            v = self.db.get(vid)
            if v is None:
                continue
            if v.status == VideoStatus.QUARANTINED:
                res.errors.append((v, "이미 격리되어 있습니다"))
                continue
            src = Path(v.path)
            if not src.exists():
                res.errors.append((v, "파일이 없습니다"))
                continue
            qdir = self.quarantine_dir_for(v)
            root = qdir.parent
            rel = src.relative_to(root) if src.is_relative_to(root) else Path(src.name)
            try:
                dst = _unique_path(qdir / rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
            except OSError as e:
                res.errors.append((v, str(e)))
                continue
            self.db.set_status(vid, VideoStatus.QUARANTINED, path=str(dst), original_path=str(src))
            res.done.append(vid)
        return res

    def restore(self, ids: list[int]) -> OpResult:
        res = OpResult()
        for vid in ids:
            v = self.db.get(vid)
            if v is None:
                continue
            if v.status != VideoStatus.QUARANTINED or not v.original_path:
                # 표시만 된 항목은 표시 해제가 곧 복원
                if v.status == VideoStatus.MARKED:
                    self.db.set_status(vid, VideoStatus.ACTIVE)
                    res.done.append(vid)
                continue
            src, dst = Path(v.path), Path(v.original_path)
            if dst.exists():
                res.errors.append((v, f"원래 위치에 같은 이름의 파일이 있습니다: {dst}"))
                continue
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
            except OSError as e:
                res.errors.append((v, str(e)))
                continue
            self.db.set_status(vid, VideoStatus.ACTIVE, path=str(dst), clear_original=True)
            res.done.append(vid)
            self._remove_empty_dirs(src.parent)
        return res

    # ---- 영구 삭제 ----
    def check_purge(self, ids: list[int], verify_content: bool = True,
                    progress: ProgressFn | None = None,
                    cancel: CancelFn | None = None) -> list[PurgeCheck]:
        """삭제 전 점검. 남겨 둘 '정상' 사본이 존재하고 내용이 같은 경우만 ok."""
        checks: list[PurgeCheck] = []
        targets = set(ids)
        hash_cache: dict[str, str] = {}

        def current_hash(path: str) -> str:
            if path not in hash_cache:
                hash_cache[path] = full_hash(path, cancel)
            return hash_cache[path]

        for i, vid in enumerate(ids, 1):
            if cancel and cancel():
                raise Cancelled
            if progress:
                progress(i, len(ids))
            v = self.db.get(vid)
            if v is None:
                continue
            if v.status == VideoStatus.ACTIVE:
                checks.append(PurgeCheck(v, False, "정리 대상으로 표시되지 않았습니다"))
                continue
            if not os.path.exists(v.path):
                checks.append(PurgeCheck(v, False, "파일이 없습니다"))
                continue
            if not v.full_hash:
                v.full_hash = current_hash(v.path)
                self.db.set_hashes(v.id, full_hash=v.full_hash)

            keepers = [k for k in self.db.videos([VideoStatus.ACTIVE])
                       if k.full_hash == v.full_hash and k.id not in targets
                       and k.size == v.size and os.path.exists(k.path)]
            if not keepers:
                checks.append(PurgeCheck(v, False, "남아 있는 사본이 없습니다 (마지막 사본)"))
                continue
            keeper = keepers[0]
            if verify_content:
                try:
                    if current_hash(v.path) != current_hash(keeper.path):
                        checks.append(PurgeCheck(v, False, "남길 사본과 내용이 달라졌습니다", keeper.path))
                        continue
                except OSError as e:
                    checks.append(PurgeCheck(v, False, f"확인 중 오류: {e}", keeper.path))
                    continue
            checks.append(PurgeCheck(v, True, "", keeper.path))
        return checks

    def purge(self, checks: list[PurgeCheck]) -> OpResult:
        """check_purge 에서 ok 로 확인된 항목만 실제로 삭제한다."""
        res = OpResult()
        for c in checks:
            if not c.ok:
                continue
            v = c.video
            try:
                os.remove(v.path)
            except FileNotFoundError:
                pass
            except OSError as e:
                res.errors.append((v, str(e)))
                continue
            self.db.delete_video(v.id)
            self.db.clear_position(v.path)
            res.done.append(v.id)
            if v.status == VideoStatus.QUARANTINED:
                self._remove_empty_dirs(Path(v.path).parent)
        return res

    @staticmethod
    def _remove_empty_dirs(start: Path) -> None:
        """격리 폴더 안에서 비게 된 하위 폴더를 정리 (격리 폴더 자체까지)."""
        d = start
        while QUARANTINE_DIRNAME in d.parts:
            try:
                d.rmdir()
            except OSError:
                return
            if d.name == QUARANTINE_DIRNAME:
                return
            d = d.parent
