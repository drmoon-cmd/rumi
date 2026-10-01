"""메인 플레이어 창."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget, QFileDialog, QInputDialog, QMainWindow, QMessageBox, QWidget,
)

from .. import APP_NAME, __version__
from ..library import LibraryDB
from ..library.scanner import VIDEO_EXTENSIONS
from .controls import ControlBar
from .library_window import LibraryWindow
from ..subtitles import looks_like_sensor_data
from .mpv_widget import DEFAULT_HWDEC, HWDEC_MODES, MpvWidget
from .playlist import PlaylistPanel
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
<tr><td><b>F9</b></td><td>재생목록</td></tr>
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


class MainWindow(QMainWindow):
    def __init__(self, files: list[str] | None = None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setAcceptDrops(True)
        self.settings = QSettings()
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

        self._hide_timer = QTimer(self, singleShot=True, interval=CONTROLS_HIDE_MS)
        self._hide_timer.timeout.connect(self._auto_hide_controls)
        self._save_timer = QTimer(self, interval=5000)
        self._save_timer.timeout.connect(self._save_position)
        self._save_timer.start()

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
        self.a_playlist = A("재생목록", lambda: self.dock.setVisible(not self.dock.isVisible()), "F9")

        self.a_library = A("라이브러리…", lambda: self.show_library(0), "Ctrl+L")
        self.a_dupes = A("중복 동영상 정리…", lambda: self.show_library(1), "Ctrl+D")

        self.a_keys = A("단축키 안내", self.show_shortcuts, "F1")
        self.a_about = A(f"{APP_NAME} 정보", self.show_about)

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
        m.addSeparator()
        m.addAction(self.a_screenshot)

        m = mb.addMenu("라이브러리(&L)")
        m.addActions([self.a_library, self.a_dupes])

        m = mb.addMenu("도움말(&H)")
        m.addActions([self.a_keys, self.a_about])

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
        self.set_hwdec(s.value("hwdec", DEFAULT_HWDEC), announce=False)
        self.playlist.set_repeat_mode(s.value("repeat", "none"))


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
        s.setValue("hwdec", self._hwdec)
        if self._library:
            self._library.close()
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
    def play(self, path: str) -> None:
        self._save_position()
        self._current = path
        self._time, self._duration = 0.0, 0.0
        self._data_sub_checked = False
        self.controls.reset()
        self.playlist.set_current(path)
        self.player.play(path)
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
        for a in self.hwdec_group.actions():
            a.setChecked(a.data() == mode)
        if announce:
            self.osd(f"하드웨어 가속: {HWDEC_MODES[mode]}")

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
        if self._current:
            self.db.clear_position(self._current)
        nxt = self.playlist.neighbor(1, auto=True)
        if nxt:
            self.play(nxt)

    def _save_position(self) -> None:
        path = self._current
        if self._closed or not path or "://" in path or self._time <= 0:
            return
        if self._duration and self._time >= self._duration * RESUME_END_MARGIN:
            self.db.clear_position(path)
        elif self._time >= RESUME_MIN_SECONDS:
            self.db.save_position(path, self._time, self._duration or None)

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
