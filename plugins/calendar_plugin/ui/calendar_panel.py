"""Calendar plugin UI panel."""
from __future__ import annotations

from typing import Any

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QListWidget, QPushButton


class CalendarPanel(QWidget):
    def __init__(self, plugin: Any = None, parent: Any = None) -> None:
        super().__init__(parent)
        self.plugin = plugin
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Calendar Plugin"))
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        layout.addWidget(refresh_btn)

    def refresh(self) -> None:
        self.list_widget.clear()
        if not self.plugin:
            return
        try:
            events = self.plugin.scheduler.today()
            for event in events:
                self.list_widget.addItem(f"{event.start} {event.title}")
        except Exception:
            pass
