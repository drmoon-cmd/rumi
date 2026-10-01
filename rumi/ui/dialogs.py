"""설정/조정 대화상자: 영상 보정, 소리, 자막 모양, 환경 설정(단축키·마우스), 책갈피."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFontComboBox, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QKeySequenceEdit, QLabel,
    QMessageBox, QPushButton, QSlider, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from ..media_tools import EQ_PRESETS, AudioSettings
from .util import fmt_time


def _slider(lo: int, hi: int, value: int, on_change: Callable[[int], None]) -> tuple[QWidget, QSlider]:
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    s.setValue(value)
    label = QLabel(str(value))
    label.setMinimumWidth(34)
    label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def changed(v):
        label.setText(str(v))
        on_change(v)

    s.valueChanged.connect(changed)
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(s, 1)
    row.addWidget(label)
    return w, s


# ------------------------------------------------------------------ 영상 보정
VIDEO_ADJUST = [("brightness", "밝기"), ("contrast", "명암"), ("saturation", "채도"), ("gamma", "감마")]


class VideoAdjustDialog(QDialog):
    """밝기·명암·채도·감마. 움직이는 즉시 화면에 반영된다."""

    def __init__(self, player, parent=None):
        super().__init__(parent)
        self.setWindowTitle("영상 보정")
        self.player = player
        self.sliders: dict[str, QSlider] = {}
        form = QFormLayout()
        for prop, label in VIDEO_ADJUST:
            cur = int(getattr(player, prop) or 0)
            w, s = _slider(-100, 100, cur, lambda v, p=prop: setattr(self.player, p, v))
            self.sliders[prop] = s
            form.addRow(label, w)
        reset = QPushButton("모두 초기화")
        reset.setAutoDefault(False)
        reset.clicked.connect(self.reset)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        hint = QLabel("단축키: 1/2 명암 · 3/4 밝기 · 5/6 감마 · 7/8 채도")
        hint.setStyleSheet("color: palette(mid);")
        lay.addWidget(hint)
        row = QHBoxLayout()
        row.addWidget(reset)
        row.addStretch(1)
        row.addWidget(buttons)
        lay.addLayout(row)
        self.resize(420, self.sizeHint().height())

    def reset(self) -> None:
        for s in self.sliders.values():
            s.setValue(0)


# ------------------------------------------------------------------ 소리
class AudioDialog(QDialog):
    """소리 크기 자동 맞춤, 저음/고음, 소리 싱크."""

    changed = Signal(object)            # AudioSettings
    delay_changed = Signal(float)       # 초

    def __init__(self, settings: AudioSettings, delay: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("소리 설정")
        self.s = AudioSettings(settings.normalize, settings.bass, settings.treble)

        self.normalize = QCheckBox("소리 크기 자동 맞춤 (작은 대화는 키우고 큰 소리는 줄임)")
        self.normalize.setChecked(self.s.normalize)
        self.normalize.toggled.connect(self._emit)
        self.preset = QComboBox()
        self.preset.addItems(list(EQ_PRESETS))
        self.preset.addItem("사용자 지정")
        self.preset.activated.connect(self._apply_preset)
        bass_w, self.bass = _slider(-12, 12, self.s.bass, lambda _v: self._emit())
        treble_w, self.treble = _slider(-12, 12, self.s.treble, lambda _v: self._emit())
        self.delay = QDoubleSpinBox()
        self.delay.setRange(-10, 10)
        self.delay.setSingleStep(0.1)
        self.delay.setDecimals(2)
        self.delay.setSuffix(" 초")
        self.delay.setValue(delay)
        self.delay.valueChanged.connect(self.delay_changed)
        self._sync_preset()

        form = QFormLayout()
        form.addRow(self.normalize)
        form.addRow("이퀄라이저", self.preset)
        form.addRow("저음 (dB)", bass_w)
        form.addRow("고음 (dB)", treble_w)
        form.addRow("소리 싱크", self.delay)
        hint = QLabel("소리 싱크: + 는 소리를 늦추고, - 는 당깁니다. 단축키 Ctrl+[ / Ctrl+] / Ctrl+Backspace")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        reset = QPushButton("초기화")
        reset.setAutoDefault(False)
        reset.clicked.connect(self.reset)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(hint)
        row = QHBoxLayout()
        row.addWidget(reset)
        row.addStretch(1)
        row.addWidget(buttons)
        lay.addLayout(row)
        self.resize(460, self.sizeHint().height())

    def _apply_preset(self, index: int) -> None:
        name = self.preset.itemText(index)
        if name in EQ_PRESETS:
            b, t = EQ_PRESETS[name]
            self.bass.blockSignals(True)
            self.treble.blockSignals(True)
            self.bass.setValue(b)
            self.treble.setValue(t)
            self.bass.blockSignals(False)
            self.treble.blockSignals(False)
            self._emit()

    def _sync_preset(self) -> None:
        cur = (self.bass.value(), self.treble.value())
        name = next((n for n, v in EQ_PRESETS.items() if v == cur), "사용자 지정")
        self.preset.setCurrentText(name)

    def _emit(self, *_):
        self.s = AudioSettings(self.normalize.isChecked(), self.bass.value(), self.treble.value())
        self._sync_preset()
        self.changed.emit(self.s)

    def reset(self) -> None:
        self.normalize.setChecked(False)
        self.preset.setCurrentText("기본")
        self._apply_preset(0)
        self.delay.setValue(0)


# ------------------------------------------------------------------ 자막 모양
SUB_DEFAULTS = {
    "font": "", "size": 55, "color": "#FFFFFF", "border_color": "#000000", "border_size": 3,
    "position": 100, "background": False, "override_ass": False,
}


class SubtitleStyleDialog(QDialog):
    changed = Signal(dict)

    def __init__(self, style: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("자막 모양")
        self.style = {**SUB_DEFAULTS, **style}

        self.font = QFontComboBox()
        if self.style["font"]:
            self.font.setCurrentFont(QFont(self.style["font"]))
        self.font.currentFontChanged.connect(lambda f: self._set("font", f.family()))
        self.size = QSpinBox()
        self.size.setRange(20, 120)
        self.size.setValue(int(self.style["size"]))
        self.size.valueChanged.connect(lambda v: self._set("size", v))
        self.color_btn = QPushButton()
        self.color_btn.clicked.connect(lambda: self._pick("color"))
        self.border_btn = QPushButton()
        self.border_btn.clicked.connect(lambda: self._pick("border_color"))
        self.border = QSpinBox()
        self.border.setRange(0, 10)
        self.border.setValue(int(self.style["border_size"]))
        self.border.valueChanged.connect(lambda v: self._set("border_size", v))
        pos_w, self.pos = _slider(0, 100, int(self.style["position"]), lambda v: self._set("position", v))
        self.background = QCheckBox("글자 뒤에 반투명 배경 상자")
        self.background.setChecked(bool(self.style["background"]))
        self.background.toggled.connect(lambda on: self._set("background", on))
        self.override = QCheckBox("스타일이 들어 있는 자막(ASS)에도 적용")
        self.override.setChecked(bool(self.style["override_ass"]))
        self.override.toggled.connect(lambda on: self._set("override_ass", on))
        self._paint_buttons()

        form = QFormLayout()
        form.addRow("글꼴", self.font)
        form.addRow("크기", self.size)
        form.addRow("글자색", self.color_btn)
        form.addRow("테두리색", self.border_btn)
        form.addRow("테두리 두께", self.border)
        form.addRow("세로 위치 (100 = 맨 아래)", pos_w)
        form.addRow(self.background)
        form.addRow(self.override)
        reset = QPushButton("기본값")
        reset.setAutoDefault(False)
        reset.clicked.connect(self.reset)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        row = QHBoxLayout()
        row.addWidget(reset)
        row.addStretch(1)
        row.addWidget(buttons)
        lay.addLayout(row)

    def _set(self, key, value) -> None:
        self.style[key] = value
        self.changed.emit(dict(self.style))

    def _pick(self, key: str) -> None:
        c = QColorDialog.getColor(QColor(self.style[key]), self, "색 고르기")
        if c.isValid():
            self._set(key, c.name().upper())
            self._paint_buttons()

    def _paint_buttons(self) -> None:
        for btn, key in ((self.color_btn, "color"), (self.border_btn, "border_color")):
            c = self.style[key]
            btn.setText(c)
            btn.setStyleSheet(f"background: {c}; color: {'#000' if QColor(c).lightness() > 128 else '#fff'};")

    def reset(self) -> None:
        self.style = dict(SUB_DEFAULTS)
        for w in (self.size, self.border, self.pos, self.background, self.override, self.font):
            w.blockSignals(True)
        self.size.setValue(SUB_DEFAULTS["size"])
        self.border.setValue(SUB_DEFAULTS["border_size"])
        self.pos.setValue(SUB_DEFAULTS["position"])
        self.background.setChecked(False)
        self.override.setChecked(False)
        for w in (self.size, self.border, self.pos, self.background, self.override, self.font):
            w.blockSignals(False)
        self._paint_buttons()
        self.changed.emit(dict(self.style))


# ------------------------------------------------------------------ 환경 설정 (단축키, 마우스)
MOUSE_CHOICES = {
    "left_click": ("왼쪽 클릭", {"pause": "재생 / 일시정지", "none": "아무것도 안 함"}),
    "double_click": ("더블클릭", {"fullscreen": "전체화면", "none": "아무것도 안 함"}),
    "middle_click": ("가운데 버튼", {"mute": "음소거", "fullscreen": "전체화면", "pause": "재생 / 일시정지",
                                   "none": "아무것도 안 함"}),
    "wheel": ("휠", {"volume": "볼륨", "seek": "앞뒤로 5초 이동", "none": "아무것도 안 함"}),
    "ctrl_wheel": ("Ctrl + 휠", {"zoom": "화면 확대 / 축소", "volume": "볼륨", "none": "아무것도 안 함"}),
}
MOUSE_DEFAULTS = {"left_click": "pause", "double_click": "fullscreen", "middle_click": "mute",
                  "wheel": "volume", "ctrl_wheel": "zoom"}


class PreferencesDialog(QDialog):
    """단축키와 마우스 동작 설정. 확인을 누르면 적용된다."""

    def __init__(self, actions: list[QAction], defaults: dict[str, str], mouse: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("환경 설정")
        self.resize(640, 560)
        self.actions = actions
        self.defaults = defaults

        # 단축키 탭
        self.table = QTableWidget(len(actions), 2)
        self.table.setHorizontalHeaderLabels(["동작", "단축키 (칸을 누르고 새 키 입력)"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.editors: list[QKeySequenceEdit] = []
        for r, a in enumerate(actions):
            it = QTableWidgetItem(a.text().replace("&", ""))
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 0, it)
            ed = QKeySequenceEdit(a.shortcut())
            ed.setClearButtonEnabled(True)
            self.editors.append(ed)
            self.table.setCellWidget(r, 1, ed)
        restore = QPushButton("단축키 모두 기본값으로")
        restore.clicked.connect(self._restore_defaults)
        keys = QWidget()
        kl = QVBoxLayout(keys)
        kl.addWidget(self.table, 1)
        kl.addWidget(restore, 0, Qt.AlignLeft)

        # 마우스 탭
        mouse_w = QWidget()
        form = QFormLayout(mouse_w)
        self.mouse_boxes: dict[str, QComboBox] = {}
        for key, (label, choices) in MOUSE_CHOICES.items():
            box = QComboBox()
            for value, text in choices.items():
                box.addItem(text, value)
            box.setCurrentIndex(max(box.findData(mouse.get(key, MOUSE_DEFAULTS[key])), 0))
            self.mouse_boxes[key] = box
            form.addRow(label, box)
        note = QLabel("화면을 확대한 상태에서는 왼쪽 버튼으로 끌어 화면을 옮길 수 있습니다.")
        note.setWordWrap(True)
        form.addRow(note)

        tabs = QTabWidget()
        tabs.addTab(keys, "단축키")
        tabs.addTab(mouse_w, "마우스")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs, 1)
        lay.addWidget(buttons)

    def _restore_defaults(self) -> None:
        for a, ed in zip(self.actions, self.editors):
            ed.setKeySequence(QKeySequence(self.defaults.get(a.objectName(), "")))

    def _accept(self) -> None:
        seen: dict[str, str] = {}
        for a, ed in zip(self.actions, self.editors):
            k = ed.keySequence().toString(QKeySequence.PortableText)
            if not k:
                continue
            if k in seen:
                QMessageBox.warning(self, "단축키 겹침",
                                    f"'{k}' 키가 '{seen[k]}'와(과) '{a.text()}'에 함께 지정되어 있습니다.")
                return
            seen[k] = a.text()
        self.accept()

    def shortcuts(self) -> dict[str, str]:
        return {a.objectName(): ed.keySequence().toString(QKeySequence.PortableText)
                for a, ed in zip(self.actions, self.editors)}

    def mouse(self) -> dict[str, str]:
        return {k: box.currentData() for k, box in self.mouse_boxes.items()}


# ------------------------------------------------------------------ 책갈피
class BookmarksDialog(QDialog):
    jump = Signal(float)

    def __init__(self, db, path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("책갈피 관리")
        self.resize(460, 380)
        self.db, self.path = db, path
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["위치", "이름"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.cellDoubleClicked.connect(lambda r, _c: self._jump(r))
        go = QPushButton("이동")
        go.clicked.connect(lambda: self._jump(self.table.currentRow()))
        rename = QPushButton("이름 바꾸기…")
        rename.clicked.connect(self._rename)
        delete = QPushButton("삭제")
        delete.clicked.connect(self._delete)
        close = QPushButton("닫기")
        close.clicked.connect(self.reject)
        row = QHBoxLayout()
        for b in (go, rename, delete):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.table, 1)
        lay.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        self._rows = self.db.bookmarks(self.path)
        self.table.setRowCount(len(self._rows))
        for r, (_id, pos, name) in enumerate(self._rows):
            self.table.setItem(r, 0, QTableWidgetItem(fmt_time(pos)))
            self.table.setItem(r, 1, QTableWidgetItem(name))

    def _jump(self, row: int) -> None:
        if 0 <= row < len(self._rows):
            self.jump.emit(self._rows[row][1])

    def _rename(self) -> None:
        r = self.table.currentRow()
        if 0 <= r < len(self._rows):
            name, ok = QInputDialog.getText(self, "책갈피 이름", "이름:", text=self._rows[r][2])
            if ok:
                self.db.rename_bookmark(self._rows[r][0], name.strip())
                self.refresh()

    def _delete(self) -> None:
        r = self.table.currentRow()
        if 0 <= r < len(self._rows):
            self.db.delete_bookmark(self._rows[r][0])
            self.refresh()
