"""Jarvis knowledge panel."""
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
)


class KnowledgePanel(QFrame):
    documentSelected = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        root.setSpacing(SPACING_MD)

        title = QLabel("Knowledge Base")
        title.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {TEXT_PRIMARY}; background: transparent; border: none;")
        root.addWidget(title)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        self._search = QTextEdit()
        self._search.setPlaceholderText("Search knowledge...")
        self._search.setFixedHeight(36)
        self._search.setStyleSheet(f"QTextEdit {{ background: {SURFACE}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px; }}")
        controls.addWidget(self._search, 1)
        for label in ["Re-index", "Remove"]:
            controls.addWidget(QPushButton(label))
        root.addLayout(controls)

        self._list = QListWidget()
        self._list.setStyleSheet(f"QListWidget {{ background: {SURFACE}; color: {TEXT_SECONDARY}; border: 1px solid {BORDER}; border-radius: 6px; }} QListWidget::item {{ padding: 8px; border-bottom: 1px solid {BORDER}; }} QListWidget::item:selected {{ background: {PRIMARY}; color: #fff; }}")
        self._list.itemClicked.connect(self._on_select)
        root.addWidget(self._list, 1)

        self._preview = QTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setStyleSheet(f"QTextEdit {{ background: {SURFACE}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER}; border-radius: 6px; }}")
        root.addWidget(self._preview, 1)

    def add_document(self, doc_id: str, title: str) -> None:
        QListWidgetItem(title, self._list).setData(Qt.UserRole, doc_id)

    def _on_select(self, item: QListWidgetItem) -> None:
        doc_id = item.data(Qt.UserRole)
        self.documentSelected.emit(str(doc_id))
        self._preview.setText(f"Document: {item.text()}\n\nPreview placeholder for {doc_id}.")
