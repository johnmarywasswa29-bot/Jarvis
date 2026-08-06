"""Jarvis desktop sidebar."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.theme import BORDER, SURFACE, SURFACE_ELEVATED, SURFACE_HOVER, TEXT_PRIMARY, TEXT_SECONDARY


class Sidebar(QFrame):
    """Left navigation sidebar."""
    sectionChanged = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(220)
        self.setFrameShape(QFrame.StyledPanel)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        title = QLabel("Jarvis")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-weight: 700; font-size: 18px; padding: 14px 0; background: {SURFACE_ELEVATED}; border-bottom: 1px solid {BORDER};"
        )
        root.addWidget(title)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background: {SURFACE}; border: none; }} "
            f"QListWidget::item {{ padding: 10px 14px; color: {TEXT_SECONDARY}; border: none; }} "
            f"QListWidget::item:selected {{ background: {SURFACE_HOVER}; color: {TEXT_PRIMARY}; }}"
        )
        self._list.currentRowChanged.connect(self._emit_section)

        sections = [
            ("chat", "Chat"),
            ("goals", "Goals"),
            ("tasks", "Tasks"),
            ("knowledge", "Knowledge"),
            ("memory", "Memory"),
            ("activity", "Activity"),
            ("workspace", "Workspace"),
            ("monitor", "Monitor"),
            ("settings", "Settings"),
        ]
        self._items: dict[str, int] = {}
        for idx, (key, label) in enumerate(sections):
            QListWidgetItem(label, self._list)
            self._items[key] = idx

        root.addWidget(self._list, 1)

    def _emit_section(self, row: int) -> None:
        for key, idx in self._items.items():
            if idx == row:
                self.sectionChanged.emit(key)
                return

    def set_active(self, key: str) -> None:
        idx = self._items.get(key)
        if idx is not None:
            self._list.setCurrentRow(idx)

    def current_section(self) -> str:
        row = self._list.currentRow()
        for key, idx in self._items.items():
            if idx == row:
                return key
        return next(iter(self._items), "chat")
