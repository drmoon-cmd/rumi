"""UI 공용 도우미."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


def fmt_time(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def fmt_date(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""


def natural_key(s: str):
    """'ep2' 가 'ep10' 보다 앞에 오도록 정렬."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def reveal_in_file_manager(path: str) -> None:
    """파일 탐색기/Finder 에서 파일 위치 열기."""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
