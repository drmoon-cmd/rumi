import subprocess
import shutil

import pytest

from rumi.clips import Clip, ClipList, build_copy_export, build_reencode_export, parse_progress_seconds
from rumi.dashcam import find_partner


def test_cliplist_roundtrip_and_validation(tmp_path):
    cl = ClipList()
    cl.add(Clip("/v/a.mp4", 10, 20, 1920, 1080, 30, True))
    cl.add(Clip("/v/b.mp4", 35, 5))           # 거꾸로 찍어도 바로잡음
    assert cl.clips[1].start == 5 and cl.clips[1].end == 35
    with pytest.raises(ValueError):
        cl.add(Clip("/v/c.mp4", 3, 3.05))
    assert cl.total_duration == 40
    f = tmp_path / "x.rumiclips"
    cl.save(f)
    again = ClipList.load(f)
    assert again.clips == cl.clips
    assert again.missing_files() == ["/v/a.mp4", "/v/b.mp4"]


def test_copy_export_list_escapes_paths():
    text, args = build_copy_export([Clip("C:\\영상\\it's.mp4", 1, 2.5)], "list.txt", "out.mp4")
    assert "file 'C:/영상/it'\\''s.mp4'" in text
    assert "inpoint 1.000" in text and "outpoint 2.500" in text
    assert args[-1] == "out.mp4" and "copy" in args


def test_progress_parse():
    assert parse_progress_seconds("out_time_us=2500000") == 2.5
    assert parse_progress_seconds("frame=10") is None


@pytest.mark.parametrize("names,start,expected", [
    (["20260824_074217_F.MP4", "20260824_074217_R.MP4"], "20260824_074217_F.MP4", "20260824_074217_R.MP4"),
    (["REC_0824_074217_R.mp4", "REC_0824_074217_F.mp4"], "REC_0824_074217_R.mp4", "REC_0824_074217_F.mp4"),
    (["20260824_074217F.mp4", "20260824_074217R.mp4"], "20260824_074217F.mp4", "20260824_074217R.mp4"),
    (["drive_front.mp4", "drive_rear.mp4"], "drive_front.mp4", "drive_rear.mp4"),
    (["only_F.mp4"], "only_F.mp4", None),
])
def test_find_partner_same_folder(tmp_path, names, start, expected):
    for n in names:
        (tmp_path / n).write_bytes(b"x")
    hit = find_partner(str(tmp_path / start))
    assert (hit and hit.endswith(expected)) if expected else hit is None


def test_find_partner_front_rear_folders(tmp_path):
    (tmp_path / "Front").mkdir()
    (tmp_path / "Rear").mkdir()
    (tmp_path / "Front" / "20260824_074217.mp4").write_bytes(b"x")
    (tmp_path / "Rear" / "20260824_074217.mp4").write_bytes(b"x")
    assert find_partner(str(tmp_path / "Front" / "20260824_074217.mp4")) == str(tmp_path / "Rear" / "20260824_074217.mp4")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg 없음")
def test_exports_with_real_ffmpeg(tmp_path):
    def make(name, size, audio):
        p = tmp_path / name
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=duration=6:size={size}:rate=25"]
        if audio:
            cmd += ["-f", "lavfi", "-i", "sine=duration=6"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "25"] + (["-c:a", "aac"] if audio else []) + [str(p)]
        subprocess.run(cmd, check=True)
        return str(p)

    a = make("a.mp4", "320x240", True)
    b = make("b.mp4", "160x120", False)
    clips = [Clip(a, 1, 3, 320, 240, 25, True), Clip(b, 2, 4.5, 160, 120, 25, False)]
    out = tmp_path / "exact.mp4"
    subprocess.run(build_reencode_export(clips, str(out)), check=True, capture_output=True)
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                         "-of", "csv=p=0", str(out)]).strip())
    assert abs(dur - 4.5) < 0.2

    same = [Clip(a, 1, 3, 320, 240, 25, True), Clip(a, 4, 5, 320, 240, 25, True)]
    lst = tmp_path / "list.txt"
    text, args = build_copy_export(same, str(lst), str(tmp_path / "fast.mp4"))
    lst.write_text(text, encoding="utf-8")
    subprocess.run(args, check=True, capture_output=True)
    assert (tmp_path / "fast.mp4").stat().st_size > 0
