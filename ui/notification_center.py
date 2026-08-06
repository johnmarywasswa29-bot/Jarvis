"""Jarvis notification center."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QPropertyAnimation, QEasingCurve, QPoint, QTimer
from PySide6.QtGui import Qt
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget

from ui.theme import (
    BORDER,
    ERROR,
    PRIMARY,
    SPACING_MD,
    SPACING_SM,
    SURFACE_ELEVATED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WARNING,
)


class NotificationCenter:
    def __init__(self, parent: Optional[QWidget] = None):
        self._parent = parent or QApplication.activeWindow()
        self._frame = QFrame(self._parent, Qt.WindowFlags())
        self._frame.setWindowFlags(Qt.WindowFlags(Qt.FramelessWindowHint | Qt.SubWindow))
        self._frame.setAttribute(Qt.WA_TranslucentBackground)
        self._items = QVBoxLayout(self._frame)
        self._items.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        self._items.setSpacing(SPACING_SM)
        self._frame.resize(360, 120)
        self._move_offscreen()

    def setVisible(self, visible: bool) -> None:
        self._frame.setVisible(visible)

    def _move_offscreen(self):
        geo = self._parent.geometry()
        self._frame.move(geo.right() - 400, geo.top() + 80)

    def show_notification(self, message: str, level: str = "info"):
        item = QLabel(message)
        item.setWordWrap(True)
        item.setStyleSheet(
            f"QLabel {{ background: {SURFACE_ELEVATED}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER}; border-radius: 10px; padding: 10px 12px; }}"
        )
        color = TEXT_SECONDARY
        if level == "error":
            color = ERROR
        elif level == "warning":
            color = WARNING
        elif level == "success":
            color = "#3DDC84"
        item.setStyleSheet(
            item.styleSheet()
            + f" border-left: 4px solid {color};"
        )
        self._items.addWidget(item)
        self._frame.show()
        self._frame.raise_()

        remover = QTimer(self._frame)
        remover.setInterval(4000)
        remover.setSingleShot(True)
        remover.timeout.connect(lambda: self._remove(item))
        remover.start()

    def _remove(self, item):
        try:
            self._items.removeWidget(item)
            item.deleteLater()
        except Exception:
            pass
        if self._items.count() == 0:
            self._frame.hide()
