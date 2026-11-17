"""라이브러리 폴더를 훑어 동영상 파일을 DB에 반영."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .db import LibraryDB, VideoStatus

VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".m4v", ".mkv", ".webm", ".avi", ".mov", ".wmv", ".flv", ".f4v",
    ".mpg", ".mpeg", ".m2ts", ".mts", ".ts", ".vob", ".3gp", ".3g2", ".ogv",
    ".rm", ".rmvb", ".asf", ".divx", ".dv",
})

# 격리 파일이 놓이는 폴더 이름. 스캔 대상에서 제외된다.
QUARANTINE_DIRNAME = ".rumi_quarantine"
# 드라이브를 통째로 스캔할 때 들어가지 않는 시스템 폴더
SKIP_DIRS = frozenset({"$recycle.bin", "system volume information", "recycler", "lost+found",
                       "windows", "program files", "program files (x86)", "programdata"})

ProgressFn = Callable[[int, str], None]   # (처리한 개수, 현재 경로)
CancelFn = Callable[[], bool]
IndexProgressFn = Callable[[int, int, str], None]  # (현재, 전체, 경로)


@dataclass
class ScanResult:
    found: int = 0
    removed: int = 0
    cancelled: bool = False


def is_video(path: str | Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def scan_folder(db: LibraryDB, folder_id: int, progress: ProgressFn | None = None,
                cancel: CancelFn | None = None) -> ScanResult:
    root = db.folder_path(folder_id)
    result = ScanResult()
    if root is None or not os.path.isdir(root):
        return result

    where, known = db.scan_snapshot(folder_id)
    seen: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != QUARANTINE_DIRNAME and not d.startswith(".")
                       and not (dirpath == root and d.lower() in SKIP_DIRS)]
        with db.batch():  # 폴더 하나씩 묶어 저장
            for name in filenames:
                if cancel and cancel():
                    result.cancelled = True
                    return result
                if not is_video(name):
                    continue
                full = os.path.join(dirpath, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if known.get(full) != (st.st_size, st.st_mtime):  # 새 파일이거나 바뀐 파일만 적는다
                    db.upsert_video(full, folder_id, st.st_size, st.st_mtime, where)
                seen.add(full)
                result.found += 1
                if progress and (result.found % 50 == 0 or result.found < 50):
                    progress(result.found, full)

    # 사라진 파일 정리 (표시/격리된 항목은 기록 보존)
    for v in db.videos([VideoStatus.ACTIVE], folder_id=folder_id):
        if v.path not in seen and not os.path.exists(v.path):
            db.delete_video(v.id)
            result.removed += 1
    db.mark_scanned(folder_id)
    return result


def build_catalog(db: LibraryDB, folder_ids: list[int] | None = None, progress: IndexProgressFn | None = None,
                  cancel: CancelFn | None = None) -> int:
    """드라이브를 뽑은 뒤에도 비교할 수 있도록 파일마다 내용 요약(앞·중간·끝)과 장면 지문을 적어 둔다.

    이미 해 둔 파일은 건너뛴다. 도중에 취소해도 한 데까지는 남는다. 돌려주는 값: 이번에 처리한 파일 수.
    """
    from . import fingerprint as fp
    from ..tools import find_ffmpeg
    from .duplicates import quick_hash

    ffmpeg = find_ffmpeg()
    todo = [v for v in db.videos_missing_info(folder_ids) if os.path.exists(v.path)]
    done = 0
    for i, v in enumerate(todo, 1):
        if cancel and cancel():
            break
        if progress:
            progress(i, len(todo), v.path)
        try:
            if v.quick_hash is None and v.size > 0:
                db.set_hashes(v.id, quick_hash=quick_hash(v.path))
        except OSError:
            continue  # 지금 읽을 수 없는 파일: 다음 스캔 때 다시
        info = fp.probe(v.path, ffmpeg) if ffmpeg else fp.MediaInfo()
        mark = fp.fingerprint(v.path, info.duration, ffmpeg) if info.duration > 0 else None
        if not os.path.exists(v.path):
            continue  # 도중에 드라이브가 뽑힘
        db.set_media_info(v.id, info.duration, info.width, info.height, mark)
        done += 1
    return done
