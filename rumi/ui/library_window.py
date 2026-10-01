"""동영상 라이브러리 창: 폴더 관리, 중복 정리, 정리함(표시/격리 항목 관리)."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..library import LibraryDB, Organizer, VideoStatus, find_duplicates, scan_folder, suggest_keep
from ..library.duplicates import DuplicateGroup
from ..library.organizer import OpResult, PurgeCheck
from .util import fmt_date, fmt_size, reveal_in_file_manager
from .workers import run_with_progress

STATUS_LABELS = {
    VideoStatus.ACTIVE: "정상",
    VideoStatus.MARKED: "중복 표시",
    VideoStatus.QUARANTINED: "격리됨",
}
STATUS_COLORS = {
    VideoStatus.MARKED: QColor("#c78a00"),
    VideoStatus.QUARANTINED: QColor("#c0392b"),
}
ID_ROLE = Qt.UserRole
SORT_ROLE = Qt.UserRole + 1


class SortItem(QTableWidgetItem):
    """표시 문자열 대신 SORT_ROLE 값으로 정렬되는 셀."""

    def __lt__(self, other):
        a, b = self.data(SORT_ROLE), other.data(SORT_ROLE)
        if a is not None and b is not None:
            return a < b
        return super().__lt__(other)


def _cell(text: str, sort=None, video_id: int | None = None) -> SortItem:
    it = SortItem(text)
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    it.setToolTip(text)
    if sort is not None:
        it.setData(SORT_ROLE, sort)
    if video_id is not None:
        it.setData(ID_ROLE, video_id)
    return it


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.ExtendedSelection)
    t.verticalHeader().setVisible(False)
    t.setSortingEnabled(True)
    t.setAlternatingRowColors(True)
    t.setTextElideMode(Qt.ElideMiddle)
    t.setWordWrap(False)
    h = t.horizontalHeader()
    h.setSectionResizeMode(0, QHeaderView.Stretch)
    for i in range(1, len(headers)):
        h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
    return t


def _selected_ids(table: QTableWidget) -> list[int]:
    rows = sorted({i.row() for i in table.selectedIndexes()})
    return [table.item(r, 0).data(ID_ROLE) for r in rows]


def _report_errors(parent: QWidget, title: str, res: OpResult) -> None:
    if res.errors:
        lines = "\n".join(f"• {v.name}: {msg}" for v, msg in res.errors[:20])
        more = f"\n… 외 {len(res.errors) - 20}개" if len(res.errors) > 20 else ""
        QMessageBox.warning(parent, title, f"{len(res.done)}개 완료, {len(res.errors)}개 실패\n\n{lines}{more}")


class PurgeConfirmDialog(QDialog):
    """영구 삭제 직전 최종 확인. 점검을 통과한 파일만 삭제된다."""

    def __init__(self, checks: list[PurgeCheck], parent=None):
        super().__init__(parent)
        self.setWindowTitle("영구 삭제 확인")
        self.resize(820, 460)
        ok = [c for c in checks if c.ok]
        bad = [c for c in checks if not c.ok]
        total = sum(c.video.size for c in ok)

        info = QLabel(
            f"<b>삭제 가능 {len(ok)}개 ({fmt_size(total)})</b>"
            + (f" · <span style='color:#c0392b'>삭제 불가 {len(bad)}개 (그대로 유지)</span>" if bad else "")
            + "<br>남겨 둘 사본이 존재하고 내용이 같은지 방금 다시 확인했습니다. "
              "삭제한 파일은 휴지통을 거치지 않으며 되돌릴 수 없습니다.")
        info.setWordWrap(True)

        table = _table(["삭제할 파일", "결과", "남겨 둘 사본 / 사유"])
        table.setSortingEnabled(False)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        for c in ok + bad:
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, _cell(c.video.path))
            table.item(r, 0).setText(c.video.name)
            res = _cell("삭제" if c.ok else "건너뜀")
            res.setForeground(QBrush(QColor("#c0392b" if c.ok else "#7f8c8d")))
            table.setItem(r, 1, res)
            table.setItem(r, 2, _cell(c.kept_copy if c.ok else c.reason))

        self.confirm = QCheckBox("위 파일들을 영구 삭제하는 것을 확인했습니다")
        buttons = QDialogButtonBox()
        self.delete_btn = buttons.addButton(f"{len(ok)}개 영구 삭제", QDialogButtonBox.DestructiveRole)
        buttons.addButton("취소", QDialogButtonBox.RejectRole)
        self.delete_btn.setEnabled(False)
        self.confirm.toggled.connect(lambda on: self.delete_btn.setEnabled(on and bool(ok)))
        self.delete_btn.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addWidget(table, 1)
        lay.addWidget(self.confirm)
        lay.addWidget(buttons)


class LibraryWindow(QWidget):
    play_files = Signal(list)
    enqueue_files = Signal(list)

    def __init__(self, db: LibraryDB, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("동영상 라이브러리")
        self.resize(1100, 680)
        self.db = db
        self.org = Organizer(db)
        self._groups: list[DuplicateGroup] = []
        self._worker = None

        # 폴더 목록
        self.folder_list = QListWidget()
        self.folder_list.setTextElideMode(Qt.ElideMiddle)
        add = QPushButton("폴더 추가…")
        add.clicked.connect(self.add_folder)
        remove = QPushButton("제외")
        remove.setToolTip("라이브러리에서 폴더를 뺍니다 (파일은 그대로)")
        remove.clicked.connect(self.remove_folder)
        rescan = QPushButton("다시 스캔")
        rescan.clicked.connect(lambda: self.scan())
        fbtns = QHBoxLayout()
        for b in (add, remove, rescan):
            fbtns.addWidget(b)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("<b>라이브러리 폴더</b>"))
        ll.addWidget(self.folder_list, 1)
        ll.addLayout(fbtns)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_videos_tab(), "동영상")
        self.tabs.addTab(self._build_dupes_tab(), "중복 정리")
        self.tabs.addTab(self._build_trash_tab(), "정리함")

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 840])
        lay = QVBoxLayout(self)
        lay.addWidget(split)

        self.refresh()

    # ================= 동영상 탭 =================
    def _build_videos_tab(self) -> QWidget:
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("이름으로 찾기…")
        self.filter.textChanged.connect(self._apply_filter)
        self.videos_summary = QLabel()
        self.videos = _table(["이름", "폴더", "크기", "수정일"])
        self.videos.itemDoubleClicked.connect(lambda it: self._play_rows(self.videos))
        self.videos.setContextMenuPolicy(Qt.CustomContextMenu)
        self.videos.customContextMenuRequested.connect(lambda p: self._row_menu(self.videos, p))
        top = QHBoxLayout()
        top.addWidget(self.filter, 1)
        top.addWidget(self.videos_summary)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addLayout(top)
        lay.addWidget(self.videos)
        return w

    def _fill_videos(self) -> None:
        vids = self.db.videos([VideoStatus.ACTIVE])
        t = self.videos
        t.setSortingEnabled(False)
        t.setRowCount(0)
        t.setRowCount(len(vids))
        for r, v in enumerate(vids):
            t.setItem(r, 0, _cell(v.name, v.name.lower(), v.id))
            t.setItem(r, 1, _cell(os.path.dirname(v.path)))
            t.setItem(r, 2, _cell(fmt_size(v.size), v.size))
            t.setItem(r, 3, _cell(fmt_date(v.mtime), v.mtime))
        t.setSortingEnabled(True)
        self.videos_summary.setText(f"{len(vids)}개 · {fmt_size(sum(v.size for v in vids))}")
        self._apply_filter(self.filter.text())

    def _apply_filter(self, text: str) -> None:
        text = text.strip().lower()
        for r in range(self.videos.rowCount()):
            self.videos.setRowHidden(r, bool(text) and text not in self.videos.item(r, 0).text().lower())

    def _paths_of(self, ids: list[int]) -> list[str]:
        return [v.path for v in (self.db.get(i) for i in ids) if v]

    def _play_rows(self, table: QTableWidget) -> None:
        paths = self._paths_of(_selected_ids(table))
        if paths:
            self.play_files.emit(paths)

    def _row_menu(self, table: QTableWidget, pos) -> None:
        ids = _selected_ids(table)
        if not ids:
            return
        paths = self._paths_of(ids)
        menu = QMenu(self)
        menu.addAction("재생", lambda: self.play_files.emit(paths))
        menu.addAction("재생목록에 추가", lambda: self.enqueue_files.emit(paths))
        menu.addAction("파일 위치 열기", lambda: reveal_in_file_manager(paths[0]))
        if table is self.videos:
            menu.addSeparator()
            menu.addAction("중복으로 표시", lambda: self._do(self.org.mark, ids, "표시"))
        menu.exec(table.viewport().mapToGlobal(pos))

    # ================= 중복 정리 탭 =================
    def _build_dupes_tab(self) -> QWidget:
        find = QPushButton("중복 찾기")
        find.setDefault(True)
        find.clicked.connect(self.find_dupes)
        self.dupes_summary = QLabel("‘중복 찾기’를 눌러 내용이 완전히 같은 동영상을 찾습니다.")
        help_ = QLabel(
            "체크한 파일이 정리 대상입니다. 체크하지 않은 파일은 그대로 남습니다. "
            "기본으로 가장 오래된 파일을 남기도록 추천합니다.<br>"
            "<b>표시만 하기</b>: 파일은 그대로 두고 정리함에 올립니다. "
            "<b>격리 폴더로 이동</b>: 라이브러리 폴더 안 <code>.rumi_quarantine</code>로 옮깁니다. "
            "어느 쪽이든 정리함에서 복원하거나, 확인 후 영구 삭제할 수 있습니다.")
        help_.setWordWrap(True)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["파일 이름", "폴더", "크기", "수정일", "상태"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setTextElideMode(Qt.ElideMiddle)
        h = self.tree.header()
        h.setSectionResizeMode(0, QHeaderView.Interactive)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        for i in (2, 3, 4):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        h.resizeSection(0, 280)
        self.tree.itemDoubleClicked.connect(self._dupe_double_clicked)
        self.tree.itemChanged.connect(self._update_dupe_selection_label)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._dupe_menu)

        suggest = QPushButton("추천대로 선택")
        suggest.clicked.connect(self._apply_suggestion)
        none = QPushButton("모두 해제")
        none.clicked.connect(lambda: self._set_all_checks(False))
        self.dupe_sel_label = QLabel()
        mark = QPushButton("표시만 하기")
        mark.clicked.connect(lambda: self._apply_dupes("mark"))
        move = QPushButton("격리 폴더로 이동")
        move.clicked.connect(lambda: self._apply_dupes("quarantine"))

        top = QHBoxLayout()
        top.addWidget(find)
        top.addWidget(self.dupes_summary, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(suggest)
        bottom.addWidget(none)
        bottom.addWidget(self.dupe_sel_label, 1)
        bottom.addWidget(mark)
        bottom.addWidget(move)

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addLayout(top)
        lay.addWidget(help_)
        lay.addWidget(self.tree, 1)
        lay.addLayout(bottom)
        return w

    def find_dupes(self) -> None:
        def job(worker):
            return find_duplicates(
                self.db,
                progress=lambda stage, i, n: worker.progress.emit(f"{stage} 중… ({i}/{n})", i, n),
                cancel=worker.is_cancelled)

        self._worker = run_with_progress(self, "중복 찾는 중", job, self._show_groups, self._error)

    def _show_groups(self, groups: list[DuplicateGroup]) -> None:
        self._groups = groups
        self.tree.blockSignals(True)
        self.tree.clear()
        for gi, g in enumerate(groups):
            top = QTreeWidgetItem([f"{len(g.videos)}개 동일 · 각 {fmt_size(g.size)} · "
                                   f"정리하면 {fmt_size(g.wasted_bytes)} 확보"])
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            top.setFlags(top.flags() & ~Qt.ItemIsUserCheckable)
            keep = suggest_keep(g)
            for v in g.videos:
                child = QTreeWidgetItem([v.name, os.path.dirname(v.path), fmt_size(v.size),
                                         fmt_date(v.mtime), STATUS_LABELS[v.status]])
                child.setData(0, ID_ROLE, v.id)
                child.setData(0, SORT_ROLE, gi)
                child.setToolTip(0, v.path)
                child.setToolTip(1, v.path)
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Unchecked if v.id == keep.id else Qt.Checked)
                if v.status in STATUS_COLORS:
                    child.setForeground(4, QBrush(STATUS_COLORS[v.status]))
                top.addChild(child)
            self.tree.addTopLevelItem(top)
            top.setFirstColumnSpanned(True)
            top.setExpanded(True)
        self.tree.blockSignals(False)
        wasted = sum(g.wasted_bytes for g in groups)
        self.dupes_summary.setText(
            f"중복 그룹 {len(groups)}개 · 정리 시 최대 {fmt_size(wasted)} 확보" if groups else "중복된 동영상이 없습니다.")
        self._update_dupe_selection_label()

    def _children(self):
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            yield top, [top.child(j) for j in range(top.childCount())]

    def _set_all_checks(self, on: bool) -> None:
        self.tree.blockSignals(True)
        for _, kids in self._children():
            for k in kids:
                k.setCheckState(0, Qt.Checked if on else Qt.Unchecked)
        self.tree.blockSignals(False)
        self._update_dupe_selection_label()

    def _apply_suggestion(self) -> None:
        self._show_groups(self._groups)

    def _checked(self) -> tuple[list[int], int, list[str]]:
        """(체크된 id, 총 크기, 모든 사본이 체크된 그룹의 대표 파일명)"""
        ids, size, all_checked = [], 0, []
        for top, kids in self._children():
            checked = [k for k in kids if k.checkState(0) == Qt.Checked]
            if kids and len(checked) == len(kids):
                all_checked.append(kids[0].text(0))
            for k in checked:
                ids.append(k.data(0, ID_ROLE))
                v = self.db.get(k.data(0, ID_ROLE))
                size += v.size if v else 0
        return ids, size, all_checked

    def _update_dupe_selection_label(self, *_):
        ids, size, _ = self._checked()
        self.dupe_sel_label.setText(f"정리 대상 {len(ids)}개 · {fmt_size(size)}" if ids else "")

    def _apply_dupes(self, action: str) -> None:
        ids, size, all_checked = self._checked()
        if not ids:
            QMessageBox.information(self, "중복 정리", "정리할 파일을 체크해 주세요.")
            return
        if all_checked:
            QMessageBox.warning(
                self, "중복 정리",
                "다음 그룹은 모든 사본이 체크되어 있습니다. 그룹마다 최소 한 개는 남겨야 합니다.\n\n"
                + "\n".join(f"• {n}" for n in all_checked[:10]))
            return
        if action == "quarantine":
            msg = (f"{len(ids)}개 파일({fmt_size(size)})을 격리 폴더(.rumi_quarantine)로 옮깁니다.\n"
                   "정리함에서 언제든 원래 위치로 복원할 수 있습니다.")
            fn, label = self.org.quarantine, "격리"
        else:
            msg = f"{len(ids)}개 파일({fmt_size(size)})을 중복으로 표시합니다. 파일은 움직이지 않습니다."
            fn, label = self.org.mark, "표시"
        if QMessageBox.question(self, "중복 정리", msg) != QMessageBox.Yes:
            return
        # 이미 표시된 항목을 격리할 때 생기는 '이미 정리 대상' 오류는 표시 단계에서만 의미 있다
        if action == "mark":
            ids = [i for i in ids if (v := self.db.get(i)) and v.status == VideoStatus.ACTIVE]
        self._do(fn, ids, label)
        self.find_dupes()

    def _dupe_double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        vid = item.data(0, ID_ROLE)
        if vid is not None:
            self.play_files.emit(self._paths_of([vid]))

    def _dupe_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if not item or item.data(0, ID_ROLE) is None:
            return
        path = item.toolTip(0)
        menu = QMenu(self)
        menu.addAction("재생 (미리 보기)", lambda: self.play_files.emit([path]))
        menu.addAction("파일 위치 열기", lambda: reveal_in_file_manager(path))
        menu.addAction("이 파일만 남기기", lambda: self._keep_only(item))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _keep_only(self, item: QTreeWidgetItem) -> None:
        top = item.parent()
        self.tree.blockSignals(True)
        for j in range(top.childCount()):
            c = top.child(j)
            c.setCheckState(0, Qt.Unchecked if c is item else Qt.Checked)
        self.tree.blockSignals(False)
        self._update_dupe_selection_label()

    # ================= 정리함 탭 =================
    def _build_trash_tab(self) -> QWidget:
        self.trash_summary = QLabel()
        self.trash = _table(["파일 이름", "상태", "원래 폴더", "현재 위치", "크기", "정리한 날짜"])
        th = self.trash.horizontalHeader()
        th.setSectionResizeMode(0, QHeaderView.Interactive)
        th.setSectionResizeMode(2, QHeaderView.Stretch)
        th.setSectionResizeMode(3, QHeaderView.Stretch)
        th.resizeSection(0, 220)
        self.trash.itemDoubleClicked.connect(lambda it: self._play_rows(self.trash))
        self.trash.setContextMenuPolicy(Qt.CustomContextMenu)
        self.trash.customContextMenuRequested.connect(lambda p: self._row_menu(self.trash, p))

        restore = QPushButton("복원 / 표시 해제")
        restore.clicked.connect(lambda: self._do(self.org.restore, _selected_ids(self.trash), "복원"))
        move = QPushButton("격리 폴더로 이동")
        move.clicked.connect(lambda: self._do(self.org.quarantine, _selected_ids(self.trash), "격리"))
        purge = QPushButton("영구 삭제…")
        purge.setStyleSheet("color: #c0392b; font-weight: bold;")
        purge.clicked.connect(self.purge_selected)
        select_all = QPushButton("모두 선택")
        select_all.clicked.connect(self.trash.selectAll)

        help_ = QLabel("영구 삭제 전에 남겨 둘 사본이 실제로 존재하고 내용이 같은지 다시 확인합니다. "
                       "마지막 남은 사본은 삭제되지 않습니다.")
        help_.setWordWrap(True)

        btns = QHBoxLayout()
        btns.addWidget(select_all)
        btns.addStretch(1)
        btns.addWidget(restore)
        btns.addWidget(move)
        btns.addWidget(purge)

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self.trash_summary)
        lay.addWidget(help_)
        lay.addWidget(self.trash, 1)
        lay.addLayout(btns)
        return w

    def _fill_trash(self) -> None:
        vids = self.db.videos([VideoStatus.MARKED, VideoStatus.QUARANTINED])
        t = self.trash
        t.setSortingEnabled(False)
        t.setRowCount(0)
        t.setRowCount(len(vids))
        for r, v in enumerate(vids):
            name = _cell(v.name, v.name.lower(), v.id)
            name.setToolTip(v.path)
            t.setItem(r, 0, name)
            st = _cell(STATUS_LABELS[v.status])
            st.setForeground(QBrush(STATUS_COLORS[v.status]))
            t.setItem(r, 1, st)
            t.setItem(r, 2, _cell(os.path.dirname(v.original_path or v.path)))
            t.setItem(r, 3, _cell(v.path if v.status == VideoStatus.QUARANTINED else "(제자리)"))
            t.setItem(r, 4, _cell(fmt_size(v.size), v.size))
            t.setItem(r, 5, _cell(fmt_date(v.status_changed_at), v.status_changed_at or 0))
        t.setSortingEnabled(True)
        n_q = sum(v.status == VideoStatus.QUARANTINED for v in vids)
        self.trash_summary.setText(
            f"<b>정리함 {len(vids)}개 · {fmt_size(sum(v.size for v in vids))}</b> "
            f"(격리됨 {n_q}개, 표시만 됨 {len(vids) - n_q}개)")
        self.tabs.setTabText(2, f"정리함 ({len(vids)})" if vids else "정리함")

    def purge_selected(self) -> None:
        ids = _selected_ids(self.trash)
        if not ids:
            QMessageBox.information(self, "영구 삭제", "삭제할 항목을 선택해 주세요.")
            return

        def job(worker):
            return self.org.check_purge(
                ids, verify_content=True,
                progress=lambda i, n: worker.progress.emit(f"남길 사본과 내용 대조 중… ({i}/{n})", i, n),
                cancel=worker.is_cancelled)

        def confirm(checks: list[PurgeCheck]):
            dlg = PurgeConfirmDialog(checks, self)
            if dlg.exec() == QDialog.Accepted:
                res = self.org.purge(checks)
                _report_errors(self, "영구 삭제", res)
                self.refresh()
                if res.done:
                    QMessageBox.information(self, "영구 삭제", f"{len(res.done)}개 파일을 삭제했습니다.")

        self._worker = run_with_progress(self, "삭제 전 확인 중", job, confirm, self._error)

    # ================= 공통 =================
    def _do(self, fn, ids: list[int], label: str) -> None:
        if not ids:
            return
        res = fn(ids)
        _report_errors(self, label, res)
        self.refresh()

    def refresh(self) -> None:
        self.folder_list.clear()
        for fid, path in self.db.folders():
            it = QListWidgetItem(path)
            it.setData(ID_ROLE, fid)
            it.setToolTip(path)
            self.folder_list.addItem(it)
        self._fill_videos()
        self._fill_trash()

    def add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "라이브러리에 추가할 폴더")
        if path:
            fid = self.db.add_folder(path)
            self.refresh()
            self.scan([fid])

    def remove_folder(self) -> None:
        it = self.folder_list.currentItem()
        if not it:
            return
        if QMessageBox.question(self, "폴더 제외",
                                f"라이브러리에서 제외할까요? (파일은 삭제되지 않습니다)\n\n{it.text()}") == QMessageBox.Yes:
            self.db.remove_folder(it.data(ID_ROLE))
            self.refresh()

    def scan(self, folder_ids: list[int] | None = None) -> None:
        ids = folder_ids or [fid for fid, _ in self.db.folders()]
        if not ids:
            QMessageBox.information(self, "스캔", "먼저 ‘폴더 추가…’로 동영상 폴더를 등록해 주세요.")
            return

        def job(worker):
            total = 0
            for fid in ids:
                r = scan_folder(
                    self.db, fid,
                    progress=lambda n, p: worker.progress.emit(f"스캔 중… {n}개\n{os.path.basename(p)}", 0, 0),
                    cancel=worker.is_cancelled)
                total += r.found
                if r.cancelled:
                    break
            return total

        self._worker = run_with_progress(self, "폴더 스캔", job, lambda _n: self.refresh(), self._error)

    def _error(self, msg: str) -> None:
        QMessageBox.critical(self, "오류", msg)
