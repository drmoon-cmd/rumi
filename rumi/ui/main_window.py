"""메인 플레이어 창."""

from __future__ import annotations

import json
import os
import platform
import re
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QLockFile, QPoint, QSettings, QStandardPaths, Qt, QTimer, QUrl, qVersion
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QDialog, QDockWidget, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMenu,
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
from .. import picture
from .. import vr as vrfmt
from ..recorder import StreamRecorder
from ..media_tools import AudioSettings, build_audio_filter
from .capture import build_contact_sheet
from .clips_panel import ClipsPanel
from .grid import LAYOUTS as GRID_LAYOUTS, GridWindow, video_filter as grid_video_filter
from .dialogs import (
    MOUSE_DEFAULTS, SUB_DEFAULTS, AudioDialog, BookmarksDialog, PreferencesDialog, SkipDialog, SubtitleStyleDialog,
    VideoAdjustDialog,
)
from .fullscreen import enter_fullscreen, is_fullscreen, leave_fullscreen
from .merge_dialog import MergeDialog
from .hover_reveal import HoverReveal, bottom_zone, top_zone
from .seek_preview import SeekPreview
from .load_guard import LoadGuard
from .vr_view import VrView
from .watchdog import HangWatchdog
from .workers import run_with_progress
from .pip import MAX_PIPS, PipView
from .playlist import PlaylistPanel
from .theme import DEFAULT_THEME, THEMES, apply_theme
from .util import fmt_time, reveal_in_file_manager

