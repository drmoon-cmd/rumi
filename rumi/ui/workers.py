"""오래 걸리는 작업(스캔, 해시 계산)을 백그라운드에서 실행하고 진행 상황을 보여준다."""

from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import QProgressDialog, QWidget

from ..library.duplicates import Cancelled


class Worker(QThread):
    progress = Signal(str, int, int)   # (설명, 현재, 전체; 전체가 0이면 미정)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, fn: Callable[["Worker"], Any], parent=None):
        super().__init__(parent)
        self._fn = fn
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def is_cancelled(self) -> bool:
        return self._cancel

    def run(self) -> None:
        try:
            result = self._fn(self)
        except Cancelled:
            self.cancelled.emit()
            return
        except Exception:
            self.failed.emit(traceback.format_exc())
            return
        if self._cancel:
            self.cancelled.emit()
        else:
            self.succeeded.emit(result)


def run_with_progress(parent: QWidget, title: str, fn: Callable[[Worker], Any],
                      on_done: Callable[[Any], None],
                      on_error: Callable[[str], None] | None = None) -> Worker:
    """진행 대화상자를 띄우고 fn 을 백그라운드에서 실행. 취소 버튼 지원."""
    dlg = QProgressDialog(title, "취소", 0, 0, parent)
    dlg.setWindowTitle(title)
    dlg.setWindowModality(Qt.WindowModal)
    dlg.setMinimumDuration(300)
    dlg.setMinimumWidth(420)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)

    worker = Worker(fn, parent)

    def on_progress(text: str, cur: int, total: int):
        dlg.setLabelText(text)
        dlg.setMaximum(total)
        dlg.setValue(min(cur, total) if total else 0)

    def finish():
        dlg.close()
        worker.deleteLater()

    worker.progress.connect(on_progress)
    worker.succeeded.connect(lambda r: (finish(), on_done(r)))
    worker.failed.connect(lambda msg: (finish(), on_error and on_error(msg)))
    worker.cancelled.connect(finish)
    dlg.canceled.connect(worker.cancel)
    worker.start()
    return worker
