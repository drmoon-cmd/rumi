"""동영상 라이브러리 창: 폴더 관리, 중복 정리, 정리함(표시/격리 항목 관리)."""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSplitter, QTableView, QToolButton, QTableWidget, QTableWidgetItem, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..library import (
    LibraryDB, Organizer, VideoStatus, build_catalog, find_duplicates, scan_folder, suggest_keep,
)
from ..library import volumes as vol
from ..library.duplicates import DuplicateGroup
from ..library.organizer import OpResult, PurgeCheck
from .util import fmt_date, fmt_size, fmt_time, reveal_in_file_manager
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
OFFLINE_COLOR = QColor("#8a8f98")
OFFLINE_LABEL = "연결 안 됨"
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


def _resolution(v) -> str:
    return f"{v.width}×{v.height}" if v.width and v.height else ""


def _left(seconds: float) -> str:
    if seconds < 90:
        return "1분 안쪽"
    if seconds < 5400:
        return f"약 {seconds / 60:.0f}분"
    return f"약 {seconds / 3600:.1f}시간"


class ComparePane(QScrollArea):
    """고른 중복 그룹의 영상들을 장면 그림으로 나란히 보여 준다 (스캔 때 저장해 둔 그림이라
    뽑아 둔 드라이브의 영상도 보인다). 지우기 전에 정말 같은 영상인지 눈으로 확인하는 용도."""

    THUMB_H = 84

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumHeight(200)
        self.show_message("위 목록에서 그룹이나 파일을 고르면 장면을 나란히 비교합니다.")

    def show_message(self, text: str) -> None:
        label = QLabel(text)
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        label.setEnabled(False)
        self.setWidget(label)

    def show_group(self, rows: list[dict]) -> None:
        """rows: {title, detail, tag, tag_color, thumbs(JPEG 목록), dim}"""
        box = QWidget()
        grid = QGridLayout(box)
        grid.setContentsMargins(8, 6, 8, 6)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        for r, row in enumerate(rows):
            tag = f" <span style='color:{row['tag_color']}'><b>[{row['tag']}]</b></span>" if row.get("tag") else ""
            text = QLabel(f"<b>{row['title']}</b>{tag}<br><span style='color:#8a8f98'>{row['detail']}</span>")
            text.setTextFormat(Qt.RichText)
            text.setWordWrap(True)
            text.setMinimumWidth(240)
            text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(text, r, 0)
            thumbs = row.get("thumbs") or []
            if not any(thumbs):
                none = QLabel("비교용 그림 없음 — 이 드라이브를 연결하고 ‘다시 스캔’하면 만들어집니다")
                none.setEnabled(False)
                grid.addWidget(none, r, 1, 1, 3)
                continue
            for c, data in enumerate(thumbs[:3]):
                pic = QLabel()
                pm = QPixmap()
                if data and pm.loadFromData(data):
                    pic.setPixmap(pm.scaledToHeight(self.THUMB_H, Qt.SmoothTransformation))
                else:
                    pic.setText("—")
                    pic.setAlignment(Qt.AlignCenter)
                    pic.setFixedSize(int(self.THUMB_H * 16 / 9), self.THUMB_H)
                grid.addWidget(pic, r, c + 1)
        grid.setColumnStretch(0, 1)
        grid.setRowStretch(len(rows), 1)
        self.setWidget(box)


class DriveDialog(QDialog):
    """지금 연결된 드라이브 중에서 통째로 등록할 것을 고른다 (여러 개 한 번에)."""

    def __init__(self, db: LibraryDB, parent=None):
        super().__init__(parent)
        self.setWindowTitle("드라이브 추가")
        self.resize(520, 380)
        known = {v.serial for v in db.volumes().values()}
        roots = {os.path.normcase(p.rstrip("\\/")) for _id, p in db.folders()}
        self.list = QListWidget()
        for m in vol.current_mounts():
            whole = os.path.normcase(m.root.rstrip("\\/")) in roots
            name = f"{m.root}   {m.label}" if m.label else m.root
            note = "  — 이미 통째로 등록됨" if whole else ("  — 일부 폴더가 등록됨" if m.serial in known else "")
            it = QListWidgetItem(name + note)
            it.setData(ID_ROLE, m.root)
            it.setFlags((it.flags() | Qt.ItemIsUserCheckable) if not whole else Qt.NoItemFlags)
            it.setCheckState(Qt.Unchecked)
            self.list.addItem(it)
        info = QLabel("체크한 드라이브 전체에서 동영상을 찾아 목록과 지문을 기억합니다. "
                      "스캔이 끝난 드라이브는 뽑아도 되고, 나중에 다른 드라이브와 비교할 수 있습니다.")
        info.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("추가하고 스캔")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addWidget(self.list, 1)
        lay.addWidget(buttons)

    def roots(self) -> list[str]:
        items = [self.list.item(i) for i in range(self.list.count())]
        return [it.data(ID_ROLE) for it in items if it.checkState() == Qt.Checked]


def _selected_ids(table) -> list[int]:
    rows = sorted({i.row() for i in table.selectedIndexes()})
    if isinstance(table, QTableView) and not isinstance(table, QTableWidget):
        return [table.model().video_id(r) for r in rows]
    return [table.item(r, 0).data(ID_ROLE) for r in rows]