SUBTITLE_EXTENSIONS = {".srt", ".smi", ".sami", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup"}
MAX_RECENT = 10
RESUME_MIN_SECONDS = 10      # 이 시간 이상 본 파일만 이어보기
RESUME_END_MARGIN = 0.95     # 95% 이상 봤으면 다 본 것으로 간주

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
<tr><td><b>Ctrl + Shift + R</b></td><td>녹화 시작/중지 (스트림 저장)</td></tr>
<tr><td><b>Ctrl + Shift + M</b></td><td>동영상 합치기 (.ts 조각 등 여러 파일을 하나로)</td></tr>
<tr><td><b>Alt + Backspace</b></td><td>화질 설정 모두 기본으로 (느려졌을 때)</td></tr>
<tr><td><b>Shift + D</b></td><td>대사 크게 켜기·끄기</td></tr>
<tr><td><b>Alt + V</b></td><td>VR / 360° 보기 켜기·끄기 — 끌어서 둘러보기, Ctrl+휠 시야, Alt+←/→ 돌기, Alt+0 정면</td></tr>
<tr><td><b>Ctrl + G</b></td><td>화면 분할 보기 (CCTV) — 파일 골라 열기, Esc 닫기, Space 전체 일시정지</td></tr>
</table>
"""


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
        self.message = QLabel(self)
        self.message.setStyleSheet("background: rgba(0, 0, 0, 170); color: white; padding: 6px 10px;"
                                   "border-radius: 4px; font-size: 15px;")
        self.message.hide()
        self._message_timer = QTimer(self, singleShot=True, interval=1800)
        self._message_timer.timeout.connect(self.message.hide)
        self.overlay = False
        self.setObjectName("videoArea")
        self.setStyleSheet("#videoArea { background: black; }")

    def show_message(self, text: str) -> None:
        self.message.setText(text)
        self.message.adjustSize()
        self.message.move(16, 16)
        self.message.show()
        self.message.raise_()
        self._message_timer.start()

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
        self._watchdog = HangWatchdog(data_dir() / "hang.log", self)
        self._library: LibraryWindow | None = None
        self._current: str | None = None
        self._time = 0.0
        self._duration = 0.0
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
        self._recording: str | None = None   # 녹화 중인 파일 경로
        self._quality_pref = "default"  # 고른 화질 개선 (영상에 따라 더 가벼운 것을 쓸 수 있음)
        self._skip_rules: dict[str, dict] = {}  # 폴더 → {"intro": 초, "outro": 초}
        self._cur_skip = (0, 0)
        self._outro_done = False
        self._recorder: StreamRecorder | None = None  # 인터넷 스트림 저장 (ffmpeg)
        self._record_started = 0.0

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

        self._reveal = HoverReveal(
            self.area, [(self.area.topbar, top_zone), (self.controls, bottom_zone)],
            cursor_widgets=[self.video, self.area])
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
        self.a_merge = A("동영상 합치기 (여러 파일을 하나로)…", lambda: self.open_merge_dialog(), "Ctrl+Shift+M")
        self.a_quit = A("끝내기", self.close, QKeySequence.Quit)

        self.a_play = A("재생 / 일시정지", self.toggle_pause, "Space")
        self.a_stop = A("정지", self.stop)
        self.a_record = A("녹화 시작/중지", self.toggle_record, "Ctrl+Shift+R", checkable=True)
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
        self.a_exit_full = A("전체화면 해제", lambda: is_fullscreen(self) and self.toggle_fullscreen(), "Esc")
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
        self.a_vr = A("VR 보기 켜기/끄기", lambda: self.vr.toggle(), "Alt+V", checkable=True)
        self.a_vr_auto = A("VR 영상 자동으로 알아보기", self._toggle_vr_auto, checkable=True)
        self.a_vr_reset = A("정면으로 되돌리기", lambda: self.vr.reset_view(), "Alt+0")
        self.a_vr_wider = A("시야 넓게", lambda: self.vr.change_fov(10), "Alt+-")
        self.a_vr_narrower = A("시야 좁게 (확대)", lambda: self.vr.change_fov(-10), "Alt+=")
        self.a_vr_left = A("왼쪽으로 돌기", lambda: self.vr.turn(d_yaw=-15), "Alt+Left")
        self.a_vr_right = A("오른쪽으로 돌기", lambda: self.vr.turn(d_yaw=15), "Alt+Right")
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
        self.a_dialogue = A("대사 크게", self.toggle_dialogue, "Shift+D", checkable=True)
        self.a_smooth = A("부드러운 움직임 (끊김 줄이기)", lambda: self.toggle_smooth(), checkable=True)
        self.a_skip = A("오프닝·엔딩 건너뛰기 (이 폴더)…", self.show_skip_dialog)
        self.a_picture_reset = A("화질 설정 모두 기본으로 (느려졌을 때)", lambda: self.reset_picture(), "Alt+Backspace")
        self.a_adelay_minus = A("소리 싱크 -0.1초", lambda: self.change_audio_delay(-0.1), "Ctrl+[")
        self.a_adelay_plus = A("소리 싱크 +0.1초", lambda: self.change_audio_delay(0.1), "Ctrl+]")
        self.a_adelay_reset = A("소리 싱크 초기화", lambda: self.change_audio_delay(None), "Ctrl+Backspace")
        self.a_sub_style = A("자막 모양 (글꼴·색·위치)…", self.show_sub_style)
        self.a_grid = A("화면 분할 보기 (파일 골라 열기)…", lambda: self.open_grid(), "Ctrl+G")

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
        m.addAction(self.a_merge)
        m.addSeparator()
        m.addAction(self.a_prefs)
        m.addSeparator()
        m.addAction(self.a_quit)

        m = mb.addMenu("재생(&P)")
        m.addActions([self.a_play, self.a_stop])
        rec = m.addMenu("녹화 (스트림 저장)")
        rec.addAction(self.a_record)
        rec.addAction("녹화 폴더 열기", self.open_record_dir)
        rec.addAction("녹화 폴더 바꾸기…", self.choose_record_dir)
        m.addAction(self.a_skip)
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
        m.addActions([self.a_normalize, self.a_dialogue, self.a_audio, self.a_adelay_minus, self.a_adelay_plus, self.a_adelay_reset])

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
        vr_menu = m.addMenu("VR / 360° 보기")
        vr_menu.addAction(self.a_vr)
        vr_menu.addSeparator()
        self.vr_group = QActionGroup(self)
        for key, label in [(None, "끄기 (원래 모양 그대로)")] + [(k, v[2]) for k, v in vrfmt.FORMATS.items()]:
            a = vr_menu.addAction(label, lambda k=key: self.vr.set_format(k))
            a.setCheckable(True)
            a.setData(key)
            self.vr_group.addAction(a)
        vr_menu.addSeparator()
        vr_menu.addActions([self.a_vr_reset, self.a_vr_left, self.a_vr_right, self.a_vr_wider, self.a_vr_narrower])
        vr_menu.addSeparator()
        vr_menu.addAction(self.a_vr_auto)
        m.addSeparator()
        q_menu = m.addMenu("화질 개선")
        self.quality_group = QActionGroup(self)
        for key, label in picture.QUALITY_PRESETS.items():
            a = q_menu.addAction(label, lambda k=key: self.set_picture_quality(k))
            a.setCheckable(True)
            a.setData(key)
            self.quality_group.addAction(a)
        hdr_menu = m.addMenu("HDR 색 보정")
        self.hdr_group = QActionGroup(self)
        for key, label in picture.HDR_PRESETS.items():
            a = hdr_menu.addAction(label, lambda k=key: self.set_hdr_mode(k))
            a.setCheckable(True)
            a.setData(key)
            self.hdr_group.addAction(a)
        m.addAction(self.a_smooth)
        m.addAction(self.a_picture_reset)
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
        grid = m.addMenu("화면 분할 보기 (CCTV)")
        grid.addAction(self.a_grid)
        grid.addAction("재생목록 영상으로 열기", lambda: self.open_grid(from_playlist=True))
        grid.addSeparator()
        for name in GRID_LAYOUTS:
            grid.addAction(name + " 로 파일 열기…", lambda n=name: self.open_grid(n))
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
        self.vr = VrView(self.player, v, self.osd, self)
        self.vr.auto = self.settings.value("vrAuto", True, type=bool)
        self.a_vr_auto.setChecked(self.vr.auto)
        self.vr.changed.connect(self._on_vr_changed)
        self._on_vr_changed(None)
        v.resized.connect(self.vr.on_resized)
        self.load_guard = LoadGuard(self.player, lambda: self.isVisible() and not self.isMinimized() and (
            self._hwdec == "no" or (picture.is_heavy(
                self.a_smooth.isChecked(), self.video.quality, self.video.hdr) and not self.video.compat_mode)), self)
        self._hwdec_hint_shown = False
        self.load_guard.overloaded.connect(self._on_overloaded)

        c.play_pause.connect(self.toggle_pause)
        c.stop.connect(self.stop)
        c.prev.connect(lambda: self.play_neighbor(-1))
        c.next.connect(lambda: self.play_neighbor(1))
        c.seek_ratio.connect(self._seek_ratio)
        c.volume_set.connect(lambda val: self._set_prop("volume", val))
        c.mute_toggle.connect(self.toggle_mute)
        c.fullscreen_toggle.connect(self.toggle_fullscreen)
        c.record_toggle.connect(self.toggle_record)
        self._record_timer = QTimer(self, interval=1000)
        self._record_timer.timeout.connect(self._update_record_label)
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
        self.playlist.merge_requested.connect(self.open_merge_dialog)
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
        # 지난번에 정상적으로 끝나지 않았으면(멈춰서 강제로 끈 경우 등) 무거운 화질 설정 없이 시작한다
        # Rumi 를 두 개 띄운 경우(파일을 더블클릭해 하나 더 열림)는 먼저 뜬 쪽만 이 표시를 다룬다
        data_dir().mkdir(parents=True, exist_ok=True)
        self._instance_lock = QLockFile(str(data_dir() / "running.lock"))
        self._instance_lock.setStaleLockTime(0)  # 시간이 아니라 그 프로그램이 살아 있는지로 판단
        self._primary = self._instance_lock.tryLock(0)
        crashed = self._primary and s.value("running", False, type=bool)
        heavy = picture.is_heavy(s.value("smoothMotion", False, type=bool),
                                 s.value("pictureQuality", "default"), s.value("hdrMode", "auto"))
        self._safe_started = crashed and heavy
        if not self._safe_started:
            self.set_picture_quality(s.value("pictureQuality", "default"), announce=False)
            self.set_hdr_mode(s.value("hdrMode", "auto"), announce=False)
            self.a_smooth.setChecked(s.value("smoothMotion", False, type=bool))
            self.toggle_smooth(announce=False)
        else:
            QTimer.singleShot(800, lambda: QMessageBox.information(
                self, APP_NAME, "지난번에 Rumi 가 정상적으로 끝나지 않았습니다.\n\n"
                "PC 가 느려지는 것을 막기 위해 화질 개선·부드러운 움직임·HDR 보정을 끈 채로 시작했습니다. "
                "화면 메뉴에서 다시 켤 수 있습니다."))
        if self._primary:
            s.setValue("running", True)
            s.sync()
        self.load_guard.start()
        self._skip_rules = self._load_json("skipRules")
        self.playlist.set_repeat_mode(s.value("repeat", "none"))
        try:
            self.playlist.restore_state(json.loads(s.value("playlists", "") or "{}"))
        except (ValueError, TypeError):
            pass  # 저장된 재생목록이 깨졌으면 빈 목록으로 시작
        self._mouse = {**MOUSE_DEFAULTS, **self._load_json("mouse")}
        a = self._load_json("audio")
        self._audio = AudioSettings(bool(a.get("normalize", False)), int(a.get("bass", 0)), int(a.get("treble", 0)),
                                    bool(a.get("dialogue", False)))
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
        self._reveal.stop()
        self._watchdog.stop()
        self.load_guard.stop()
        self.stop_record(report=False)
        if is_fullscreen(self):
            self.toggle_fullscreen()
        s = self.settings
        s.setValue("geometry", self.saveGeometry())
        self.hide()  # 위치를 저장한 뒤, 정리하는 동안 창이 굳어 보이지 않게 먼저 숨긴다
        s.setValue("windowState", self.saveState())
        s.setValue("volume", self.player.volume)
        s.setValue("repeat", self.playlist.repeat_mode().value)
        s.setValue("playlists", json.dumps(self.playlist.to_state(), ensure_ascii=False))
        s.setValue("mouse", json.dumps(self._mouse))
        s.setValue("audio", json.dumps({"normalize": self._audio.normalize, "bass": self._audio.bass,
                                        "treble": self._audio.treble, "dialogue": self._audio.dialogue}))
        s.setValue("pictureQuality", self._quality_pref)
        s.setValue("hdrMode", self.video.hdr)
        s.setValue("smoothMotion", self.a_smooth.isChecked())
        if self._primary:
            s.setValue("running", False)  # 정상 종료 표시
        s.setValue("skipRules", json.dumps(self._skip_rules, ensure_ascii=False))
        s.setValue("subStyle", json.dumps(self._sub_style, ensure_ascii=False))
        s.setValue("hwdecMode", self._hwdec)
        s.setValue("compatMode", self.a_compat.isChecked())
        s.setValue("pipAuto", self.a_pip_auto.isChecked())
        if self._library:
            self._library.close()
        if getattr(self, "_grid", None) is not None:
            self._grid.close()
        for pip in list(self._pips):
            pip.close_pip()
        self._sync_timer.stop()
        # 재생 엔진을 끄는 동안이나 끈 뒤에 도착하는 알림(파일 끝 등)이 꺼진 엔진을 건드리지 않게
        self.video.blockSignals(True)
        self._closed = True
        self.video.shutdown()
        QCoreApplication.removePostedEvents(self, QEvent.MetaCall)
        self.db.close()
        super().closeEvent(e)

    def release_instance_lock(self) -> None:
        if getattr(self, "_primary", False):
            self._instance_lock.unlock()
            self._primary = False

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

    def open_merge_dialog(self, paths: list[str] | None = None) -> None:
        if not paths:
            exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))
            paths, _ = QFileDialog.getOpenFileNames(
                self, "하나로 합칠 동영상 (여러 개 선택)", self.settings.value("lastDir", ""),
                f"동영상 ({exts});;모든 파일 (*)")
            if not paths:
                return
        MergeDialog(paths, self).exec()

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
        self.stop_record(local_only=True)  # 인터넷 스트림 저장은 다른 영상을 봐도 계속된다
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
        self.stop_record(local_only=True)
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

    # ------------------------------------------------------------------ 녹화
    def record_dir(self) -> str:
        default = os.path.join(QStandardPaths.writableLocation(QStandardPaths.MoviesLocation), "Rumi 녹화")
        return self.settings.value("recordDir", "") or default

    def choose_record_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "녹화 파일을 저장할 폴더", self.record_dir())
        if d:
            self.settings.setValue("recordDir", d)
            self.osd(f"녹화 폴더: {d}")

    def open_record_dir(self) -> None:
        d = self.record_dir()
        os.makedirs(d, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def toggle_record(self) -> None:
        if self._recording:
            self.stop_record()
        else:
            self.start_record()

    def start_record(self) -> None:
        """재생 중인 영상을 다시 인코딩하지 않고 그대로 파일에 저장한다 (화질 손실 없음).

        - 인터넷 스트림: ffmpeg 가 처음부터 따로 받아 저장한다. 예고편처럼 끝이 있는 영상은
          끝까지 저장되면 저절로 끝나고, 생방송은 중지할 때까지 녹화한다.
        - 내 PC 의 파일: 지금 위치부터 중지할 때까지를 저장한다.
        암호화(DRM)된 스트림은 저장되지 않는다."""
        if self._current is None:
            self.osd("재생 중인 영상이 없습니다")
            self.a_record.setChecked(False)
            return
        try:
            title = self.player.media_title or ""
        except Exception:
            title = ""
        title = title or os.path.basename(self._current.split("?")[0]) or "stream"
        title = os.path.splitext(title)[0] if re.search(r"\.\w{2,4}$", title) else title
        title = re.sub(r'[\\/:*?"<>|\s]+', "_", title).strip("._")[:60] or "stream"
        folder = self.record_dir()
        is_stream = "://" in self._current
        try:
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f"{title}_{time.strftime('%Y%m%d_%H%M%S')}.mkv")
            if is_stream:
                ffmpeg = find_ffmpeg()
                if not ffmpeg:
                    raise RuntimeError("변환 도구(ffmpeg)를 찾지 못했습니다.")
                self._recorder = StreamRecorder(ffmpeg, self)
                self._recorder.progress.connect(self._update_record_label)
                self._recorder.finished.connect(self._on_record_finished)
                self._recorder.start(self._current, path)
            else:
                self.player["stream-record"] = path
        except Exception as e:
            self.a_record.setChecked(False)
            QMessageBox.warning(self, "녹화", f"녹화를 시작하지 못했습니다.\n\n{e}")
            return
        self._recording = path
        self._record_started = time.monotonic()
        if not is_stream:
            self._record_timer.start()
        self._update_record_label()
        self.a_record.setChecked(True)
        self.osd("● 저장 시작 (끝까지 받으면 저절로 멈춥니다)" if is_stream else "● 녹화 시작")

    def stop_record(self, report: bool = True, local_only: bool = False) -> None:
        path = self._recording
        if not path:
            return
        if self._recorder is not None:
            if local_only:
                return
            if report:
                self._recorder.stop()  # 마무리되면 _on_record_finished 가 불린다
            else:
                self._recorder.shutdown()
                self._recording = None
            return
        self._recording = None
        try:
            self.player["stream-record"] = ""
        except Exception:
            pass
        self._record_timer.stop()
        self.controls.set_recording(None)
        self.a_record.setChecked(False)
        if report:
            QTimer.singleShot(500, lambda: self._report_record(path))

    def _update_record_label(self, size: int | None = None) -> None:
        secs = time.monotonic() - self._record_started
        text = f"● 녹화 {fmt_time(secs)}"
        if size:
            text += f" · {size / 1_048_576:.1f} MB"
        self.controls.set_recording(text)

    def _on_record_finished(self, path: str, ok: bool, message: str) -> None:
        self._recording = None
        if self._recorder is not None:
            self._recorder.deleteLater()
            self._recorder = None
        self.controls.set_recording(None)
        self.a_record.setChecked(False)
        if ok:
            size = os.path.getsize(path) / 1_048_576
            self.osd(f"저장 완료: {os.path.basename(path)} ({size:.1f} MB)")
            box = QMessageBox(QMessageBox.Information, "녹화", f"{message}\n\n{path}\n({size:.1f} MB)", parent=self)
            folder_btn = box.addButton("폴더 열기", QMessageBox.ActionRole)
            box.addButton(QMessageBox.Ok)
            box.exec()
            if box.clickedButton() is folder_btn:
                reveal_in_file_manager(path)
        else:
            QMessageBox.warning(self, "녹화", message)

    def _report_record(self, path: str) -> None:
        size = os.path.getsize(path) if os.path.exists(path) else 0
        if size < 10_000:
            if os.path.exists(path):
                os.remove(path)
            QMessageBox.warning(self, "녹화", "녹화된 내용이 없습니다.\n\n"
                                "녹화 시간이 너무 짧았거나, 녹화를 지원하지 않는 형식일 수 있습니다.")
            return
        self.osd(f"녹화 저장: {os.path.basename(path)} ({size / 1_048_576:.1f} MB)")

    def _take_screenshot(self) -> bool:
        """지금 장면을 저장한다. 파일을 여는 중이라 아직 장면이 없거나 저장할 수 없으면 False."""
        try:
            self.player.screenshot()
            return True
        except Exception:
            return False

    def screenshot(self) -> None:
        if self._current:
            self.osd("스크린샷을 바탕화면에 저장했습니다" if self._take_screenshot()
                     else "스크린샷을 저장하지 못했습니다 (영상을 여는 중이거나 저장할 수 없는 위치)")

    def osd(self, text: str) -> None:
        if self._closed:
            return
        if getattr(self, "vr", None) is not None and self.vr.active:
            self.area.show_message(text)  # VR 보기 중 mpv 글자는 펼친 화면과 함께 휘어진다
            return
        try:
            self.player.show_text(text, 1500)
        except SystemError:
            pass

    # ------------------------------------------------------------------ 화질 / 움직임
    def set_picture_quality(self, key: str, announce: bool = True) -> None:
        if key not in picture.QUALITY_PRESETS:
            key = "default"
        if key == "anime" and self.video.software_rendering:
            self.osd("그래픽 카드 가속이 없는 환경이라 애니메이션 화질 개선을 쓸 수 없습니다")
            key = self._quality_pref
        extra = ""
        if key == "anime" and self.a_smooth.isChecked():
            # 가장 무거운 두 가지를 함께 켜면 그래픽 카드가 꽉 차 PC 전체가 느려진다
            self.a_smooth.setChecked(False)
            self.toggle_smooth(announce=False)
            extra = "  · 부드러운 움직임은 함께 쓸 수 없어 껐습니다"
        self._quality_pref = key
        for a in self.quality_group.actions():
            a.setChecked(a.data() == key)
        applied = self._apply_quality()
        if announce:
            msg = f"화질 개선: {picture.QUALITY_PRESETS[key]}"
            if key != "default" and self.video.compat_mode:
                msg += "  (호환 모드가 켜져 있어 적용되지 않습니다)"
            elif applied != key:
                msg += f"  (이 영상은 커서 '{picture.QUALITY_PRESETS[applied]}'(으)로 적용)"
            self.osd(msg + extra)

    def _video_height(self) -> int | None:
        try:
            for t in self.player.track_list or []:
                if t.get("type") == "video" and t.get("selected"):
                    return int(t.get("demux-h") or 0) or None
        except Exception:
            pass
        return None

    def _apply_quality(self) -> str:
        """고른 화질 개선을 지금 영상에 맞게 적용한다 (큰 영상에는 Anime4K 대신 가벼운 것). 적용한 것을 돌려준다."""
        applied = picture.effective_quality(self._quality_pref, self._video_height() if self._current else None)
        if applied != self.video.quality:
            self.video.set_picture(quality=applied)
            self.load_guard.reset()
        return applied

    def set_hdr_mode(self, key: str, announce: bool = True) -> None:
        if key not in picture.HDR_PRESETS:
            key = "auto"
        self.video.set_picture(hdr=key)
        self.load_guard.reset()
        for a in self.hdr_group.actions():
            a.setChecked(a.data() == key)
        if announce:
            msg = f"HDR 색 보정: {picture.HDR_PRESETS[key]}"
            if self._current and not picture.is_hdr(self.player.video_params):
                msg += "  (지금 영상은 HDR 이 아니어서 차이가 없습니다)"
            self.osd(msg)

    def toggle_smooth(self, announce: bool = True) -> None:
        on = self.a_smooth.isChecked()
        extra = ""
        if on and self._quality_pref == "anime":
            self._quality_pref = "hq"
            for a in self.quality_group.actions():
                a.setChecked(a.data() == "hq")
            self._apply_quality()
            extra = f"  · 애니메이션 화질 개선은 함께 쓸 수 없어 '{picture.QUALITY_PRESETS['hq']}'(으)로 바꿨습니다"
        screen = self.screen()
        hz = screen.refreshRate() if screen is not None else None
        self.video.set_smooth(on, hz)
        self.load_guard.reset()
        if announce:
            self.osd((f"부드러운 움직임 켜짐 (화면 {hz:.0f}Hz 에 맞춤)" if on and hz else
                      "부드러운 움직임 켜짐" if on else "부드러운 움직임 꺼짐") + extra)

    def reset_picture(self, announce: bool = True) -> None:
        """화질 관련 설정을 모두 기본으로 (느려졌을 때)."""
        self.a_smooth.setChecked(False)
        self.toggle_smooth(announce=False)
        self.set_picture_quality("default", announce=False)
        self.set_hdr_mode("auto", announce=False)
        if announce:
            self.osd("화질 설정을 모두 기본으로 되돌렸습니다")

    def _on_overloaded(self, reason: str, kind: str = "render") -> None:
        if kind == "decode":
            # 그래픽 설정을 낮춰도 소용없다: CPU 가 영상을 푸는 것 자체가 버겁다
            if self._hwdec == "no" and not self._hwdec_hint_shown:
                self._hwdec_hint_shown = True
                text = (f"CPU 가 영상을 제때 풀지 못합니다 ({reason}). "
                        "화면 → 하드웨어 가속 → '켜기 - 복사 방식'을 켜면 그래픽 카드가 대신 풀어 훨씬 가벼워집니다")
                self.osd(text)
                self.statusBar().showMessage(text, 20000)
            return
        step = picture.step_down(self.a_smooth.isChecked(), self._quality_pref, self.video.hdr)
        if step is None:
            return
        (smooth, quality, hdr), msg = step
        if smooth != self.a_smooth.isChecked():
            self.a_smooth.setChecked(smooth)
            self.toggle_smooth(announce=False)
        if quality != self._quality_pref:
            self.set_picture_quality(quality, announce=False)
        if hdr != self.video.hdr:
            self.set_hdr_mode(hdr, announce=False)
        text = f"PC 가 버거워해서 {msg} ({reason}). 화면 메뉴에서 다시 켤 수 있습니다"
        self.osd(text)
        self.statusBar().showMessage(text, 15000)

    # ------------------------------------------------------------------ 오프닝·엔딩 건너뛰기
    @staticmethod
    def _folder_key(path: str) -> str | None:
        if not path or "://" in path:
            return None
        return os.path.normcase(os.path.dirname(os.path.abspath(path)))

    def _skip_rule(self, path: str) -> tuple[int, int]:
        rule = self._skip_rules.get(self._folder_key(path) or "", {})
        try:
            return int(rule.get("intro", 0)), int(rule.get("outro", 0))
        except (TypeError, ValueError, AttributeError):
            return 0, 0

    def show_skip_dialog(self) -> None:
        key = self._folder_key(self._current or "")
        if key is None:
            self.osd("내 PC 의 영상을 재생한 뒤에 설정할 수 있습니다")
            return
        intro, outro = self._skip_rule(self._current)
        dlg = SkipDialog(os.path.dirname(os.path.abspath(self._current)), intro, outro,
                         self._time, self._duration, self)
        if dlg.exec() != QDialog.Accepted:
            return
        intro, outro = dlg.values()
        if intro or outro:
            self._skip_rules[key] = {"intro": intro, "outro": outro}
        else:
            self._skip_rules.pop(key, None)
        self._cur_skip = (intro, outro)
        self.settings.setValue("skipRules", json.dumps(self._skip_rules, ensure_ascii=False))
        self.osd(f"이 폴더: 오프닝 {intro}초 · 엔딩 {outro}초 건너뛰기" if intro or outro else "이 폴더 건너뛰기 끔")

    def _on_vr_changed(self, fmt) -> None:
        if fmt is None:
            self.area.message.hide()
        elif self._zoom > 0:
            self.reset_zoom()
        self.a_vr.setChecked(fmt is not None)
        for a in self.vr_group.actions():
            a.setChecked(a.data() == fmt)
        for a in (self.a_vr_reset, self.a_vr_left, self.a_vr_right, self.a_vr_wider, self.a_vr_narrower):
            a.setEnabled(fmt is not None)

    def _toggle_vr_auto(self) -> None:
        self.vr.auto = self.a_vr_auto.isChecked()
        self.settings.setValue("vrAuto", self.vr.auto)

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
        hang = self._watchdog.tail()
        if 'File "' in hang:
            info += "\n\n[화면 멈춤 기록]\n" + hang
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
        prev, self._time = self._time, t
        self.controls.set_time(t)
        outro = self._cur_skip[1]
        if outro and not self._outro_done and self._clip_index is None and self._duration > outro + 5:
            edge = self._duration - outro
            if t >= edge:
                self._outro_done = True  # 이 파일에서는 한 번만 판단
                if prev < edge and 0 <= t - prev < 2.0:  # 탐색으로 건너온 것이 아니라 재생하다 닿음
                    self.osd("엔딩 건너뜀")
                    QTimer.singleShot(0, self._on_eof)

    def _on_duration(self, d: float) -> None:
        self._duration = d
        self.controls.set_duration(d)

    def _on_file_loaded(self) -> None:
        path = self._current
        if not path:
            return
        self.vr.on_file_loaded(path)
        self.load_guard.reset()
        applied = self._apply_quality()
        if applied != self._quality_pref and not self.video.compat_mode:
            self.statusBar().showMessage(
                f"큰 영상이라 화질 개선을 '{picture.QUALITY_PRESETS[applied]}'(으)로 가볍게 적용합니다", 8000)
        self._cur_skip = self._skip_rule(path)
        self._outro_done = False
        if self._clip_index is not None:
            return  # 구간 재생은 이어보기 없이 구간 시작점부터
        self._update_auto_pip()
        if self._skip_resume:
            self._skip_resume = False
            return
        pos = self.db.load_position(path)
        dur = self.player.duration or 0
        intro = self._cur_skip[0]
        if pos and pos >= RESUME_MIN_SECONDS and (not dur or pos < dur * RESUME_END_MARGIN) and pos > intro:
            self.seek(pos, absolute=True)
            self.osd(f"이어보기 {fmt_time(pos)}  (처음부터: Home)")
        elif intro and (not dur or intro < dur - 5):
            self.seek(intro, absolute=True)
            self.osd(f"오프닝 건너뜀 ({fmt_time(intro)}부터)")

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

    # ------------------------------------------------------------------ 화면 분할 보기
    def open_grid(self, layout_name: str = "4분할 (2×2)", from_playlist: bool = False) -> None:
        """여러 영상을 격자로 나눠 전체화면에 보여 준다. 기본은 파일을 직접 고른다."""
        if from_playlist:
            paths = [p for p in self.playlist.paths() if "://" not in p and os.path.exists(p)]
            if not paths:
                self.osd("재생목록에 동영상이 없습니다")
                return
        else:
            start = self.settings.value("gridDir", "") or self.settings.value("lastDir", "")
            paths, _ = QFileDialog.getOpenFileNames(self, "분할 화면에 띄울 동영상 (여러 개 선택)", start,
                                                    grid_video_filter())
            if not paths:
                return
            self.settings.setValue("gridDir", os.path.dirname(paths[0]))
        if getattr(self, "_grid", None) is not None:
            self._grid.close()
        if self._current:
            self.player.pause = True
        self._grid = GridWindow(paths, layout_name, self._hwdec, self.a_compat.isChecked())
        self._grid.destroyed.connect(self._on_grid_closed)
        self._grid.resize(self.size())
        self._grid.show()
        self._grid.toggle_fullscreen()

    def _on_grid_closed(self) -> None:
        self._grid = None
        # 분할 화면이 닫히면 메인 창을 다시 앞으로 (뒤에 깔린 채 대화상자가 가려지지 않게)
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------------ 마우스
    def _mouse_action(self, key: str) -> None:
        action = self._mouse.get(key, "none")
        {"pause": self.toggle_pause, "fullscreen": self.toggle_fullscreen,
         "mute": self.toggle_mute}.get(action, lambda: None)()

    def _on_wheel(self, direction: int, ctrl: bool) -> None:
        action = self._mouse.get("ctrl_wheel" if ctrl else "wheel", "none")
        if action == "zoom" and self.vr.active:
            self.vr.change_fov(-5 * direction)  # 위로 굴리면 시야를 좁혀 확대
            return
        if action == "volume":
            self.change_volume(5 * direction)
        elif action == "seek":
            self.seek(5 * direction)
        elif action == "zoom":
            self.change_zoom(0.1 * direction)

    def _on_drag(self, dx: float, dy: float) -> None:
        """VR 보기 중이면 끌어서 둘러보기, 확대 중이면 끌어서 화면 이동."""
        if self.vr.active:
            self.vr.rotate_by(dx, dy)
            return
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
        if not self._take_screenshot():
            self._burst_left = 0
            self._burst_timer.stop()
            self.osd("연속 캡처를 중단했습니다 (장면을 저장하지 못함)")
            return
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
        self.a_dialogue.setChecked(self._audio.dialogue)
        if announce:
            self.osd("소리 효과 적용" if not self._audio.is_default() else "소리 효과 끔")

    def toggle_normalize(self) -> None:
        self._audio.normalize = self.a_normalize.isChecked()
        self.apply_audio(announce=False)
        self.osd("소리 크기 자동 맞춤 켜짐" if self._audio.normalize else "소리 크기 자동 맞춤 꺼짐")

    def toggle_dialogue(self) -> None:
        self._audio.dialogue = self.a_dialogue.isChecked()
        self.apply_audio(announce=False)
        self.osd("대사 크게 켜짐" if self._audio.dialogue else "대사 크게 꺼짐")

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
        if not is_fullscreen(self):
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
        if getattr(self, "_grid", None) is not None:
            self._grid.close()
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
        if is_fullscreen(self):
            self.menuBar().show()
            for d, visible in zip(docks, self._docks_visible):
                d.setVisible(visible)
            self.area.set_overlay(False)
            self._reveal.stop()
            leave_fullscreen(self)
        else:
            # 재생목록·구간 목록 등 모든 패널을 숨긴다 (탭으로 겹친 패널 포함)
            self._docks_visible = [d.isVisible() for d in docks]
            self.menuBar().hide()
            for d in docks:
                d.hide()
            self.area.set_overlay(True)
            enter_fullscreen(self)
            self._reveal.start()
            self._reveal.show_all()  # 들어갈 때 잠깐 보여 주고 2.5초 뒤 숨김
        self.a_full.setChecked(is_fullscreen(self))

    def main_menu(self) -> QMenu:
        """메뉴바의 모든 메뉴를 담은 팝업 (오른쪽 클릭, 전체화면 메뉴 버튼)."""
        menu = QMenu(self)
        for a in self.menuBar().actions():
            if a.menu():
                menu.addMenu(a.menu())
        if is_fullscreen(self):
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
