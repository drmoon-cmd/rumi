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

    # 자가 진단(--selftest 결과파일): 사용자의 설정·재생 기록을 건드리지 않도록 다른 이름으로 실행
    selftest_out = None
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        selftest_out = os.path.abspath(sys.argv[i + 1]) if i + 1 < len(sys.argv) else os.path.abspath("selftest.txt")
        del sys.argv[i:i + 2]
    name = APP_NAME if selftest_out is None else APP_NAME + "SelfTest"

    app = QApplication(sys.argv)
    app.setApplicationName(name)
    app.setOrganizationName(name)

    # Qt 기본 대화상자 버튼(닫기/확인/취소 등)을 한국어로
    from PySide6.QtCore import QLibraryInfo, QTranslator
    translator = QTranslator(app)
    if translator.load("qtbase_ko", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(translator)

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
    selftest = None
    if selftest_out is not None:
        from .selftest import SelfTest
        selftest = SelfTest(win, selftest_out)
    code = app.exec()
    if selftest is not None:
        selftest.note("창을 닫고 정상적으로 끝남")
        code = selftest.exit_code()
    # 창을 닫은 뒤에는 남은 것을 기다리지 않고 곧바로 끝낸다. 재생 엔진이나 외부 도구가 어떤 이유로
    # 멈춰 있어도 프로그램이 뒤에 남아 CPU·그래픽 카드를 계속 쓰는 일이 없게 한다.
    from PySide6.QtCore import QSettings
    QSettings().sync()
    win.release_instance_lock()
    for stream in (sys.stdout, sys.stderr):  # Windows 설치판(창 모드)에서는 None 이다
        try:
            if stream is not None:
                stream.flush()
        except Exception:
            pass
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
