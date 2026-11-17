"""SQLite 기반 동영상 라이브러리 저장소."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

from . import volumes as vol


class VideoStatus(str, Enum):
    ACTIVE = "active"            # 정상
    MARKED = "marked"            # 중복으로 표시만 됨 (파일 위치 그대로)
    QUARANTINED = "quarantined"  # 격리 폴더로 이동됨


@dataclass
class Volume:
    """한 번이라도 스캔한 드라이브. 지금 연결되어 있지 않아도 기록은 남는다."""
    id: int
    serial: str
    label: str            # 드라이브에 붙어 있던 이름
    alias: str            # Rumi 에서 붙인 이름 (없으면 빈 문자열)
    mount: str | None     # 지금 연결된 위치 ("E:\\"). 연결되어 있지 않으면 None
    last_mount: str       # 마지막으로 연결됐던 위치
    last_seen: float | None

    @property
    def online(self) -> bool:
        return self.mount is not None

    @property
    def name(self) -> str:
        """화면에 보일 이름. 따로 붙인 이름이 없으면 드라이브 이름 + 고유 번호 앞자리
        (같은 이름의 외장 하드가 여러 개여도 구분되도록)."""
        if self.alias:
            return self.alias
        where = (self.mount or self.last_mount or "").rstrip("\\/")
        if self.serial.startswith("path:"):
            return where or self.serial[5:]
        label = self.label or "드라이브"
        return f"{label} ({where}) #{self.serial[:4]}" if self.online and where else f"{label} #{self.serial}"


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
    volume_id: int | None = None
    rel_path: str | None = None      # 드라이브 안에서의 경로
    duration: float | None = None    # 재생 길이 (초)
    width: int | None = None
    height: int | None = None
    fingerprint: str | None = None   # 장면 지문 (rumi.library.fingerprint)
    keep_id: int | None = None       # 정리 대상으로 표시할 때 '남기기로 한 사본'
    info_tried: bool = False         # 길이·지문을 만들어 봤는가

    @property
    def name(self) -> str:
        return self.path.replace("\\", "/").rsplit("/", 1)[-1]

    @property
    def folder(self) -> str:
        """들어 있는 폴더 (드라이브 안에서의 경로를 알면 그것으로)."""
        if self.rel_path is not None:
            return os.path.dirname(self.rel_path) or os.sep
        return os.path.dirname(self.path)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Video":
        return cls(
            id=row["id"], path=row["path"], folder_id=row["folder_id"],
            size=row["size"], mtime=row["mtime"],
            quick_hash=row["quick_hash"], full_hash=row["full_hash"],
            status=VideoStatus(row["status"]),
            original_path=row["original_path"],
            status_changed_at=row["status_changed_at"],
            volume_id=row["volume_id"], rel_path=row["rel_path"], duration=row["duration"],
            width=row["width"], height=row["height"], fingerprint=row["fingerprint"],
            keep_id=row["keep_id"], info_tried=bool(row["info_tried"]),
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
CREATE TABLE IF NOT EXISTS volumes (
    id         INTEGER PRIMARY KEY,
    serial     TEXT NOT NULL UNIQUE,
    label      TEXT NOT NULL DEFAULT '',
    alias      TEXT NOT NULL DEFAULT '',
    last_mount TEXT NOT NULL DEFAULT '',
    last_seen  REAL
);
CREATE TABLE IF NOT EXISTS thumbs (
    video_id INTEGER NOT NULL,
    idx      INTEGER NOT NULL,
    jpeg     BLOB NOT NULL,
    PRIMARY KEY (video_id, idx)
);
CREATE TABLE IF NOT EXISTS bookmarks (
    id       INTEGER PRIMARY KEY,
    path     TEXT NOT NULL,
    position REAL NOT NULL,
    name     TEXT NOT NULL DEFAULT '',
    created  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bookmarks_path ON bookmarks(path);
CREATE TABLE IF NOT EXISTS playback (
    path       TEXT PRIMARY KEY,
    position   REAL NOT NULL,
    duration   REAL,
    updated_at REAL NOT NULL
);
"""


