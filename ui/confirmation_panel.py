"""Jarvis confirmation panel."""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui.theme import (
    BORDER,
    ERROR,
    PRIMARY,
    SPACING_MD,
    SPACING_SM,
    SUCCESS,
    SURFACE,
    SURFACE_ELEVATED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WARNING,
)


class ConfirmationCard(QWidget):
    approveRequested = Signal(str)
    rejectRequested = Signal(str)

    def __init__(self, item: dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._confirmation_id = item.get("confirmation_id", "")
        self._build_ui(item)

    def _build_ui(self, item: dict[str, Any]) -> None:
        self.setObjectName("confirmation_card")
        root = QHBoxLayout(self)
        root.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        root.setSpacing(SPACING_MD)

        info = QVBoxLayout()
        info.setSpacing(SPACING_SM)
        info.setContentsMargins(0, 0, 0, 0)

        title = QLabel(item.get("objective", "Untitled proposal"))
        title.setWordWrap(True)
        title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: 600; font-size: 14px;")
        info.addWidget(title)

        meta = QLabel(
            f"Tool: {item.get('action_tool', '')} | Risk: {item.get('risk_level', '')} | Status: {item.get('step_status', '')}"
        )
        meta.setWordWrap(True)
        meta.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
        info.addWidget(meta)

        params = item.get("action_parameters")
        if params:
            param_text = ", ".join(f"{k}={v}" for k, v in params.items())
            param_label = QLabel(param_text)
            param_label.setWordWrap(True)
            param_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
            info.addWidget(param_label)

        refs = item.get("source_references", [])
        if refs:
            ref = refs[0]
            ref_text = f"Source: {ref.get('identifier', '')}"
            if ref.get("excerpt"):
                ref_text += f" — {ref['excerpt'][:120]}"
            ref_label = QLabel(ref_text)
            ref_label.setWordWrap(True)
            ref_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px; font-style: italic;")
            info.addWidget(ref_label)

        errors = item.get("validation_errors", [])
        if errors:
            err_text = "; ".join(errors)
            err_label = QLabel(f"Validation: {err_text}")
            err_label.setWordWrap(True)
            err_label.setStyleSheet(f"color: {ERROR}; font-size: 11px;")
            info.addWidget(err_label)

        root.addLayout(info, 1)

        actions = QVBoxLayout()
        actions.setSpacing(SPACING_SM)
        actions.setContentsMargins(0, 0, 0, 0)

        approve_btn = QPushButton("Approve")
        approve_btn.setCursor(Qt.PointingHandCursor)
        approve_btn.setStyleSheet(
            f"QPushButton {{ background: {SUCCESS}; color: #fff; border: none; padding: 8px 14px; border-radius: 8px; font-weight: 600; }}"
            f"QPushButton:disabled {{ background: {BORDER}; color: {TEXT_SECONDARY}; }}"
        )
        approve_btn.clicked.connect(lambda: self.approveRequested.emit(self._confirmation_id))
        actions.addWidget(approve_btn)

        reject_btn = QPushButton("Reject")
        reject_btn.setCursor(Qt.PointingHandCursor)
        reject_btn.setStyleSheet(
            f"QPushButton {{ background: {ERROR}; color: #fff; border: none; padding: 8px 14px; border-radius: 8px; font-weight: 600; }}"
            f"QPushButton:disabled {{ background: {BORDER}; color: {TEXT_SECONDARY}; }}"
        )
        reject_btn.clicked.connect(lambda: self.rejectRequested.emit(self._confirmation_id))
        actions.addWidget(reject_btn)

        root.addLayout(actions)

    def confirmation_id(self) -> str:
        return self._confirmation_id

    def set_in_flight(self, in_flight: bool) -> None:
        for btn in self.findChildren(QPushButton):
            btn.setDisabled(in_flight)


class ConfirmationPanel(QWidget):
    approveRequested = Signal(str)
    rejectRequested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._cards: dict[str, ConfirmationCard] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        root.setSpacing(SPACING_MD)

        header = QLabel("Confirmations")
        header.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: 700; font-size: 16px;")
        root.addWidget(header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        container = QWidget()
        self._list = QVBoxLayout(container)
        self._list.setSpacing(SPACING_MD)
        self._scroll.setWidget(container)
        root.addWidget(self._scroll, 1)

        self._empty = QLabel("No pending confirmations.")
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 13px;")
        root.addWidget(self._empty)

    def _refresh_empty(self) -> None:
        self._empty.setVisible(not self._cards)

    def add_pending(self, item: dict[str, Any]) -> None:
        confirmation_id = item.get("confirmation_id", "")
        if confirmation_id in self._cards:
            return
        card = ConfirmationCard(item)
        card.approveRequested.connect(self._on_approve)
        card.rejectRequested.connect(self._on_reject)
        self._cards[confirmation_id] = card
        self._list.addWidget(card)
        self._refresh_empty()

    def remove_pending(self, confirmation_id: str) -> None:
        card = self._cards.pop(confirmation_id, None)
        if card is None:
            return
        try:
            self._list.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        except Exception:
            pass
        self._refresh_empty()

    def set_in_flight(self, confirmation_id: str, in_flight: bool) -> None:
        card = self._cards.get(confirmation_id)
        if card is not None:
            card.set_in_flight(in_flight)

    def clear(self) -> None:
        for card in list(self._cards.values()):
            try:
                self._list.removeWidget(card)
                card.setParent(None)
                card.deleteLater()
            except Exception:
                pass
        self._cards.clear()
        self._refresh_empty()

    def _on_approve(self, confirmation_id: str) -> None:
        self.approveRequested.emit(confirmation_id)

    def _on_reject(self, confirmation_id: str) -> None:
        self.rejectRequested.emit(confirmation_id)
