"""Jarvis activity panel."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.theme import (
    BORDER,
    PRIMARY,
    SPACING_MD,
    SURFACE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_TERTIARY,
)


class ActivityPanel(QFrame):
    itemSelected = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        root.setSpacing(SPACING_MD)

        title = QLabel("Activity")
        title.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {TEXT_PRIMARY}; border: none; background: transparent;")
        root.addWidget(title)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(QPushButton("Clear"))
        root.addLayout(controls)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background: {SURFACE}; color: {TEXT_SECONDARY}; border: 1px solid {BORDER}; border-radius: 6px; }}"
            f"QListWidget::item {{ padding: 8px; border-bottom: 1px solid {BORDER}; }}"
            f"QListWidget::item:selected {{ background: {PRIMARY}; color: #fff; }}"
        )
        root.addWidget(self._list, 1)

    def append_activity(self, source: str, message: str, level: str = "info") -> None:
        color = TEXT_TERTIARY
        if level == "error":
            color = "#FF5A5A"
        elif level == "warning":
            color = "#FFB84D"
        elif level == "success":
            color = "#3DDC84"
        item = QListWidgetItem(f"[{source}] {message}")
        item.setForeground(Qt.GlobalColor.white if level == "error" else Qt.GlobalColor.lightGray)
        self._list.addItem(item)

    def clear(self) -> None:
        self._list.clear()
