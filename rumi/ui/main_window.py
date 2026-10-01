"""메인 플레이어 창."""

from __future__ import annotations

import json
import os
import platform
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QSettings, QStandardPaths, Qt, QTimer, qVersion
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QFileDialog, QInputDialog, QMainWindow, QMessageBox, QWidget,
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
from .clips_panel import ClipsPanel
from .pip import MAX_PIPS, PipView
from .playlist import PlaylistPanel
from .theme import DEFAULT_THEME, THEMES, apply_theme
from .util import fmt_time

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
<tr><td><b>Ctrl + L</b></td><td>라이브러리 / 중복 정리</td></tr>
</table>
"""


def data_dir() -> Path:
    return Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))


class VideoArea(QWidget):
    """영상 + 컨트롤 바. 전체화면에서는 컨트롤 바가 영상 위에 겹쳐 뜬다."""

    def __init__(self, video: MpvWidget, controls: ControlBar, parent=None):
        super().__init__(parent)
        self.video, self.controls = video, controls
        video.setParent(self)
        controls.setParent(self)
        self.overlay = False
        self.setObjectName("videoArea")
        self.setStyleSheet("#videoArea { background: black; }")

    def set_overlay(self, on: bool) -> None:
        self.overlay = on
        self.controls.show()
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
        self._dock_was_visible = False
        self._closed = False
        self._tracks: list = []
        self._data_sub_checked = False
        self._hwdec = DEFAULT_HWDEC
        self._pips: list[PipView] = []
        self._clip_index: int | None = None   # 구간 이어 재생 중이면 현재 구간 번호
        self._mark_in: tuple[str, float] | None = None
        self._skip_resume = False

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

    def _build_menus(self) -> None:
        mb = self.menuBar()

        m = mb.addMenu("파일(&F)")
        m.addActions([self.a_open, self.a_open_folder, self.a_open_url])
        self.recent_menu = m.addMenu("최근 파일")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
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
        m.addSeparator()
        clip = m.addMenu("구간 잘라 이어 보기")
        clip.addActions([self.a_mark_in, self.a_mark_out, self.a_clips])

        m = mb.addMenu("오디오(&A)")
        m.addActions([self.a_vol_up, self.a_vol_down, self.a_mute])
        m.addSeparator()
        self.audio_menu = m.addMenu("오디오 트랙")

        m = mb.addMenu("자막(&S)")
        m.addAction(self.a_sub_load)
        self.sub_menu = m.addMenu("자막 트랙")
        m.addSeparator()
        m.addActions([self.a_sub_toggle, self.a_sub_earlier, self.a_sub_later, self.a_sub_reset,
                      self.a_sub_bigger, self.a_sub_smaller])

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
        m.addAction(self.a_screenshot)

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
        v.clicked.connect(self.toggle_pause)
        v.double_clicked.connect(self.toggle_fullscreen)
        v.wheel_scrolled.connect(lambda d: self.change_volume(5 * d))
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
        if self.isFullScreen():
            self.menuBar().show()
            self.dock.setVisible(self._dock_was_visible)
            self.area.set_overlay(False)
            self._hide_timer.stop()
            self.video.unsetCursor()
            self.showMaximized() if self._was_maximized else self.showNormal()
        else:
            self._was_maximized = self.isMaximized()
            self._dock_was_visible = self.dock.isVisible()
            self.menuBar().hide()
            self.dock.hide()
            self.area.set_overlay(True)
            self.showFullScreen()
            self._hide_timer.start()
        self.a_full.setChecked(self.isFullScreen())

    def _on_mouse_activity(self) -> None:
        if self.isFullScreen():
            self.controls.show()
            self.video.unsetCursor()
            self._hide_timer.start()

    def _auto_hide_controls(self) -> None:
        if not self.isFullScreen():
            return
        if self.controls.underMouse():
            self._hide_timer.start()
            return
        self.controls.hide()
        self.video.setCursor(Qt.BlankCursor)

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
