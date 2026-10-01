"""재생목록 패널."""

from __future__ import annotations

import os
import random
from enum import Enum

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QListWidget, QListWidgetItem, QMenu,
    QToolButton, QVBoxLayout, QWidget,
)

from ..library.scanner import is_video
from .util import natural_key, reveal_in_file_manager


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


class _List(QListWidget):
    files_dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAcceptDrops(True)

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


class PlaylistPanel(QWidget):
    play_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.list = _List()
        self.list.itemActivated.connect(lambda it: self._play_item(it))
        self.list.files_dropped.connect(self.add_files)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self._current: QListWidgetItem | None = None

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

        bar = QHBoxLayout()
        bar.addWidget(tool("정렬", "이름순 정렬", self.sort_by_name))
        bar.addWidget(tool("삭제", "선택 항목 빼기 (Del)", self.remove_selected))
        bar.addWidget(tool("비우기", "목록 비우기", self.clear))
        bar.addStretch(1)
        bar.addWidget(self.repeat)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.list)
        lay.addLayout(bar)

        delete = QAction(self)
        delete.setShortcut(Qt.Key_Delete)
        delete.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        delete.triggered.connect(self.remove_selected)
        self.addAction(delete)

    # ---- 목록 조작 ----
    def paths(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    def set_files(self, paths: list[str]) -> None:
        self.clear()
        self.add_files(paths)

    def add_files(self, paths: list[str]) -> None:
        for p in expand_paths(paths):
            item = QListWidgetItem(os.path.basename(p) or p)
            item.setData(Qt.UserRole, p)
            item.setToolTip(p)
            self.list.addItem(item)

    def clear(self) -> None:
        self.list.clear()
        self._current = None

    def remove_selected(self) -> None:
        for it in self.list.selectedItems():
            if it is self._current:
                self._current = None
            self.list.takeItem(self.list.row(it))

    def sort_by_name(self) -> None:
        current = self.current_path()
        paths = sorted(self.paths(), key=lambda p: natural_key(os.path.basename(p)))
        self.set_files(paths)
        if current:
            self.set_current(current)

    # ---- 현재 항목 ----
    def current_path(self) -> str | None:
        return self._current.data(Qt.UserRole) if self._current else None

    def set_current(self, path: str) -> None:
        if self._current is not None:
            f = self._current.font()
            f.setBold(False)
            self._current.setFont(f)
            self._current = None
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.UserRole) == path:
                f = QFont(it.font())
                f.setBold(True)
                it.setFont(f)
                self._current = it
                self.list.scrollToItem(it)
                return

    def repeat_mode(self) -> RepeatMode:
        return RepeatMode(self.repeat.currentData())

    def set_repeat_mode(self, mode: str) -> None:
        idx = self.repeat.findData(RepeatMode(mode))
        if idx >= 0:
            self.repeat.setCurrentIndex(idx)

    def neighbor(self, step: int, auto: bool = False) -> str | None:
        """다음(step=1)/이전(step=-1) 파일. auto=True 는 재생 끝났을 때 반복 모드를 따른다."""
        n = self.list.count()
        if n == 0:
            return None
        cur = self.list.row(self._current) if self._current else -1
        mode = self.repeat_mode()
        if auto and mode == RepeatMode.ONE and cur >= 0:
            return self.current_path()
        if mode == RepeatMode.SHUFFLE and n > 1:
            choices = [i for i in range(n) if i != cur]
            return self.list.item(random.choice(choices)).data(Qt.UserRole)
        nxt = cur + step
        if 0 <= nxt < n:
            return self.list.item(nxt).data(Qt.UserRole)
        if mode == RepeatMode.ALL:
            return self.list.item(nxt % n).data(Qt.UserRole)
        return None

    def _play_item(self, item: QListWidgetItem) -> None:
        self.play_requested.emit(item.data(Qt.UserRole))

    def _menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        menu = QMenu(self)
        if item:
            menu.addAction("재생", lambda: self._play_item(item))
            path = item.data(Qt.UserRole)
            if os.path.exists(path):
                menu.addAction("파일 위치 열기", lambda: reveal_in_file_manager(path))
            menu.addAction("목록에서 빼기", self.remove_selected)
            menu.addSeparator()
        menu.addAction("이름순 정렬", self.sort_by_name)
        menu.addAction("목록 비우기", self.clear)
        menu.exec(self.list.mapToGlobal(pos))
