"""구간 목록 패널과 내보내기 대화상자."""

from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import QProcess, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QRadioButton,
    QToolButton, QVBoxLayout, QWidget,
)

from ..clips import (
    CLIPLIST_SUFFIX, Clip, ClipList, build_copy_export, build_reencode_export, parse_progress_seconds,
)
from ..tools import find_ffmpeg
from .util import fmt_time, reveal_in_file_manager

CLIP_ROLE = Qt.UserRole


class ClipsPanel(QWidget):
    play_requested = Signal(int)       # 이 번호의 구간부터 이어서 재생

    def __init__(self, parent=None):
        super().__init__(parent)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.itemActivated.connect(lambda it: self.play_requested.emit(self.list.row(it)))
        self.list.model().rowsMoved.connect(lambda *_: self._update_summary())
        self.summary = QLabel()
        self.hint = QLabel("재생 중 <b>I</b> 키로 시작점, <b>O</b> 키로 끝점을 찍으면 구간이 추가됩니다.")
        self.hint.setWordWrap(True)

        def tool(text, tip, slot):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(slot)
            return b

        play = QPushButton("▶ 이어서 재생")
        play.setFocusPolicy(Qt.NoFocus)
        play.clicked.connect(self._play_from_selection)
        export = QPushButton("파일로 내보내기…")
        export.setFocusPolicy(Qt.NoFocus)
        export.clicked.connect(self.export)

        row1 = QHBoxLayout()
        row1.addWidget(play)
        row1.addWidget(export)
        row2 = QHBoxLayout()
        row2.addWidget(tool("삭제", "선택한 구간 빼기", self.remove_selected))
        row2.addWidget(tool("비우기", "목록 비우기", self.clear))
        row2.addStretch(1)
        row2.addWidget(tool("열기…", "구간 목록 파일 열기", self.open_dialog))
        row2.addWidget(tool("저장…", "구간 목록을 파일로 저장", self.save_dialog))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.hint)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.summary)
        lay.addLayout(row1)
        lay.addLayout(row2)
        self._update_summary()

    # ---- 목록 ----
    def clips(self) -> list[Clip]:
        return [self.list.item(i).data(CLIP_ROLE) for i in range(self.list.count())]

    def add_clip(self, clip: Clip) -> None:
        cl = ClipList()
        cl.add(clip)  # 검증 (너무 짧으면 ValueError)
        item = QListWidgetItem(self._label(clip))
        item.setData(CLIP_ROLE, clip)
        item.setToolTip(clip.path)
        self.list.addItem(item)
        self.list.scrollToItem(item)
        self._update_summary()

    @staticmethod
    def _label(c: Clip) -> str:
        return f"{fmt_time(c.start)}~{fmt_time(c.end)} ({c.duration:.1f}초)  {c.name}"

    def remove_selected(self) -> None:
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))
        self._update_summary()

    def clear(self) -> None:
        self.list.clear()
        self._update_summary()

    def highlight(self, index: int | None) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            f = it.font()
            f.setBold(i == index)
            it.setFont(f)

    def _update_summary(self) -> None:
        cl = ClipList(self.clips())
        self.summary.setText(f"구간 {len(cl.clips)}개 · 총 {fmt_time(cl.total_duration)}" if cl.clips else "구간 없음")

    def _play_from_selection(self) -> None:
        if self.list.count():
            row = self.list.currentRow()
            self.play_requested.emit(row if row >= 0 else 0)

    # ---- 저장/열기 ----
    def save_dialog(self) -> None:
        if not self.list.count():
            return
        path, _ = QFileDialog.getSaveFileName(self, "구간 목록 저장", "구간목록" + CLIPLIST_SUFFIX,
                                              f"구간 목록 (*{CLIPLIST_SUFFIX})")
        if path:
            if not path.endswith(CLIPLIST_SUFFIX):
                path += CLIPLIST_SUFFIX
            ClipList(self.clips()).save(path)

    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "구간 목록 열기", "", f"구간 목록 (*{CLIPLIST_SUFFIX})")
        if not path:
            return
        try:
            cl = ClipList.load(path)
        except (OSError, ValueError, TypeError) as e:
            QMessageBox.warning(self, "구간 목록", f"파일을 읽지 못했습니다:\n{e}")
            return
        self.clear()
        for c in cl.clips:
            self.add_clip(c)
        if missing := cl.missing_files():
            QMessageBox.warning(self, "구간 목록", "다음 파일을 찾을 수 없습니다:\n" + "\n".join(missing[:10]))

    # ---- 내보내기 ----
    def export(self) -> None:
        clips = self.clips()
        if not clips:
            QMessageBox.information(self, "내보내기", "먼저 I / O 키로 구간을 추가해 주세요.")
            return
        if missing := ClipList(clips).missing_files():
            QMessageBox.warning(self, "내보내기", "다음 파일을 찾을 수 없습니다:\n" + "\n".join(missing[:10]))
            return
        ExportDialog(clips, self).exec()


