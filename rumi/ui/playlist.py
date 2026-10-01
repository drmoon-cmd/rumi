"""재생목록 패널: 여러 재생목록을 탭으로 관리한다."""

from __future__ import annotations

import os
import random
from enum import Enum

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QInputDialog, QListWidget, QListWidgetItem, QMenu,
    QMessageBox, QTabBar, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from ..library.scanner import is_video
from .util import natural_key, reveal_in_file_manager

DEFAULT_TAB_NAME = "재생목록"


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


class PlaylistTab(QListWidget):
    """탭 하나 = 재생목록 하나."""

    files_dropped = Signal(list)

    def __init__(self, name: str):
        super().__init__()
        self.name = name
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

    def add_files(self, paths: list[str]) -> None:
        for p in expand_paths(paths):
            item = QListWidgetItem(os.path.basename(p) or p)
            item.setData(Qt.UserRole, p)
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


class PlaylistPanel(QWidget):
    play_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setElideMode(Qt.ElideRight)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.tabBarDoubleClicked.connect(self._on_tab_double_clicked)
        bar = self.tabs.tabBar()
        bar.setContextMenuPolicy(Qt.CustomContextMenu)
        bar.customContextMenuRequested.connect(self._tab_menu)
        add = QToolButton()
        add.setText("＋")
        add.setToolTip("새 재생목록 탭")
        add.setAutoRaise(True)
        add.setFocusPolicy(Qt.NoFocus)
        add.clicked.connect(lambda: self.new_tab())
        self.tabs.setCornerWidget(add, Qt.TopRightCorner)
        self._playing: PlaylistTab | None = None  # 재생 중인 항목이 있는 탭

        self.repeat = QComboBox()
        for mode, label in REPEAT_LABELS.items():
            self.repeat.addItem(label, mode)
        self.repeat.setFocusPolicy(Qt.NoFocus)

        def tool(text, tip, slot):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(slot)
            return b

        row = QHBoxLayout()
        row.addWidget(tool("정렬", "이름순 정렬", lambda: self.tab().sort_by_name()))
        row.addWidget(tool("삭제", "선택 항목 빼기 (Del)", self.remove_selected))
        row.addWidget(tool("비우기", "이 탭 비우기", self.clear))
        row.addStretch(1)
        row.addWidget(self.repeat)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.tabs, 1)
        lay.addLayout(row)

        delete = QAction(self)
        delete.setShortcut(Qt.Key_Delete)
        delete.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        delete.triggered.connect(self.remove_selected)
        self.addAction(delete)

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
        idx = self.tabs.addTab(t, name)
        self.tabs.setCurrentIndex(idx)
        self._update_close_buttons()
        return t

    def rename_tab(self, index: int) -> None:
        t = self.tab(index)
        name, ok = QInputDialog.getText(self, "탭 이름 바꾸기", "이름:", text=t.name)
        if ok and name.strip():
            t.name = name.strip()
            self.tabs.setTabText(index, t.name)

    def close_tab(self, index: int) -> None:
        if self.tabs.count() <= 1:
            return
        t = self.tab(index)
        if t.count() and QMessageBox.question(
                self, "탭 닫기", f"'{t.name}' 탭을 닫을까요? (목록 {t.count()}개, 파일은 지워지지 않습니다)"
        ) != QMessageBox.Yes:
            return
        if t is self._playing:
            self._playing = None
        self.tabs.removeTab(index)
        t.deleteLater()
        self._update_close_buttons()

    def _update_close_buttons(self) -> None:
        # 탭이 하나뿐이면 닫기 버튼을 숨긴다
        only = self.tabs.count() <= 1
        for i in range(self.tabs.count()):
            btn = self.tabs.tabBar().tabButton(i, QTabBar.RightSide)
            if btn:
                btn.setVisible(not only)

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
            close = menu.addAction("탭 닫기", lambda: self.close_tab(index))
            close.setEnabled(self.tabs.count() > 1)
        menu.exec(self.tabs.tabBar().mapToGlobal(pos))

    # ---- 보고 있는 탭에 대한 조작 (메인 창에서 사용) ----
    def paths(self) -> list[str]:
        return self.tab().paths()

    def set_files(self, paths: list[str]) -> None:
        t = self.tab()
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
            menu.addSeparator()
        menu.addAction("이름순 정렬", tab.sort_by_name)
        menu.addAction("이 탭 비우기", self.clear)
        menu.exec(tab.mapToGlobal(pos))

    # ---- 저장/복원 ----
    def to_state(self) -> dict:
        return {"current": self.tabs.currentIndex(),
                "tabs": [{"name": t.name, "paths": t.paths()} for t in self.all_tabs()]}

    def restore_state(self, state: dict) -> None:
        tabs = [t for t in (state or {}).get("tabs", []) if isinstance(t, dict)]
        if not tabs:
            return
        for t in self.all_tabs():
            self.tabs.removeTab(0)
            t.deleteLater()
        self._playing = None
        for t in tabs:
            new = self.new_tab(str(t.get("name") or DEFAULT_TAB_NAME))
            # 사라진 파일은 빼고 복원 (URL 은 유지)
            new.add_files([p for p in t.get("paths", []) if "://" in p or os.path.exists(p)])
        self.tabs.setCurrentIndex(min(max(int(state.get("current", 0)), 0), self.tabs.count() - 1))
