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
from .duplicates import Cancelled, full_hash, quick_hash
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
    kind: str = "exact"            # "exact": 내용이 같은 사본이 남음 / "similar": 장면이 같은 다른 파일이 남음
    offline_keeper: bool = False   # 남길 사본이 지금 연결되지 않은 드라이브에 있어 다시 확인하지 못함


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
    def _label(self, v: Video) -> str:
        """어느 드라이브의 어느 파일인지 (연결되지 않은 드라이브의 파일을 가리킬 때)."""
        volume = self.db.volume(v.volume_id)
        return f"드라이브 '{volume.name}' 의 {v.rel_path}" if volume and v.rel_path is not None else v.path

    def mark(self, ids: list[int], keep: dict[int, int] | None = None) -> OpResult:
        """keep: 정리 대상 → 남기기로 한 사본 (중복 정리 화면에서 고른 것). 영구 삭제 전에 그 사본을 확인한다."""
        res = OpResult()
        for vid, kid in (keep or {}).items():
            if vid in ids:
                self.db.set_keep(vid, kid)
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
    def quarantine(self, ids: list[int], keep: dict[int, int] | None = None) -> OpResult:
        res = OpResult()
        for vid, kid in (keep or {}).items():
            if vid in ids:
                self.db.set_keep(vid, kid)
        for vid in ids:
            v = self.db.get(vid)
            if v is None:
                continue
            if v.status == VideoStatus.QUARANTINED:
                res.errors.append((v, "이미 격리되어 있습니다"))
                continue
            if not self.db.is_online(v):
                res.errors.append((v, "드라이브가 연결되어 있지 않습니다 (연결한 뒤 다시 해 주세요)"))
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
            if not self.db.is_online(v):
                res.errors.append((v, "드라이브가 연결되어 있지 않습니다 (연결한 뒤 다시 해 주세요)"))
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
        """삭제 전 점검. 남겨 둘 '정상' 사본이 있어야 ok.

        - 내용이 같은 사본이 연결되어 있으면: 두 파일 전체를 다시 대조한다.
        - 장면이 같은 영상(화질·형식이 다른 사본)을 남기기로 했으면: 그 파일이 그대로 있는지 본다.
        - 남길 사본이 연결되지 않은 드라이브에만 있으면: ok 이지만 offline_keeper 로 표시한다.
          (지금 확인할 수 없으므로, 사용자가 따로 동의해야만 지운다)
        """
        self.db.refresh_volumes()
        checks: list[PurgeCheck] = []
        targets = set(ids)
        hash_cache: dict[str, str] = {}
        by_size: dict[int, list[Video]] = {}
        for k in self.db.videos([VideoStatus.ACTIVE]):
            if k.id not in targets:
                by_size.setdefault(k.size, []).append(k)

        def current_hash(path: str) -> str:
            if path not in hash_cache:
                hash_cache[path] = full_hash(path, cancel)
            return hash_cache[path]

        def present(k: Video) -> bool:
            return self.db.is_online(k) and os.path.exists(k.path)

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
            if not self.db.is_online(v):
                checks.append(PurgeCheck(v, False, "드라이브가 연결되어 있지 않습니다"))
                continue
            if not os.path.exists(v.path):
                checks.append(PurgeCheck(v, False, "파일이 없습니다"))
                continue
            try:
                if not v.quick_hash:
                    v.quick_hash = quick_hash(v.path)
                    self.db.set_hashes(v.id, quick_hash=v.quick_hash)
            except OSError as e:
                checks.append(PurgeCheck(v, False, f"확인 중 오류: {e}"))
                continue

            same = [k for k in by_size.get(v.size, [])
                    if (k.full_hash == v.full_hash if k.full_hash and v.full_hash else k.quick_hash == v.quick_hash)]
            near = self.db.get(v.keep_id) if v.keep_id else None
            if near is not None and (near.status != VideoStatus.ACTIVE or near.id in targets or near in same):
                near = None

            keeper = next((k for k in same if present(k)), None)
            if keeper is not None:
                if verify_content:
                    try:
                        if current_hash(v.path) != current_hash(keeper.path):
                            checks.append(PurgeCheck(v, False, "남길 사본과 내용이 달라졌습니다", keeper.path))
                            continue
                    except OSError as e:
                        checks.append(PurgeCheck(v, False, f"확인 중 오류: {e}", keeper.path))
                        continue
                checks.append(PurgeCheck(v, True, "", keeper.path))
                continue
            if near is not None and present(near):
                try:
                    unchanged = os.path.getsize(near.path) == near.size
                except OSError:
                    unchanged = False
                if unchanged:
                    checks.append(PurgeCheck(v, True, "", near.path, kind="similar"))
                else:
                    checks.append(PurgeCheck(v, False, "남기기로 한 파일이 바뀌었습니다 (다시 스캔해 주세요)", near.path))
                continue
            away = same[0] if same else near
            if away is not None:
                checks.append(PurgeCheck(v, True, "", self._label(away), kind="exact" if same else "similar",
                                         offline_keeper=True))
                continue
            checks.append(PurgeCheck(v, False, "남아 있는 사본이 없습니다 (마지막 사본)"))
        return checks

    def purge(self, checks: list[PurgeCheck], allow_offline_keeper: bool = False) -> OpResult:
        """check_purge 에서 ok 로 확인된 항목만 실제로 삭제한다.

        남길 사본이 연결되지 않은 드라이브에 있는 항목은 allow_offline_keeper 일 때만 지운다."""
        res = OpResult()
        for c in checks:
            if not c.ok or (c.offline_keeper and not allow_offline_keeper):
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
