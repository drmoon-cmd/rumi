"""동영상 합치기 창: 파일 순서를 확인·조정한 뒤 하나의 파일로 잇는다 (다시 인코딩하지 않음)."""

from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import QProcess, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QVBoxLayout,
)

from ..library.scanner import VIDEO_EXTENSIONS
from ..merge import build_merge, default_output, expand_paths
from ..tools import find_ffmpeg
from .util import fmt_size, natural_key, reveal_in_file_manager


def _name_key(path: str):
    return natural_key(os.path.basename(path))


class MergeDialog(QDialog):
    def __init__(self, paths: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("동영상 합치기")
        self.setMinimumSize(560, 460)
        self.proc: QProcess | None = None
        self._tmp: tempfile.TemporaryDirectory | None = None
        self._cancelled = False

        info = QLabel("아래 순서대로 이어 붙여 파일 하나로 만듭니다. 다시 인코딩하지 않아 빠르고 화질 손실이 없습니다.\n"
                      "끌어서 순서를 바꿀 수 있습니다. 모든 파일의 형식(코덱·해상도)이 같아야 합니다.")
        info.setWordWrap(True)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.model().rowsMoved.connect(self._update_summary)

        add = QPushButton("파일 추가…")
        add.clicked.connect(self._add_dialog)
        remove = QPushButton("빼기")
        remove.clicked.connect(self._remove_selected)
        sort = QPushButton("이름순 정렬")
        sort.clicked.connect(self._sort)
        up = QPushButton("▲")
        up.setToolTip("선택 항목 위로")
        up.clicked.connect(lambda: self._move(-1))
        down = QPushButton("▼")
        down.setToolTip("선택 항목 아래로")
        down.clicked.connect(lambda: self._move(1))
        row = QHBoxLayout()
        for b in (add, remove, sort):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(up)
        row.addWidget(down)
        self.summary = QLabel()

        self.out = QLineEdit()
        browse = QPushButton("찾아보기…")
        browse.clicked.connect(self._browse)
        out_row = QHBoxLayout()
        out_row.addWidget(self.out, 1)
        out_row.addWidget(browse)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.hide()
        self.status = QLabel()
        self.status.setWordWrap(True)

        self.buttons = QDialogButtonBox()
        self.start_btn = self.buttons.addButton("합치기", QDialogButtonBox.AcceptRole)
        self.close_btn = self.buttons.addButton("닫기", QDialogButtonBox.RejectRole)
        self.start_btn.clicked.connect(self.start)
        self.close_btn.clicked.connect(self.reject)
        self._poll = QTimer(self, interval=500)
        self._poll.timeout.connect(self._update_progress)

        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addWidget(self.list, 1)
        lay.addLayout(row)
        lay.addWidget(self.summary)
        lay.addWidget(QLabel("저장할 파일"))
        lay.addLayout(out_row)
        lay.addWidget(self.progress)
        lay.addWidget(self.status)
        lay.addWidget(self.buttons)

        self._add(expand_paths(paths, _name_key), sort=True)

    # ---- 목록 ----
    def paths(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    def _add(self, paths: list[str], sort: bool = False) -> None:
        have = set(self.paths())
        for p in paths:
            if p in have:
                continue
            item = QListWidgetItem(os.path.basename(p))
            item.setData(Qt.UserRole, p)
            item.setToolTip(p)
            self.list.addItem(item)
        if sort:
            self._sort()
        if self.list.count() and not self.out.text():
            self.out.setText(default_output(self.paths()))
        self._update_summary()

    def _add_dialog(self) -> None:
        start = os.path.dirname(self.paths()[0]) if self.list.count() else ""
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(self, "합칠 동영상 추가", start, f"동영상 ({exts});;모든 파일 (*)")
        self._add(expand_paths(files, _name_key))

    def _remove_selected(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))
        self._update_summary()

    def _sort(self) -> None:
        paths = sorted(self.paths(), key=_name_key)
        self.list.clear()
        for p in paths:
            item = QListWidgetItem(os.path.basename(p))
            item.setData(Qt.UserRole, p)
            item.setToolTip(p)
            self.list.addItem(item)
        self._update_summary()

    def _move(self, step: int) -> None:
        rows = sorted((self.list.row(i) for i in self.list.selectedItems()), reverse=step > 0)
        for r in rows:
            nr = r + step
            if not 0 <= nr < self.list.count():
                return
            item = self.list.takeItem(r)
            self.list.insertItem(nr, item)
            item.setSelected(True)
        self._update_summary()

    def _total_size(self) -> int:
        total = 0
        for p in self.paths():
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
        return total

    def _update_summary(self, *_args) -> None:
        self.summary.setText(f"{self.list.count()}개 파일 · 합계 {fmt_size(self._total_size())}")

    def _browse(self) -> None:
        path, flt = QFileDialog.getSaveFileName(self, "저장할 파일", self.out.text(),
                                                "MP4 동영상 (*.mp4);;MKV 동영상 (*.mkv);;TS 동영상 (*.ts)")
        if path:
            if not os.path.splitext(path)[1]:
                path += ".mkv" if "mkv" in flt.lower() else ".ts" if "*.ts" in flt else ".mp4"
            self.out.setText(path)

    # ---- 실행 ----
    def start(self) -> None:
        paths = self.paths()
        if len(paths) < 2:
            QMessageBox.information(self, "동영상 합치기", "합칠 파일을 2개 이상 넣어 주세요.")
            return
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            QMessageBox.critical(self, "동영상 합치기", "변환 도구(ffmpeg)를 찾지 못했습니다. 프로그램을 다시 설치해 주세요.")
            return
        out = self.out.text().strip()
        if not out:
            return
        if not os.path.splitext(out)[1]:
            out += ".mp4"
            self.out.setText(out)
        norm = os.path.normcase(os.path.abspath(out))
        if any(norm == os.path.normcase(os.path.abspath(p)) for p in paths):
            QMessageBox.warning(self, "동영상 합치기", "원본 파일과 같은 이름으로는 저장할 수 없습니다.")
            return
        if os.path.exists(out) and QMessageBox.question(
                self, "동영상 합치기", f"이미 있는 파일입니다. 덮어쓸까요?\n{out}") != QMessageBox.Yes:
            return

        self._tmp = tempfile.TemporaryDirectory(prefix="rumi_")
        list_file = os.path.join(self._tmp.name, "list.ffconcat")
        text, args = build_merge(paths, list_file, out, ffmpeg)
        with open(list_file, "w", encoding="utf-8") as f:
            f.write(text)

        self._output, self._expected = out, max(self._total_size(), 1)
        self._log: list[str] = []
        self._cancelled = False
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.SeparateChannels)
        self.proc.readyReadStandardOutput.connect(lambda: self.proc.readAllStandardOutput())
        self.proc.readyReadStandardError.connect(
            lambda: self._log.append(bytes(self.proc.readAllStandardError()).decode("utf-8", "replace")))
        self.proc.finished.connect(self._finished)
        self.progress.setValue(0)
        self.progress.show()
        self.status.setText("합치는 중…")
        self.start_btn.setEnabled(False)
        self.close_btn.setText("취소")
        self.list.setEnabled(False)
        self.out.setEnabled(False)
        self.proc.start(args[0], args[1:])
        self._poll.start()

    def _update_progress(self) -> None:
        # 다시 인코딩하지 않으므로 결과 파일 크기가 원본 합계와 거의 같다
        try:
            done = os.path.getsize(self._output)
        except OSError:
            return
        self.progress.setValue(int(min(done / self._expected, 0.99) * 1000))
        self.status.setText(f"합치는 중… {fmt_size(done)} / 약 {fmt_size(self._expected)}")

    def _finished(self, code: int, _status) -> None:
        self._poll.stop()
        self.proc = None
        self.close_btn.setText("닫기")
        if self._tmp:
            self._tmp.cleanup()
            self._tmp = None
        if not self._cancelled and code == 0 and os.path.exists(self._output):
            self.progress.setValue(1000)
            self.status.setText(f"완료: {self._output} ({fmt_size(os.path.getsize(self._output))})")
            if QMessageBox.question(self, "동영상 합치기", "합치기를 마쳤습니다. 파일 위치를 열까요?") == QMessageBox.Yes:
                reveal_in_file_manager(self._output)
            self.accept()
            return
        if self._cancelled:
            self.status.setText("취소했습니다.")
        else:
            tail = "".join(self._log)[-1200:].strip()
            self.status.setText("합치기에 실패했습니다.")
            QMessageBox.critical(self, "동영상 합치기",
                                 "합치지 못했습니다. 파일마다 형식(코덱·해상도)이 다르면 이어 붙일 수 없습니다."
                                 + (f"\n\n{tail}" if tail else ""))
            try:
                os.remove(self._output)
            except OSError:
                pass
        self.progress.hide()
        self.start_btn.setEnabled(True)
        self.list.setEnabled(True)
        self.out.setEnabled(True)

    def reject(self) -> None:
        if self.proc is not None:
            self._cancelled = True
            self.proc.kill()
            self.proc.waitForFinished(3000)
            try:
                os.remove(self._output)
            except OSError:
                pass
            return
        super().reject()
