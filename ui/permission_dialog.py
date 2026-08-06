"""Jarvis permission dialog."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ui.theme import (
    BORDER,
    ERROR,
    PRIMARY,
    SURFACE_ELEVATED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_TERTIARY,
    WARNING,
)


class PermissionDialog(QDialog):
    def __init__(self, action: str, details: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Permission required")
        self.setModal(True)
        self.setFixedWidth(520)
        self.setStyleSheet(f"QDialog {{ background: {SURFACE_ELEVATED}; border: 1px solid {BORDER}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        title = QLabel("Permission request")
        title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 16px; font-weight: 600; border: none; background: transparent;")
        root.addWidget(title)

        desc = QLabel(f"Action: {action}")
        desc.setStyleSheet(f"color: {TEXT_SECONDARY}; border: none; background: transparent;")
        root.addWidget(desc)

        detail_box = QTextEdit()
        detail_box.setReadOnly(True)
        detail_box.setText(details)
        detail_box.setStyleSheet(f"QTextEdit {{ background: {SURFACE_ELEVATED}; color: {TEXT_TERTIARY}; border: 1px solid {BORDER}; border-radius: 8px; padding: 10px; }}")
        root.addWidget(detail_box, 1)

        warning = QLabel("Review carefully before approving. This action may affect your system.")
        warning.setStyleSheet(f"color: {WARNING}; border: none; background: transparent;")
        root.addWidget(warning)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        deny = QPushButton("Deny")
        deny.setStyleSheet(f"QPushButton {{ background: transparent; color: {ERROR}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 18px; }} QPushButton:hover {{ border-color: {ERROR}; }}")
        allow = QPushButton("Allow")
        allow.setDefault(True)
        allow.setStyleSheet(f"QPushButton {{ background: {PRIMARY}; color: #FFFFFF; border: none; border-radius: 8px; padding: 8px 18px; font-weight: 600; }}")
        deny.clicked.connect(self.reject)
        allow.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(deny)
        buttons.addWidget(allow)
        root.addLayout(buttons)

        self._deny = deny
        self._allow = allow

    def auto_deny(self) -> bool:
        return False

    def confirm(self) -> bool:
        return self.exec() == QDialog.Accepted
