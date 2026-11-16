"""동영상 라이브러리: 폴더 스캔, 중복 탐지, 격리/삭제 관리 (GUI 비의존)."""

from .db import LibraryDB, Video, VideoStatus, Volume
from .duplicates import DuplicateGroup, find_duplicates, suggest_keep
from .organizer import Organizer, PurgeCheck
from .scanner import VIDEO_EXTENSIONS, build_catalog, scan_folder

__all__ = [
    "LibraryDB", "Video", "VideoStatus", "Volume",
    "DuplicateGroup", "find_duplicates", "suggest_keep",
    "Organizer", "PurgeCheck",
    "VIDEO_EXTENSIONS", "build_catalog", "scan_folder",
]
