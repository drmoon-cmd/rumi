"""메인 플레이어 창."""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QSettings, QStandardPaths, Qt, QTimer, qVersion
from PySide6.QtGui import QAction, QActionGroup, QCursor, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMenu,
    QMessageBox, QToolButton, QWidget,
)

from .. import APP_NAME, __version__
from ..library import LibraryDB
from ..library.scanner import VIDEO_EXTENSIONS
from .controls import ControlBar
from .library_window import LibraryWindow
from ..subtitles import looks_like_sensor_data
from .mpv_widget import DEFAULT_HWDEC, HWDEC_MODES, MpvWidget
from ..clips import Clip
from ..dashcam import find_partner
from ..tools import find_ffmpeg
from ..media_tools import AudioSettings, build_audio_filter
from .capture import build_contact_sheet
from .clips_panel import ClipsPanel
from .dialogs import (
    MOUSE_DEFAULTS, SUB_DEFAULTS, AudioDialog, BookmarksDialog, PreferencesDialog, SubtitleStyleDialog,
    VideoAdjustDialog,
)
from .seek_preview import SeekPreview
from .workers import run_with_progress
from .pip import MAX_PIPS, PipView
from .playlist import PlaylistPanel
from .theme import DEFAULT_THEME, THEMES, apply_theme
from .util import fmt_time, reveal_in_file_manager

