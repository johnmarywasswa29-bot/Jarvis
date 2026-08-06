"""Jarvis UI theme constants."""
from __future__ import annotations

from typing import Final

# Base palette - dark modern
BACKGROUND: Final[str] = "#0F1115"
SURFACE: Final[str] = "#1A1D23"
SURFACE_ELEVATED: Final[str] = "#22262E"
SURFACE_HOVER: Final[str] = "#2A2E38"
BORDER: Final[str] = "#2E323A"
DIVIDER: Final[str] = "#2A2E38"

PRIMARY: Final[str] = "#7C8AFF"
PRIMARY_HOVER: Final[str] = "#6A78E6"
ACCENT: Final[str] = "#4F6DEX"
SUCCESS: Final[str] = "#3DDC84"
WARNING: Final[str] = "#FFB84D"
ERROR: Final[str] = "#FF5A5A"

TEXT_PRIMARY: Final[str] = "#F3F4F8"
TEXT_SECONDARY: Final[str] = "#9CA0A8"
TEXT_TERTIARY: Final[str] = "#6B7078"
TEXT_ON_PRIMARY: Final[str] = "#FFFFFF"

FONT_FAMILY: Final[str] = "Segoe UI"
FONT_SIZE_SM: Final[int] = 11
FONT_SIZE_MD: Final[int] = 13
FONT_SIZE_LG: Final[int] = 15
FONT_SIZE_XL: Final[int] = 18

RADIUS_SM: Final[int] = 4
RADIUS_MD: Final[int] = 8
RADIUS_LG: Final[int] = 12
RADIUS_XL: Final[int] = 16

SPACING_XS: Final[int] = 4
SPACING_SM: Final[int] = 8
SPACING_MD: Final[int] = 16
SPACING_LG: Final[int] = 24
SPACING_XL: Final[int] = 32

SIDEBAR_WIDTH: Final[int] = 220
RIGHT_PANEL_WIDTH: Final[int] = 300
STATUS_BAR_HEIGHT: Final[int] = 28

# Component styles
CHAT_USER_BUBBLE: Final[str] = PRIMARY
CHAT_ASSISTANT_BUBBLE: Final[str] = SURFACE_ELEVATED
CODE_BG: Final[str] = "#0D1117"
SCROLLBAR_BG: Final[str] = SURFACE
SCROLLBAR_HANDLE: Final[str] = BORDER

_SHARED_QSS = f"""
QWidget {{
    background-color: {BACKGROUND};
    color: {TEXT_PRIMARY};
    font-family: {FONT_FAMILY};
    font-size: {FONT_SIZE_MD}px;
}}

QMainWindow {{
    background-color: {BACKGROUND};
}}

QScrollArea {{
    background-color: transparent;
    border: none;
}}

QScrollBar:vertical {{
    background: {SCROLLBAR_BG};
    width: 10px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: {SCROLLBAR_HANDLE};
    border-radius: 5px;
    min-height: 40px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    background: none;
    height: 0px;
}}

QPushButton {{
    background-color: {SURFACE_ELEVATED};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
    padding: 6px 12px;
    font-size: {FONT_SIZE_MD}px;
}}
QPushButton:hover {{
    background-color: {SURFACE_HOVER};
    border-color: {PRIMARY};
}}
QPushButton:pressed {{
    background-color: {PRIMARY};
    color: {TEXT_ON_PRIMARY};
}}
QPushButton:disabled {{
    background-color: {SURFACE};
    color: {TEXT_TERTIARY};
    border-color: {BORDER};
}}

QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {SURFACE};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 6px 10px;
    font-size: {FONT_SIZE_MD}px;
    selection-background-color: {PRIMARY};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {PRIMARY};
}}

QListWidget, QTreeWidget, QTableWidget {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    outline: none;
}}
QListWidget::item, QTreeWidget::item, QTableWidget::item {{
    padding: 6px 8px;
    border-bottom: 1px solid {BORDER};
}}
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {{
    background-color: {PRIMARY};
    color: {TEXT_ON_PRIMARY};
}}
QListWidget::item:hover, QTreeWidget::item:hover, QTableWidget::item:hover {{
    background-color: {SURFACE_HOVER};
}}

QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    background: {SURFACE};
}}
QTabBar::tab {{
    background: {BACKGROUND};
    color: {TEXT_SECONDARY};
    padding: 6px 14px;
    border: 1px solid transparent;
    border-bottom: none;
    border-top-left-radius: {RADIUS_SM}px;
    border-top-right-radius: {RADIUS_SM}px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background: {SURFACE};
    color: {TEXT_PRIMARY};
    border-color: {BORDER};
}}
QTabBar::tab:hover {{
    color: {TEXT_PRIMARY};
}}

QProgressBar {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    text-align: center;
    color: {TEXT_PRIMARY};
    height: 14px;
}}
QProgressBar::chunk {{
    background: {PRIMARY};
    border-radius: 3px;
}}

QMenuBar {{
    background: {BACKGROUND};
    color: {TEXT_PRIMARY};
    border-bottom: 1px solid {BORDER};
    padding: 2px 4px;
}}
QMenuBar::item {{
    padding: 4px 10px;
    background: transparent;
    border-radius: {RADIUS_SM}px;
}}
QMenuBar::item:selected {{
    background: {SURFACE_HOVER};
}}
QMenu {{
    background: {SURFACE_ELEVATED};
    border: 1px solid {BORDER};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 24px;
    border-radius: {RADIUS_SM}px;
}}
QMenu::item:selected {{
    background: {PRIMARY};
    color: {TEXT_ON_PRIMARY};
}}
"""


def apply(app) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet(_SHARED_QSS)
