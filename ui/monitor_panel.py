"""Monitor panel: live workspace/system state display."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QFrame, QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from ui.theme import BORDER, SPACING_MD, SPACING_SM, SURFACE, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_TERTIARY


class MonitorPanel(QFrame):
    stateChanged = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._signals = _MonitorSignals()
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        root.setSpacing(SPACING_MD)

        title = QLabel("Monitor")
        title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 16px; font-weight: 600; border: none; background: transparent;")
        root.addWidget(title)

        self._workspace_section = _kv_section("Workspace")
        self._git_section = _kv_section("Git")
        self._goals_section = _kv_section("Goals")
        self._tasks_section = _kv_section("Tasks")
        self._ollama_section = _kv_section("Ollama")
        self._mic_section = _kv_section("Microphone")
        self._resources_section = _kv_section("Resources")

        for sec in [
            self._workspace_section,
            self._git_section,
            self._goals_section,
            self._tasks_section,
            self._ollama_section,
            self._mic_section,
            self._resources_section,
        ]:
            root.addWidget(sec)

        root.addStretch(1)

        self._signals.stateChanged.connect(self._on_state)

    def _on_state(self, state: str):
        lines = [line.strip() for line in state.split("\n") if line.strip()]
        mapping = {
            "Workspace": self._workspace_section._list,
            "Git": self._git_section._list,
            "Goals": self._goals_section._list,
            "Tasks": self._tasks_section._list,
            "Ollama": self._ollama_section._list,
            "Microphone": self._mic_section._list,
            "Resources": self._resources_section._list,
        }
        for line in lines:
            prefix = line.split(":")[0] if ":" in line else ""
            target = mapping.get(prefix)
            if target is None:
                continue
            QListWidgetItem(line, target)

    def set_microphone_state(self, state: str):
        self._mic_section._list.clear()
        QListWidgetItem(f"Microphone: {state}", self._mic_section._list)


class _MonitorSignals(QObject):
    stateChanged = Signal(str)


class _kv_section(QFrame):
    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        self._header = QLabel(title)
        self._header.setStyleSheet(f"color: {TEXT_TERTIARY}; font-size: 11px; border: none; background: transparent;")
        root.addWidget(self._header)
        self._list = QListWidget()
        self._list.setStyleSheet(f"QListWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; }} QListWidget::item {{ padding: 4px 8px; color: {TEXT_SECONDARY}; }}")
        root.addWidget(self._list, 0)
