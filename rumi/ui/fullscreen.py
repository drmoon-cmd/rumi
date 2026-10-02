"""전체화면 들어가기/나오기 (메인 창, 분할 화면 창 공용).

Windows: OpenGL 창이 전체화면이 되면 '독점 전체화면'으로 취급되어 오른쪽 클릭 메뉴, 툴팁 같은
팝업이 뜨지 않는다 (Qt 문서의 알려진 문제). Qt 가 권하는 대로 창에 1픽셀 테두리(WS_BORDER)를
준다. 그런데 테두리만큼 영상 영역이 화면보다 작아지면 Qt 는 창이 더 이상 전체화면이 아니라고
판단해 상태를 '보통'으로 바꿔 버리고, 그 뒤로는 창 모드로 돌아가는 요청을 무시한다.
그래서 전체화면 여부는 직접 기억하고, 나올 때는 테두리를 먼저 없애 Qt 가 전체화면 상태를
다시 알아차리게 한 다음 창 모드로 돌린다.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

GWL_STYLE = -16
WS_BORDER = 0x00800000
WS_CAPTION = 0x00C00000
SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0020  # NOSIZE | NOMOVE | NOZORDER | FRAMECHANGED


def _user32():
    import ctypes
    user32 = ctypes.windll.user32
    user32.GetWindowLongPtrW.restype = user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    return user32


def _hwnd(widget: QWidget):
    import ctypes
    return ctypes.c_void_p(int(widget.winId()))


def _style(widget: QWidget) -> int | None:
    if sys.platform != "win32":
        return None
    try:
        user32 = _user32()
        return int(user32.GetWindowLongPtrW(_hwnd(widget), GWL_STYLE))
    except Exception:
        return None


def _set_style(widget: QWidget, style: int) -> None:
    if sys.platform != "win32":
        return
    try:
        user32 = _user32()
        hwnd = _hwnd(widget)
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style)
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FLAGS)
    except Exception:
        pass


def is_fullscreen(widget: QWidget) -> bool:
    return getattr(widget, "_rumi_fs", None) is not None


def enter_fullscreen(widget: QWidget) -> None:
    if is_fullscreen(widget):
        return
    widget._rumi_fs = {"maximized": widget.isMaximized(), "style": _style(widget),
                       "geometry": widget.saveGeometry()}
    widget.showFullScreen()
    style = _style(widget)
    if style is not None:
        _set_style(widget, style | WS_BORDER)


def leave_fullscreen(widget: QWidget) -> None:
    state = getattr(widget, "_rumi_fs", None)
    if state is None:
        return
    widget._rumi_fs = None
    style = _style(widget)
    if style is not None and style & WS_BORDER:
        # 테두리를 없애면 영상 영역이 다시 화면 크기가 되어 Qt 가 전체화면 상태로 돌아온다
        _set_style(widget, style & ~WS_BORDER)
        QApplication.processEvents()
    if not widget.isFullScreen():
        # 그래도 Qt 가 전체화면으로 알고 있지 않으면 상태를 직접 맞춘 뒤 되돌린다
        widget.setWindowState(widget.windowState() | Qt.WindowFullScreen)
    widget.showMaximized() if state["maximized"] else widget.showNormal()
    # 마지막 안전장치(Windows): 제목 표시줄이 사라진 채로 남으면 원래 창 모양·위치로 복원
    old, now = state["style"], _style(widget)
    if old is not None and now is not None and (old & WS_CAPTION) and not (now & WS_CAPTION):
        _set_style(widget, old)
        if state["maximized"]:
            widget.showMaximized()
        else:
            widget.restoreGeometry(state["geometry"])


def toggle_fullscreen(widget: QWidget) -> None:
    if is_fullscreen(widget):
        leave_fullscreen(widget)
    else:
        enter_fullscreen(widget)
