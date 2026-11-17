"""앱 전체 테마 (색 구성 여러 가지). Fusion 스타일 + 팔레트 + 스타일시트."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

THEMES = {"dark": "어둡게", "light": "밝게", "cinema": "시네마 (검정 + 호박색)", "midnight": "미드나이트 (남색 + 청록)",
          "graphite": "그래파이트 (회색 + 주황)", "violet": "바이올렛 (보라)", "paper": "페이퍼 (따뜻한 밝은색)"}
DEFAULT_THEME = "dark"

_COLORS = {
    "dark": dict(bg="#1e1f22", panel="#26282c", raised="#2f3237", border="#3a3d43", text="#e6e7e9",
                 dim="#9a9ea6", accent="#3d8fe0", accent_text="#ffffff", hover="#353940", danger="#e5534b"),
    "light": dict(bg="#f4f5f7", panel="#ffffff", raised="#ffffff", border="#d5d8dd", text="#1f2328",
                  dim="#656d76", accent="#2f7bd3", accent_text="#ffffff", hover="#e8ecf1", danger="#cf222e"),
    # 영화관처럼 새까만 바탕: 영상에 시선이 가도록 테두리와 강조색을 절제했다
    "cinema": dict(bg="#000000", panel="#0c0c0d", raised="#17181a", border="#2a2b2e", text="#e9e6df",
                   dim="#8f8a80", accent="#e0a23a", accent_text="#141005", hover="#222326", danger="#e5534b"),
    "midnight": dict(bg="#0f1524", panel="#151c2e", raised="#1c2540", border="#2b3655", text="#dfe6f5",
                     dim="#8b97b5", accent="#2fc4b2", accent_text="#04201c", hover="#243052", danger="#ff6b6b"),
    "graphite": dict(bg="#2a2a2a", panel="#323232", raised="#3c3c3c", border="#4a4a4a", text="#ededed",
                     dim="#a6a6a6", accent="#f08a24", accent_text="#1c1004", hover="#464646", danger="#ef5350"),
    "violet": dict(bg="#191622", panel="#211d2e", raised="#2a2540", border="#3b3556", text="#ebe7f7",
                   dim="#9d95b8", accent="#9a7bf0", accent_text="#ffffff", hover="#332d4d", danger="#f0637a"),
    "paper": dict(bg="#f3efe6", panel="#fbf8f1", raised="#fffdf8", border="#ddd5c3", text="#2b2620",
                  dim="#7a7062", accent="#b5622a", accent_text="#ffffff", hover="#ebe4d5", danger="#b3261e"),
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

/* 재생목록 탭 줄: 고른 탭은 바탕색으로 또렷하게, 좌우 이동 단추는 누르기 쉽게 크게 */
#playlistTabs QTabBar::tab {{ background: transparent; color: {dim}; padding: 6px 12px; margin: 3px 2px 3px 0;
    border: 1px solid transparent; border-radius: 6px; min-width: 40px; }}
#playlistTabs QTabBar::tab:selected {{ background: {raised}; color: {text}; border: 1px solid {border};
    border-bottom: 2px solid {accent}; font-weight: bold; }}
#playlistTabs QTabBar::tab:hover:!selected {{ background: {hover}; color: {text}; }}
#playlistTabs QTabBar::tear {{ width: 0; border: none; image: none; }}
#playlistTabs QTabBar::scroller {{ width: 0; }}
#playlistTabs QTabBar QToolButton {{ width: 0; border: none; }}
#playlistTabs QTabWidget::pane, QTabWidget#playlistTabs::pane {{ border: none; top: 0; }}
#tabStrip QToolButton {{ color: {text}; background: {raised}; border: 1px solid {border}; border-radius: 6px;
    padding: 3px 0; min-width: 23px; min-height: 22px; font-size: 13px; }}
#tabStrip QToolButton:hover {{ background: {hover}; }}
#tabStrip QToolButton:disabled {{ color: {border}; background: transparent; }}
#tabStrip QToolButton::menu-indicator {{ image: none; width: 0; }}
#tabClose {{ color: {dim}; background: transparent; border: none; border-radius: 8px; padding: 0;
    min-width: 16px; max-width: 16px; min-height: 16px; max-height: 16px; font-size: 13px; }}
#tabClose:hover {{ background: {hover}; color: {text}; }}

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
