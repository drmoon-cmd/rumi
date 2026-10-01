import os
from pathlib import Path

import pytest

from rumi.library import LibraryDB, Organizer, VideoStatus, find_duplicates, scan_folder, suggest_keep
from rumi.library.scanner import QUARANTINE_DIRNAME


def write(path: Path, data: bytes, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "videos"
    big = os.urandom(5 * 1024 * 1024)
    write(root / "a" / "movie.mp4", big, mtime=1000)
    write(root / "b" / "movie copy.mkv", big, mtime=2000)
    write(root / "c" / "deep" / "movie (2).mp4", big, mtime=3000)
    # 크기는 같지만 가운데만 다른 파일 (부분 해시로는 못 거르고 전체 해시에서 걸러져야 함)
    tweaked = bytearray(big)
    tweaked[len(big) // 4] ^= 0xFF
    write(root / "other.mp4", bytes(tweaked))
    write(root / "small1.avi", b"x" * 100)
    write(root / "small2.avi", b"x" * 100)
    write(root / "notes.txt", b"x" * 100)
    db = LibraryDB(tmp_path / "lib.sqlite3")
    fid = db.add_folder(root)
    scan_folder(db, fid)
    yield db, root
    db.close()


def by_name(db, name):
    return next(v for v in db.videos() if v.name == name)


def test_scan_finds_only_videos(lib):
    db, _ = lib
    names = sorted(v.name for v in db.videos())
    assert names == ["movie (2).mp4", "movie copy.mkv", "movie.mp4", "other.mp4", "small1.avi", "small2.avi"]


def test_find_duplicates_groups_exact_copies(lib):
    db, _ = lib
    groups = find_duplicates(db)
    sets = sorted(sorted(v.name for v in g.videos) for g in groups)
    assert sets == [["movie (2).mp4", "movie copy.mkv", "movie.mp4"], ["small1.avi", "small2.avi"]]
    # 낭비 용량이 큰 그룹이 먼저
    assert len(groups[0].videos) == 3


def test_suggest_keep_prefers_oldest(lib):
    db, _ = lib
    g = find_duplicates(db)[0]
    assert suggest_keep(g).name == "movie.mp4"


def test_mark_quarantine_restore_roundtrip(lib):
    db, root = lib
    org = Organizer(db)
    dup = by_name(db, "movie (2).mp4")
    original = dup.path

    assert org.mark([dup.id]).done == [dup.id]
    assert db.get(dup.id).status == VideoStatus.MARKED
    assert os.path.exists(original)  # 표시는 파일을 건드리지 않음

    assert org.quarantine([dup.id]).done == [dup.id]
    moved = db.get(dup.id)
    assert moved.status == VideoStatus.QUARANTINED
    assert not os.path.exists(original)
    assert Path(moved.path) == root / QUARANTINE_DIRNAME / "c" / "deep" / "movie (2).mp4"
    assert moved.original_path == original

    # 재스캔해도 격리 폴더는 무시되고 격리 기록은 유지
    scan_folder(db, moved.folder_id)
    assert db.get(dup.id).status == VideoStatus.QUARANTINED
    assert not any(QUARANTINE_DIRNAME in v.path for v in db.videos([VideoStatus.ACTIVE]))

    assert org.restore([dup.id]).done == [dup.id]
    back = db.get(dup.id)
    assert back.status == VideoStatus.ACTIVE and back.path == original
    assert os.path.exists(original)
    assert not (root / QUARANTINE_DIRNAME).exists()  # 빈 격리 폴더 정리


def test_purge_requires_marking_and_remaining_copy(lib):
    db, _ = lib
    find_duplicates(db)
    org = Organizer(db)
    a, b, c = (by_name(db, n) for n in ("movie.mp4", "movie copy.mkv", "movie (2).mp4"))

    # 표시 안 된 파일은 삭제 불가
    assert not org.check_purge([a.id])[0].ok

    # 세 개 모두 정리 대상으로 지정하면 남는 사본이 없으므로 모두 거부
    org.quarantine([a.id, b.id, c.id])
    checks = org.check_purge([a.id, b.id, c.id])
    assert not any(ch.ok for ch in checks)
    assert "마지막 사본" in checks[0].reason
    assert org.purge(checks).done == []

    # 하나를 복원하면 나머지 둘은 삭제 가능
    org.restore([a.id])
    checks = org.check_purge([b.id, c.id])
    assert all(ch.ok for ch in checks)
    assert all(ch.kept_copy == db.get(a.id).path for ch in checks)
    res = org.purge(checks)
    assert sorted(res.done) == sorted([b.id, c.id])
    assert db.get(b.id) is None and db.get(c.id) is None
    assert os.path.exists(db.get(a.id).path)


def test_purge_detects_changed_keeper(lib):
    db, _ = lib
    find_duplicates(db)
    org = Organizer(db)
    s1, s2 = by_name(db, "small1.avi"), by_name(db, "small2.avi")
    org.mark([s2.id])
    Path(s1.path).write_bytes(b"y" * 100)  # 남길 사본이 그사이 바뀜
    check = org.check_purge([s2.id])[0]
    assert not check.ok and "내용이 달라" in check.reason
    assert os.path.exists(s2.path)


def test_quarantine_name_collision(lib):
    db, root = lib
    org = Organizer(db)
    s1 = by_name(db, "small1.avi")
    write(root / QUARANTINE_DIRNAME / "small1.avi", b"old")
    org.quarantine([s1.id])
    assert Path(db.get(s1.id).path).name == "small1 (1).avi"


def test_playback_position(tmp_path):
    db = LibraryDB(tmp_path / "x.sqlite3")
    assert db.load_position("/v.mp4") is None
    db.save_position("/v.mp4", 12.5, 100)
    db.save_position("/v.mp4", 30.0, 100)
    assert db.load_position("/v.mp4") == 30.0
    db.clear_position("/v.mp4")
    assert db.load_position("/v.mp4") is None
