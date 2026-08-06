"""Jarvis task panel."""
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


class TaskPanel(QFrame):
    taskAction = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        root.setSpacing(SPACING_MD)

        header = QHBoxLayout()
        title = QLabel("Running Tasks")
        title.setStyleSheet(f"font-size: 16px; font-weight: 600; border: none; background: transparent; color: {TEXT_PRIMARY};")
        header.addWidget(title)
        header.addStretch(1)
        root.addLayout(header)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background: {SURFACE}; color: {TEXT_SECONDARY}; border: 1px solid {BORDER}; border-radius: 6px; }}"
            f"QListWidget::item {{ padding: 8px 10px; border-bottom: 1px solid {BORDER}; }}"
            f"QListWidget::item:selected {{ background: {PRIMARY}; color: #fff; }}"
        )
        root.addWidget(self._list, 1)

    def set_tasks(self, tasks) -> None:
        self._list.clear()
        for task in tasks:
            label = getattr(task, "title", None) or getattr(task, "name", None) or getattr(task, "id", "task")
            status = getattr(task, "status", "queued")
            item = QListWidgetItem(f"{label}  [{status}]")
            item.setForeground(Qt.GlobalColor.lightGray if status in {"queued", "ready"} else Qt.GlobalColor.white)
            self._list.addItem(item)