# 예전 판에서 만든 저장소에 더할 칸 (표, 칸, 종류)
_NEW_COLUMNS = [
    ("folders", "volume_id", "INTEGER"), ("folders", "rel_path", "TEXT"), ("folders", "scanned_at", "REAL"),
    ("videos", "volume_id", "INTEGER"), ("videos", "rel_path", "TEXT"), ("videos", "original_rel", "TEXT"),
    ("videos", "duration", "REAL"), ("videos", "width", "INTEGER"), ("videos", "height", "INTEGER"),
    ("videos", "fingerprint", "TEXT"), ("videos", "info_tried", "INTEGER NOT NULL DEFAULT 0"),
    ("videos", "keep_id", "INTEGER"), ("videos", "thumbs_tried", "INTEGER NOT NULL DEFAULT 0"),
]


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
        for table, column, kind in _NEW_COLUMNS:
            have = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            if column not in have:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_volume ON videos(volume_id)")
        self._conn.commit()
        self._lock = threading.RLock()
        self._batch = 0
        self._mounts: dict[str, vol.Mount] = {}   # 지금 연결된 드라이브 (고유 번호 → 위치)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, params: Iterable = ()) -> sqlite3.Cursor:
        with self._lock:
            if self._batch:
                return self._conn.execute(sql, tuple(params))
            with self._conn:
                return self._conn.execute(sql, tuple(params))

    @contextmanager
    def batch(self):
        """여러 변경을 한 번에 저장한다 (파일마다 디스크에 쓰지 않도록). 짧게 묶어 쓸 것:
        묶는 동안 다른 스레드(화면)는 저장소를 쓰지 못하고 기다린다."""
        with self._lock:
            self._batch += 1
            try:
                yield
            finally:
                self._batch -= 1
                if not self._batch:
                    self._conn.commit()

    def _query(self, sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    # ---- 드라이브 ----
    def refresh_volumes(self) -> None:
        """지금 연결된 드라이브를 다시 살펴, 저장된 파일 위치를 현재 드라이브 문자에 맞춘다.

        연결되지 않은 드라이브의 파일은 실제 경로와 겹치지 않는 표시용 경로(<고유번호>\\…)로 바꿔 둔다.
        다른 하드가 같은 드라이브 문자를 쓰더라도 엉뚱한 파일을 가리키지 않게 하기 위해서다.
        """
        mounts = {m.serial: m for m in vol.current_mounts()}
        with self._lock:
            self._mounts = mounts
            self._adopt_unassigned()
            rows = self._query("SELECT * FROM volumes")
            # 연결이 끊긴 드라이브부터 처리해 드라이브 문자를 비워 준다
            for row in sorted(rows, key=lambda r: r["serial"] in mounts):
                m = mounts.get(row["serial"])
                if m is None:
                    # 고유 번호를 못 읽는 환경(마운트 위치로 구분): 위치가 그대로 있으면 연결된 것으로 본다
                    if row["serial"].startswith("path:") and os.path.isdir(row["serial"][5:]):
                        root = row["serial"][5:]
                        m = vol.Mount(row["serial"], root if root.endswith(os.sep) else root + os.sep, row["label"])
                        self._mounts[m.serial] = m
                if m is None:
                    self._repath(row["id"], vol.offline_path(row["serial"], ""))
                else:
                    self._repath(row["id"], m.root)
                    self._exec("UPDATE volumes SET label=?, last_mount=?, last_seen=? WHERE id=?",
                               (m.label, m.root, time.time(), row["id"]))

    def _repath(self, volume_id: int, root: str) -> None:
        """이 드라이브의 폴더·파일 경로를 root 기준으로 다시 적는다 (이미 맞으면 건드리지 않는다)."""
        with self._lock, self._conn:
            c = self._conn
            for table in ("videos", "folders"):
                c.execute(f"UPDATE OR IGNORE {table} SET path = ? || rel_path "
                          f"WHERE volume_id = ? AND rel_path IS NOT NULL AND path != ? || rel_path",
                          (root, volume_id, root))
            c.execute("UPDATE videos SET original_path = ? || original_rel "
                      "WHERE volume_id = ? AND original_rel IS NOT NULL", (root, volume_id))
            # 드라이브 맨 위를 폴더로 등록한 경우 끝의 구분자를 뗀 모양으로 맞춘다 ("E:\\" 는 그대로)
            if not vol.is_offline_path(root):
                for r in c.execute("SELECT id FROM folders WHERE volume_id=? AND rel_path=''",
                                   (volume_id,)).fetchall():
                    c.execute("UPDATE OR IGNORE folders SET path=? WHERE id=?",
                              (str(Path(root)), r["id"]))

    def _adopt_unassigned(self) -> None:
        """예전 판에서 등록한 폴더: 지금 연결되어 있으면 어느 드라이브인지 적어 둔다."""
        for row in self._query("SELECT id, path FROM folders WHERE volume_id IS NULL"):
            if os.path.isdir(row["path"]):
                self._assign_folder(row["id"], row["path"])

    def _assign_folder(self, folder_id: int, path: str) -> int | None:
        m = vol.mount_of(path)
        if m is None:
            return None
        self._mounts[m.serial] = m
        volume_id = self._volume_id(m)
        self._exec("UPDATE folders SET volume_id=?, rel_path=? WHERE id=?",
                   (volume_id, vol.relative_to_root(path, m.root), folder_id))
        with self._lock, self._conn:
            for r in self._conn.execute(
                    "SELECT id, path, original_path FROM videos WHERE folder_id=? AND volume_id IS NULL",
                    (folder_id,)).fetchall():
                orig = r["original_path"]
                self._conn.execute(
                    "UPDATE videos SET volume_id=?, rel_path=?, original_rel=? WHERE id=?",
                    (volume_id, vol.relative_to_root(r["path"], m.root),
                     vol.relative_to_root(orig, m.root) if orig else None, r["id"]))
        return volume_id

    def _volume_id(self, m: vol.Mount) -> int:
        self._exec("INSERT OR IGNORE INTO volumes(serial, label, last_mount, last_seen) VALUES (?, ?, ?, ?)",
                   (m.serial, m.label, m.root, time.time()))
        return self._query("SELECT id FROM volumes WHERE serial=?", (m.serial,))[0]["id"]

    def _volume_from_row(self, row: sqlite3.Row) -> Volume:
        m = self._mounts.get(row["serial"])
        return Volume(row["id"], row["serial"], row["label"], row["alias"], m.root if m else None,
                      row["last_mount"], row["last_seen"])

    def volumes(self) -> dict[int, Volume]:
        return {r["id"]: self._volume_from_row(r) for r in self._query("SELECT * FROM volumes")}

    def volume(self, volume_id: int | None) -> Volume | None:
        if volume_id is None:
            return None
        rows = self._query("SELECT * FROM volumes WHERE id=?", (volume_id,))
        return self._volume_from_row(rows[0]) if rows else None

    def rename_volume(self, volume_id: int, alias: str) -> None:
        self._exec("UPDATE volumes SET alias=? WHERE id=?", (alias.strip(), volume_id))

    def is_online(self, video: "Video") -> bool:
        """이 파일이 있는 드라이브가 지금 연결되어 있는가 (드라이브를 모르는 예전 기록은 경로로 판단)."""
        if vol.is_offline_path(video.path):
            return False
        if video.volume_id is None:
            return True
        v = self.volume(video.volume_id)
        return v is not None and v.online

    # ---- 폴더 ----
    def add_folder(self, path: str | Path) -> int:
        p = str(Path(path).resolve())
        self.refresh_volumes()  # 뽑힌 드라이브의 기록이 같은 드라이브 문자를 차지하고 있지 않게
        self._exec("INSERT OR IGNORE INTO folders(path) VALUES (?)", (p,))
        fid = self._query("SELECT id FROM folders WHERE path = ?", (p,))[0]["id"]
        self._assign_folder(fid, p)
        return fid

    def remove_folder(self, folder_id: int) -> None:
        """폴더를 라이브러리에서 제외 (파일은 건드리지 않음). 격리된 항목은 기록을 유지."""
        self._exec(
            "DELETE FROM videos WHERE folder_id = ? AND status = 'active'", (folder_id,))
        self._exec("DELETE FROM folders WHERE id = ?", (folder_id,))
        self._exec("DELETE FROM thumbs WHERE video_id NOT IN (SELECT id FROM videos)")
        self._exec("DELETE FROM volumes WHERE id NOT IN (SELECT volume_id FROM folders WHERE volume_id IS NOT NULL)"
                   " AND id NOT IN (SELECT volume_id FROM videos WHERE volume_id IS NOT NULL)")

    def folders(self) -> list[tuple[int, str]]:
        return [(r["id"], r["path"]) for r in self._query("SELECT id, path FROM folders ORDER BY path")]

    def folder_rows(self) -> list[sqlite3.Row]:
        """(id, path, volume_id, rel_path, scanned_at, 파일 수, 지문 없는 파일 수) — 화면 표시용."""
        return self._query(
            "SELECT f.id, f.path, f.volume_id, f.rel_path, f.scanned_at, "
            "(SELECT COUNT(*) FROM videos v WHERE v.folder_id = f.id AND v.status='active') AS n, "
            "(SELECT COUNT(*) FROM videos v WHERE v.folder_id = f.id AND v.status='active' "
            " AND (v.info_tried = 0 OR (v.thumbs_tried = 0 AND v.duration > 0))) AS todo "
            "FROM folders f ORDER BY f.path")

    def folder_path(self, folder_id: int) -> str | None:
        rows = self._query("SELECT path FROM folders WHERE id = ?", (folder_id,))
        return rows[0]["path"] if rows else None

    def folder_volume(self, folder_id: int) -> Volume | None:
        rows = self._query("SELECT volume_id FROM folders WHERE id = ?", (folder_id,))
        return self.volume(rows[0]["volume_id"]) if rows else None

    def mark_scanned(self, folder_id: int) -> None:
        self._exec("UPDATE folders SET scanned_at=? WHERE id=?", (time.time(), folder_id))

    # ---- 동영상 ----
    def scan_snapshot(self, folder_id: int) -> tuple[tuple[int, str] | None, dict[str, tuple[int, float]]]:
        """스캔을 빠르게 하기 위한 준비물: (드라이브 id, 연결 위치), 이 폴더의 {경로: (크기, 수정시각)}.
        드라이브 정보까지 제대로 적힌 파일만 넣는다 (그런 파일이 바뀌지 않았으면 스캔 때 건드릴 필요가 없다)."""
        v = self.folder_volume(folder_id)
        where = (v.id, v.mount) if v is not None and v.mount else None
        rows = self._query("SELECT path, size, mtime FROM videos WHERE folder_id=? AND "
                           + ("volume_id=? AND rel_path IS NOT NULL" if where else "volume_id IS NULL"),
                           (folder_id, where[0]) if where else (folder_id,))
        return where, {r["path"]: (r["size"], r["mtime"]) for r in rows}

    def upsert_video(self, path: str, folder_id: int | None, size: int, mtime: float,
                     where: tuple[int, str] | None = None) -> int:
        """새 파일이면 추가, 크기/수정시각이 바뀌었으면 해시를 초기화.
        where: scan_snapshot 이 준 (드라이브 id, 연결 위치) — 주면 파일마다 다시 찾지 않는다."""
        volume_id = rel = None
        if where is not None:
            volume_id, rel = where[0], vol.relative_to_root(path, where[1])
        elif folder_id is not None:
            f = self._query("SELECT volume_id FROM folders WHERE id=?", (folder_id,))
            v = self.volume(f[0]["volume_id"]) if f else None
            if v is not None and v.mount:
                volume_id, rel = v.id, vol.relative_to_root(path, v.mount)
        rows = self._query("SELECT id, size, mtime FROM videos WHERE path = ?", (path,))
        if not rows:
            cur = self._exec(
                "INSERT INTO videos(path, folder_id, size, mtime, volume_id, rel_path) VALUES (?, ?, ?, ?, ?, ?)",
                (path, folder_id, size, mtime, volume_id, rel))
            return cur.lastrowid
        row = rows[0]
        if row["size"] != size or row["mtime"] != mtime:
            self._exec(
                "UPDATE videos SET size=?, mtime=?, quick_hash=NULL, full_hash=NULL, duration=NULL, width=NULL,"
                " height=NULL, fingerprint=NULL, info_tried=0, thumbs_tried=0, folder_id=?, volume_id=?, rel_path=?"
                " WHERE id=?", (size, mtime, folder_id, volume_id, rel, row["id"]))
            self._exec("DELETE FROM thumbs WHERE video_id=?", (row["id"],))
        else:
            self._exec("UPDATE videos SET folder_id=?, volume_id=?, rel_path=? WHERE id=?",
                       (folder_id, volume_id, rel, row["id"]))
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

    def set_media_info(self, video_id: int, duration: float | None, width: int | None, height: int | None,
                       fingerprint: str | None) -> None:
        """길이·해상도·장면 지문을 적는다. 못 읽은 파일도 '해 봤음'으로 적어 매번 다시 시도하지 않는다."""
        self._exec("UPDATE videos SET duration=?, width=?, height=?, fingerprint=?, info_tried=1 WHERE id=?",
                   (duration or None, width or None, height or None, fingerprint, video_id))

    def videos_missing_info(self, folder_ids: Iterable[int] | None = None) -> list[Video]:
        """아직 지문을 만들어 보지 않았거나, 지문은 있는데 비교용 그림이 없는 파일."""
        sql, params = ("SELECT * FROM videos WHERE (info_tried = 0 OR (thumbs_tried = 0 AND duration > 0))"
                       " AND status != 'quarantined'"), []
        if folder_ids is not None:
            ids = list(folder_ids)
            sql += f" AND folder_id IN ({','.join('?' * len(ids))})"
            params += ids
        return [Video.from_row(r) for r in self._query(sql + " ORDER BY path", params)]

    def set_thumbs(self, video_id: int, pictures: list[bytes]) -> None:
        """비교용 작은 그림(JPEG)을 적는다. 못 만든 경우도 '해 봤음'으로 적는다."""
        with self.batch():
            self._exec("DELETE FROM thumbs WHERE video_id=?", (video_id,))
            for i, data in enumerate(pictures):
                if data:
                    self._exec("INSERT INTO thumbs(video_id, idx, jpeg) VALUES (?, ?, ?)", (video_id, i, data))
            self._exec("UPDATE videos SET thumbs_tried=1 WHERE id=?", (video_id,))

    def thumbs(self, video_id: int) -> list[bytes]:
        return [bytes(r["jpeg"]) for r in
                self._query("SELECT jpeg FROM thumbs WHERE video_id=? ORDER BY idx", (video_id,))]

    def set_keep(self, video_id: int, keep_id: int | None) -> None:
        self._exec("UPDATE videos SET keep_id=? WHERE id=?", (keep_id, video_id))

    def set_status(self, video_id: int, status: VideoStatus, *, path: str | None = None,
                   original_path: str | None = None, clear_original: bool = False) -> None:
        sets, params = ["status=?", "status_changed_at=?"], [status.value, time.time()]
        root = None
        if path is not None or original_path is not None:
            rows = self._query("SELECT volume_id FROM videos WHERE id=?", (video_id,))
            v = self.volume(rows[0]["volume_id"]) if rows else None
            root = v.mount if v else None
        if path is not None:
            sets.append("path=?")
            params.append(path)
            if root:
                sets.append("rel_path=?")
                params.append(vol.relative_to_root(path, root))
        if original_path is not None:
            sets.append("original_path=?")
            params.append(original_path)
            if root:
                sets.append("original_rel=?")
                params.append(vol.relative_to_root(original_path, root))
        elif clear_original:
            sets.append("original_path=NULL")
            sets.append("original_rel=NULL")
        if status == VideoStatus.ACTIVE:
            sets.append("keep_id=NULL")
        params.append(video_id)
        self._exec(f"UPDATE videos SET {', '.join(sets)} WHERE id=?", params)

    def delete_video(self, video_id: int) -> None:
        self._exec("DELETE FROM videos WHERE id=?", (video_id,))
        self._exec("DELETE FROM thumbs WHERE video_id=?", (video_id,))

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

    # ---- 책갈피 ----
    def add_bookmark(self, path: str, position: float, name: str = "") -> int:
        cur = self._exec("INSERT INTO bookmarks(path, position, name, created) VALUES (?, ?, ?, ?)",
                         (path, position, name, time.time()))
        return cur.lastrowid

    def bookmarks(self, path: str) -> list[tuple[int, float, str]]:
        """(id, 위치, 이름) — 위치 순."""
        rows = self._query("SELECT id, position, name FROM bookmarks WHERE path=? ORDER BY position", (path,))
        return [(r["id"], r["position"], r["name"]) for r in rows]

    def rename_bookmark(self, bookmark_id: int, name: str) -> None:
        self._exec("UPDATE bookmarks SET name=? WHERE id=?", (name, bookmark_id))

    def delete_bookmark(self, bookmark_id: int) -> None:
        self._exec("DELETE FROM bookmarks WHERE id=?", (bookmark_id,))

