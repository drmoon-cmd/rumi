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

ProgressFn = Callable[[int, str], None]   # (처리한 개수, 현재 경로)
CancelFn = Callable[[], bool]


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

    seen: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != QUARANTINE_DIRNAME and not d.startswith(".")]
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
            db.upsert_video(full, folder_id, st.st_size, st.st_mtime)
            seen.add(full)
            result.found += 1
            if progress:
                progress(result.found, full)

    # 사라진 파일 정리 (표시/격리된 항목은 기록 보존)
    for v in db.videos([VideoStatus.ACTIVE], folder_id=folder_id):
        if v.path not in seen and not os.path.exists(v.path):
            db.delete_video(v.id)
            result.removed += 1
    return result
