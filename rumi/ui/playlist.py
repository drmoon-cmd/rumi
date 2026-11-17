"""재생목록 패널: 여러 재생목록을 탭으로 관리한다."""

from __future__ import annotations

import os
import random
from enum import Enum

from PySide6.QtCore import QDir, QSettings, QSortFilterProxyModel, QStandardPaths, Qt, Signal
from PySide6.QtGui import QAction, QFont, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QFileSystemModel, QHBoxLayout, QInputDialog, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QSplitter, QTabBar, QTabWidget, QToolButton,
    QTreeView, QVBoxLayout, QWidget,
)

from ..library.scanner import VIDEO_EXTENSIONS, is_video
from ..playlist_io import PLAYLIST_EXTENSIONS, load_playlist, save_m3u
from .util import fmt_size, natural_key, reveal_in_file_manager

DEFAULT_TAB_NAME = "재생목록"
TAB_NAME_MAX = 14   # 탭에 보이는 이름 길이 (더 길면 줄이고, 전체 이름은 마우스를 올리면 보인다)


class RepeatMode(str, Enum):
    NONE = "none"
    ALL = "all"
    ONE = "one"
    SHUFFLE = "shuffle"


REPEAT_LABELS = {
    RepeatMode.NONE: "순서대로",
    RepeatMode.ALL: "전체 반복",
    RepeatMode.ONE: "한 개 반복",
    RepeatMode.SHUFFLE: "무작위",
}


def expand_paths(paths: list[str]) -> list[str]:
    """폴더는 안의 동영상 파일들로 펼친다 (자연 정렬)."""
    out: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            found = []
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                found += [os.path.join(dirpath, f) for f in filenames if is_video(f)]
            out += sorted(found, key=natural_key)
        elif os.path.isfile(p) or "://" in p:
            out.append(p)
    return out


def _drive_missing(path: str) -> bool:
    """이 파일이 든 드라이브(또는 네트워크 위치)가 지금 연결되어 있지 않은가."""
    drive = os.path.splitdrive(path)[0]
    if drive:
        return not os.path.exists(drive + os.sep)
    parts = path.split("/")
    # /media/사용자/하드이름/…, /Volumes/하드이름/…, /mnt/하드이름/…
    for depth, prefix in ((4, "/media/"), (4, "/run/media/"), (3, "/Volumes/"), (3, "/mnt/")):
        if path.startswith(prefix) and len(parts) > depth:
            return not os.path.isdir("/".join(parts[:depth]))
    return False