class ExportDialog(QDialog):
    def __init__(self, clips: list[Clip], parent=None):
        super().__init__(parent)
        self.setWindowTitle("구간을 파일로 내보내기")
        self.setMinimumWidth(520)
        self.clips = clips
        self.total = ClipList(clips).total_duration
        self.proc: QProcess | None = None
        self._tmp: tempfile.TemporaryDirectory | None = None
        uniform = ClipList(clips).uniform_format()

        info = QLabel(f"구간 {len(clips)}개 · 총 {fmt_time(self.total)}")
        self.exact = QRadioButton("정확하게 (권장) — 프레임 단위로 정확히 자르고 다시 인코딩합니다. 시간이 걸립니다.")
        self.fast = QRadioButton("빠르게 — 원본을 그대로 잘라 붙여 매우 빠르고 화질 손실이 없지만, "
                                 "시작·끝이 최대 1~2초 어긋날 수 있습니다.")
        for r in (self.exact, self.fast):
            r.setStyleSheet("QRadioButton { padding: 2px; }")
        group = QButtonGroup(self)
        group.addButton(self.exact)
        group.addButton(self.fast)
        self.exact.setChecked(True)
        if not uniform:
            self.fast.setEnabled(False)
            self.fast.setText(self.fast.text() + "\n(해상도가 다른 영상이 섞여 있어 사용할 수 없습니다)")

        first = clips[0].path
        default = os.path.join(os.path.dirname(first), os.path.splitext(os.path.basename(first))[0] + "_구간.mp4")
        self.out = QLineEdit(default)
        browse = QPushButton("찾아보기…")
        browse.clicked.connect(self._browse)
        out_row = QHBoxLayout()
        out_row.addWidget(self.out, 1)
        out_row.addWidget(browse)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.hide()
        self.status = QLabel()

        self.buttons = QDialogButtonBox()
        self.start_btn = self.buttons.addButton("내보내기", QDialogButtonBox.AcceptRole)
        self.close_btn = self.buttons.addButton("닫기", QDialogButtonBox.RejectRole)
        self.start_btn.clicked.connect(self.start)
        self.close_btn.clicked.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addWidget(self.exact)
        lay.addWidget(self.fast)
        lay.addWidget(QLabel("저장할 파일"))
        lay.addLayout(out_row)
        lay.addWidget(self.progress)
        lay.addWidget(self.status)
        lay.addWidget(self.buttons)

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "저장할 파일", self.out.text(), "MP4 동영상 (*.mp4)")
        if path:
            self.out.setText(path if path.lower().endswith(".mp4") else path + ".mp4")

    def start(self) -> None:
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            QMessageBox.critical(self, "내보내기", "변환 도구(ffmpeg)를 찾지 못했습니다. 프로그램을 다시 설치해 주세요.")
            return
        out = self.out.text().strip()
        if not out:
            return
        if any(os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(c.path)) for c in self.clips):
            QMessageBox.warning(self, "내보내기", "원본 파일과 같은 이름으로는 저장할 수 없습니다.")
            return
        if os.path.exists(out) and QMessageBox.question(
                self, "내보내기", f"이미 있는 파일입니다. 덮어쓸까요?\n{out}") != QMessageBox.Yes:
            return

        if self.fast.isChecked():
            self._tmp = tempfile.TemporaryDirectory(prefix="rumi_")
            list_file = os.path.join(self._tmp.name, "list.ffconcat")
            text, args = build_copy_export(self.clips, list_file, out, ffmpeg)
            with open(list_file, "w", encoding="utf-8") as f:
                f.write(text)
        else:
            args = build_reencode_export(self.clips, out, ffmpeg)

        self._output = out
        self._log: list[str] = []
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.SeparateChannels)
        self.proc.readyReadStandardOutput.connect(self._read_progress)
        self.proc.readyReadStandardError.connect(
            lambda: self._log.append(bytes(self.proc.readAllStandardError()).decode("utf-8", "replace")))
        self.proc.finished.connect(self._finished)
        self.progress.setValue(0)
        self.progress.show()
        self.status.setText("내보내는 중…")
        self.start_btn.setEnabled(False)
        self.close_btn.setText("취소")
        for w in (self.exact, self.fast, self.out):
            w.setEnabled(False)
        self.proc.start(args[0], args[1:])

    def _read_progress(self) -> None:
        for line in bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace").splitlines():
            sec = parse_progress_seconds(line.strip())
            if sec is not None and self.total > 0:
                self.progress.setValue(int(min(sec / self.total, 1.0) * 1000))
                self.status.setText(f"내보내는 중… {fmt_time(sec)} / {fmt_time(self.total)}")

    def _finished(self, code: int, _status) -> None:
        self.proc = None
        self.close_btn.setText("닫기")
        if self._tmp:
            self._tmp.cleanup()
            self._tmp = None
        if code == 0 and os.path.exists(self._output):
            self.progress.setValue(1000)
            self.status.setText(f"완료: {self._output}")
            if QMessageBox.question(self, "내보내기", "내보내기를 마쳤습니다. 파일 위치를 열까요?") == QMessageBox.Yes:
                reveal_in_file_manager(self._output)
            self.accept()
        else:
            if self._cancelled():
                self.status.setText("취소했습니다.")
                self._was_cancelled = False
            else:
                tail = "".join(self._log)[-1500:]
                self.status.setText("내보내기에 실패했습니다.")
                QMessageBox.critical(self, "내보내기 실패", f"ffmpeg 오류 (코드 {code}):\n\n{tail}")
            self.progress.hide()
            self.start_btn.setEnabled(True)
            for w in (self.exact, self.out):
                w.setEnabled(True)
            self.fast.setEnabled(ClipList(self.clips).uniform_format())

    def _cancelled(self) -> bool:
        return getattr(self, "_was_cancelled", False)

    def reject(self) -> None:
        if self.proc is not None:
            self._was_cancelled = True
            self.proc.kill()
            self.proc.waitForFinished(3000)
            try:
                os.remove(self._output)
            except OSError:
                pass
            return
        super().reject()
