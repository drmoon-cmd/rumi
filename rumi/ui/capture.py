"""장면 모음(썸네일 표) 만들기. 백그라운드 스레드에서 실행해도 되도록 QImage 만 사용한다."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter

from ..library.duplicates import Cancelled
from ..media_tools import build_thumbnail_args, contact_sheet_times
from .util import fmt_size, fmt_time

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # Windows: 콘솔 창 깜빡임 방지


def grab_frame(ffmpeg: str, path: str, t: float, width: int) -> QImage:
    r = subprocess.run(build_thumbnail_args(ffmpeg, path, t, width), capture_output=True,
                       creationflags=_NO_WINDOW, timeout=60)
    img = QImage()
    if r.returncode != 0 or not img.loadFromData(r.stdout, "PNG"):
        raise OSError(r.stderr.decode("utf-8", "replace")[-300:] or "장면을 가져오지 못했습니다")
    return img


def build_contact_sheet(ffmpeg: str, path: str, duration: float, video_info: str,
                        cols: int = 4, rows: int = 4, width: int = 360,
                        progress: Callable[[int, int], None] | None = None,
                        cancel: Callable[[], bool] | None = None) -> QImage:
    times = contact_sheet_times(duration, cols * rows)
    frames: list[tuple[float, QImage]] = []
    for i, t in enumerate(times, 1):
        if cancel and cancel():
            raise Cancelled
        if progress:
            progress(i, len(times))
        frames.append((t, grab_frame(ffmpeg, path, t, width)))

    gap, header = 8, 76
    th = max(img.height() for _, img in frames)
    sheet = QImage(gap + cols * (width + gap), header + rows * (th + gap), QImage.Format_RGB32)
    sheet.fill(QColor("#1e1f22"))
    p = QPainter(sheet)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)

    title = QFont()
    title.setPointSize(15)
    title.setBold(True)
    p.setFont(title)
    p.setPen(QColor("#ffffff"))
    p.drawText(QRect(gap * 2, 10, sheet.width() - gap * 4, 30), Qt.AlignLeft | Qt.AlignVCenter,
               os.path.basename(path))
    info = QFont()
    info.setPointSize(10)
    p.setFont(info)
    p.setPen(QColor("#b0b4ba"))
    size = fmt_size(os.path.getsize(path)) if os.path.exists(path) else ""
    p.drawText(QRect(gap * 2, 42, sheet.width() - gap * 4, 24), Qt.AlignLeft | Qt.AlignVCenter,
               f"길이 {fmt_time(duration)}   ·   {video_info}   ·   {size}")

    stamp = QFont()
    stamp.setPointSize(10)
    stamp.setBold(True)
    p.setFont(stamp)
    for i, (t, img) in enumerate(frames):
        x = gap + (i % cols) * (width + gap)
        y = header + (i // cols) * (th + gap)
        p.drawImage(x, y, img)
        label = fmt_time(t)
        box = QRect(x + 6, y + img.height() - 26, p.fontMetrics().horizontalAdvance(label) + 12, 20)
        p.fillRect(box, QColor(0, 0, 0, 160))
        p.setPen(QColor("#ffffff"))
        p.drawText(box, Qt.AlignCenter, label)
    p.end()
    return sheet