class PlaylistTab(QListWidget):
    """탭 하나 = 재생목록 하나."""

    files_dropped = Signal(list)

    def __init__(self, name: str):
        super().__init__()
        self.name = name
        self.pinned = False   # 고정한 탭: 닫히지 않고, 파일을 열어도 목록이 비워지지 않는다
        self._current: QListWidgetItem | None = None
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAcceptDrops(True)

    # ---- 끌어다 놓기 (파일은 추가, 목록 안 항목은 순서 변경) ----
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            self.files_dropped.emit([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])
            e.acceptProposedAction()
        else:
            super().dropEvent(e)

    # ---- 목록 ----
    def paths(self) -> list[str]:
        return [self.item(i).data(Qt.UserRole) for i in range(self.count())]

    def add_files(self, paths: list[str], as_is: bool = False) -> None:
        """as_is: 폴더 펼치기·존재 확인 없이 그대로 넣는다 (저장해 둔 목록을 되살릴 때)."""
        for p in (paths if as_is else expand_paths(paths)):
            item = QListWidgetItem(os.path.basename(p) or p)
            item.setData(Qt.UserRole, p)
            try:
                item.setToolTip(f"{p}\n{fmt_size(os.path.getsize(p))}" if "://" not in p else p)
            except OSError:
                item.setToolTip(p)
            self.addItem(item)

    def clear_all(self) -> None:
        self.clear()
        self._current = None

    def remove_selected(self) -> None:
        for it in self.selectedItems():
            if it is self._current:
                self._current = None
            self.takeItem(self.row(it))

    def sort_by_name(self) -> None:
        current = self.current_path()
        paths = sorted(self.paths(), key=lambda p: natural_key(os.path.basename(p)))
        self.clear_all()
        self.add_files(paths)
        if current:
            self.mark_current(current)

    SORT_KEYS = {
        "name": ("이름순", lambda p: natural_key(os.path.basename(p)), False),
        "name_desc": ("이름 역순", lambda p: natural_key(os.path.basename(p)), True),
        "mtime": ("수정한 날짜순", lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, False),
        "size": ("크기순", lambda p: os.path.getsize(p) if os.path.exists(p) else 0, False),
        "path": ("경로순", lambda p: natural_key(p), False),
    }

    def _replace_all(self, paths: list[str]) -> None:
        current = self.current_path()
        self.clear_all()
        self.add_files(paths)
        if current:
            self.mark_current(current)

    def sort_by(self, key: str) -> None:
        if key == "shuffle":
            paths = self.paths()
            random.shuffle(paths)
        else:
            _label, fn, reverse = self.SORT_KEYS[key]
            paths = sorted(self.paths(), key=fn, reverse=reverse)
        self._replace_all(paths)

    def move_selected(self, step: int) -> None:
        rows = sorted((self.row(it) for it in self.selectedItems()), reverse=step > 0)
        if not rows or (step < 0 and rows[0] == 0) or (step > 0 and rows[0] == self.count() - 1):
            return
        for r in rows:
            it = self.takeItem(r)
            self.insertItem(r + step, it)
            it.setSelected(True)

    def remove_missing(self) -> int:
        gone = [i for i in range(self.count())
                if "://" not in self.item(i).data(Qt.UserRole) and not os.path.exists(self.item(i).data(Qt.UserRole))]
        for i in reversed(gone):
            if self.item(i) is self._current:
                self._current = None
            self.takeItem(i)
        return len(gone)

    def remove_duplicates(self) -> int:
        seen, dup = set(), []
        for i in range(self.count()):
            key = os.path.normcase(self.item(i).data(Qt.UserRole))
            if key in seen:
                dup.append(i)
            seen.add(key)
        for i in reversed(dup):
            if self.item(i) is self._current:
                self._current = None
            self.takeItem(i)
        return len(dup)

    def apply_filter(self, text: str) -> None:
        text = text.strip().lower()
        for i in range(self.count()):
            self.setRowHidden(i, bool(text) and text not in self.item(i).text().lower())

    # ---- 재생 중 항목 ----
    def current_path(self) -> str | None:
        return self._current.data(Qt.UserRole) if self._current else None

    def unmark(self) -> None:
        if self._current is not None:
            f = self._current.font()
            f.setBold(False)
            self._current.setFont(f)
            self._current = None

    def mark_current(self, path: str) -> bool:
        self.unmark()
        for i in range(self.count()):
            it = self.item(i)
            if it.data(Qt.UserRole) == path:
                f = QFont(it.font())
                f.setBold(True)
                it.setFont(f)
                self._current = it
                self.scrollToItem(it)
                return True
        return False

    def neighbor(self, step: int, auto: bool, mode: RepeatMode) -> str | None:
        n = self.count()
        if n == 0:
            return None
        cur = self.row(self._current) if self._current else -1
        if auto and mode == RepeatMode.ONE and cur >= 0:
            return self.current_path()
        if mode == RepeatMode.SHUFFLE and n > 1:
            return self.item(random.choice([i for i in range(n) if i != cur])).data(Qt.UserRole)
        nxt = cur + step
        if 0 <= nxt < n:
            return self.item(nxt).data(Qt.UserRole)
        if mode == RepeatMode.ALL:
            return self.item(nxt % n).data(Qt.UserRole)
        return None


class _VideoFilterProxy(QSortFilterProxyModel):
    """탐색기에서 폴더·드라이브와 동영상/재생목록 파일만 보이게 한다."""

    def filterAcceptsRow(self, row: int, parent) -> bool:
        model = self.sourceModel()
        idx = model.index(row, 0, parent)
        if model.isDir(idx):
            return True
        return os.path.splitext(model.fileName(idx))[1].lower() in _EXPLORER_EXTS

    def lessThan(self, left, right) -> bool:
        m = self.sourceModel()
        ld, rd = m.isDir(left), m.isDir(right)
        if ld != rd:
            return ld  # 폴더 먼저
        return natural_key(m.fileName(left)) < natural_key(m.fileName(right))


_EXPLORER_EXTS = set(VIDEO_EXTENSIONS) | set(PLAYLIST_EXTENSIONS)


