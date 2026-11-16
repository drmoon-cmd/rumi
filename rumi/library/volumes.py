"""드라이브(볼륨) 알아보기: 드라이브 문자가 아니라 드라이브 고유 번호로 구분한다.

외장 하드는 꽂을 때마다 문자(D:, E:)가 달라질 수 있고, 서로 다른 하드가 같은 문자를 쓰기도 한다.
그래서 파일 위치를 '어느 드라이브(고유 번호) + 그 안의 경로'로 기억한다.
- Windows: 볼륨 일련번호 (포맷할 때 정해지는 번호)
- Linux: 파일 시스템 UUID, 없으면 마운트 위치
- 그 밖: 마운트 위치
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Mount:
    serial: str   # 드라이브 고유 번호 (다른 드라이브와 겹치지 않는 문자열)
    root: str     # 지금 연결된 위치. 끝에 구분자가 붙는다 ("E:\\", "/media/usb/")
    label: str    # 드라이브에 붙은 이름 (없으면 빈 문자열)


def _with_sep(path: str) -> str:
    return path if path.endswith(os.sep) else path + os.sep


# ---------------------------------------------------------------- Windows
def _win_volume_info(root: str) -> tuple[str, str] | None:
    """(일련번호, 이름). 드라이브가 준비되지 않았으면 None."""
    import ctypes
    kernel32 = ctypes.windll.kernel32
    label = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    serial = ctypes.c_uint32(0)
    max_len = ctypes.c_uint32(0)
    flags = ctypes.c_uint32(0)
    old_mode = kernel32.SetErrorMode(1)  # 빈 카드 리더 등에서 '디스크를 넣으세요' 창이 뜨지 않게
    try:
        ok = kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), label, 261, ctypes.byref(serial), ctypes.byref(max_len),
            ctypes.byref(flags), fs, 261)
    finally:
        kernel32.SetErrorMode(old_mode)
    if not ok:
        return None
    return f"{serial.value >> 16:04X}-{serial.value & 0xFFFF:04X}", label.value


def _win_root_of(path: str) -> str | None:
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    if ctypes.windll.kernel32.GetVolumePathNameW(ctypes.c_wchar_p(path), buf, 1024):
        return _with_sep(buf.value)
    drive = os.path.splitdrive(path)[0]
    return _with_sep(drive) if drive else None


def _win_mounts() -> list[Mount]:
    import ctypes
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    out = []
    for i in range(26):
        if not mask & (1 << i):
            continue
        root = f"{chr(ord('A') + i)}:\\"
        info = _win_volume_info(root)
        if info:
            out.append(Mount(info[0], root, info[1]))
    return out


# ---------------------------------------------------------------- Linux 등
def _unescape(text: str) -> str:
    return text.replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")


def _posix_mount_points() -> list[str]:
    points = set()
    try:
        with open("/proc/self/mounts", encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[2] not in _PSEUDO_FS:
                    points.add(_unescape(parts[1]))
    except OSError:
        pass
    if sys.platform == "darwin":
        points.add("/")
        try:
            points.update(os.path.join("/Volumes", n) for n in os.listdir("/Volumes"))
        except OSError:
            pass
    return sorted(p for p in points if os.path.isdir(p))


_PSEUDO_FS = frozenset({
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2", "securityfs", "pstore", "debugfs",
    "tracefs", "configfs", "fusectl", "mqueue", "hugetlbfs", "bpf", "autofs", "binfmt_misc", "squashfs",
    "overlay", "nsfs", "ramfs", "rpc_pipefs", "efivarfs",
})


def _posix_uuids() -> dict[int, str]:
    """장치 번호 → 파일 시스템 UUID"""
    out = {}
    base = "/dev/disk/by-uuid"
    try:
        for name in os.listdir(base):
            try:
                out[os.stat(os.path.join(base, name)).st_rdev] = name
            except OSError:
                continue
    except OSError:
        pass
    return out


def _posix_mount(root: str, uuids: dict[int, str] | None = None) -> Mount | None:
    try:
        dev = os.stat(root).st_dev
    except OSError:
        return None
    uuids = _posix_uuids() if uuids is None else uuids
    root = os.path.abspath(root)
    serial = uuids.get(dev) or f"path:{root}"
    label = "" if root == os.sep else os.path.basename(root)
    return Mount(serial, _with_sep(root), label)


def _posix_root_of(path: str) -> str:
    path = os.path.abspath(path)
    while not os.path.exists(path) and os.path.dirname(path) != path:
        path = os.path.dirname(path)
    while not os.path.ismount(path) and os.path.dirname(path) != path:
        path = os.path.dirname(path)
    return path


# ---------------------------------------------------------------- 공통
def current_mounts() -> list[Mount]:
    """지금 연결되어 있는 드라이브들."""
    if sys.platform == "win32":
        try:
            return _win_mounts()
        except Exception:
            return []
    uuids = _posix_uuids()
    return [m for m in (_posix_mount(p, uuids) for p in _posix_mount_points()) if m]


def mount_of(path: str) -> Mount | None:
    """이 경로가 들어 있는 드라이브. 알 수 없으면 None."""
    try:
        if sys.platform == "win32":
            root = _win_root_of(os.path.abspath(path))
            info = _win_volume_info(root) if root else None
            return Mount(info[0], root, info[1]) if info else None
        return _posix_mount(_posix_root_of(path))
    except Exception:
        return None


def relative_to_root(path: str, root: str) -> str:
    """드라이브 안에서의 경로 (앞에 구분자 없음). 드라이브 맨 위는 빈 문자열."""
    path = os.path.abspath(path)
    root = _with_sep(root)
    if os.path.normcase(_with_sep(path)) == os.path.normcase(root):
        return ""
    if os.path.normcase(path).startswith(os.path.normcase(root)):
        return path[len(root):]
    return path.lstrip(os.sep)


def offline_path(serial: str, rel: str) -> str:
    """연결되지 않은 드라이브의 파일을 가리키는 표시용 경로. 실제 파일 경로와 겹치지 않는다."""
    return f"<{serial}>{os.sep}{rel}"


def is_offline_path(path: str) -> bool:
    return path.startswith("<")
