"""SQLite 기반 동영상 라이브러리 저장소."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable


class VideoStatus(str, Enum):
    ACTIVE = "active"            # 정상
    MARKED = "marked"            # 중복으로 표시만 됨 (파일 위치 그대로)
    QUARANTINED = "quarantined"  # 격리 폴더로 이동됨


@dataclass
class Video:
    id: int
    path: str
    folder_id: int | None
    size: int
    mtime: float
    quick_hash: str | None
    full_hash: str | None
    status: VideoStatus
    original_path: str | None
    status_changed_at: float | None

    @property
    def name(self) -> str:
        return Path(self.path).name

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Video":
        return cls(
            id=row["id"], path=row["path"], folder_id=row["folder_id"],
            size=row["size"], mtime=row["mtime"],
            quick_hash=row["quick_hash"], full_hash=row["full_hash"],
            status=VideoStatus(row["status"]),
            original_path=row["original_path"],
            status_changed_at=row["status_changed_at"],
        )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    id   INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS videos (
    id                INTEGER PRIMARY KEY,
    path              TEXT NOT NULL UNIQUE,
    folder_id         INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    size              INTEGER NOT NULL,
    mtime             REAL NOT NULL,
    quick_hash        TEXT,
    full_hash         TEXT,
    status            TEXT NOT NULL DEFAULT 'active',
    original_path     TEXT,
    status_changed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_videos_size ON videos(size);
CREATE INDEX IF NOT EXISTS idx_videos_full_hash ON videos(full_hash);
CREATE TABLE IF NOT EXISTS playback (
    path       TEXT PRIMARY KEY,
    position   REAL NOT NULL,
    duration   REAL,
    updated_at REAL NOT NULL
);
"""


class LibraryDB:
    """스레드 간 공유 가능한 얇은 SQLite 래퍼."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, params: Iterable = ()) -> sqlite3.Cursor:
        with self._lock, self._conn:
            return self._conn.execute(sql, tuple(params))

    def _query(self, sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    # ---- 폴더 ----
    def add_folder(self, path: str | Path) -> int:
        p = str(Path(path).resolve())
        self._exec("INSERT OR IGNORE INTO folders(path) VALUES (?)", (p,))
        return self._query("SELECT id FROM folders WHERE path = ?", (p,))[0]["id"]

    def remove_folder(self, folder_id: int) -> None:
        """폴더를 라이브러리에서 제외 (파일은 건드리지 않음). 격리된 항목은 기록을 유지."""
        self._exec(
            "DELETE FROM videos WHERE folder_id = ? AND status = 'active'", (folder_id,))
        self._exec("DELETE FROM folders WHERE id = ?", (folder_id,))

    def folders(self) -> list[tuple[int, str]]:
        return [(r["id"], r["path"]) for r in self._query("SELECT id, path FROM folders ORDER BY path")]

    def folder_path(self, folder_id: int) -> str | None:
        rows = self._query("SELECT path FROM folders WHERE id = ?", (folder_id,))
        return rows[0]["path"] if rows else None

    # ---- 동영상 ----
    def upsert_video(self, path: str, folder_id: int | None, size: int, mtime: float) -> int:
        """새 파일이면 추가, 크기/수정시각이 바뀌었으면 해시를 초기화."""
        rows = self._query("SELECT id, size, mtime FROM videos WHERE path = ?", (path,))
        if not rows:
            cur = self._exec(
                "INSERT INTO videos(path, folder_id, size, mtime) VALUES (?, ?, ?, ?)",
                (path, folder_id, size, mtime))
            return cur.lastrowid
        row = rows[0]
        if row["size"] != size or row["mtime"] != mtime:
            self._exec(
                "UPDATE videos SET size=?, mtime=?, quick_hash=NULL, full_hash=NULL, folder_id=? WHERE id=?",
                (size, mtime, folder_id, row["id"]))
        else:
            self._exec("UPDATE videos SET folder_id=? WHERE id=?", (folder_id, row["id"]))
        return row["id"]

    def get(self, video_id: int) -> Video | None:
        rows = self._query("SELECT * FROM videos WHERE id = ?", (video_id,))
        return Video.from_row(rows[0]) if rows else None

    def videos(self, statuses: Iterable[VideoStatus] | None = None,
               folder_id: int | None = None) -> list[Video]:
        sql, params = "SELECT * FROM videos WHERE 1=1", []
        if statuses is not None:
            st = [s.value for s in statuses]
            sql += f" AND status IN ({','.join('?' * len(st))})"
            params += st
        if folder_id is not None:
            sql += " AND folder_id = ?"
            params.append(folder_id)
        return [Video.from_row(r) for r in self._query(sql + " ORDER BY path", params)]

    def set_hashes(self, video_id: int, quick_hash: str | None = None,
                   full_hash: str | None = None) -> None:
        if quick_hash is not None:
            self._exec("UPDATE videos SET quick_hash=? WHERE id=?", (quick_hash, video_id))
        if full_hash is not None:
            self._exec("UPDATE videos SET full_hash=? WHERE id=?", (full_hash, video_id))

    def set_status(self, video_id: int, status: VideoStatus, *, path: str | None = None,
                   original_path: str | None = None, clear_original: bool = False) -> None:
        sets, params = ["status=?", "status_changed_at=?"], [status.value, time.time()]
        if path is not None:
            sets.append("path=?")
            params.append(path)
        if original_path is not None:
            sets.append("original_path=?")
            params.append(original_path)
        elif clear_original:
            sets.append("original_path=NULL")
        params.append(video_id)
        self._exec(f"UPDATE videos SET {', '.join(sets)} WHERE id=?", params)

    def delete_video(self, video_id: int) -> None:
        self._exec("DELETE FROM videos WHERE id=?", (video_id,))

    # ---- 이어보기 ----
    def save_position(self, path: str, position: float, duration: float | None) -> None:
        self._exec(
            "INSERT INTO playback(path, position, duration, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET position=excluded.position, "
            "duration=excluded.duration, updated_at=excluded.updated_at",
            (path, position, duration, time.time()))

    def load_position(self, path: str) -> float | None:
        rows = self._query("SELECT position FROM playback WHERE path=?", (path,))
        return rows[0]["position"] if rows else None

    def clear_position(self, path: str) -> None:
        self._exec("DELETE FROM playback WHERE path=?", (path,))
