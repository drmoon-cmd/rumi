# PyInstaller 빌드 설정. 직접 쓰기보다 `python packaging/build.py` 를 사용하세요.
# libmpv 경로는 build.py 가 환경 변수 RUMI_LIBMPV 로 넘겨준다.
import os
import sys

block_cipher = None
root = os.path.abspath(os.path.join(SPECPATH, ".."))
libmpv = os.environ.get("RUMI_LIBMPV")
binaries = [(libmpv, ".")] if libmpv else []

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[root],
    binaries=binaries,
    # 구간 내보내기용 ffmpeg 는 imageio_ffmpeg 패키지(OS별 정적 빌드)와 함께 들어간다
    hiddenimports=["mpv", "imageio_ffmpeg"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Rumi",
    console=False,
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="Rumi")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Rumi.app",
        bundle_identifier="io.github.drmoon-cmd.rumi",
        info_plist={
            "CFBundleDisplayName": "Rumi",
            "NSHighResolutionCapable": True,
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "Video",
                "CFBundleTypeRole": "Viewer",
                "LSItemContentTypes": ["public.movie"],
            }],
        },
    )
