"""드라이브 목록 기억(뽑아 둔 드라이브와 비교)과 장면 지문."""

import os
import shutil
import sqlite3
import subprocess

import pytest

from rumi.library import LibraryDB, Organizer, VideoStatus, build_catalog, find_duplicates, scan_folder, suggest_keep
from rumi.library import fingerprint as fp
from rumi.library import volumes as vol
from rumi.tools import find_ffmpeg


class Drives:
    """가짜 드라이브: 폴더 하나가 드라이브 하나. 꽂고 뽑고 다른 위치에 다시 꽂을 수 있다."""

    def __init__(self, monkeypatch, base):
        self.base = base
        self.mounted: dict[str, str] = {}   # 고유 번호 → 위치
        monkeypatch.setattr(vol, "current_mounts", self.current)
        monkeypatch.setattr(vol, "mount_of", self.mount_of)

    def current(self):
        return [vol.Mount(s, root + os.sep, "하드") for s, root in self.mounted.items()]

    def mount_of(self, path):
        path = os.path.abspath(path)
        for m in self.current():
            if (path + os.sep).startswith(m.root):
                return m
        return None

    def plug(self, serial, name):
        root = str(self.base / name)
        os.makedirs(root, exist_ok=True)
        self.mounted[serial] = root
        return root

    def unplug(self, serial, keep_files_as=None):
        root = self.mounted.pop(serial)
        if keep_files_as:  # 파일은 그대로 두고 위치만 옮긴다 (다음에 다른 문자로 꽂히는 상황)
            os.rename(root, str(self.base / keep_files_as))
        return root


@pytest.fixture
def drives(tmp_path, monkeypatch):
    return Drives(monkeypatch, tmp_path)


@pytest.fixture
def db(tmp_path):
    d = LibraryDB(tmp_path / "lib.sqlite3")
    yield d
    d.close()


def put(root, rel, data):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def add_and_scan(db, root, catalog=True):
    fid = db.add_folder(root)
    scan_folder(db, fid)
    if catalog:
        build_catalog(db, [fid])
    return fid


def test_drive_is_recognized_at_a_new_location(db, drives):
    root = drives.plug("AAAA-0001", "E")
    put(root, "movies/a.mp4", b"a" * 5000)
    add_and_scan(db, root)
    drives.unplug("AAAA-0001", keep_files_as="hidden")
    db.refresh_volumes()
    v = db.videos()[0]
    assert not db.is_online(v) and not os.path.exists(v.path)
    assert v.rel_path == os.path.join("movies", "a.mp4")
    # 같은 하드가 다른 위치(다른 드라이브 문자)에 꽂힘
    os.rename(str(drives.base / "hidden"), str(drives.base / "F"))
    drives.mounted["AAAA-0001"] = str(drives.base / "F")
    db.refresh_volumes()
    v = db.videos()[0]
    assert db.is_online(v) and v.path == os.path.join(str(drives.base / "F"), "movies", "a.mp4")
    assert os.path.exists(v.path)
    assert db.folders()[0][1] == str(drives.base / "F")


def test_other_drive_on_same_letter_does_not_mix_up_files(db, drives):
    root = drives.plug("AAAA-0001", "E")
    put(root, "a.mp4", b"a" * 5000)
    add_and_scan(db, root)
    drives.unplug("AAAA-0001", keep_files_as="A_away")
    # 다른 하드가 같은 위치에, 같은 이름의 파일을 갖고 꽂힘
    root = drives.plug("BBBB-0002", "E")
    put(root, "a.mp4", b"b" * 7000)
    add_and_scan(db, root)
    vids = db.videos()
    assert len(vids) == 2
    assert sorted(db.is_online(v) for v in vids) == [False, True]
    assert {v.size for v in vids} == {5000, 7000}
    assert len(db.volumes()) == 2


def test_duplicates_found_across_drives_never_connected_together(db, drives):
    data = os.urandom(4 * 1024 * 1024)
    root = drives.plug("AAAA-0001", "E")
    put(root, "movie.mp4", data)
    put(root, "only_here.mp4", os.urandom(1000))
    add_and_scan(db, root)
    drives.unplug("AAAA-0001", keep_files_as="A_away")
    root = drives.plug("BBBB-0002", "E")
    put(root, "same movie renamed.mkv", data)
    add_and_scan(db, root)

    groups = find_duplicates(db)
    assert len(groups) == 1
    g = groups[0]
    assert g.kind == "exact" and not g.verified
    assert sorted(v.name for v in g.videos) == ["movie.mp4", "same movie renamed.mkv"]