class VideoModel(QAbstractTableModel):
    """동영상 목록. 파일이 수만~수십만 개여도 창이 바로 뜨도록, 보이는 줄만 그때그때 만들어 준다
    (칸마다 위젯 항목을 미리 만들어 두는 방식은 3만 개에 4초가 걸렸다). 찾기와 정렬도 여기서 한다."""

    HEADERS = ["이름", "드라이브", "폴더", "크기", "길이", "해상도", "수정일"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple] = []     # (video, 드라이브 이름, 연결됨, 찾기용 소문자 글)
        self._shown: list[int] = []      # 찾기·정렬을 거쳐 화면에 보이는 줄 → _rows 번호
        self._words: list[str] = []
        self._sort = (0, Qt.AscendingOrder)

    def set_videos(self, rows: list[tuple]) -> None:
        self.beginResetModel()
        self._rows = rows
        self._rebuild()
        self.endResetModel()

    def set_filter(self, text: str) -> None:
        words = text.strip().lower().split()
        if words != self._words:
            self.beginResetModel()
            self._words = words
            self._rebuild()
            self.endResetModel()

    def sort(self, column: int, order=Qt.AscendingOrder) -> None:
        self.beginResetModel()
        self._sort = (column, order)
        self._rebuild()
        self.endResetModel()

    def _sort_key(self, column: int):
        rows = self._rows
        keys = {0: lambda i: rows[i][0].name.lower(), 1: lambda i: rows[i][1].lower(),
                2: lambda i: rows[i][0].folder.lower(), 3: lambda i: rows[i][0].size,
                4: lambda i: rows[i][0].duration or 0,
                5: lambda i: (rows[i][0].width or 0) * (rows[i][0].height or 0), 6: lambda i: rows[i][0].mtime}
        return keys[column]

    def _rebuild(self) -> None:
        words, rows = self._words, self._rows
        shown = [i for i, r in enumerate(rows) if all(w in r[3] for w in words)] if words else list(range(len(rows)))
        column, order = self._sort
        shown.sort(key=self._sort_key(column), reverse=order == Qt.DescendingOrder)
        self._shown = shown

    def video_id(self, row: int) -> int:
        return self._rows[self._shown[row]][0].id

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._shown)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        v, drive, online, _hay = self._rows[self._shown[index.row()]]
        c = index.column()
        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            text = (v.name, drive, v.folder, fmt_size(v.size), fmt_time(v.duration) if v.duration else "",
                    _resolution(v), fmt_date(v.mtime))[c]
            if role == Qt.ToolTipRole:
                return text if online else f"{text}  ({OFFLINE_LABEL})"
            return text
        if role == Qt.ForegroundRole and not online:
            return QBrush(OFFLINE_COLOR)
        return None


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
        ok = [c for c in checks if c.ok and not c.offline_keeper]
        away = [c for c in checks if c.ok and c.offline_keeper]
        bad = [c for c in checks if not c.ok]
        total = sum(c.video.size for c in ok)

        info = QLabel(
            f"<b>삭제 가능 {len(ok)}개 ({fmt_size(total)})</b>"
            + (f" · <span style='color:#c78a00'>확인 못 함 {len(away)}개</span>" if away else "")
            + (f" · <span style='color:#c0392b'>삭제 불가 {len(bad)}개 (그대로 유지)</span>" if bad else "")
            + "<br>남겨 둘 사본이 실제로 있는지 방금 다시 확인했습니다. 내용이 같은 사본은 파일 전체를 대조했고, "
              "화질·형식이 다른 사본은 남기기로 한 파일이 그대로 있는지 확인했습니다. "
              "삭제한 파일은 휴지통을 거치지 않으며 되돌릴 수 없습니다.")
        info.setWordWrap(True)

        table = _table(["삭제할 파일", "결과", "남겨 둘 사본 / 사유"])
        table.setSortingEnabled(False)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        for c in ok + away + bad:
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, _cell(c.video.path))
            table.item(r, 0).setText(c.video.name)
            res = _cell("확인 못 함" if c.offline_keeper else "삭제" if c.ok else "건너뜀")
            res.setForeground(QBrush(QColor("#c78a00" if c.offline_keeper else "#c0392b" if c.ok else "#7f8c8d")))
            table.setItem(r, 1, res)
            kept = c.kept_copy or ""
            if c.offline_keeper:
                kept = "연결 안 된 " + kept
            if c.ok and c.kind == "similar":
                kept = "(화질·형식이 다른 사본) " + kept
            table.setItem(r, 2, _cell(kept if c.ok else c.reason))

        self.confirm = QCheckBox("위 파일들을 영구 삭제하는 것을 확인했습니다")
        self.trust_away = QCheckBox(
            f"남길 사본이 연결되지 않은 드라이브에 있는 {len(away)}개도 삭제합니다 "
            "(그 사본이 지금도 있는지는 확인할 수 없습니다)")
        self.trust_away.setVisible(bool(away))
        buttons = QDialogButtonBox()
        self.delete_btn = buttons.addButton("", QDialogButtonBox.DestructiveRole)
        buttons.addButton("취소", QDialogButtonBox.RejectRole)

        def update():
            n = len(ok) + (len(away) if self.trust_away.isChecked() else 0)
            self.delete_btn.setText(f"{n}개 영구 삭제")
            self.delete_btn.setEnabled(self.confirm.isChecked() and n > 0)

        self.confirm.toggled.connect(lambda _on: update())
        self.trust_away.toggled.connect(lambda _on: update())
        update()
        self.delete_btn.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addWidget(table, 1)
        lay.addWidget(self.trust_away)
        lay.addWidget(self.confirm)
        lay.addWidget(buttons)

    def allow_offline_keeper(self) -> bool:
        return self.trust_away.isChecked()


