"""앱 전체 테마 (어둡게/밝게). Fusion 스타일 + 팔레트 + 스타일시트."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

THEMES = {"dark": "어둡게", "light": "밝게"}
DEFAULT_THEME = "dark"

_COLORS = {
    "dark": dict(bg="#1e1f22", panel="#26282c", raised="#2f3237", border="#3a3d43", text="#e6e7e9",
                 dim="#9a9ea6", accent="#3d8fe0", accent_text="#ffffff", hover="#353940", danger="#e5534b"),
    "light": dict(bg="#f4f5f7", panel="#ffffff", raised="#ffffff", border="#d5d8dd", text="#1f2328",
                  dim="#656d76", accent="#2f7bd3", accent_text="#ffffff", hover="#e8ecf1", danger="#cf222e"),
}

_QSS = """
QMainWindow, QDialog {{ background: {bg}; }}
QToolTip {{ background: {raised}; color: {text}; border: 1px solid {border}; padding: 4px 6px; }}

/* 메뉴 */
QMenuBar {{ background: {panel}; color: {text}; border-bottom: 1px solid {border}; padding: 2px 4px; }}
QMenuBar::item {{ background: transparent; padding: 5px 10px; border-radius: 4px; }}
QMenuBar::item:selected, QMenuBar::item:pressed {{ background: {hover}; }}
QMenu {{ background: {raised}; color: {text}; border: 1px solid {border}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 7px 28px 7px 26px; border-radius: 5px; margin: 1px 2px; }}
QMenu::item:selected {{ background: {accent}; color: {accent_text}; }}
QMenu::item:disabled {{ color: {dim}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 5px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 7px; }}
QMenu::right-arrow {{ width: 10px; height: 10px; }}

/* 컨트롤 바 / 탐색 바 */
#controlBar {{ background: {panel}; border-top: 1px solid {border}; }}
#controlBar QLabel {{ color: {text}; }}
QSlider::groove:horizontal {{ height: 4px; background: {border}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {accent_text}; border: 2px solid {accent}; width: 10px; height: 10px;
                             margin: -5px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: {accent}; }}
#seekSlider::groove:horizontal {{ height: 6px; border-radius: 3px; }}
#seekSlider::sub-page:horizontal {{ border-radius: 3px; }}
QToolButton {{ color: {text}; border: none; border-radius: 5px; padding: 4px; }}
QToolButton:hover {{ background: {hover}; }}
QToolButton:pressed {{ background: {border}; }}

/* 패널 */
QDockWidget {{ color: {text}; }}
QDockWidget::title {{ background: {panel}; padding: 6px 8px; border-bottom: 1px solid {border}; }}
QListWidget, QTreeWidget, QTableWidget {{ background: {panel}; color: {text}; border: 1px solid {border};
    border-radius: 6px; alternate-background-color: {raised}; outline: 0; }}
QListWidget::item, QTreeWidget::item {{ padding: 4px 4px; }}
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {{
    background: {accent}; color: {accent_text}; }}
QListWidget::item:hover, QTreeWidget::item:hover {{ background: {hover}; }}
QHeaderView::section {{ background: {raised}; color: {dim}; border: none; border-bottom: 1px solid {border};
    padding: 5px 6px; }}
QTabWidget::pane {{ border: 1px solid {border}; border-radius: 6px; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {dim}; padding: 7px 14px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {text}; border-bottom: 2px solid {accent}; }}
QTabBar::tab:hover {{ color: {text}; }}

/* 입력 */
QPushButton {{ background: {raised}; color: {text}; border: 1px solid {border}; border-radius: 6px; padding: 6px 12px; }}
QPushButton:hover {{ background: {hover}; }}
QPushButton:pressed {{ background: {border}; }}
QPushButton:default {{ background: {accent}; color: {accent_text}; border-color: {accent}; }}
QPushButton:disabled {{ color: {dim}; }}
QLineEdit, QComboBox {{ background: {raised}; color: {text}; border: 1px solid {border}; border-radius: 6px;
    padding: 5px 8px; selection-background-color: {accent}; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {accent}; }}
QComboBox QAbstractItemView {{ background: {raised}; color: {text}; border: 1px solid {border};
    selection-background-color: {accent}; }}
QProgressBar {{ background: {raised}; border: 1px solid {border}; border-radius: 6px; text-align: center; color: {text}; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 5px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 4px; min-height: 24px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {border}; border-radius: 4px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


def apply_theme(app: QApplication, name: str) -> str:
    name = name if name in _COLORS else DEFAULT_THEME
    c = _COLORS[name]
    app.setStyle(QStyleFactory.create("Fusion"))
    pal = QPalette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.Base, "panel"), (QPalette.AlternateBase, "raised"),
                      (QPalette.Button, "raised"), (QPalette.ToolTipBase, "raised")):
        pal.setColor(role, QColor(c[key]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText, QPalette.ToolTipText):
        pal.setColor(role, QColor(c["text"]))
    pal.setColor(QPalette.PlaceholderText, QColor(c["dim"]))
    pal.setColor(QPalette.Mid, QColor(c["dim"]))
    pal.setColor(QPalette.Highlight, QColor(c["accent"]))
    pal.setColor(QPalette.HighlightedText, QColor(c["accent_text"]))
    pal.setColor(QPalette.Link, QColor(c["accent"]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(c["dim"]))
    app.setPalette(pal)
    app.setStyleSheet(_QSS.format(**c))
    return name


def danger_color(name: str) -> str:
    return _COLORS.get(name, _COLORS[DEFAULT_THEME])["danger"]
