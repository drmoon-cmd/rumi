import shutil
import subprocess

import pytest

from rumi.media_tools import (
    AudioSettings, build_audio_filter, build_thumbnail_args, contact_sheet_times,
)


def test_audio_filter():
    assert build_audio_filter(AudioSettings()) == ""
    f = build_audio_filter(AudioSettings(normalize=True, bass=20, treble=-3))
    assert "bass=g=12" in f and "treble=g=-3" in f and "dynaudnorm" in f


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg 없음")
def test_thumbnail_and_contact_sheet(tmp_path):
    src = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=duration=8:size=320x240:rate=25", "-pix_fmt", "yuv420p", str(src)], check=True)
    png = subprocess.run(build_thumbnail_args("ffmpeg", str(src), 3.0, 160), capture_output=True, check=True).stdout
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_contact_sheet_times():
    t = contact_sheet_times(100, 4)
    assert t == [20, 40, 60, 80]
    with pytest.raises(ValueError):
        contact_sheet_times(0, 4)
