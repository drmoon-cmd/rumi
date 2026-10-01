"""Rumi 실행 파일 빌드: `python packaging/build.py`

결과물
  Windows : dist/Rumi/Rumi.exe   (이어서 Inno Setup 으로 설치 파일 생성: packaging/windows/rumi.iss)
  macOS   : dist/Rumi.app
  Linux   : dist/Rumi/Rumi

libmpv 위치를 직접 지정하려면 RUMI_LIBMPV=/경로/libmpv... 환경 변수를 설정하세요.
"""

from __future__ import annotations

import ctypes.util
import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def find_libmpv() -> str | None:
    if path := os.environ.get("RUMI_LIBMPV"):
        return path
    if sys.platform == "win32":
        for pattern in ("libmpv-2.dll", "mpv-2.dll", "mpv-1.dll"):
            hits = glob.glob(os.path.join(HERE, "windows", "**", pattern), recursive=True)
            if hits:
                return hits[0]
        return None
    if sys.platform == "darwin":
        try:
            prefix = subprocess.check_output(["brew", "--prefix", "mpv"], text=True).strip()
            hits = sorted(glob.glob(os.path.join(prefix, "lib", "libmpv.*.dylib")))
            if hits:
                return hits[0]
        except (OSError, subprocess.CalledProcessError):
            pass
    name = ctypes.util.find_library("mpv")
    if name and not os.path.isabs(name):
        for d in ("/usr/lib/x86_64-linux-gnu", "/usr/lib64", "/usr/lib", "/usr/local/lib",
                  "/usr/lib/aarch64-linux-gnu"):
            if os.path.exists(os.path.join(d, name)):
                return os.path.join(d, name)
    return name


def main() -> int:
    libmpv = find_libmpv()
    if not libmpv or not os.path.exists(libmpv):
        print("libmpv 를 찾지 못했습니다. README 의 '설치 파일 만들기'를 참고하세요.", file=sys.stderr)
        return 1
    print(f"libmpv: {libmpv}")
    env = dict(os.environ, RUMI_LIBMPV=libmpv)
    for d in ("build", "dist"):
        shutil.rmtree(os.path.join(ROOT, d), ignore_errors=True)
    return subprocess.call(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", os.path.join(ROOT, "dist"), "--workpath", os.path.join(ROOT, "build"),
         os.path.join(HERE, "rumi.spec")],
        cwd=ROOT, env=env)


if __name__ == "__main__":
    sys.exit(main())