class PlaylistPanel(QWidget):
    play_requested = Signal(str)
    merge_requested = Signal(list)   # 여러 파일을 하나로 합치기
    tab_closed = Signal(str)         # 닫은 탭 이름 (되돌릴 수 있다고 알려 주기 위해)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("playlistTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        self.tabs.setElideMode(Qt.ElideNone)      # 이름을 '블랙…'처럼 자르지 않는다 (넘치면 좌우로 넘긴다)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.tabBarDoubleClicked.connect(self._on_tab_double_clicked)
        bar = self.tabs.tabBar()
        bar.setDrawBase(False)
        bar.setExpanding(False)
        bar.setContextMenuPolicy(Qt.CustomContextMenu)
        bar.customContextMenuRequested.connect(self._tab_menu)
        # 닫기(×)는 하나만 만들어 보고 있는 탭에 붙인다
        self._close_btn = QToolButton()
        self._close_btn.setObjectName("tabClose")
        self._close_btn.setText("✕")
        self._close_btn.setToolTip("이 탭 닫기 (Ctrl+Shift+T 로 되돌리기)")
        self._close_btn.setFocusPolicy(Qt.NoFocus)
        self._close_btn.clicked.connect(lambda: self.close_tab(self.tabs.currentIndex()))

        # 탭 줄 오른쪽: ◀ ▶ (탭이 넘칠 때 이동) · ▾ (모든 탭 목록) · ＋ (새 탭)
        def strip_button(text, tip, slot=None):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            if slot:
                b.clicked.connect(slot)
            return b

        self.tab_prev_btn = strip_button("◀", "이전 탭 (Ctrl+Shift+Tab)", lambda: self.step_tab(-1))
        self.tab_next_btn = strip_button("▶", "다음 탭 (Ctrl+Tab)", lambda: self.step_tab(1))
        self.tab_list_btn = strip_button("▾", "모든 탭 보기 · 바로 가기")
        self.tab_list_btn.setPopupMode(QToolButton.InstantPopup)
        tab_menu = QMenu(self.tab_list_btn)
        tab_menu.aboutToShow.connect(lambda: self._build_tab_list(tab_menu))
        self.tab_list_btn.setMenu(tab_menu)
        add = strip_button("＋", "새 재생목록 탭", lambda: self.new_tab())
        strip = QWidget()
        strip.setObjectName("tabStrip")
        strip_lay = QHBoxLayout(strip)
        strip_lay.setContentsMargins(4, 2, 0, 2)
        strip_lay.setSpacing(3)
        for b in (self.tab_prev_btn, self.tab_next_btn, self.tab_list_btn, add):
            strip_lay.addWidget(b)
        self.tabs.setCornerWidget(strip, Qt.TopRightCorner)
        self._playing: PlaylistTab | None = None  # 재생 중인 항목이 있는 탭
        self._closed: list[dict] = []             # 닫은 탭 (되돌리기용, 최근 10개)
        self.tabs.currentChanged.connect(lambda _i: self._update_close_buttons())
        bar.tabMoved.connect(lambda _a, _b: self._update_close_buttons())

        self.repeat = QComboBox()
        for mode, label in REPEAT_LABELS.items():
            self.repeat.addItem(label, mode)
        self.repeat.setFocusPolicy(Qt.NoFocus)

        # ---- 위: 탐색기 켜기/끄기 + 찾기
        self.explorer_btn = QToolButton()
        self.explorer_btn.setText("📁 탐색기")
        self.explorer_btn.setToolTip("드라이브·폴더에서 동영상 고르기")
        self.explorer_btn.setCheckable(True)
        self.explorer_btn.setChecked(True)
        self.explorer_btn.setFocusPolicy(Qt.NoFocus)
        self.explorer_btn.toggled.connect(lambda on: self.explorer.setVisible(on))
        self.search = QLineEdit()
        self.search.setPlaceholderText("목록에서 찾기…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda t: self.tab().apply_filter(t))
        self.tabs.currentChanged.connect(lambda _i: self.tab() and self.tab().apply_filter(self.search.text()))
        top = QHBoxLayout()
        top.addWidget(self.explorer_btn)
        top.addWidget(self.search, 1)

        # ---- 탐색기 (드라이브/폴더/동영상)
        self.fs = QFileSystemModel(self)
        self.fs.setRootPath("")
        self.fs.setFilter(QDir.AllDirs | QDir.Files | QDir.NoDotAndDotDot | QDir.Drives)
        # 동영상·재생목록 파일만 보이게 거른다. Qt 의 이름 패턴 필터 대신 확장자를 직접 검사해
        # 한글 등 어떤 파일 이름이든 확장자만 맞으면 보이도록 한다.
        self.fs_proxy = _VideoFilterProxy(self)
        self.fs_proxy.setSourceModel(self.fs)
        self.explorer = QTreeView()
        self.explorer.setModel(self.fs_proxy)
        self.explorer.setSortingEnabled(True)
        self.explorer.sortByColumn(0, Qt.AscendingOrder)
        self.explorer.setHeaderHidden(True)
        for col in (1, 2, 3):
            self.explorer.hideColumn(col)
        self.explorer.setDragEnabled(True)  # 목록으로 끌어다 놓기
        self.explorer.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.explorer.doubleClicked.connect(self._explorer_activated)
        self.explorer.setContextMenuPolicy(Qt.CustomContextMenu)
        self.explorer.customContextMenuRequested.connect(self._explorer_menu)
        self.fs.directoryLoaded.connect(self._on_dir_loaded)
        home = QStandardPaths.writableLocation(QStandardPaths.MoviesLocation) or QDir.homePath()
        self.go_to_folder(home)

        self.split = QSplitter(Qt.Vertical)
        self.split.addWidget(self.explorer)
        self.split.addWidget(self.tabs)
        self.split.setStretchFactor(0, 2)
        self.split.setStretchFactor(1, 3)

        # ---- 아래: 추가 / 삭제 / 정렬 / 목록 메뉴 + 반복 모드
        def menu_button(text, tip, build):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setPopupMode(QToolButton.InstantPopup)
            b.setFocusPolicy(Qt.NoFocus)
            m = QMenu(b)
            m.aboutToShow.connect(lambda m=m: (m.clear(), build(m)))
            b.setMenu(m)
            return b

        row = QHBoxLayout()
        row.addWidget(menu_button("추가", "파일·폴더·URL 추가", self._build_add_menu))
        row.addWidget(menu_button("삭제", "목록에서 빼기·정리", self._build_remove_menu))
        row.addWidget(menu_button("정렬", "정렬·순서 바꾸기", self._build_sort_menu))
        row.addWidget(menu_button("목록", "재생목록 파일 저장·불러오기", self._build_list_menu))
        row.addStretch(1)
        row.addWidget(self.repeat)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(top)
        lay.addWidget(self.split, 1)
        lay.addLayout(row)

        for key, slot in ((Qt.Key_Delete, self.remove_selected),
                          (QKeySequence("Ctrl+Up"), lambda: self.tab().move_selected(-1)),
                          (QKeySequence("Ctrl+Down"), lambda: self.tab().move_selected(1))):
            a = QAction(self)
            a.setShortcut(key)
            a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            a.triggered.connect(slot)
            self.addAction(a)

        self.new_tab(DEFAULT_TAB_NAME)

    # ---- 탭 ----
    def tab(self, index: int | None = None) -> PlaylistTab:
        """보고 있는 탭 (index 를 주면 그 탭)."""
        return self.tabs.widget(self.tabs.currentIndex() if index is None else index)

    def all_tabs(self) -> list[PlaylistTab]:
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def new_tab(self, name: str | None = None, paths: list[str] | None = None) -> PlaylistTab:
        if name is None:
            existing = {t.name for t in self.all_tabs()}
            n = 2
            name = f"{DEFAULT_TAB_NAME} {n}"
            while name in existing:
                n += 1
                name = f"{DEFAULT_TAB_NAME} {n}"
        t = PlaylistTab(name)
        t.itemActivated.connect(lambda it, t=t: self._play_item(t, it))
        t.files_dropped.connect(t.add_files)
        t.setContextMenuPolicy(Qt.CustomContextMenu)
        t.customContextMenuRequested.connect(lambda pos, t=t: self._item_menu(t, pos))
        if paths:
            t.add_files(paths)
        t.name = name
        idx = self.tabs.addTab(t, self._tab_label(t))
        self._set_tab_text(idx)
        self.tabs.setCurrentIndex(idx)
        self._update_close_buttons()
        return t

    def _tab_label(self, t: PlaylistTab) -> str:
        name = t.name if len(t.name) <= TAB_NAME_MAX else t.name[:TAB_NAME_MAX - 1] + "…"
        return f"📌 {name}" if t.pinned else name

    def _set_tab_text(self, index: int) -> None:
        t = self.tab(index)
        self.tabs.setTabText(index, self._tab_label(t))
        self.tabs.setTabToolTip(index, f"{t.name} · {t.count()}개" + (" · 고정됨" if t.pinned else ""))

    def step_tab(self, step: int) -> None:
        """이전/다음 탭으로 (끝에서는 반대쪽 끝으로 돈다)."""
        n = self.tabs.count()
        if n > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % n)

    def _build_tab_list(self, menu: QMenu) -> None:
        """모든 탭을 세로로 보여 주는 메뉴: 탭이 많아도 한 번에 골라 갈 수 있다."""
        menu.clear()
        current = self.tabs.currentIndex()
        for i, t in enumerate(self.all_tabs()):
            mark = "📌 " if t.pinned else ""
            playing = "  ▶" if t is self._playing and t.current_path() else ""
            a = menu.addAction(f"{mark}{t.name}   ({t.count()}개){playing}",
                               lambda i=i: self.tabs.setCurrentIndex(i))
            a.setCheckable(True)
            a.setChecked(i == current)
        menu.addSeparator()
        menu.addAction("새 탭", lambda: self.new_tab())
        reopen = menu.addAction("닫은 탭 다시 열기\tCtrl+Shift+T", self.reopen_closed_tab)
        reopen.setEnabled(self.can_reopen_tab())

    def rename_tab(self, index: int) -> None:
        t = self.tab(index)
        name, ok = QInputDialog.getText(self, "탭 이름 바꾸기", "이름:", text=t.name)
        if ok and name.strip():
            t.name = name.strip()
            self._set_tab_text(index)

    def set_pinned(self, index: int, on: bool) -> None:
        t = self.tab(index)
        t.pinned = on
        self._set_tab_text(index)
        self._update_close_buttons()

    def close_tab(self, index: int) -> None:
        """탭 닫기. 묻지 않고 닫되, '닫은 탭 다시 열기'로 되돌릴 수 있게 기억해 둔다."""
        if self.tabs.count() <= 1:
            return
        t = self.tab(index)
        if t.pinned:
            return
        if t.count():
            self._closed.append({"name": t.name, "paths": t.paths(), "index": index})
            del self._closed[:-10]
        if t is self._playing:
            self._playing = None
        self._detach_close()
        self.tabs.removeTab(index)
        t.deleteLater()
        self._update_close_buttons()
        if t.count():
            self.tab_closed.emit(t.name)

    def can_reopen_tab(self) -> bool:
        return bool(self._closed)

    def reopen_closed_tab(self) -> bool:
        if not self._closed:
            return False
        c = self._closed.pop()
        t = self.new_tab(c["name"], c["paths"])
        self.tabs.tabBar().moveTab(self.tabs.indexOf(t), min(c["index"], self.tabs.count() - 1))
        self.tabs.setCurrentWidget(t)
        return True

    def _detach_close(self) -> None:
        """닫기 버튼을 탭에서 떼어 둔다 (탭을 지우면 붙어 있던 버튼도 함께 지워지기 때문)."""
        bar = self.tabs.tabBar()
        for i in range(self.tabs.count()):
            if bar.tabButton(i, QTabBar.RightSide) is self._close_btn:
                bar.setTabButton(i, QTabBar.RightSide, None)
        self._close_btn.setParent(self)
        self._close_btn.hide()

    def _update_close_buttons(self) -> None:
        # 닫기(×) 버튼은 지금 보고 있는 탭에만 둔다: 다른 탭을 고르려다 닫는 실수를 막는다.
        # 탭이 하나뿐이거나 고정한 탭이면 없다.
        bar = self.tabs.tabBar()
        current = self.tabs.currentIndex()
        show = self.tabs.count() > 1 and current >= 0 and not self.tab(current).pinned
        if not show or bar.tabButton(current, QTabBar.RightSide) is not self._close_btn:
            self._detach_close()
        if show:
            if bar.tabButton(current, QTabBar.RightSide) is not self._close_btn:
                bar.setTabButton(current, QTabBar.RightSide, self._close_btn)
            self._close_btn.show()
        many = self.tabs.count() > 1
        self.tab_prev_btn.setEnabled(many)
        self.tab_next_btn.setEnabled(many)
        if current >= 0:
            self._set_tab_text(current)

    def _on_tab_double_clicked(self, index: int) -> None:
        if index >= 0:
            self.rename_tab(index)
        else:
            self.new_tab()

    def _tab_menu(self, pos) -> None:
        index = self.tabs.tabBar().tabAt(pos)
        menu = QMenu(self)
        menu.addAction("새 탭", lambda: self.new_tab())
        if index >= 0:
            menu.addAction("이름 바꾸기…", lambda: self.rename_tab(index))
            pinned = self.tab(index).pinned
            pin = menu.addAction("탭 고정 (닫히지 않게)", lambda: self.set_pinned(index, not pinned))
            pin.setCheckable(True)
            pin.setChecked(pinned)
            close = menu.addAction("탭 닫기", lambda: self.close_tab(index))
            close.setEnabled(self.tabs.count() > 1 and not pinned)
        reopen = menu.addAction("닫은 탭 다시 열기\tCtrl+Shift+T", self.reopen_closed_tab)
        reopen.setEnabled(self.can_reopen_tab())
        menu.exec(self.tabs.tabBar().mapToGlobal(pos))

    # ---- 탐색기 ----
    def _view_index(self, path: str):
        return self.fs_proxy.mapFromSource(self.fs.index(path))

    def _path_of(self, view_index) -> str:
        return self.fs.filePath(self.fs_proxy.mapToSource(view_index))

    def go_to_folder(self, path: str) -> None:
        """탐색기에서 그 폴더를 펼쳐 보여 준다. 폴더 목록은 백그라운드로 읽히므로
        아직 안 읽힌 상위 폴더가 있으면 다 읽힌 뒤 다시 시도한다."""
        self._goto = path
        idx = self._view_index(path)
        if idx.isValid():
            self.explorer.setCurrentIndex(idx)
            self.explorer.expand(idx)
            self.explorer.scrollTo(idx, QAbstractItemView.PositionAtTop)

    def _on_dir_loaded(self, loaded: str) -> None:
        target = getattr(self, "_goto", None)
        if target and os.path.normcase(target).startswith(os.path.normcase(loaded)):
            idx = self._view_index(target)
            if idx.isValid():
                self.explorer.setCurrentIndex(idx)
                self.explorer.expand(idx)
                self.explorer.scrollTo(idx, QAbstractItemView.PositionAtTop)
            if os.path.normcase(loaded) == os.path.normcase(target):
                self._goto = None

    def _explorer_paths(self) -> list[str]:
        rows = {i.row(): i for i in self.explorer.selectionModel().selectedRows(0)}
        return [self._path_of(i) for _, i in sorted(rows.items())]

    def _explorer_activated(self, index) -> None:
        path = self._path_of(index)
        if os.path.isdir(path):
            return  # 폴더는 펼치기만 (기본 동작)
        if path.lower().endswith(PLAYLIST_EXTENSIONS):
            self.open_playlist_file(path)
            return
        t = self.tab()
        if path not in t.paths():
            t.add_files([path])
        self._playing = t
        self.play_requested.emit(path)

    def _explorer_menu(self, pos) -> None:
        paths = self._explorer_paths()
        if not paths:
            return
        first = paths[0]
        menu = QMenu(self)
        if os.path.isfile(first) and is_video(first):
            menu.addAction("재생", lambda: self._explorer_activated(self.explorer.indexAt(pos)))
        menu.addAction("지금 탭에 추가", lambda: self.tab().add_files(paths))
        menu.addAction("지금 탭을 이것으로 바꾸고 재생", lambda: self._replace_and_play(paths))
        name = os.path.basename(first.rstrip("/\\")) or first
        menu.addAction("새 탭으로 열기", lambda: self.new_tab(name, paths))
        if len(paths) > 1 or os.path.isdir(first):
            menu.addAction("하나로 합치기…", lambda: self.merge_requested.emit(paths))
        menu.addSeparator()
        menu.addAction("파일 위치 열기", lambda: reveal_in_file_manager(first))
        menu.exec(self.explorer.viewport().mapToGlobal(pos))

    def _replace_and_play(self, paths: list[str]) -> None:
        self.set_files(paths)
        t = self.tab()
        if t.count():
            self._play_item(t, t.item(0))

    # ---- 추가/삭제/정렬/목록 메뉴 ----
    def _build_add_menu(self, m: QMenu) -> None:
        m.addAction("파일 추가…", self.add_files_dialog)
        m.addAction("폴더 추가… (하위 폴더 포함)", self.add_folder_dialog)
        m.addAction("URL 추가…", self.add_url_dialog)

    def _build_remove_menu(self, m: QMenu) -> None:
        m.addAction("선택 항목 빼기\tDel", self.remove_selected)
        m.addAction("없는 파일 정리", lambda: self._report("없는 파일", self.tab().remove_missing()))
        m.addAction("중복 항목 정리", lambda: self._report("중복 항목", self.tab().remove_duplicates()))
        m.addSeparator()
        m.addAction("이 탭 비우기", self.clear)

    def _build_sort_menu(self, m: QMenu) -> None:
        for key, (label, _fn, _rev) in PlaylistTab.SORT_KEYS.items():
            m.addAction(label, lambda k=key: self.tab().sort_by(k))
        m.addAction("무작위로 섞기", lambda: self.tab().sort_by("shuffle"))
        m.addSeparator()
        m.addAction("선택 항목 위로\tCtrl+↑", lambda: self.tab().move_selected(-1))
        m.addAction("선택 항목 아래로\tCtrl+↓", lambda: self.tab().move_selected(1))

    def _build_list_menu(self, m: QMenu) -> None:
        m.addAction("새 탭", lambda: self.new_tab())
        m.addSeparator()
        m.addAction("재생목록 불러오기… (새 탭)", self.open_playlist_dialog)
        m.addAction("이 탭을 재생목록 파일로 저장…", self.save_playlist_dialog)

    def _report(self, what: str, n: int) -> None:
        QMessageBox.information(self, "재생목록", f"{what} {n}개를 목록에서 뺐습니다." if n else f"{what}이(가) 없습니다.")

    def _last_dir(self) -> str:
        return QSettings().value("lastDir", "") or QDir.homePath()

    def add_files_dialog(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(self, "재생목록에 파일 추가", self._last_dir(),
                                                f"동영상 ({exts});;모든 파일 (*)")
        if files:
            QSettings().setValue("lastDir", os.path.dirname(files[0]))
            self.tab().add_files(files)

    def add_folder_dialog(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "재생목록에 폴더 추가", self._last_dir())
        if d:
            QSettings().setValue("lastDir", d)
            self.tab().add_files([d])

    def add_url_dialog(self) -> None:
        url, ok = QInputDialog.getText(self, "URL 추가", "스트림 주소 (http, https, rtsp 등):")
        if ok and "://" in url:
            self.tab().add_files([url.strip()])

    def open_playlist_dialog(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "재생목록 불러오기", self._last_dir(),
                                           "재생목록 (*.m3u8 *.m3u *.pls);;모든 파일 (*)")
        if f:
            self.open_playlist_file(f)

    def open_playlist_file(self, path: str) -> None:
        try:
            items = load_playlist(path)
        except OSError as e:
            QMessageBox.warning(self, "재생목록", f"읽지 못했습니다:\n{e}")
            return
        t = self.new_tab(os.path.splitext(os.path.basename(path))[0])
        t.add_files([p for p in items if "://" in p or os.path.exists(p)])
        missing = len(items) - t.count()
        if missing:
            QMessageBox.information(self, "재생목록", f"찾을 수 없는 파일 {missing}개는 빼고 불러왔습니다.")

    def save_playlist_dialog(self) -> None:
        t = self.tab()
        if not t.count():
            return
        f, _ = QFileDialog.getSaveFileName(self, "재생목록 저장", os.path.join(self._last_dir(), t.name + ".m3u8"),
                                           "재생목록 (*.m3u8)")
        if f:
            save_m3u(f if f.lower().endswith((".m3u8", ".m3u")) else f + ".m3u8", t.paths())

    # ---- 보고 있는 탭에 대한 조작 (메인 창에서 사용) ----
    def paths(self) -> list[str]:
        return self.tab().paths()

    def set_files(self, paths: list[str]) -> None:
        """파일 열기: 보고 있는 탭의 목록을 바꾼다. 고정한 탭이면 비우지 않고 뒤에 더한다."""
        t = self.tab()
        if t.pinned:
            t.add_files(paths)
            return
        t.clear_all()
        t.add_files(paths)
        if t is self._playing:
            self._playing = None

    def add_files(self, paths: list[str]) -> None:
        self.tab().add_files(paths)

    def clear(self) -> None:
        t = self.tab()
        t.clear_all()
        if t is self._playing:
            self._playing = None

    def remove_selected(self) -> None:
        self.tab().remove_selected()

    def sort_by_name(self) -> None:
        self.tab().sort_by_name()

    # ---- 재생 중 항목 ----
    def current_path(self) -> str | None:
        return self._playing.current_path() if self._playing else None

    def set_current(self, path: str) -> None:
        """재생 중 표시. 재생 중이던 탭 → 보고 있는 탭 → 나머지 탭 순서로 찾는다."""
        order = [t for t in (self._playing, self.tab()) if t is not None]
        order += [t for t in self.all_tabs() if t not in order]
        for t in self.all_tabs():
            t.unmark()
        for t in order:
            if t.mark_current(path):
                self._playing = t
                return
        self._playing = None

    def repeat_mode(self) -> RepeatMode:
        return RepeatMode(self.repeat.currentData())

    def set_repeat_mode(self, mode: str) -> None:
        idx = self.repeat.findData(RepeatMode(mode))
        if idx >= 0:
            self.repeat.setCurrentIndex(idx)

    def neighbor(self, step: int, auto: bool = False) -> str | None:
        """다음(step=1)/이전(step=-1) 파일. 재생 중인 탭 기준 (다른 탭을 보고 있어도 그대로)."""
        t = self._playing or self.tab()
        return t.neighbor(step, auto, self.repeat_mode())

    def _play_item(self, tab: PlaylistTab, item: QListWidgetItem) -> None:
        self._playing = tab
        self.play_requested.emit(item.data(Qt.UserRole))

    def _item_menu(self, tab: PlaylistTab, pos) -> None:
        item = tab.itemAt(pos)
        menu = QMenu(self)
        if item:
            menu.addAction("재생", lambda: self._play_item(tab, item))
            path = item.data(Qt.UserRole)
            if os.path.exists(path):
                menu.addAction("파일 위치 열기", lambda: reveal_in_file_manager(path))
            others = [t for t in self.all_tabs() if t is not tab]
            copy = menu.addMenu("다른 탭으로 복사")
            selected = [it.data(Qt.UserRole) for it in tab.selectedItems()] or [path]
            for t in others:
                copy.addAction(t.name, lambda t=t: t.add_files(selected))
            if others:
                copy.addSeparator()
            copy.addAction("새 탭으로…", lambda: self.new_tab(paths=selected))
            menu.addAction("목록에서 빼기", tab.remove_selected)
            if len(selected) > 1:
                menu.addAction(f"선택한 {len(selected)}개 하나로 합치기…",
                               lambda: self.merge_requested.emit(selected))
            menu.addSeparator()
        menu.addAction("이름순 정렬", tab.sort_by_name)
        menu.addAction("이 탭 비우기", self.clear)
        menu.exec(tab.mapToGlobal(pos))

    # ---- 저장/복원 ----
    def to_state(self) -> dict:
        cur = self.explorer.currentIndex()
        return {"current": self.tabs.currentIndex(),
                "tabs": [{"name": t.name, "paths": t.paths(), "pinned": t.pinned} for t in self.all_tabs()],
                "explorer": self.explorer_btn.isChecked(),
                "explorerPath": self._path_of(cur) if cur.isValid() else "",
                "split": self.split.sizes()}

    def restore_state(self, state: dict) -> None:
        state = state or {}
        self.explorer_btn.setChecked(bool(state.get("explorer", True)))
        if state.get("explorerPath"):
            self.go_to_folder(state["explorerPath"])
        if isinstance(state.get("split"), list) and len(state["split"]) == 2:
            self.split.setSizes([int(x) for x in state["split"]])
        tabs = [t for t in state.get("tabs", []) if isinstance(t, dict)]
        if not tabs:
            return
        for t in self.all_tabs():
            self._detach_close()
            self.tabs.removeTab(0)
            t.deleteLater()
        self._playing = None
        for t in tabs:
            new = self.new_tab(str(t.get("name") or DEFAULT_TAB_NAME))
            # 사라진 파일은 빼고 복원 (URL 은 유지). 드라이브째 연결되어 있지 않은 파일은 남겨 둔다:
            # 외장 하드를 뽑았다고 목록이 비워지면 안 된다
            new.add_files([p for p in t.get("paths", []) if isinstance(p, str)
                           and ("://" in p or os.path.isfile(p) or _drive_missing(p))], as_is=True)
            if t.get("pinned"):
                self.set_pinned(self.tabs.indexOf(new), True)
        self.tabs.setCurrentIndex(min(max(int(state.get("current", 0)), 0), self.tabs.count() - 1))
