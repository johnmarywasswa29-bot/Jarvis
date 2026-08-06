"""Jarvis goal panel."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
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


class GoalPanel(QFrame):
    goalUpdated = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        root.setSpacing(SPACING_MD)
        title = QLabel("Current Goal")
        title.setStyleSheet(f"font-size: 16px; font-weight: 600; border: none; background: transparent; color: {TEXT_PRIMARY};")
        root.addWidget(title)

        self._goal_label = QLabel("No active goal")
        self._plan_text = QTextEdit()
        self._plan_text.setReadOnly(True)
        self._plan_text.setStyleSheet(f"QTextEdit {{ background: {SURFACE}; color: {TEXT_SECONDARY}; border: 1px solid {BORDER}; border-radius: 6px; }}")
        self._progress = QProgressBar()
        self._progress.setValue(0)
        self._stats = QLabel("")
        self._stats.setStyleSheet(f"color: {TEXT_TERTIARY}; font-size: 12px; border: none; background: transparent;")
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        for label, fn in [("Pause", lambda: None), ("Resume", lambda: None), ("Cancel", lambda: None)]:
            b = QPushButton(label)
            b.clicked.connect(fn)
            actions.addWidget(b)
        actions.addStretch(1)

        root.addWidget(self._goal_label)
        root.addWidget(self._plan_text, 1)
        root.addWidget(self._progress)
        root.addWidget(self._stats)
        root.addLayout(actions)

    def set_goal(self, goal) -> None:
        title = getattr(goal, "title", None) or getattr(goal, "name", None) or "Unnamed goal"
        progress = getattr(goal, "progress", None) or 0
        tasks = getattr(goal, "tasks", None) or []
        completed = sum(1 for t in tasks if getattr(t, "status", None) == "completed")
        self._goal_label.setText(title)
        try:
            pct = int(progress * 100)
        except Exception:
            pct = 0
        self._progress.setValue(max(0, min(100, pct)))
        self._stats.setText(f"{completed} of {len(tasks)} tasks completed")
        plan = getattr(goal, "plan", None) or ""
        self._plan_text.setText(plan)