def test_purge_with_keeper_on_unplugged_drive_needs_consent(db, drives):
    data = os.urandom(2 * 1024 * 1024)
    root = drives.plug("AAAA-0001", "E")
    put(root, "movie.mp4", data)
    add_and_scan(db, root)
    drives.unplug("AAAA-0001", keep_files_as="A_away")
    root = drives.plug("BBBB-0002", "E")
    target_path = put(root, "copy.mp4", data)
    add_and_scan(db, root)
    org = Organizer(db)
    target = next(v for v in db.videos() if v.name == "copy.mp4")
    org.mark([target.id])
    checks = org.check_purge([target.id])
    assert checks[0].ok and checks[0].offline_keeper
    assert org.purge(checks).done == [] and os.path.exists(target_path)   # 동의 없이는 지우지 않는다
    assert org.purge(checks, allow_offline_keeper=True).done == [target.id]
    assert not os.path.exists(target_path)


def test_unplugged_file_cannot_be_purged_or_quarantined(db, drives):
    data = os.urandom(2 * 1024 * 1024)
    root = drives.plug("AAAA-0001", "E")
    put(root, "movie.mp4", data)
    put(root, "copy.mp4", data)
    add_and_scan(db, root)
    drives.unplug("AAAA-0001", keep_files_as="A_away")
    db.refresh_volumes()
    org = Organizer(db)
    target = next(v for v in db.videos() if v.name == "copy.mp4")
    assert org.quarantine([target.id]).errors
    org.mark([target.id])
    assert not org.check_purge([target.id])[0].ok


def test_quarantine_and_restore_follow_the_drive(db, drives):
    data = os.urandom(2 * 1024 * 1024)
    root = drives.plug("AAAA-0001", "E")
    put(root, "x/movie.mp4", data)
    put(root, "y/copy.mp4", data)
    add_and_scan(db, root)
    org = Organizer(db)
    target = next(v for v in db.videos() if v.name == "copy.mp4")
    assert org.quarantine([target.id]).done
    drives.unplug("AAAA-0001", keep_files_as="F")
    drives.mounted["AAAA-0001"] = str(drives.base / "F")
    db.refresh_volumes()
    v = db.get(target.id)
    assert v.path.startswith(str(drives.base / "F")) and os.path.exists(v.path)
    assert org.restore([target.id]).done
    assert os.path.exists(os.path.join(str(drives.base / "F"), "y", "copy.mp4"))


def test_old_database_is_upgraded(tmp_path, drives):
    root = drives.plug("AAAA-0001", "E")
    path = put(root, "a.mp4", b"a" * 5000)
    old = sqlite3.connect(tmp_path / "old.sqlite3")
    old.executescript("""
        CREATE TABLE folders (id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE);
        CREATE TABLE videos (id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL, size INTEGER NOT NULL,
            mtime REAL NOT NULL, quick_hash TEXT, full_hash TEXT, status TEXT NOT NULL DEFAULT 'active',
            original_path TEXT, status_changed_at REAL);""")
    old.execute("INSERT INTO folders(path) VALUES (?)", (root,))
    old.execute("INSERT INTO videos(path, folder_id, size, mtime) VALUES (?, 1, 5000, 1.0)", (path,))
    old.commit()
    old.close()
    db = LibraryDB(tmp_path / "old.sqlite3")
    db.refresh_volumes()
    v = db.videos()[0]
    assert v.volume_id is not None and v.rel_path == "a.mp4" and db.is_online(v)
    db.close()


# ---------------------------------------------------------------- 장면 지문
def test_parse_info():
    text = """Input #0, matroska,webm, from 'x.mkv':
  Duration: 01:02:03.50, start: 0.000000, bitrate: 1234 kb/s
  Stream #0:0(eng): Video: h264 (High), yuv420p(tv, bt709), 1920x1080 [SAR 1:1 DAR 16:9], 23.98 fps
  Stream #0:1: Audio: aac, 48000 Hz"""
    info = fp.parse_info(text)
    assert (info.duration, info.width, info.height) == (3723.5, 1920, 1080)
    assert fp.parse_info("nothing").duration == 0


def test_dhash_and_matching():
    ramp = bytes((x * 28) % 256 for _y in range(8) for x in range(9))
    assert fp.dhash(ramp) is not None
    assert fp.dhash(bytes([7] * 72)) is None           # 단색 장면은 비교에서 뺀다
    a = [0x1234567890ABCDEF + i for i in range(fp.FRAMES)]
    assert fp.matches(a, list(a))
    assert fp.decode(fp.encode(a)) == a
    noisy = [h ^ 0b111 for h in a]                      # 장면마다 3칸씩 다름
    assert fp.matches(a, noisy)
    assert not fp.matches(a, [h ^ 0xFFFFFFFF for h in a])
    assert not fp.matches(a[:3] + [None] * 7, a)        # 비교할 장면이 너무 적으면 판단하지 않는다
    assert fp.decode("99:abc") is None and fp.decode(None) is None


needs_ffmpeg = pytest.mark.skipif(not find_ffmpeg(), reason="ffmpeg 없음")


def make_video(path, seed, size="320x180", extra=()):
    r = subprocess.run(
        [find_ffmpeg(), "-v", "error", "-y", "-f", "lavfi",
         "-i", f"gradients=size={size}:rate=10:speed=0.05:nb_colors=5:seed={seed}",
         "-t", "20", "-c:v", "mpeg4", "-q:v", "6", *extra, str(path)], capture_output=True)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")