class LibraryWindow(QWidget):
    play_files = Signal(list)
    enqueue_files = Signal(list)

    def __init__(self, db: LibraryDB, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("동영상 라이브러리")
        self.resize(1200, 720)
        self.db = db
        self.org = Organizer(db)
        self._groups: list[DuplicateGroup] = []
        self._volumes: dict = {}
        self._shown = False
        self._plan_rows: list = []
        self._worker = None

        # 폴더 목록
        self.folder_list = QListWidget()
        self.folder_list.setTextElideMode(Qt.ElideMiddle)
        self.folder_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.folder_list.customContextMenuRequested.connect(self._folder_menu)
        add_drive = QPushButton("드라이브 추가…")
        add_drive.setToolTip("연결된 드라이브를 통째로 등록합니다 (여러 개를 한 번에 고를 수 있음)")
        add_drive.clicked.connect(self.add_drives)
        add = QPushButton("폴더 추가…")
        add.clicked.connect(self.add_folder)
        rename = QPushButton("이름 붙이기")
        rename.setToolTip("드라이브에 알아보기 쉬운 이름을 붙입니다 (예: 백업 3번)")
        rename.clicked.connect(self.rename_drive)
        remove = QPushButton("제외")
        remove.setToolTip("라이브러리에서 폴더를 뺍니다 (파일은 그대로)")
        remove.clicked.connect(self.remove_folder)
        rescan = QPushButton("연결된 것 다시 스캔")
        rescan.clicked.connect(lambda: self.scan())
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        for b in (add_drive, add):
            row1.addWidget(b)
        for b in (rename, remove, rescan):
            row2.addWidget(b)
        self.drive_summary = QLabel()
        self.drive_summary.setWordWrap(True)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("<b>드라이브·폴더</b>"))
        ll.addWidget(self.folder_list, 1)
        ll.addWidget(self.drive_summary)
        ll.addLayout(row1)
        ll.addLayout(row2)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_videos_tab(), "동영상")
        self.tabs.addTab(self._build_dupes_tab(), "중복 정리")
        self.tabs.addTab(self._build_trash_tab(), "정리함")
        self.tabs.addTab(self._build_plan_tab(), "드라이브별 정리 계획")

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([330, 870])
        # 정리할 파일이 있는 드라이브가 연결되면 알려 주는 줄
        self.banner = QFrame()
        self.banner.setFrameShape(QFrame.StyledPanel)
        self.banner_label = QLabel()
        self.banner_label.setWordWrap(True)
        banner_btn = QPushButton("정리 계획 보기")
        banner_btn.clicked.connect(lambda: self.tabs.setCurrentIndex(3))
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(10, 6, 10, 6)
        bl.addWidget(self.banner_label, 1)
        bl.addWidget(banner_btn)
        self.banner.hide()

        lay = QVBoxLayout(self)
        lay.addWidget(self.banner)
        lay.addWidget(split, 1)

        # 드라이브를 꽂거나 뽑으면 목록을 새로 본다 (창이 보일 때만)
        self._mounts_seen = vol.mount_signature()
        self._mount_timer = QTimer(self, interval=3000)
        self._mount_timer.timeout.connect(self._check_mounts)
        self._mount_timer.start()

        self.refresh()

    # ================= 동영상 탭 =================
    def _build_videos_tab(self) -> QWidget:
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("이름·폴더로 찾기 (뽑아 둔 드라이브의 파일도 찾습니다)…")
        self.filter.textChanged.connect(self._apply_filter)
        self.videos_summary = QLabel()
        self.video_model = VideoModel(self)
        self.videos = QTableView()
        self.videos.setModel(self.video_model)
        self.videos.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.videos.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.videos.verticalHeader().setVisible(False)
        self.videos.verticalHeader().setDefaultSectionSize(26)
        self.videos.setAlternatingRowColors(True)
        self.videos.setTextElideMode(Qt.ElideMiddle)
        self.videos.setWordWrap(False)
        self.videos.setShowGrid(False)
        self.videos.setSortingEnabled(True)
        self.videos.sortByColumn(0, Qt.AscendingOrder)
        vh = self.videos.horizontalHeader()
        vh.setSectionResizeMode(2, QHeaderView.Stretch)   # 칸 너비를 내용에 맞추지 않는다 (모든 줄을 재야 해서 느리다)
        for col, width in ((0, 260), (1, 150), (3, 76), (4, 64), (5, 84), (6, 122)):
            vh.resizeSection(col, width)
        self.videos.doubleClicked.connect(lambda _i: self._play_rows(self.videos))
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
        rows, offline = [], 0
        for v in vids:
            on = self._online(v)
            offline += not on
            drive = self._drive_name(v)
            rows.append((v, drive, on, f"{v.name} {drive} {v.folder}".lower()))
        self.video_model.set_videos(rows)
        self.videos_summary.setText(f"{len(vids)}개 · {fmt_size(sum(v.size for v in vids))}"
                                    + (f" (연결 안 된 드라이브 {offline}개 포함)" if offline else ""))
        self._apply_filter(self.filter.text())

    def _apply_filter(self, text: str) -> None:
        self.video_model.set_filter(text)

    def _online(self, v) -> bool:
        volume = self._volumes.get(v.volume_id) if v.volume_id is not None else None
        return not vol.is_offline_path(v.path) and (v.volume_id is None or (volume is not None and volume.online))

    def _drive_name(self, v) -> str:
        volume = self._volumes.get(v.volume_id) if v.volume_id is not None else None
        return volume.name if volume else ""

    def _paths_of(self, ids: list[int], tell: bool = True) -> list[str]:
        """재생할 수 있는(연결된) 파일의 경로. 뽑아 둔 드라이브의 파일을 골랐으면 어느 드라이브인지 알려 준다."""
        vids = [v for v in (self.db.get(i) for i in ids) if v]
        away = [v for v in vids if not self._online(v)]
        if away and tell:
            names = sorted({self._drive_name(v) or "?" for v in away})
            QMessageBox.information(
                self, "드라이브 연결 필요",
                f"고른 파일 {len(away)}개는 지금 연결되지 않은 드라이브에 있습니다.\n\n"
                + "\n".join(f"• {n}" for n in names[:10]) + "\n\n드라이브를 연결하면 자동으로 알아봅니다.")
        return [v.path for v in vids if v not in away]

    def _play_rows(self, table) -> None:
        paths = self._paths_of(_selected_ids(table))
        if paths:
            self.play_files.emit(paths)

    def _row_menu(self, table, pos) -> None:
        ids = _selected_ids(table)
        if not ids:
            return
        paths = self._paths_of(ids, tell=False)
        menu = QMenu(self)
        for a in (menu.addAction("재생", lambda: self.play_files.emit(paths)),
                  menu.addAction("재생목록에 추가", lambda: self.enqueue_files.emit(paths)),
                  menu.addAction("파일 위치 열기", lambda: reveal_in_file_manager(paths[0]))):
            a.setEnabled(bool(paths))
        if not paths:
            menu.addAction(f"({OFFLINE_LABEL}: 드라이브를 연결하면 재생할 수 있습니다)").setEnabled(False)
        if table is self.videos:
            menu.addSeparator()
            menu.addAction("중복으로 표시", lambda: self._do(self.org.mark, ids, "표시"))
        menu.exec(table.viewport().mapToGlobal(pos))

    # ================= 중복 정리 탭 =================
    def _build_dupes_tab(self) -> QWidget:
        find = QPushButton("중복 찾기")
        find.setDefault(True)
        find.clicked.connect(self.find_dupes)
        self.similar_check = QCheckBox("이름·화질·형식이 달라도 같은 영상 찾기")
        self.similar_check.setChecked(True)
        self.similar_check.setToolTip("스캔할 때 만들어 둔 장면 지문으로 비교합니다. 끄면 내용이 완전히 같은 파일만 찾습니다.")
        self.dupes_summary = QLabel("‘중복 찾기’를 누르면 등록한 모든 드라이브(뽑아 둔 것 포함)에서 같은 동영상을 찾습니다.")
        self.dupes_summary.setWordWrap(True)
        help_ = QLabel(
            "체크한 파일이 정리 대상입니다. 체크하지 않은 파일은 그대로 남습니다. "
            "내용이 같은 파일은 가장 오래된 것을, 화질만 다른 영상은 해상도가 높은 것을 남기도록 추천합니다. "
            "뽑아 둔 드라이브의 파일은 ‘표시만 하기’까지 되고, 옮기거나 지우는 것은 연결했을 때 됩니다.<br>"
            "<b>표시만 하기</b>: 파일은 그대로 두고 정리함에 올립니다. "
            "<b>격리 폴더로 이동</b>: 라이브러리 폴더 안 <code>.rumi_quarantine</code>로 옮깁니다. "
            "어느 쪽이든 정리함에서 복원하거나, 확인 후 영구 삭제할 수 있습니다.")
        help_.setWordWrap(True)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["파일 이름", "드라이브", "폴더", "크기", "해상도", "길이", "수정일", "상태"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setTextElideMode(Qt.ElideMiddle)
        h = self.tree.header()
        h.setSectionResizeMode(0, QHeaderView.Interactive)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        for i in (1, 3, 4, 5, 6, 7):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        h.resizeSection(0, 300)
        self.tree.itemDoubleClicked.connect(self._dupe_double_clicked)
        self.tree.itemChanged.connect(self._update_dupe_selection_label)
        self.tree.itemChanged.connect(lambda *_: self._show_compare())
        self.tree.currentItemChanged.connect(lambda *_: self._show_compare())
        self.compare = ComparePane()
        self.dupe_split = QSplitter(Qt.Vertical)
        self.dupe_split.addWidget(self.tree)
        self.dupe_split.addWidget(self.compare)
        self.dupe_split.setStretchFactor(0, 1)
        self.dupe_split.setStretchFactor(1, 1)
        self.dupe_split.setSizes([300, 300])
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._dupe_menu)

        suggest = QPushButton("추천대로 선택")
        suggest.clicked.connect(self._apply_suggestion)
        by_drive = QToolButton()
        by_drive.setText("드라이브 기준 선택 ▾")
        by_drive.setToolTip("어느 드라이브의 사본을 지우고 어느 드라이브의 사본을 남길지 한 번에 고릅니다")
        by_drive.setPopupMode(QToolButton.InstantPopup)
        drive_menu = QMenu(by_drive)
        drive_menu.aboutToShow.connect(lambda: self._build_drive_menu(drive_menu))
        by_drive.setMenu(drive_menu)
        none = QPushButton("모두 해제")
        none.clicked.connect(lambda: self._set_all_checks(False))
        self.dupe_sel_label = QLabel()
        mark = QPushButton("표시만 하기")
        mark.clicked.connect(lambda: self._apply_dupes("mark"))
        move = QPushButton("격리 폴더로 이동")
        move.clicked.connect(lambda: self._apply_dupes("quarantine"))

        top = QHBoxLayout()
        top.addWidget(find)
        top.addWidget(self.similar_check)
        top.addWidget(self.dupes_summary, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(suggest)
        bottom.addWidget(by_drive)
        bottom.addWidget(none)
        bottom.addWidget(self.dupe_sel_label, 1)
        bottom.addWidget(mark)
        bottom.addWidget(move)

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addLayout(top)
        lay.addWidget(help_)
        lay.addWidget(self.dupe_split, 1)
        lay.addLayout(bottom)
        return w

    def find_dupes(self) -> None:
        similar = self.similar_check.isChecked()

        def job(worker):
            return find_duplicates(
                self.db,
                progress=lambda stage, i, n: worker.progress.emit(f"{stage} 중… ({i}/{n})", i, n),
                cancel=worker.is_cancelled, similar=similar)

        self._worker = run_with_progress(self, "중복 찾는 중", job, self._show_groups, self._error)

    def _show_groups(self, groups: list[DuplicateGroup]) -> None:
        self._groups = groups
        self._volumes = self.db.volumes()
        self.tree.blockSignals(True)
        self.tree.clear()
        for gi, g in enumerate(groups):
            if g.kind == "similar":
                title = f"같은 영상 {len(g.videos)}개 (화질·형식·이름이 다름)"
            elif g.verified:
                title = f"{len(g.videos)}개 동일 · 각 {fmt_size(g.size)}"
            else:
                title = f"{len(g.videos)}개 동일 · 각 {fmt_size(g.size)} (뽑아 둔 드라이브는 기록으로 비교)"
            top = QTreeWidgetItem([f"{title} · 정리하면 {fmt_size(g.wasted_bytes)} 확보"])
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            top.setFlags(top.flags() & ~Qt.ItemIsUserCheckable)
            keep = suggest_keep(g)
            for v in g.videos:
                on = self._online(v)
                state = STATUS_LABELS[v.status] if on else (
                    OFFLINE_LABEL if v.status == VideoStatus.ACTIVE else f"{STATUS_LABELS[v.status]} · {OFFLINE_LABEL}")
                child = QTreeWidgetItem([v.name, self._drive_name(v), v.folder, fmt_size(v.size), _resolution(v),
                                         fmt_time(v.duration) if v.duration else "", fmt_date(v.mtime), state])
                child.setData(0, ID_ROLE, v.id)
                child.setData(0, SORT_ROLE, gi)
                tip = v.path if on else f"{self._drive_name(v)} ({OFFLINE_LABEL}) · {v.rel_path}"
                child.setToolTip(0, tip)
                child.setToolTip(2, tip)
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Unchecked if v.id == keep.id else Qt.Checked)
                if not on:
                    for c in range(1, 8):
                        child.setForeground(c, QBrush(OFFLINE_COLOR))
                elif v.status in STATUS_COLORS:
                    child.setForeground(7, QBrush(STATUS_COLORS[v.status]))
                top.addChild(child)
            self.tree.addTopLevelItem(top)
            top.setFirstColumnSpanned(True)
            top.setExpanded(True)
        self.tree.blockSignals(False)
        wasted = sum(g.wasted_bytes for g in groups)
        n_similar = sum(g.kind == "similar" for g in groups)
        todo = len(self.db.videos_missing_info())
        note = f" · 지문이나 비교용 그림이 없는 파일 {todo}개 (드라이브를 연결하고 ‘다시 스캔’)" if todo else ""
        self.dupes_summary.setText(
            (f"중복 그룹 {len(groups)}개 (내용 동일 {len(groups) - n_similar} · 화질만 다름 {n_similar}) · "
             f"정리 시 최대 {fmt_size(wasted)} 확보" if groups else "중복된 동영상이 없습니다.") + note)
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
        sizes = {v.id: v.size for g in self._groups for v in g.videos}
        for top, kids in self._children():
            checked = [k for k in kids if k.checkState(0) == Qt.Checked]
            if kids and len(checked) == len(kids):
                all_checked.append(kids[0].text(0))
            for k in checked:
                ids.append(k.data(0, ID_ROLE))
                size += sizes.get(k.data(0, ID_ROLE), 0)
        return ids, size, all_checked

    def _keep_map(self) -> dict[int, int]:
        """정리 대상 → 같은 그룹에서 남기는 파일 (영구 삭제 전에 그 파일을 확인하는 데 쓴다)."""
        keep = {}
        for gi, (_top, kids) in enumerate(self._children()):
            left = [k.data(0, ID_ROLE) for k in kids if k.checkState(0) != Qt.Checked]
            if not left or gi >= len(self._groups):
                continue
            best = suggest_keep(self._groups[gi]).id
            keeper = best if best in left else left[0]
            for k in kids:
                if k.checkState(0) == Qt.Checked:
                    keep[k.data(0, ID_ROLE)] = keeper
        return keep

    # ---- 장면 나란히 보기
    def _show_compare(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return self.compare.show_message("위 목록에서 그룹이나 파일을 고르면 장면을 나란히 비교합니다.")
        top = item.parent() or item
        gi = self.tree.indexOfTopLevelItem(top)
        if not (0 <= gi < len(self._groups)):
            return
        checked = {top.child(j).data(0, ID_ROLE): top.child(j).checkState(0) == Qt.Checked
                   for j in range(top.childCount())}
        rows = []
        for v in self._groups[gi].videos:
            on = self._online(v)
            rate = f" · {v.size * 8 / v.duration / 1e6:.1f} Mbps" if v.duration else ""
            detail = " · ".join(x for x in (
                self._drive_name(v) + ("" if on else f" ({OFFLINE_LABEL})"), v.folder, _resolution(v),
                fmt_size(v.size) + rate, fmt_time(v.duration) if v.duration else "") if x)
            rows.append({"title": v.name, "detail": detail, "thumbs": self.db.thumbs(v.id),
                         "tag": "정리 대상" if checked.get(v.id) else "남김",
                         "tag_color": "#c0392b" if checked.get(v.id) else "#2e9e5b"})
        self.compare.show_group(rows)

    # ---- 드라이브 기준으로 고르기
    def _build_drive_menu(self, menu: QMenu) -> None:
        menu.clear()
        ids = sorted({v.volume_id for g in self._groups for v in g.videos if v.volume_id is not None},
                     key=lambda i: self._volumes[i].name.lower() if i in self._volumes else "")
        if not ids:
            menu.addAction("(먼저 ‘중복 찾기’를 눌러 주세요)").setEnabled(False)
            return
        clear = menu.addMenu("이 드라이브의 사본을 정리 대상으로 (다른 곳에 사본이 있을 때만)")
        keep = menu.addMenu("이 드라이브의 사본을 남기고 나머지를 정리 대상으로")
        for i in ids:
            volume = self._volumes.get(i)
            name = (volume.name if volume else "?") + ("" if volume and volume.online else f"  ({OFFLINE_LABEL})")
            clear.addAction(name, lambda i=i: self._select_by_drive(i, keep=False))
            keep.addAction(name, lambda i=i: self._select_by_drive(i, keep=True))

    def _select_by_drive(self, volume_id: int, keep: bool) -> None:
        """그룹마다: 이 드라이브의 사본을 정리 대상으로(keep=False) 또는 이 드라이브의 사본만 남기기(keep=True).
        그 드라이브에 사본이 없는 그룹, 그 드라이브에만 사본이 있는 그룹은 건드리지 않는다."""
        self.tree.blockSignals(True)
        changed = 0
        for gi, (_top, kids) in enumerate(self._children()):
            if gi >= len(self._groups):
                continue
            on_drive = {v.id for v in self._groups[gi].videos if v.volume_id == volume_id}
            if not on_drive or len(on_drive) == len(kids):
                continue
            for k in kids:
                here = k.data(0, ID_ROLE) in on_drive
                k.setCheckState(0, Qt.Checked if here != keep else Qt.Unchecked)
            if keep and len(on_drive) > 1:
                # 그 드라이브 안에도 사본이 여럿이면 추천 기준으로 하나만 남긴다
                best = min((v for v in self._groups[gi].videos if v.id in on_drive),
                           key=lambda v: (v.status != VideoStatus.ACTIVE, -(v.width or 0) * (v.height or 0),
                                          -v.size, v.mtime, v.path)).id
                for k in kids:
                    if k.data(0, ID_ROLE) in on_drive:
                        k.setCheckState(0, Qt.Unchecked if k.data(0, ID_ROLE) == best else Qt.Checked)
            changed += 1
        self.tree.blockSignals(False)
        self._update_dupe_selection_label()
        self._show_compare()
        self.dupes_summary.setText(f"{changed}개 그룹의 선택을 바꿨습니다. 확인한 뒤 ‘표시만 하기’를 누르면 정리 계획에 올라갑니다.")

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
        keep = self._keep_map()
        self._do(lambda chosen: fn(chosen, keep), ids, label)
        self.find_dupes()

    def _dupe_double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        vid = item.data(0, ID_ROLE)
        if vid is not None and (paths := self._paths_of([vid])):
            self.play_files.emit(paths)

    def _dupe_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if not item or item.data(0, ID_ROLE) is None:
            return
        paths = self._paths_of([item.data(0, ID_ROLE)], tell=False)
        menu = QMenu(self)
        for a in (menu.addAction("재생 (미리 보기)", lambda: self.play_files.emit(paths)),
                  menu.addAction("파일 위치 열기", lambda: reveal_in_file_manager(paths[0]))):
            a.setEnabled(bool(paths))
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
        self.trash = _table(["파일 이름", "상태", "드라이브", "원래 폴더", "현재 위치", "크기", "정리한 날짜"])
        th = self.trash.horizontalHeader()
        th.setSectionResizeMode(0, QHeaderView.Interactive)
        th.setSectionResizeMode(3, QHeaderView.Stretch)
        th.setSectionResizeMode(4, QHeaderView.Stretch)
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

        help_ = QLabel("영구 삭제 전에 남겨 둘 사본이 실제로 있는지 다시 확인합니다. 마지막 남은 사본은 삭제되지 않습니다. "
                       "남길 사본이 뽑아 둔 드라이브에 있으면 따로 동의한 경우에만 지웁니다.")
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
            on = self._online(v)
            st = _cell(STATUS_LABELS[v.status] + ("" if on else f" · {OFFLINE_LABEL}"))
            st.setForeground(QBrush(STATUS_COLORS[v.status] if on else OFFLINE_COLOR))
            t.setItem(r, 1, st)
            t.setItem(r, 2, _cell(self._drive_name(v)))
            t.setItem(r, 3, _cell(os.path.dirname(v.original_path or v.path) if on else v.folder))
            t.setItem(r, 4, _cell((v.path if on else v.rel_path or "") if v.status == VideoStatus.QUARANTINED
                                  else "(제자리)"))
            t.setItem(r, 5, _cell(fmt_size(v.size), v.size))
            t.setItem(r, 6, _cell(fmt_date(v.status_changed_at), v.status_changed_at or 0))
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
        self._purge_ids(ids)

    def _purge_ids(self, ids: list[int]) -> None:

        def job(worker):
            return self.org.check_purge(
                ids, verify_content=True,
                progress=lambda i, n: worker.progress.emit(f"남길 사본과 내용 대조 중… ({i}/{n})", i, n),
                cancel=worker.is_cancelled)

        def confirm(checks: list[PurgeCheck]):
            dlg = PurgeConfirmDialog(checks, self)
            if dlg.exec() == QDialog.Accepted:
                res = self.org.purge(checks, allow_offline_keeper=dlg.allow_offline_keeper())
                _report_errors(self, "영구 삭제", res)
                self.refresh()
                if res.done:
                    QMessageBox.information(self, "영구 삭제", f"{len(res.done)}개 파일을 삭제했습니다.")

        self._worker = run_with_progress(self, "삭제 전 확인 중", job, confirm, self._error)

    # ================= 드라이브별 정리 계획 =================
    def _build_plan_tab(self) -> QWidget:
        help_ = QLabel(
            "중복 정리에서 ‘표시만 하기’로 올려 둔 파일을 드라이브별로 모아 보여 줍니다. 드라이브를 한 번에 연결하지 "
            "못해도, 미리 표시해 두었다가 그 드라이브를 꽂았을 때 한 번에 처리할 수 있습니다.<br>"
            "‘정리 실행’을 누르면 남길 사본을 다시 확인한 뒤 삭제 확인 창을 보여 줍니다. "
            "남길 사본이 뽑아 둔 드라이브에 있는 파일은 따로 동의해야만 지웁니다.")
        help_.setWordWrap(True)
        self.plan = _table(["드라이브", "상태", "정리 대기", "확보 용량", "마지막 연결"])
        self.plan.setSelectionMode(QAbstractItemView.SingleSelection)
        self.plan.itemSelectionChanged.connect(self._update_plan_buttons)
        self.plan.itemDoubleClicked.connect(lambda _it: self._plan_show_in_trash())
        self.plan_run = QPushButton("이 드라이브 정리 실행…")
        self.plan_run.setStyleSheet("font-weight: bold;")
        self.plan_run.clicked.connect(self._plan_run)
        self.plan_show = QPushButton("정리함에서 보기")
        self.plan_show.clicked.connect(self._plan_show_in_trash)
        self.plan_summary = QLabel()
        row = QHBoxLayout()
        row.addWidget(self.plan_summary, 1)
        row.addWidget(self.plan_show)
        row.addWidget(self.plan_run)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(help_)
        lay.addWidget(self.plan, 1)
        lay.addLayout(row)
        return w

    def _fill_plan(self) -> None:
        self._plan_rows = self.org.plan()
        t = self.plan
        t.setSortingEnabled(False)
        t.setRowCount(0)
        t.setRowCount(len(self._plan_rows))
        for r, row in enumerate(self._plan_rows):
            cells = [_cell(row.name), _cell("연결됨" if row.online else OFFLINE_LABEL),
                     _cell(f"{len(row.ids)}개", len(row.ids)), _cell(fmt_size(row.size), row.size),
                     _cell(fmt_date(row.last_seen), row.last_seen or 0)]
            cells[0].setData(ID_ROLE, r)
            for c, cell in enumerate(cells):
                if not row.online:
                    cell.setForeground(QBrush(OFFLINE_COLOR))
                t.setItem(r, c, cell)
        t.setSortingEnabled(True)
        ready = [row for row in self._plan_rows if row.online]
        total = sum(row.size for row in self._plan_rows)
        self.plan_summary.setText(
            f"정리 대기 {sum(len(r.ids) for r in self._plan_rows)}개 · {fmt_size(total)} · 드라이브 {len(self._plan_rows)}개"
            if self._plan_rows else "정리 대기 중인 파일이 없습니다. 중복 정리에서 ‘표시만 하기’를 누르면 여기에 모입니다.")
        self.tabs.setTabText(3, f"드라이브별 정리 계획 ({len(self._plan_rows)})" if self._plan_rows
                             else "드라이브별 정리 계획")
        if ready:
            self.banner_label.setText(
                "<b>지금 연결된 드라이브에 정리 대기 파일이 있습니다:</b> "
                + ", ".join(f"{r.name} {len(r.ids)}개 ({fmt_size(r.size)})" for r in ready[:4])
                + (" …" if len(ready) > 4 else ""))
        self.banner.setVisible(bool(ready))
        self._update_plan_buttons()

    def _plan_current(self):
        rows = sorted({i.row() for i in self.plan.selectedIndexes()})
        if not rows:
            return None
        index = self.plan.item(rows[0], 0).data(ID_ROLE)
        return self._plan_rows[index] if 0 <= index < len(self._plan_rows) else None

    def _update_plan_buttons(self) -> None:
        row = self._plan_current()
        self.plan_show.setEnabled(row is not None)
        self.plan_run.setEnabled(row is not None and row.online)
        self.plan_run.setToolTip("" if row is None or row.online else "이 드라이브를 연결하면 실행할 수 있습니다")

    def _plan_run(self) -> None:
        row = self._plan_current()
        if row is not None and row.online:
            self._purge_ids(list(row.ids))

    def _plan_show_in_trash(self) -> None:
        row = self._plan_current()
        if row is None:
            return
        wanted = set(row.ids)
        self.tabs.setCurrentIndex(2)
        self.trash.clearSelection()
        mode = self.trash.selectionMode()
        self.trash.setSelectionMode(QAbstractItemView.MultiSelection)
        first = None
        for r in range(self.trash.rowCount()):
            if self.trash.item(r, 0).data(ID_ROLE) in wanted:
                self.trash.selectRow(r)
                first = r if first is None else first
        self.trash.setSelectionMode(mode)
        if first is not None:
            self.trash.scrollToItem(self.trash.item(first, 0))

    def _check_mounts(self) -> None:
        if not self.isVisible() or (self._worker is not None and self._worker_running()):
            return
        now = vol.mount_signature()
        if now != self._mounts_seen:
            self._mounts_seen = now
            self.refresh()

    def _worker_running(self) -> bool:
        try:
            return self._worker.isRunning()
        except RuntimeError:   # 이미 정리된 작업
            return False

    # ================= 공통 =================
    def _do(self, fn, ids: list[int], label: str) -> None:
        if not ids:
            return
        res = fn(ids)
        _report_errors(self, label, res)
        self.refresh()

    def refresh(self) -> None:
        self.db.refresh_volumes()
        self._volumes = self.db.volumes()
        self.folder_list.clear()
        todo_online = 0
        for row in self.db.folder_rows():
            volume = self._volumes.get(row["volume_id"])
            on = volume.online if volume else os.path.isdir(row["path"])
            if volume:
                inside = row["rel_path"] or "(전체)"
                text = f"{'●' if on else '○'} {volume.name}  ›  {inside}"
            else:
                text = f"{'●' if on else '○'} {row['path']}"
            detail = f"동영상 {row['n']}개"
            if row["todo"]:
                detail += f" · 지문 없음 {row['todo']}개"
                todo_online += row["todo"] if on else 0
            if not on:
                detail += f" · {OFFLINE_LABEL}"
            it = QListWidgetItem(f"{text}\n     {detail}")
            it.setData(ID_ROLE, row["id"])
            it.setData(SORT_ROLE, row["volume_id"])
            it.setToolTip((row["path"] if on else f"{OFFLINE_LABEL} — 연결하면 자동으로 알아봅니다")
                          + (f"\n마지막 스캔: {fmt_date(row['scanned_at'])}" if row["scanned_at"] else "\n아직 스캔하지 않음")
                          + (f"\n드라이브 고유 번호: {volume.serial}" if volume else ""))
            if not on:
                it.setForeground(QBrush(OFFLINE_COLOR))
            self.folder_list.addItem(it)
        n_on = sum(v.online for v in self._volumes.values())
        self.drive_summary.setText(
            f"드라이브 {len(self._volumes)}개 기억 중 · 지금 연결 {n_on}개"
            + (f"<br><span style='color:#c78a00'>지문이 없는 파일 {todo_online}개 — ‘연결된 것 다시 스캔’을 눌러 주세요</span>"
               if todo_online else ""))
        self._fill_videos()
        self._fill_trash()
        self._fill_plan()

    def showEvent(self, e):
        super().showEvent(e)
        if self._shown:
            self.refresh()  # 창을 다시 열 때마다 드라이브 연결 상태를 새로 본다
        self._shown = True

    def add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "라이브러리에 추가할 폴더")
        if path:
            fid = self.db.add_folder(path)
            self.refresh()
            self.scan([fid])

    def add_drives(self) -> None:
        dlg = DriveDialog(self.db, self)
        if dlg.exec() == QDialog.Accepted and dlg.roots():
            ids = [self.db.add_folder(root) for root in dlg.roots()]
            self.refresh()
            self.scan(ids)

    def rename_drive(self) -> None:
        it = self.folder_list.currentItem()
        volume = self._volumes.get(it.data(SORT_ROLE)) if it else None
        if volume is None:
            QMessageBox.information(self, "이름 붙이기", "왼쪽 목록에서 드라이브(또는 그 안의 폴더)를 먼저 골라 주세요.")
            return
        name, ok = QInputDialog.getText(
            self, "드라이브 이름 붙이기",
            f"이 드라이브를 부를 이름 (비우면 원래 이름 ‘{volume.label or volume.last_mount}’):", text=volume.alias)
        if ok:
            self.db.rename_volume(volume.id, name)
            self.refresh()

    def _folder_menu(self, pos) -> None:
        it = self.folder_list.itemAt(pos)
        if not it:
            return
        self.folder_list.setCurrentItem(it)
        menu = QMenu(self)
        menu.addAction("이 폴더만 다시 스캔", lambda: self.scan([it.data(ID_ROLE)]))
        menu.addAction("드라이브 이름 붙이기…", self.rename_drive)
        menu.addAction("제외", self.remove_folder)
        menu.exec(self.folder_list.viewport().mapToGlobal(pos))

    def remove_folder(self) -> None:
        it = self.folder_list.currentItem()
        if not it:
            return
        if QMessageBox.question(self, "폴더 제외",
                                "라이브러리에서 제외할까요? 기억해 둔 목록과 지문도 지워집니다. "
                                f"(파일은 삭제되지 않습니다)\n\n{it.text()}") == QMessageBox.Yes:
            self.db.remove_folder(it.data(ID_ROLE))
            self.refresh()

    def scan(self, folder_ids: list[int] | None = None) -> None:
        self.db.refresh_volumes()
        ids = folder_ids or [fid for fid, _ in self.db.folders()]
        if not ids:
            QMessageBox.information(self, "스캔", "먼저 ‘드라이브 추가…’나 ‘폴더 추가…’로 동영상이 있는 곳을 등록해 주세요.")
            return

        def job(worker):
            for fid in ids:
                r = scan_folder(
                    self.db, fid,
                    progress=lambda n, p: worker.progress.emit(f"파일 찾는 중… {n}개\n{os.path.basename(p)}", 0, 0),
                    cancel=worker.is_cancelled)
                if r.cancelled:
                    return 0
            started = time.monotonic()

            def progress(i, n, path):
                spent = time.monotonic() - started
                left = f" · 남은 시간 {_left(spent / (i - 1) * (n - i + 1))}" if i > 5 else ""
                worker.progress.emit(
                    f"지문 만드는 중… ({i}/{n}){left}\n{os.path.basename(path)}\n"
                    "취소해도 한 데까지는 남고, 다음에 이어서 합니다.", i, n)

            return build_catalog(self.db, ids, progress=progress, cancel=worker.is_cancelled)

        self._worker = run_with_progress(self, "스캔", job, lambda _n: self.refresh(), self._error,
                                         on_cancel=self.refresh)

    def _error(self, msg: str) -> None:
        QMessageBox.critical(self, "오류", msg)
