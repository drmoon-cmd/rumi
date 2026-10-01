"""외부 도구(ffmpeg) 찾기."""

from __future__ import annotations

import glob
import os
import shutil
import sys


def find_ffmpeg() -> str | None:
    """번들 안 → RUMI_FFMPEG → imageio-ffmpeg → PATH 순서로 ffmpeg 실행 파일을 찾는다."""
    if path := os.environ.get("RUMI_FFMPEG"):
        if os.path.exists(path):
            return path
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        pattern = "ffmpeg*.exe" if sys.platform == "win32" else "ffmpeg*"
        hits = [h for h in glob.glob(os.path.join(bundle, pattern)) if os.path.isfile(h)]
        if hits:
            return hits[0]
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    return shutil.which("ffmpeg")