SUBTITLE_EXTENSIONS = {".srt", ".smi", ".sami", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup"}
MAX_RECENT = 10
RESUME_MIN_SECONDS = 10      # 이 시간 이상 본 파일만 이어보기
RESUME_END_MARGIN = 0.95     # 95% 이상 봤으면 다 본 것으로 간주
CONTROLS_HIDE_MS = 2500

SHORTCUT_HELP = """
<table cellpadding=3>
<tr><td><b>Space</b></td><td>재생 / 일시정지</td></tr>
<tr><td><b>← / →</b></td><td>5초 뒤로 / 앞으로</td></tr>
<tr><td><b>Ctrl + ← / →</b></td><td>30초 뒤로 / 앞으로</td></tr>
<tr><td><b>, / .</b></td><td>한 프레임 뒤로 / 앞으로</td></tr>
<tr><td><b>Home</b></td><td>처음으로</td></tr>
<tr><td><b>PgUp / PgDn</b></td><td>이전 / 다음 파일</td></tr>
<tr><td><b>↑ / ↓, 휠</b></td><td>볼륨</td></tr>
<tr><td><b>M</b></td><td>음소거</td></tr>
<tr><td><b>[ / ] / Backspace</b></td><td>느리게 / 빠르게 / 기본 속도</td></tr>
<tr><td><b>L</b></td><td>A-B 구간 반복</td></tr>
<tr><td><b>Enter, F, 더블클릭</b></td><td>전체화면</td></tr>
<tr><td><b>Esc</b></td><td>전체화면 해제</td></tr>
<tr><td><b>V</b></td><td>자막 보이기 / 숨기기</td></tr>
<tr><td><b>Z / X</b></td><td>자막 싱크 -0.1초 / +0.1초</td></tr>
<tr><td><b>S</b></td><td>스크린샷 (바탕화면)</td></tr>
<tr><td><b>F9 / F10</b></td><td>재생목록 / 구간 목록</td></tr>
<tr><td><b>I / O</b></td><td>구간 시작점 / 끝점(구간 추가)</td></tr>
<tr><td><b>Ctrl + P</b></td><td>PIP로 파일 열기</td></tr>
<tr><td><b>1/2 · 3/4 · 5/6 · 7/8</b></td><td>명암 · 밝기 · 감마 · 채도</td></tr>
<tr><td><b>Ctrl + 휠, Ctrl +/-/0</b></td><td>화면 확대 / 축소 / 초기화 (확대 중 끌어서 이동)</td></tr>
<tr><td><b>Ctrl + R</b></td><td>90° 회전</td></tr>
<tr><td><b>B / Ctrl + B</b></td><td>책갈피 추가 / 관리</td></tr>
<tr><td><b>N</b></td><td>소리 크기 자동 맞춤</td></tr>
<tr><td><b>Ctrl + [ / ] / Backspace</b></td><td>소리 싱크 -0.1초 / +0.1초 / 초기화</td></tr>
<tr><td><b>Ctrl + ,</b></td><td>환경 설정 (단축키·마우스)</td></tr>
<tr><td><b>Ctrl + L</b></td><td>라이브러리 / 중복 정리</td></tr>
</table>
"""


def _windows_fullscreen_border(widget: QWidget) -> None:
    """Windows: OpenGL 창이 전체화면이 되면 '독점 전체화면'으로 취급되어 오른쪽 클릭 메뉴,
    툴팁 같은 팝업이 뜨지 않는다 (Qt 문서의 알려진 문제). Qt 가 권하는 대로 창에 1픽셀
    테두리(WS_BORDER)를 줘서 일반 창으로 남게 한다."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        get_style = user32.GetWindowLongPtrW
        set_style = user32.SetWindowLongPtrW
        get_style.restype = set_style.restype = ctypes.c_ssize_t
        get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
        set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        GWL_STYLE, WS_BORDER = -16, 0x00800000
        SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0020  # NOSIZE | NOMOVE | NOZORDER | FRAMECHANGED
        hwnd = ctypes.c_void_p(int(widget.winId()))
        set_style(hwnd, GWL_STYLE, get_style(hwnd, GWL_STYLE) | WS_BORDER)
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FLAGS)
    except Exception:
        pass  # 실패해도 전체화면 자체는 동작한다


def data_dir() -> Path:
    return Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))


class TopBar(QWidget):
    """전체화면에서 화면 위쪽에 마우스를 대면 나타나는 제목·메뉴 줄."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("topBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("#topBar { background: rgba(20, 21, 24, 225); border-bottom: 1px solid #3a3d43; }"
                           "#topBar QLabel { color: #e6e7e9; font-weight: bold; }"
                           "#topBar QToolButton { color: #e6e7e9; padding: 5px 10px; }")
        self.menu_btn = QToolButton()
        self.menu_btn.setText("☰ 메뉴")
        self.menu_btn.setFocusPolicy(Qt.NoFocus)
        self.title = QLabel()
        self.exit_btn = QToolButton()
        self.exit_btn.setText("창 모드로 (Esc)")
        self.exit_btn.setFocusPolicy(Qt.NoFocus)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.addWidget(self.menu_btn)
        row.addSpacing(8)
        row.addWidget(self.title, 1)
        row.addWidget(self.exit_btn)
        self.hide()


class VideoArea(QWidget):
    """영상 + 컨트롤 바. 전체화면에서는 제목 줄과 컨트롤 바가 영상 위에 겹쳐 뜬다."""

    def __init__(self, video: MpvWidget, controls: ControlBar, parent=None):
        super().__init__(parent)
        self.video, self.controls = video, controls
        video.setParent(self)
        controls.setParent(self)
        self.topbar = TopBar(self)
        self.overlay = False
        self.setObjectName("videoArea")
        self.setStyleSheet("#videoArea { background: black; }")

    def set_overlay(self, on: bool) -> None:
        self.overlay = on
        self.controls.setVisible(not on)
        self.topbar.hide()
        self._relayout()

    def resizeEvent(self, e):
        self._relayout()
        super().resizeEvent(e)

    def _relayout(self) -> None:
        w, h = self.width(), self.height()
        ch = self.controls.sizeHint().height()
        self.video.setGeometry(0, 0, w, h if self.overlay else max(h - ch, 0))
        self.controls.setGeometry(0, h - ch, w, ch)
        self.controls.raise_()
        self.topbar.setGeometry(0, 0, w, self.topbar.sizeHint().height())
        self.topbar.raise_()
        for pip in self.findChildren(PipView):
            pip.move_within_parent(pip.pos())
            pip.raise_()


class MainWindow(QMainWindow):
    def __init__(self, files: list[str] | None = None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setAcceptDrops(True)
        self.settings = QSettings()
        self._theme = apply_theme(QApplication.instance(), self.settings.value("theme", DEFAULT_THEME))
        self.db = LibraryDB(data_dir() / "library.sqlite3")
        self._library: LibraryWindow | None = None
        self._current: str | None = None
        self._time = 0.0
        self._duration = 0.0
        self._was_maximized = False
        self._docks_visible = [True, True]
        self._closed = False
        self._tracks: list = []
        self._data_sub_checked = False
        self._hwdec = DEFAULT_HWDEC
        self._pips: list[PipView] = []
        self._clip_index: int | None = None   # 구간 이어 재생 중이면 현재 구간 번호
        self._mark_in: tuple[str, float] | None = None
        self._skip_resume = False  # 다음 파일 로드 때 이어보기 건너뛰기
        self._zoom = 0.0
        self._pan = [0.0, 0.0]
        self._mouse = dict(MOUSE_DEFAULTS)
        self._audio = AudioSettings()
        self._sub_style = dict(SUB_DEFAULTS)
        self._burst_left = 0

        self.video = MpvWidget()
        self.player = self.video.player
        self.controls = ControlBar()
        self.area = VideoArea(self.video, self.controls)
        self.setCentralWidget(self.area)

        self.playlist = PlaylistPanel()
        self.dock = QDockWidget("재생목록", self)
        self.dock.setObjectName("playlist")
        self.dock.setWidget(self.playlist)
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.clips_panel = ClipsPanel()
        self.clips_dock = QDockWidget("구간 목록", self)
        self.clips_dock.setObjectName("clips")
        self.clips_dock.setWidget(self.clips_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, self.clips_dock)
        self.tabifyDockWidget(self.dock, self.clips_dock)
        self.dock.raise_()

        self._hide_timer = QTimer(self, singleShot=True, interval=CONTROLS_HIDE_MS)
        self._hide_timer.timeout.connect(self._auto_hide_controls)
        self._save_timer = QTimer(self, interval=5000)
        self._save_timer.timeout.connect(self._save_position)
        self._save_timer.start()
        self._sync_timer = QTimer(self, interval=500)
        self._sync_timer.timeout.connect(self._sync_pips)
        self._sync_timer.start()

        self._build_actions()
        self._build_menus()
        self._connect()
        self._restore_settings()

        if files:
            QTimer.singleShot(0, lambda: self.open_paths(files))

    # ------------------------------------------------------------------ 액션
    def _act(self, text: str, slot, shortcut=None, checkable=False) -> QAction:
        a = QAction(text, self)
        if shortcut:
            if isinstance(shortcut, (list, tuple)):
                a.setShortcuts([QKeySequence(s) for s in shortcut])
            else:
                a.setShortcut(QKeySequence(shortcut))
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        self.addAction(a)  # 메뉴바가 숨겨진 전체화면에서도 단축키 동작
        return a

    def _build_actions(self) -> None:
        A = self._act
        self.a_open = A("파일 열기…", self.open_files_dialog, QKeySequence.Open)
        self.a_open_folder = A("폴더 열기…", self.open_folder_dialog, "Ctrl+Shift+O")
        self.a_open_url = A("URL 열기…", self.open_url_dialog, "Ctrl+U")
        self.a_quit = A("끝내기", self.close, QKeySequence.Quit)

        self.a_play = A("재생 / 일시정지", self.toggle_pause, "Space")
        self.a_stop = A("정지", self.stop)
        self.a_prev = A("이전 파일", lambda: self.play_neighbor(-1), "PgUp")
        self.a_next = A("다음 파일", lambda: self.play_neighbor(1), "PgDown")
        self.a_back5 = A("5초 뒤로", lambda: self.seek(-5), "Left")
        self.a_fwd5 = A("5초 앞으로", lambda: self.seek(5), "Right")
        self.a_back30 = A("30초 뒤로", lambda: self.seek(-30), "Ctrl+Left")
        self.a_fwd30 = A("30초 앞으로", lambda: self.seek(30), "Ctrl+Right")
        self.a_frame_back = A("한 프레임 뒤로", lambda: self.player.frame_back_step(), ",")
        self.a_frame_fwd = A("한 프레임 앞으로", lambda: self.player.frame_step(), ".")
        self.a_start = A("처음으로", lambda: self.seek(0, absolute=True), "Home")
        self.a_ab = A("A-B 구간 반복", self.ab_loop, "L")
        self.a_slower = A("느리게 (-0.1)", lambda: self.change_speed(-0.1), "[")
        self.a_faster = A("빠르게 (+0.1)", lambda: self.change_speed(0.1), "]")
        self.a_speed_reset = A("기본 속도", lambda: self.set_speed(1.0), "Backspace")

        self.a_vol_up = A("볼륨 크게", lambda: self.change_volume(5), "Up")
        self.a_vol_down = A("볼륨 작게", lambda: self.change_volume(-5), "Down")
        self.a_mute = A("음소거", self.toggle_mute, "M")

        self.a_sub_load = A("자막 파일 불러오기…", self.load_subtitle_dialog, "Alt+O")
        self.a_sub_toggle = A("자막 보이기", self.toggle_subtitles, "V", checkable=True)
        self.a_sub_toggle.setChecked(True)
        self.a_sub_earlier = A("자막 싱크 -0.1초", lambda: self.change_sub_delay(-0.1), "Z")
        self.a_sub_later = A("자막 싱크 +0.1초", lambda: self.change_sub_delay(0.1), "X")
        self.a_sub_reset = A("자막 싱크 초기화", lambda: self.change_sub_delay(None), "Shift+X")
        self.a_sub_bigger = A("자막 크게", lambda: self.change_sub_scale(0.1), "Alt+Up")
        self.a_sub_smaller = A("자막 작게", lambda: self.change_sub_scale(-0.1), "Alt+Down")

        self.a_full = A("전체화면", self.toggle_fullscreen, ["Return", "F", "Enter"], checkable=True)
        self.a_exit_full = A("전체화면 해제", lambda: self.isFullScreen() and self.toggle_fullscreen(), "Esc")
        self.a_ontop = A("항상 위", self.toggle_on_top, "Ctrl+T", checkable=True)
        self.a_screenshot = A("스크린샷", self.screenshot, "S")
        self.a_playlist = A("재생목록", lambda: self._toggle_dock(self.dock), "F9")
        self.a_clips = A("구간 목록", lambda: self._toggle_dock(self.clips_dock), "F10")
        self.a_mark_in = A("구간 시작점 찍기", self.mark_in, "I")
        self.a_mark_out = A("구간 끝점 찍기 (구간 추가)", self.mark_out, "O")
        self.a_pip_open = A("PIP로 파일 열기…", self.open_pip_dialog, "Ctrl+P")
        self.a_pip_current = A("지금 영상을 PIP로 하나 더 띄우기", lambda: self._current and self.add_pip(self._current, sync=True))
        self.a_pip_close = A("모든 PIP 닫기", self.close_all_pips)
        self.a_pip_auto = A("블랙박스 뒤 카메라 자동 PIP", self._on_pip_auto_toggled, checkable=True)

        self.a_library = A("라이브러리…", lambda: self.show_library(0), "Ctrl+L")
        self.a_dupes = A("중복 동영상 정리…", lambda: self.show_library(1), "Ctrl+D")

        self.a_keys = A("단축키 안내", self.show_shortcuts, "F1")
        self.a_about = A(f"{APP_NAME} 정보", self.show_about)
        self.a_diag = A("진단 정보 복사", self.copy_diagnostics)
        self.a_compat = A("호환 모드 (화면이 깨지거나 검게 나올 때)", lambda: self.set_compat_mode(), checkable=True)

        # 영상 보정 / 확대 / 회전
        self.a_contrast_dn = A("명암 -", lambda: self.adjust_video("contrast", -2), "1")
        self.a_contrast_up = A("명암 +", lambda: self.adjust_video("contrast", 2), "2")
        self.a_bright_dn = A("밝기 -", lambda: self.adjust_video("brightness", -2), "3")
        self.a_bright_up = A("밝기 +", lambda: self.adjust_video("brightness", 2), "4")
        self.a_gamma_dn = A("감마 -", lambda: self.adjust_video("gamma", -2), "5")
        self.a_gamma_up = A("감마 +", lambda: self.adjust_video("gamma", 2), "6")
        self.a_sat_dn = A("채도 -", lambda: self.adjust_video("saturation", -2), "7")
        self.a_sat_up = A("채도 +", lambda: self.adjust_video("saturation", 2), "8")
        self.a_video_adjust = A("영상 보정…", self.show_video_adjust, "Ctrl+E")
        self.a_zoom_in = A("화면 확대", lambda: self.change_zoom(0.1), ["Ctrl+=", "Ctrl++"])
        self.a_zoom_out = A("화면 축소", lambda: self.change_zoom(-0.1), "Ctrl+-")
        self.a_zoom_reset = A("확대 초기화", self.reset_zoom, "Ctrl+0")
        self.a_rotate = A("90° 회전", self.rotate, "Ctrl+R")
        self.a_hflip = A("좌우 반전", lambda: self.toggle_flip("hflip", self.a_hflip), checkable=True)
        self.a_vflip = A("상하 반전", lambda: self.toggle_flip("vflip", self.a_vflip), checkable=True)
        self.a_transform_reset = A("회전·반전 초기화", self.reset_transform)

        # 편의 기능
        self.a_bookmark_add = A("책갈피 추가", self.add_bookmark, "B")
        self.a_bookmarks = A("책갈피 관리…", self.show_bookmarks, "Ctrl+B")
        self.a_burst = A("연속 캡처…", self.burst_capture)
        self.a_sheet = A("장면 모음 만들기…", self.make_contact_sheet)
        self.a_prefs = A("환경 설정 (단축키·마우스)…", self.show_preferences, "Ctrl+,")

        # 소리 / 자막
        self.a_audio = A("소리 설정 (이퀄라이저·싱크)…", self.show_audio_dialog)
        self.a_normalize = A("소리 크기 자동 맞춤", self.toggle_normalize, "N", checkable=True)
        self.a_adelay_minus = A("소리 싱크 -0.1초", lambda: self.change_audio_delay(-0.1), "Ctrl+[")
        self.a_adelay_plus = A("소리 싱크 +0.1초", lambda: self.change_audio_delay(0.1), "Ctrl+]")
        self.a_adelay_reset = A("소리 싱크 초기화", lambda: self.change_audio_delay(None), "Ctrl+Backspace")
        self.a_sub_style = A("자막 모양 (글꼴·색·위치)…", self.show_sub_style)

        # 단축키 사용자 지정을 위해 각 동작에 고정 이름을 붙이고 기본 단축키를 기억한다
        self._default_shortcuts: dict[str, str] = {}
        for name, value in list(vars(self).items()):
            if name.startswith("a_") and isinstance(value, QAction):
                value.setObjectName(name)
                self._default_shortcuts[name] = value.shortcut().toString(QKeySequence.PortableText)

    def _build_menus(self) -> None:
        mb = self.menuBar()

        m = mb.addMenu("파일(&F)")
        m.addActions([self.a_open, self.a_open_folder, self.a_open_url])
        self.recent_menu = m.addMenu("최근 파일")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        m.addSeparator()
        m.addAction(self.a_prefs)
        m.addSeparator()
        m.addAction(self.a_quit)

        m = mb.addMenu("재생(&P)")
        m.addActions([self.a_play, self.a_stop])
        m.addSeparator()
        m.addActions([self.a_prev, self.a_next])
        m.addSeparator()
        m.addActions([self.a_back5, self.a_fwd5, self.a_back30, self.a_fwd30,
                      self.a_frame_back, self.a_frame_fwd, self.a_start])
        m.addSeparator()
        speed = m.addMenu("재생 속도")
        speed.addActions([self.a_slower, self.a_faster, self.a_speed_reset])
        speed.addSeparator()
        for s in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
            speed.addAction(f"{s}x", lambda s=s: self.set_speed(s))
        m.addAction(self.a_ab)
        self.bookmark_menu = m.addMenu("책갈피")
        self.bookmark_menu.aboutToShow.connect(self._fill_bookmark_menu)
        m.addSeparator()
        clip = m.addMenu("구간 잘라 이어 보기")
        clip.addActions([self.a_mark_in, self.a_mark_out, self.a_clips])

        m = mb.addMenu("오디오(&A)")
        m.addActions([self.a_vol_up, self.a_vol_down, self.a_mute])
        m.addSeparator()
        self.audio_menu = m.addMenu("오디오 트랙")
        m.addSeparator()
        m.addActions([self.a_normalize, self.a_audio, self.a_adelay_minus, self.a_adelay_plus, self.a_adelay_reset])

        m = mb.addMenu("자막(&S)")
        m.addAction(self.a_sub_load)
        self.sub_menu = m.addMenu("자막 트랙")
        m.addSeparator()
        m.addActions([self.a_sub_toggle, self.a_sub_earlier, self.a_sub_later, self.a_sub_reset,
                      self.a_sub_bigger, self.a_sub_smaller])
        m.addSeparator()
        m.addAction(self.a_sub_style)

        m = mb.addMenu("화면(&V)")
        m.addActions([self.a_full, self.a_ontop, self.a_playlist])
        aspect = m.addMenu("화면 비율")
        group = QActionGroup(self)
        for label, value in (("자동", "-1"), ("16:9", "16:9"), ("4:3", "4:3"),
                             ("2.35:1", "2.35:1"), ("1.85:1", "1.85:1")):
            a = aspect.addAction(label, lambda v=value: self._set_prop("video_aspect_override", v))
            a.setCheckable(True)
            a.setChecked(value == "-1")
            group.addAction(a)
        m.addAction(self.a_video_adjust)
        zoom = m.addMenu("확대 / 축소")
        zoom.addActions([self.a_zoom_in, self.a_zoom_out, self.a_zoom_reset])
        rot = m.addMenu("회전 / 반전")
        rot.addActions([self.a_rotate, self.a_hflip, self.a_vflip, self.a_transform_reset])
        m.addSeparator()
        hw = m.addMenu("하드웨어 가속")
        self.hwdec_group = QActionGroup(self)
        for value, label in HWDEC_MODES.items():
            a = hw.addAction(label, lambda v=value: self.set_hwdec(v))
            a.setCheckable(True)
            a.setData(value)
            self.hwdec_group.addAction(a)
        m.addAction(self.a_compat)
        theme = m.addMenu("테마")
        self.theme_group = QActionGroup(self)
        for key, label in THEMES.items():
            a = theme.addAction(label, lambda k=key: self.set_theme(k))
            a.setCheckable(True)
            a.setChecked(key == self._theme)
            self.theme_group.addAction(a)
        m.addSeparator()
        pip = m.addMenu("PIP (화면 속 화면)")
        pip.addActions([self.a_pip_open, self.a_pip_current, self.a_pip_auto, self.a_pip_close])
        m.addSeparator()
        cap = m.addMenu("캡처")
        cap.addActions([self.a_screenshot, self.a_burst, self.a_sheet])

        m = mb.addMenu("라이브러리(&L)")
        m.addActions([self.a_library, self.a_dupes])

        m = mb.addMenu("도움말(&H)")
        m.addActions([self.a_keys, self.a_diag, self.a_about])

        self._rebuild_track_menus([])

    def _connect(self) -> None:
        v, c = self.video, self.controls
        v.time_changed.connect(self._on_time)
        v.duration_changed.connect(self._on_duration)
        v.pause_changed.connect(c.set_paused)
        v.volume_changed.connect(c.set_volume)
        v.mute_changed.connect(c.set_muted)
        v.speed_changed.connect(c.set_speed)
        v.eof_reached.connect(self._on_eof)
        v.file_loaded.connect(self._on_file_loaded)
        v.load_failed.connect(self._on_load_failed)
        v.tracks_changed.connect(self._rebuild_track_menus)
        v.sub_text_changed.connect(self._check_data_subtitle)
        v.renderer_ready.connect(lambda: self.a_compat.setChecked(v.compat_mode))
        v.clicked.connect(lambda: self._mouse_action("left_click"))
        v.double_clicked.connect(lambda: self._mouse_action("double_click"))
        v.middle_clicked.connect(lambda: self._mouse_action("middle_click"))
        v.wheel_scrolled.connect(self._on_wheel)
        v.dragged.connect(self._on_drag)
        v.mouse_moved.connect(self._on_mouse_activity)

        c.play_pause.connect(self.toggle_pause)
        c.stop.connect(self.stop)
        c.prev.connect(lambda: self.play_neighbor(-1))
        c.next.connect(lambda: self.play_neighbor(1))
        c.seek_ratio.connect(self._seek_ratio)
        c.volume_set.connect(lambda val: self._set_prop("volume", val))
        c.mute_toggle.connect(self.toggle_mute)
        c.fullscreen_toggle.connect(self.toggle_fullscreen)
        c.playlist_toggle.connect(self.a_playlist.trigger)
        v.context_menu_requested.connect(lambda pos: self.main_menu().exec(pos))
        top = self.area.topbar
        top.menu_btn.clicked.connect(
            lambda: self.main_menu().exec(top.menu_btn.mapToGlobal(top.menu_btn.rect().bottomLeft())))
        top.exit_btn.clicked.connect(self.toggle_fullscreen)
        self.windowTitleChanged.connect(top.title.setText)
        self.seek_preview = SeekPreview(self)
        c.seek_hover.connect(self._on_seek_hover)
        c.seek_hover_end.connect(self.seek_preview.hide_preview)

        self.playlist.play_requested.connect(self.play)
        self.clips_panel.play_requested.connect(self.play_clips)
        v.pause_changed.connect(lambda _p: self._sync_pips(force=True))
        v.start_observing()

    # ------------------------------------------------------------------ 설정
    def _restore_settings(self) -> None:
        s = self.settings
        if geo := s.value("geometry"):
            self.restoreGeometry(geo)
        else:
            self.resize(1280, 760)
        if state := s.value("windowState"):
            self.restoreState(state)
        self.player.volume = float(s.value("volume", 100))
        desktop = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation)
        if desktop and os.path.isdir(desktop):
            self.player["screenshot-directory"] = desktop
        # 0.1.1 까지 저장된 'hwdec' 값은 무시하고 새 기본값(끄기)부터 시작한다
        self.set_hwdec(s.value("hwdecMode", DEFAULT_HWDEC), announce=False)
        self.a_compat.setChecked(s.value("compatMode", False, type=bool))
        self.set_compat_mode(announce=False)
        self.a_pip_auto.setChecked(s.value("pipAuto", False, type=bool))
        self.playlist.set_repeat_mode(s.value("repeat", "none"))
        try:
            self.playlist.restore_state(json.loads(s.value("playlists", "") or "{}"))
        except (ValueError, TypeError):
            pass  # 저장된 재생목록이 깨졌으면 빈 목록으로 시작
        self._mouse = {**MOUSE_DEFAULTS, **self._load_json("mouse")}
        a = self._load_json("audio")
        self._audio = AudioSettings(bool(a.get("normalize", False)), int(a.get("bass", 0)), int(a.get("treble", 0)))
        self.apply_audio(announce=False)
        self._sub_style = {**SUB_DEFAULTS, **self._load_json("subStyle")}
        self.apply_sub_style(self._sub_style)
        self.apply_shortcuts(self._load_json("shortcuts"))

    def _load_json(self, key: str) -> dict:
        try:
            value = json.loads(self.settings.value(key, "") or "{}")
            return value if isinstance(value, dict) else {}
        except (ValueError, TypeError):
            return {}


    def closeEvent(self, e):
        if self._closed:
            return super().closeEvent(e)
        self._save_position()
        self._save_timer.stop()
        self._hide_timer.stop()
        if self.isFullScreen():
            self.toggle_fullscreen()
        s = self.settings
        s.setValue("geometry", self.saveGeometry())
        s.setValue("windowState", self.saveState())
        s.setValue("volume", self.player.volume)
        s.setValue("repeat", self.playlist.repeat_mode().value)
        s.setValue("playlists", json.dumps(self.playlist.to_state(), ensure_ascii=False))
        s.setValue("mouse", json.dumps(self._mouse))
        s.setValue("audio", json.dumps({"normalize": self._audio.normalize, "bass": self._audio.bass,
                                        "treble": self._audio.treble}))
        s.setValue("subStyle", json.dumps(self._sub_style, ensure_ascii=False))
        s.setValue("hwdecMode", self._hwdec)
        s.setValue("compatMode", self.a_compat.isChecked())
        s.setValue("pipAuto", self.a_pip_auto.isChecked())
        if self._library:
            self._library.close()
        for pip in list(self._pips):
            pip.close_pip()
        self._sync_timer.stop()
        self.video.shutdown()
        self.db.close()
        self._closed = True
        super().closeEvent(e)

    # ------------------------------------------------------------------ 열기
    def open_paths(self, paths: list[str], play: bool = True) -> None:
        subs = [p for p in paths if Path(p).suffix.lower() in SUBTITLE_EXTENSIONS]
        media = [p for p in paths if p not in subs]
        if media:
            self.playlist.set_files(media)
            first = self.playlist.paths()
            if first and play:
                self.play(first[0])
        for s in subs:
            self.add_subtitle(s)

    def enqueue(self, paths: list[str]) -> None:
        empty = not self.playlist.paths()
        self.playlist.add_files(paths)
        if empty and self.playlist.paths():
            self.play(self.playlist.paths()[0])

    def open_files_dialog(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(
            self, "파일 열기", self.settings.value("lastDir", ""),
            f"동영상 ({exts});;모든 파일 (*)")
        if files:
            self.settings.setValue("lastDir", os.path.dirname(files[0]))
            self.open_paths(files)

    def open_folder_dialog(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "폴더 열기", self.settings.value("lastDir", ""))
        if d:
            self.settings.setValue("lastDir", d)
            self.open_paths([d])

    def open_url_dialog(self) -> None:
        url, ok = QInputDialog.getText(self, "URL 열기", "스트림 주소 (http, https, rtsp 등):")
        if ok and url.strip():
            self.open_paths([url.strip()])

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.open_paths(paths)

    # ------------------------------------------------------------------ 재생
    def play(self, path: str, start: float | None = None) -> None:
        """파일 재생. start 를 주면 이어보기 대신 그 위치부터 재생한다."""
        self._end_clip_mode()
        self._save_position()
        self._skip_resume = start is not None
        self.seek_preview.clear_cache()
        self._current = path
        self._time, self._duration = 0.0, 0.0
        self._data_sub_checked = False
        self.controls.reset()
        self.playlist.set_current(path)
        self.video.load(path, **({"start": f"{start:.3f}"} if start is not None else {}))
        self.player.pause = False
        self.setWindowTitle(f"{os.path.basename(path) or path} - {APP_NAME}")
        self._add_recent(path)

    def play_neighbor(self, step: int, auto: bool = False) -> None:
        nxt = self.playlist.neighbor(step, auto=auto)
        if nxt:
            self.play(nxt)
        elif not auto:
            self.osd("목록의 처음/끝입니다")

    def stop(self) -> None:
        self._save_position()
        self.player.command("stop")
        self._current = None
        self.controls.reset()
        self.controls.set_paused(True)
        self.setWindowTitle(APP_NAME)

    def toggle_pause(self) -> None:
        if self._current is None:
            paths = self.playlist.paths()
            if paths:
                self.play(self.playlist.current_path() or paths[0])
            else:
                self.open_files_dialog()
            return
        if self.player.eof_reached:
            self.seek(0, absolute=True)
        self.player.pause = not self.player.pause

    def seek(self, seconds: float, absolute: bool = False) -> None:
        if self._current is None:
            return
        try:
            self.player.seek(seconds, "absolute" if absolute else "relative", "exact")
        except SystemError:
            return  # 아직 파일이 열리는 중
        if not absolute:
            self.osd(f"{'+' if seconds > 0 else ''}{seconds:g}초")

    def _seek_ratio(self, ratio: float) -> None:
        if self._duration > 0:
            self.seek(ratio * self._duration, absolute=True)

    def change_speed(self, delta: float) -> None:
        self.set_speed(round((self.player.speed or 1.0) + delta, 2))

    def set_speed(self, s: float) -> None:
        s = min(max(s, 0.1), 4.0)
        self.player.speed = s
        self.osd(f"재생 속도 {s:.2f}x")

    def ab_loop(self) -> None:
        self.player.command("ab-loop")
        a, b = self.player.ab_loop_a, self.player.ab_loop_b
        if a in (None, "no"):
            self.osd("구간 반복 해제")
        elif b in (None, "no"):
            self.osd(f"구간 반복 시작점: {fmt_time(float(a))}")
        else:
            self.osd(f"구간 반복: {fmt_time(float(a))} ~ {fmt_time(float(b))}")

    def change_volume(self, delta: int) -> None:
        v = min(max((self.player.volume or 0) + delta, 0), 130)
        self.player.volume = v
        self.osd(f"볼륨 {int(v)}%")

    def toggle_mute(self) -> None:
        self.player.mute = not self.player.mute
        self.osd("음소거" if self.player.mute else "음소거 해제")

    def screenshot(self) -> None:
        if self._current:
            self.player.screenshot()
            self.osd("스크린샷을 바탕화면에 저장했습니다")

    def osd(self, text: str) -> None:
        try:
            self.player.show_text(text, 1500)
        except SystemError:
            pass

    def _set_prop(self, name: str, value) -> None:
        try:
            setattr(self.player, name, value)
        except (SystemError, AttributeError, TypeError):
            pass

    def set_hwdec(self, mode: str, announce: bool = True) -> None:
        if mode not in HWDEC_MODES:
            mode = DEFAULT_HWDEC
        self._hwdec = mode
        self._set_prop("hwdec", mode)
        for pip in self._pips:
            pip.player.hwdec = mode
        for a in self.hwdec_group.actions():
            a.setChecked(a.data() == mode)
        if announce:
            msg = f"하드웨어 가속: {HWDEC_MODES[mode]}"
            # 일부 그래픽 드라이버는 하드웨어 가속 + 고화질 스케일러 조합에서 화면이 깨진다.
            # 호환 모드를 함께 켜면 해결되므로 하드웨어 가속을 켤 때 같이 켠다 (원하면 다시 끌 수 있음).
            if mode != "no" and not self.a_compat.isChecked():
                self.a_compat.setChecked(True)
                self.set_compat_mode(announce=False)
                msg += " · 호환 모드도 함께 켰습니다"
            self.osd(msg)

    def set_theme(self, name: str) -> None:
        self._theme = apply_theme(QApplication.instance(), name)
        self.settings.setValue("theme", self._theme)
        self.controls.refresh_icons()

    def set_compat_mode(self, announce: bool = True) -> None:
        self.video.set_compat_mode(self.a_compat.isChecked())
        for pip in self._pips:
            pip.video.set_compat_mode(self.a_compat.isChecked())
        if self.video.software_rendering:
            self.a_compat.setChecked(True)
        if announce:
            self.osd("호환 모드 켜짐" if self.video.compat_mode else "호환 모드 꺼짐")

    def copy_diagnostics(self) -> None:
        p = self.player

        def prop(name):
            try:
                return getattr(p, name.replace("-", "_"))
            except Exception:
                return None

        vp = prop("video-params") or {}
        info = "\n".join([
            f"{APP_NAME} {__version__}",
            f"OS: {platform.platform()}",
            f"Qt: {qVersion()}",
            f"mpv: {prop('mpv-version')}",
            f"OpenGL: {self.video.gl_renderer or '(초기화 전)'}",
            f"하드웨어 가속 설정: {self._hwdec} / 실제 사용: {prop('hwdec-current') or 'no'}",
            f"호환 모드: {'켜짐' if self.video.compat_mode else '꺼짐'}",
            f"ffmpeg(내보내기): {find_ffmpeg() or '없음'}",
            f"영상 코덱: {prop('video-codec')}",
            f"영상 형식: {vp.get('w')}x{vp.get('h')} {vp.get('pixelformat')}",
            f"재생 위치: {fmt_time(self._time)} / {fmt_time(self._duration or None)}",
        ])
        info += "\n\n[메인 영상]\n" + "\n".join(self.video.diagnostics())
        for i, pip in enumerate(self._pips, 1):
            info += f"\n\n[PIP {i}] 동기화={'켜짐' if pip.sync.isChecked() else '꺼짐'}\n" + "\n".join(pip.video.diagnostics())
        QApplication.clipboard().setText(info)
        box = QMessageBox(QMessageBox.Information, "진단 정보",
                          "진단 정보를 복사했습니다. 그대로 붙여 넣어 보내 주세요.\n\n"
                          + "\n".join(info.splitlines()[:10]), parent=self)
        box.setDetailedText(info)
        box.exec()

    # ------------------------------------------------------------------ 자막/트랙
    def load_subtitle_dialog(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(SUBTITLE_EXTENSIONS))
        start = os.path.dirname(self._current) if self._current and os.path.exists(self._current) else ""
        f, _ = QFileDialog.getOpenFileName(self, "자막 불러오기", start, f"자막 ({exts});;모든 파일 (*)")
        if f:
            self.add_subtitle(f)

    def add_subtitle(self, path: str) -> None:
        if self._current is None:
            return
        try:
            self.player.sub_add(path)
            self.osd(f"자막: {os.path.basename(path)}")
        except SystemError:
            QMessageBox.warning(self, "자막", f"자막을 불러오지 못했습니다:\n{path}")

    def toggle_subtitles(self) -> None:
        on = not bool(self.player.sub_visibility)
        self.player.sub_visibility = on
        self.a_sub_toggle.setChecked(on)
        self.osd("자막 보이기" if on else "자막 숨기기")

    def change_sub_delay(self, delta: float | None) -> None:
        d = 0.0 if delta is None else round((self.player.sub_delay or 0) + delta, 2)
        self.player.sub_delay = d
        self.osd(f"자막 싱크 {d:+.1f}초")

    def change_sub_scale(self, delta: float) -> None:
        s = min(max(round((self.player.sub_scale or 1.0) + delta, 2), 0.3), 3.0)
        self.player.sub_scale = s
        self.osd(f"자막 크기 {int(s * 100)}%")

    def _check_data_subtitle(self, text: str) -> None:
        """블랙박스 영상의 G-센서 데이터 트랙이 자막으로 보이면 한 번 숨긴다 (메뉴에서 다시 켤 수 있음)."""
        if self._data_sub_checked or not text:
            return
        self._data_sub_checked = True
        if not looks_like_sensor_data(text):
            return
        track = next((t for t in self._tracks if t.get("type") == "sub" and t.get("selected")), None)
        if track and not track.get("external"):
            self._set_prop("sid", "no")
            self.osd("센서 데이터 트랙이라 자막을 숨겼습니다 (자막 메뉴에서 다시 켤 수 있음)")

    def _rebuild_track_menus(self, tracks: list) -> None:
        self._tracks = tracks
        for menu, kind, prop in ((self.audio_menu, "audio", "aid"), (self.sub_menu, "sub", "sid")):
            menu.clear()
            group = QActionGroup(menu)
            items = [t for t in tracks if t.get("type") == kind]
            if kind == "sub":
                off = menu.addAction("끄기", lambda p=prop: self._set_prop(p, "no"))
                off.setCheckable(True)
                off.setChecked(not any(t.get("selected") for t in items))
                group.addAction(off)
            for t in items:
                parts = [t.get("title"), t.get("lang"), t.get("codec")]
                label = f"{t['id']}: " + " · ".join(str(p) for p in parts if p)
                if t.get("external"):
                    label += " (외부)"
                a = menu.addAction(label, lambda p=prop, i=t["id"]: self._set_prop(p, i))
                a.setCheckable(True)
                a.setChecked(bool(t.get("selected")))
                group.addAction(a)
            menu.setEnabled(bool(items))

    # ------------------------------------------------------------------ 상태 이벤트
    def _on_time(self, t: float) -> None:
        self._time = t
        self.controls.set_time(t)

    def _on_duration(self, d: float) -> None:
        self._duration = d
        self.controls.set_duration(d)

    def _on_file_loaded(self) -> None:
        path = self._current
        if not path:
            return
        if self._clip_index is not None:
            return  # 구간 재생은 이어보기 없이 구간 시작점부터
        self._update_auto_pip()
        if self._skip_resume:
            self._skip_resume = False
            return
        pos = self.db.load_position(path)
        dur = self.player.duration or 0
        if pos and pos >= RESUME_MIN_SECONDS and (not dur or pos < dur * RESUME_END_MARGIN):
            self.seek(pos, absolute=True)
            self.osd(f"이어보기 {fmt_time(pos)}  (처음부터: Home)")

    def _on_load_failed(self, msg: str) -> None:
        name = os.path.basename(self._current or "") or self._current
        self.osd(f"재생할 수 없습니다: {name}")
        self.statusBar().showMessage(f"재생 실패: {self._current} ({msg})", 8000)

    def _on_eof(self) -> None:
        if self._clip_index is not None:
            self.play_clips(self._clip_index + 1)
            return
        if self._current:
            self.db.clear_position(self._current)
        nxt = self.playlist.neighbor(1, auto=True)
        if nxt:
            self.play(nxt)

    def _save_position(self) -> None:
        path = self._current
        if self._closed or self._clip_index is not None or not path or "://" in path or self._time <= 0:
            return
        if self._duration and self._time >= self._duration * RESUME_END_MARGIN:
            self.db.clear_position(path)
        elif self._time >= RESUME_MIN_SECONDS:
            self.db.save_position(path, self._time, self._duration or None)

    # ------------------------------------------------------------------ 마우스
    def _mouse_action(self, key: str) -> None:
        action = self._mouse.get(key, "none")
        {"pause": self.toggle_pause, "fullscreen": self.toggle_fullscreen,
         "mute": self.toggle_mute}.get(action, lambda: None)()

    def _on_wheel(self, direction: int, ctrl: bool) -> None:
        action = self._mouse.get("ctrl_wheel" if ctrl else "wheel", "none")
        if action == "volume":
            self.change_volume(5 * direction)
        elif action == "seek":
            self.seek(5 * direction)
        elif action == "zoom":
            self.change_zoom(0.1 * direction)

    def _on_drag(self, dx: float, dy: float) -> None:
        """확대 중일 때 끌어서 화면 이동."""
        if self._zoom <= 0:
            return
        scale = 2 ** self._zoom
        self._pan[0] = min(max(self._pan[0] + dx / scale, -0.5), 0.5)
        self._pan[1] = min(max(self._pan[1] + dy / scale, -0.5), 0.5)
        self._set_prop("video_pan_x", self._pan[0])
        self._set_prop("video_pan_y", self._pan[1])

    def _on_seek_hover(self, ratio: float, pos) -> None:
        if self._duration > 0:
            self.seek_preview.show_at(self._current, ratio * self._duration, pos)

    # ------------------------------------------------------------------ 영상 보정 / 확대 / 회전
    VIDEO_LABELS = {"brightness": "밝기", "contrast": "명암", "saturation": "채도", "gamma": "감마"}

    def adjust_video(self, prop: str, delta: int) -> None:
        value = min(max(int(getattr(self.player, prop) or 0) + delta, -100), 100)
        self._set_prop(prop, value)
        self.osd(f"{self.VIDEO_LABELS[prop]} {value:+d}")

    def show_video_adjust(self) -> None:
        VideoAdjustDialog(self.player, self).show()

    def change_zoom(self, delta: float) -> None:
        self._zoom = round(min(max(self._zoom + delta, 0.0), 3.0), 2)
        self._set_prop("video_zoom", self._zoom)
        if self._zoom == 0:
            self._pan = [0.0, 0.0]
            self._set_prop("video_pan_x", 0)
            self._set_prop("video_pan_y", 0)
        self.osd(f"화면 확대 {2 ** self._zoom * 100:.0f}%" + ("  (끌어서 이동)" if self._zoom > 0 else ""))

    def reset_zoom(self) -> None:
        self._zoom = 0.1
        self.change_zoom(-0.1)

    def rotate(self) -> None:
        angle = (int(self.player.video_rotate or 0) + 90) % 360
        self._set_prop("video_rotate", angle)
        self.osd(f"회전 {angle}°")

    def toggle_flip(self, name: str, action: QAction) -> None:
        try:
            self.player.command("vf", "toggle", name)
        except SystemError:
            return
        self.osd(("좌우" if name == "hflip" else "상하") + (" 반전" if action.isChecked() else " 반전 해제"))

    def reset_transform(self) -> None:
        self._set_prop("video_rotate", 0)
        try:
            self.player.command("vf", "clr", "")
        except SystemError:
            pass
        self.a_hflip.setChecked(False)
        self.a_vflip.setChecked(False)
        self.osd("회전·반전 초기화")

    # ------------------------------------------------------------------ 책갈피 / 캡처
    def add_bookmark(self) -> None:
        if not self._current or "://" in self._current:
            return
        self.db.add_bookmark(self._current, self._time)
        self.osd(f"책갈피 추가 {fmt_time(self._time)}  (관리: Ctrl+B)")

    def _fill_bookmark_menu(self) -> None:
        m = self.bookmark_menu
        m.clear()
        m.addActions([self.a_bookmark_add, self.a_bookmarks])
        marks = self.db.bookmarks(self._current) if self._current else []
        if marks:
            m.addSeparator()
        for _id, pos, name in marks:
            m.addAction(f"{fmt_time(pos)}  {name}".rstrip(), lambda p=pos: self.seek(p, absolute=True))

    def show_bookmarks(self) -> None:
        if not self._current:
            return
        dlg = BookmarksDialog(self.db, self._current, self)
        dlg.jump.connect(lambda p: self.seek(p, absolute=True))
        dlg.show()

    def burst_capture(self) -> None:
        if not self._current:
            return
        count, ok = QInputDialog.getInt(self, "연속 캡처", "몇 장을 찍을까요?", 10, 2, 200)
        if not ok:
            return
        interval, ok = QInputDialog.getDouble(self, "연속 캡처", "간격 (초):", 1.0, 0.1, 60, 1)
        if not ok:
            return
        self._burst_left = count
        self._burst_timer = QTimer(self, interval=int(interval * 1000))
        self._burst_timer.timeout.connect(self._burst_tick)
        self.player.pause = False
        self._burst_tick()
        self._burst_timer.start()

    def _burst_tick(self) -> None:
        if self._burst_left <= 0 or not self._current:
            self._burst_timer.stop()
            self.osd("연속 캡처를 마쳤습니다 (바탕화면)")
            return
        self.player.screenshot()
        self._burst_left -= 1
        self.osd(f"연속 캡처 · 남은 {self._burst_left}장")

    def make_contact_sheet(self) -> None:
        path, duration = self._current, self._duration
        if not path or "://" in path or duration <= 0:
            QMessageBox.information(self, "장면 모음", "먼저 동영상을 재생해 주세요.")
            return
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            QMessageBox.critical(self, "장면 모음", "변환 도구(ffmpeg)를 찾지 못했습니다.")
            return
        default = os.path.splitext(path)[0] + "_장면모음.jpg"
        out, _ = QFileDialog.getSaveFileName(self, "장면 모음 저장", default,
                                             "JPEG 이미지 (*.jpg);;PNG 이미지 (*.png)")
        if not out:
            return
        vp = self.player.video_params or {}
        codec = (self.player.video_codec or "").split(" / ")[0]  # 'H.264 / AVC / ...' → 'H.264'
        info = f"{vp.get('w')}x{vp.get('h')}  {codec}".strip()

        def job(worker):
            return build_contact_sheet(
                ffmpeg, path, duration, info, 4, 4, 360,
                progress=lambda i, n: worker.progress.emit(f"장면 가져오는 중… ({i}/{n})", i, n),
                cancel=worker.is_cancelled)

        def done(img):
            if img.save(out, quality=90):
                if QMessageBox.question(self, "장면 모음",
                                        f"저장했습니다.\n{out}\n\n파일 위치를 열까요?") == QMessageBox.Yes:
                    reveal_in_file_manager(out)
            else:
                QMessageBox.warning(self, "장면 모음", f"저장하지 못했습니다:\n{out}")

        self._sheet_worker = run_with_progress(self, "장면 모음 만들기", job, done,
                                               lambda msg: QMessageBox.critical(self, "장면 모음", msg))

    # ------------------------------------------------------------------ 소리
    def apply_audio(self, announce: bool = True) -> None:
        self._set_prop("af", build_audio_filter(self._audio))
        self.a_normalize.setChecked(self._audio.normalize)
        if announce:
            self.osd("소리 효과 적용" if not self._audio.is_default() else "소리 효과 끔")

    def toggle_normalize(self) -> None:
        self._audio.normalize = self.a_normalize.isChecked()
        self.apply_audio(announce=False)
        self.osd("소리 크기 자동 맞춤 켜짐" if self._audio.normalize else "소리 크기 자동 맞춤 꺼짐")

    def show_audio_dialog(self) -> None:
        dlg = AudioDialog(self._audio, float(self.player.audio_delay or 0), self)

        def changed(settings):
            self._audio = settings
            self.apply_audio(announce=False)

        dlg.changed.connect(changed)
        dlg.delay_changed.connect(lambda d: self._set_prop("audio_delay", d))
        dlg.show()

    def change_audio_delay(self, delta: float | None) -> None:
        d = 0.0 if delta is None else round(float(self.player.audio_delay or 0) + delta, 2)
        self._set_prop("audio_delay", d)
        self.osd(f"소리 싱크 {d:+.1f}초")

    # ------------------------------------------------------------------ 자막 모양
    def apply_sub_style(self, style: dict) -> None:
        self._sub_style = {**SUB_DEFAULTS, **style}
        st = self._sub_style
        p = self.player

        def opt(name, value):
            try:
                p[name] = value
            except Exception:
                pass  # 재생 엔진 버전에 없는 옵션은 건너뜀

        if st["font"]:
            opt("sub-font", st["font"])
        opt("sub-font-size", int(st["size"]))
        opt("sub-color", st["color"])
        opt("sub-border-color", st["border_color"])
        opt("sub-border-size", float(st["border_size"]))
        opt("sub-pos", int(st["position"]))
        opt("sub-ass-override", "force" if st["override_ass"] else "scale")
        if st["background"]:
            opt("sub-back-color", "#99000000")
            opt("sub-border-style", "background-box")
        else:
            opt("sub-border-style", "outline-and-shadow")

    def show_sub_style(self) -> None:
        dlg = SubtitleStyleDialog(self._sub_style, self)
        dlg.changed.connect(self.apply_sub_style)
        dlg.show()

    # ------------------------------------------------------------------ 환경 설정
    def _shortcut_actions(self) -> list[QAction]:
        return [a for name, a in vars(self).items() if name.startswith("a_") and isinstance(a, QAction)]

    def apply_shortcuts(self, overrides: dict) -> None:
        for a in self._shortcut_actions():
            key = overrides.get(a.objectName())
            if key is not None:
                a.setShortcut(QKeySequence(key))

    def show_preferences(self) -> None:
        dlg = PreferencesDialog(self._shortcut_actions(), self._default_shortcuts, self._mouse, self)
        if dlg.exec() != PreferencesDialog.Accepted:
            return
        keys = dlg.shortcuts()
        self.apply_shortcuts(keys)
        changed = {k: v for k, v in keys.items() if v != self._default_shortcuts.get(k, "")}
        self.settings.setValue("shortcuts", json.dumps(changed))
        self._mouse = dlg.mouse()
        self.settings.setValue("mouse", json.dumps(self._mouse))
        self.osd("설정을 적용했습니다")

    # ------------------------------------------------------------------ 구간 잘라 이어 보기
    def mark_in(self) -> None:
        if not self._current:
            return
        self._mark_in = (self._current, self._time)
        self.osd(f"구간 시작점 {fmt_time(self._time)}  (끝점: O)")

    def mark_out(self) -> None:
        if not self._current:
            return
        if not self._mark_in or self._mark_in[0] != self._current:
            self.osd("먼저 I 키로 이 영상의 시작점을 찍어 주세요")
            return
        vp = self.player.video_params or {}
        try:
            fps = float(self.player.container_fps or 0)
        except (TypeError, ValueError):
            fps = 0.0
        clip = Clip(self._current, self._mark_in[1], self._time,
                    width=int(vp.get("w") or 0), height=int(vp.get("h") or 0), fps=fps,
                    has_audio=any(t.get("type") == "audio" for t in self._tracks))
        try:
            self.clips_panel.add_clip(clip)
        except ValueError as e:
            self.osd(str(e))
            return
        self._mark_in = None
        self.osd(f"구간 추가: {fmt_time(clip.start)} ~ {fmt_time(clip.end)}")
        if not self.isFullScreen():
            self.clips_dock.show()
            self.clips_dock.raise_()

    def play_clips(self, index: int) -> None:
        clips = self.clips_panel.clips()
        while 0 <= index < len(clips) and not os.path.exists(clips[index].path):
            index += 1  # 사라진 파일은 건너뜀
        if not (0 <= index < len(clips)):
            if self._clip_index is not None:
                self._end_clip_mode()
                self.osd("구간 재생을 마쳤습니다")
            return
        self._save_position()
        c = clips[index]
        self._clip_index = index
        self._current = c.path
        self._time, self._duration = c.start, 0.0
        self._data_sub_checked = False
        self.controls.reset()
        self.playlist.set_current(c.path)
        self.video.load(c.path, start=f"{c.start:.3f}", end=f"{c.end:.3f}")
        self.player.pause = False
        self.clips_panel.highlight(index)
        self.setWindowTitle(f"[구간 {index + 1}/{len(clips)}] {c.name} - {APP_NAME}")
        self.osd(f"구간 {index + 1}/{len(clips)}: {c.name}")

    def _end_clip_mode(self) -> None:
        if self._clip_index is not None:
            self._clip_index = None
            self.clips_panel.highlight(None)

    def _toggle_dock(self, dock: QDockWidget) -> None:
        if dock.isVisible() and not dock.visibleRegion().isEmpty():
            dock.hide()
        else:
            dock.show()
            dock.raise_()

    # ------------------------------------------------------------------ PIP
    def add_pip(self, path: str, sync: bool, auto_partner: bool = False) -> PipView | None:
        if len(self._pips) >= MAX_PIPS:
            self.osd(f"PIP는 최대 {MAX_PIPS}개까지 띄울 수 있습니다")
            return None
        pip = PipView(self.area, path, sync, auto_partner)
        pip.player.hwdec = self._hwdec
        pip.video.set_compat_mode(self.a_compat.isChecked())
        pip.closed.connect(self._on_pip_closed)
        pip.sync_toggled.connect(lambda _p, _on: self._sync_pips(force=True))
        pip.swap_requested.connect(self.swap_with_pip)
        pip.main_pause_requested.connect(self.toggle_pause)
        pip.main_seek_requested.connect(lambda t: self.seek(t, absolute=True))
        pip.main_speed_requested.connect(self.set_speed)
        w = max(int(self.area.width() * 0.32), 240)
        pip.resize(w, int(w * 9 / 16) + 30)
        n = len(self._pips)
        pip.move_within_parent(self.area.rect().topRight() - pip.rect().topRight()
                               + QPoint(-12 - 24 * n, 12 + 24 * n))
        pip.show()
        pip.raise_()
        self._pips.append(pip)
        self._sync_pips(force=True)
        return pip

    def swap_with_pip(self, pip: PipView) -> None:
        """PIP 영상과 메인 영상을 맞바꾼다 (재생 위치 유지)."""
        main_path, main_t = self._current, self._time
        pip_path, pip_t = pip.path, pip.time
        if main_path is None:
            pip.close_pip()
            self.play(pip_path, start=pip_t)
            return
        pip.load(main_path, start=f"{main_t:.3f}")
        self.play(pip_path, start=pip_t)

    def _on_pip_closed(self, pip: PipView) -> None:
        if pip in self._pips:
            self._pips.remove(pip)

    def close_all_pips(self) -> None:
        for pip in list(self._pips):
            pip.close_pip()

    def open_pip_dialog(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))
        start = os.path.dirname(self._current) if self._current and os.path.exists(self._current) \
            else self.settings.value("lastDir", "")
        files, _ = QFileDialog.getOpenFileNames(self, "PIP로 열 동영상", start, f"동영상 ({exts});;모든 파일 (*)")
        for f in files:
            # 메인 영상이 있으면 기본으로 동기화 (블랙박스 앞뒤처럼 같은 시각 영상을 같이 보는 경우)
            self.add_pip(f, sync=self._current is not None)

    def _on_pip_auto_toggled(self) -> None:
        if self.a_pip_auto.isChecked():
            self._update_auto_pip(announce=True)
        else:
            for pip in [p for p in self._pips if p.auto_partner]:
                pip.close_pip()

    def _update_auto_pip(self, announce: bool = False) -> None:
        if not self.a_pip_auto.isChecked() or not self._current:
            return
        partner = find_partner(self._current)
        auto = next((p for p in self._pips if p.auto_partner), None)
        if partner:
            if auto is None:
                self.add_pip(partner, sync=True, auto_partner=True)
            elif auto.path != partner:
                auto.load(partner)
                self._sync_pips(force=True)
        else:
            if auto is not None:
                auto.close_pip()
            if announce:
                self.osd("짝이 되는 앞/뒤 카메라 파일을 찾지 못했습니다")

    def _sync_pips(self, force: bool = False) -> None:
        """동기화된 PIP 를 메인 영상의 재생 상태·시각·속도에 맞춘다."""
        if not self._pips or self._closed:
            return
        try:
            paused = bool(self.player.pause) or self._current is None
            speed = self.player.speed or 1.0
        except Exception:
            return
        for pip in self._pips:
            if not pip.sync.isChecked():
                continue
            try:
                p = pip.player
                if bool(p.pause) != paused:
                    p.pause = paused
                if abs((p.speed or 1.0) - speed) > 1e-3:
                    p.speed = speed
                if self._current is None:
                    continue
                target = self._time
                dur = p.duration
                if dur:
                    target = min(target, max(dur - 0.05, 0))
                # 직전 탐색이 끝나기 전에 또 탐색하면 화면이 한 번도 안 그려질 수 있으므로,
                # 탐색 중이거나 방금 탐색했으면 기다린다.
                now = time.monotonic()
                if p.seeking or (not force and now - getattr(pip, "last_seek", 0) < 2.0):
                    continue
                if abs(pip.time - target) > (0.15 if force or paused else 1.0):
                    p.seek(target, "absolute", "exact")
                    pip.time = target
                    pip.last_seek = now
            except Exception:
                continue  # 아직 파일을 여는 중

    # ------------------------------------------------------------------ 화면
    def toggle_fullscreen(self) -> None:
        docks = (self.dock, self.clips_dock)
        if self.isFullScreen():
            self.menuBar().show()
            for d, visible in zip(docks, self._docks_visible):
                d.setVisible(visible)
            self.area.set_overlay(False)
            self._hide_timer.stop()
            self.video.unsetCursor()
            self.showMaximized() if self._was_maximized else self.showNormal()
        else:
            self._was_maximized = self.isMaximized()
            # 재생목록·구간 목록 등 모든 패널을 숨긴다 (탭으로 겹친 패널 포함)
            self._docks_visible = [d.isVisible() for d in docks]
            self.menuBar().hide()
            for d in docks:
                d.hide()
            self.area.set_overlay(True)
            self.showFullScreen()
            _windows_fullscreen_border(self)
            self._hide_timer.start()
        self.a_full.setChecked(self.isFullScreen())

    FS_EDGE = 90  # 전체화면에서 위·아래 가장자리 몇 픽셀 안에 마우스가 오면 메뉴/컨트롤을 보일지

    def _on_mouse_activity(self) -> None:
        if not self.isFullScreen():
            return
        self.video.unsetCursor()
        y = self.area.mapFromGlobal(QCursor.pos()).y()
        h = self.area.height()
        if y <= self.FS_EDGE:
            self.area.topbar.show()
            self.area.topbar.raise_()
        elif y >= h - self.controls.sizeHint().height() - self.FS_EDGE:
            self.controls.show()
            self.controls.raise_()
        self._hide_timer.start()

    def _auto_hide_controls(self) -> None:
        if not self.isFullScreen():
            return
        bars = (self.controls, self.area.topbar)
        if any(b.isVisible() and b.underMouse() for b in bars) or QApplication.activePopupWidget():
            self._hide_timer.start()  # 메뉴를 쓰는 중이면 기다림
            return
        for b in bars:
            b.hide()
        self.video.setCursor(Qt.BlankCursor)

    def main_menu(self) -> QMenu:
        """메뉴바의 모든 메뉴를 담은 팝업 (오른쪽 클릭, 전체화면 메뉴 버튼)."""
        menu = QMenu(self)
        for a in self.menuBar().actions():
            if a.menu():
                menu.addMenu(a.menu())
        if self.isFullScreen():
            menu.addSeparator()
            menu.addAction("창 모드로", self.toggle_fullscreen)
        return menu

    def toggle_on_top(self) -> None:
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self.a_ontop.isChecked())
        self.show()

    # ------------------------------------------------------------------ 최근 파일
    def _add_recent(self, path: str) -> None:
        recent = [p for p in (self.settings.value("recent") or []) if p != path]
        self.settings.setValue("recent", ([path] + recent)[:MAX_RECENT])

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        recent = self.settings.value("recent") or []
        if isinstance(recent, str):
            recent = [recent]
        for p in recent:
            self.recent_menu.addAction(p, lambda p=p: self.open_paths([p]))
        if recent:
            self.recent_menu.addSeparator()
            self.recent_menu.addAction("목록 지우기", lambda: self.settings.remove("recent"))
        else:
            self.recent_menu.addAction("(없음)").setEnabled(False)

    # ------------------------------------------------------------------ 라이브러리
    def show_library(self, tab: int = 0) -> None:
        if self._library is None:
            self._library = LibraryWindow(self.db)
            self._library.play_files.connect(self.open_paths)
            self._library.enqueue_files.connect(self.enqueue)
        self._library.tabs.setCurrentIndex(tab)
        self._library.show()
        self._library.raise_()
        self._library.activateWindow()

    # ------------------------------------------------------------------ 도움말
    def show_shortcuts(self) -> None:
        QMessageBox.information(self, "단축키 안내", SHORTCUT_HELP)

    def show_about(self) -> None:
        QMessageBox.about(
            self, f"{APP_NAME} 정보",
            f"<h3>{APP_NAME} {__version__}</h3>"
            "<p>mpv 기반 동영상 플레이어와 중복 동영상 정리 도구</p>"
            f"<p>mpv {self.player.mpv_version or ''}</p>")