@needs_ffmpeg
def test_same_video_in_other_quality_found_across_drives(db, drives, tmp_path):
    original = tmp_path / "orig.mp4"
    make_video(original, seed=3)
    small = tmp_path / "small.avi"   # 해상도·화질·형식이 다른 사본
    r = subprocess.run([find_ffmpeg(), "-v", "error", "-y", "-i", str(original), "-vf", "scale=160:90",
                        "-c:v", "mpeg4", "-q:v", "14", str(small)], capture_output=True)
    assert r.returncode == 0
    other = tmp_path / "other.mp4"
    make_video(other, seed=9)

    root = drives.plug("AAAA-0001", "E")
    os.makedirs(root, exist_ok=True)
    shutil.copy(original, os.path.join(root, "My Movie (2019) 1080p.mp4"))
    shutil.copy(other, os.path.join(root, "different.mp4"))
    add_and_scan(db, root)
    v = next(v for v in db.videos() if v.name.startswith("My Movie"))
    assert (v.width, v.height) == (320, 180) and abs(v.duration - 20) < 0.5 and fp.usable(v.fingerprint)
    drives.unplug("AAAA-0001", keep_files_as="A_away")

    root = drives.plug("BBBB-0002", "E")
    target_path = shutil.copy(small, os.path.join(root, "mymovie_lowres.avi"))
    add_and_scan(db, root)

    groups = find_duplicates(db)
    assert len(groups) == 1
    g = groups[0]
    assert g.kind == "similar"
    assert sorted(v.name for v in g.videos) == ["My Movie (2019) 1080p.mp4", "mymovie_lowres.avi"]
    keep = suggest_keep(g)
    assert keep.name == "My Movie (2019) 1080p.mp4"      # 해상도가 높은 쪽을 남긴다
    assert find_duplicates(db, similar=False) == []

    # 화질이 낮은 쪽을 정리: 남길 파일이 뽑아 둔 드라이브에 있으므로 동의가 있어야 지운다
    org = Organizer(db)
    target = next(v for v in g.videos if v.id != keep.id)
    org.mark([target.id], keep={target.id: keep.id})
    checks = org.check_purge([target.id])
    assert checks[0].ok and checks[0].offline_keeper and checks[0].kind == "similar"
    assert not org.purge(checks).done and os.path.exists(target_path)

    # 남길 파일이 든 드라이브를 다시 꽂으면 확인하고 지운다
    os.rename(str(drives.base / "A_away"), str(drives.base / "G"))
    drives.mounted["AAAA-0001"] = str(drives.base / "G")
    checks = org.check_purge([target.id])
    assert checks[0].ok and not checks[0].offline_keeper
    assert org.purge(checks).done == [target.id] and not os.path.exists(target_path)


@needs_ffmpeg
def test_catalog_is_resumable_and_skips_non_videos(db, drives, tmp_path):
    root = drives.plug("AAAA-0001", "E")
    make_video(os.path.join(root, "ok.mp4"), seed=1)
    put(root, "broken.mp4", b"not a video" * 100)
    fid = add_and_scan(db, root, catalog=False)
    assert len(db.videos_missing_info()) == 2
    assert build_catalog(db, [fid]) == 2
    assert db.videos_missing_info() == []
    assert build_catalog(db, [fid]) == 0                  # 다시 돌려도 한 것은 건너뛴다
    broken = next(v for v in db.videos() if v.name == "broken.mp4")
    assert broken.fingerprint is None and broken.quick_hash


def test_unmark_clears_keep(db, drives):
    root = drives.plug("AAAA-0001", "E")
    put(root, "a.mp4", b"a" * 3000)
    put(root, "b.mp4", b"b" * 3000)
    add_and_scan(db, root)
    a, b = db.videos()
    org = Organizer(db)
    org.mark([a.id], keep={a.id: b.id})
    assert db.get(a.id).keep_id == b.id and db.get(a.id).status == VideoStatus.MARKED
    org.restore([a.id])
    assert db.get(a.id).keep_id is None


def test_real_drive_of_this_folder(tmp_path):
    """가짜 드라이브가 아닌 실제 OS 호출 (Windows 에서는 볼륨 일련번호)."""
    m = vol.mount_of(str(tmp_path))
    assert m is not None and m.serial and str(tmp_path).startswith(m.root.rstrip("\\/"))
    assert any(x.serial == m.serial for x in vol.current_mounts())
    assert vol.relative_to_root(str(tmp_path / "a" / "b.mp4"), m.root).endswith(os.path.join("a", "b.mp4"))
    db = LibraryDB(tmp_path / "lib.sqlite3")
    (tmp_path / "v").mkdir()
    (tmp_path / "v" / "x.mp4").write_bytes(b"x" * 100)
    fid = db.add_folder(tmp_path / "v")
    scan_folder(db, fid)
    db.refresh_volumes()
    v = db.videos()[0]
    assert db.is_online(v) and os.path.exists(v.path) and v.rel_path.endswith("x.mp4")
    db.close()
