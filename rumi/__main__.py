"""실행 진입점: `python -m rumi [파일...]`"""

from __future__ import annotations

import os
import sys


def _prepare_libmpv_path() -> None:
    """번들/개발 환경에서 libmpv 를 찾을 수 있도록 검색 경로를 넓힌다."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    candidates = [base, os.path.join(base, "lib"), os.path.dirname(sys.executable)]
    if sys.platform == "win32":
        for d in candidates:
            if os.path.isdir(d):
                os.add_dll_directory(d)
        os.environ["PATH"] = os.pathsep.join(candidates + [os.environ.get("PATH", "")])
    elif sys.platform == "darwin":
        extra = candidates + ["/opt/homebrew/lib", "/usr/local/lib"]
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = os.pathsep.join(
            extra + [os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")])


def main() -> int:
    _prepare_libmpv_path()

    if "--diag" in sys.argv:  # 지원용: 창을 띄우지 않고 구성 정보만 출력
        from . import __version__
        from .tools import find_ffmpeg
        print(f"Rumi {__version__}\nffmpeg: {find_ffmpeg()}")
        return 0

    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import APP_NAME

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)

    try:
        from .ui.main_window import MainWindow
    except OSError as e:  # libmpv 를 찾지 못함
        QMessageBox.critical(
            None, APP_NAME,
            f"재생 엔진(libmpv)을 불러오지 못했습니다.\n\n{e}\n\n"
            "README 의 설치 안내를 참고해 libmpv 를 설치해 주세요.")
        return 1

    files = [a for a in sys.argv[1:] if not a.startswith("-")]
    win = MainWindow(files)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
