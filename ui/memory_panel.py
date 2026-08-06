"""Jarvis memory panel."""
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
    QTextEdit,
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


class MemoryPanel(QFrame):
    memorySelected = Signal(str)
    memoryDeleted = Signal(str)
    memoryEdited = Signal(str, str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        root.setSpacing(SPACING_MD)

        title = QLabel("Memory")
        title.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {TEXT_PRIMARY}; background: transparent; border: none;")
        root.addWidget(title)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background: {SURFACE}; color: {TEXT_SECONDARY}; border: 1px solid {BORDER}; border-radius: 6px; }}"
            f"QListWidget::item {{ padding: 8px; border-bottom: 1px solid {BORDER}; }}"
            f"QListWidget::item:selected {{ background: {PRIMARY}; color: #fff; }}"
        )
        self._list.itemClicked.connect(self._on_select)
        root.addWidget(self._list, 1)

        self._editor = QTextEdit()
        self._editor.setPlaceholderText("Edit memory content...")
        self._editor.setStyleSheet(f"QTextEdit {{ background: {SURFACE}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER}; border-radius: 6px; }}")
        root.addWidget(self._editor, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addWidget(QPushButton("Save"))
        actions.addWidget(QPushButton("Delete"))
        actions.addStretch(1)
        root.addLayout(actions)

    def add_memory(self, memory_id: str, summary: str) -> None:
        QListWidgetItem(summary, self._list).setData(Qt.UserRole, memory_id)

    def _on_select(self, item: QListWidgetItem) -> None:
        memory_id = item.data(Qt.UserRole)
        self.memorySelected.emit(str(memory_id))
        self._editor.setText(item.text())
