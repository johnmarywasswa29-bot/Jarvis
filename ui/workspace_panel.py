"""Workspace panel UI."""
from __future__ import annotations

from typing import Optional

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

from workspace.workspace_manager import WorkspaceManager


class WorkspacePanel(QWidget):
    def __init__(self, mgr: Optional[WorkspaceManager] = None, parent=None):
        super().__init__(parent)
        self.mgr = mgr
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        self.title = QLabel("Workspace")
        self.title.setStyleSheet("font-size:16px; font-weight:600;")
        header.addWidget(self.title)
        header.addStretch(1)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self.refresh_btn)
        root.addLayout(header)

        meta = QHBoxLayout()
        self.project_label = QLabel("Project: -")
        self.folder_label = QLabel("Folder: -")
        self.git_label = QLabel("Git: -")
        self.confidence_label = QLabel("Confidence: -")
        for w in [self.project_label, self.folder_label, self.git_label, self.confidence_label]:
            meta.addWidget(w)
        root.addLayout(meta)

        apps = QHBoxLayout()
        self.apps_label = QLabel("Apps: -")
        apps.addWidget(self.apps_label)
        root.addLayout(apps)

        self.history_list = QListWidget()
        root.addWidget(self.history_list)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        root.addWidget(self.log)

    def set_manager(self, mgr: WorkspaceManager):
        self.mgr = mgr

    def refresh(self):
        if not self.mgr:
            self.log.append("No WorkspaceManager")
            return
        snap = self.mgr.snapshot()
        project = self.mgr.current_project()
        self.project_label.setText(f"Project: {project.name if project else '-'}")
        self.folder_label.setText(f"Folder: {snap.working_directory if snap else '-'}")
        self.git_label.setText(f"Git: {snap.git_repository if snap else '-'}")
        self.confidence_label.setText(f"Confidence: {snap.confidence:.2f}" if snap else "Confidence: -")
        self.apps_label.setText(f"Apps: {', '.join(snap.open_applications) if snap else '-'}")
        recent = self.mgr.recent_projects()
        self.history_list.clear()
        for p in recent[:20]:
            self.history_list.addItem(f"{p.name} [{p.language}] {p.git_repo}")
        self.log.append(f"Refreshed {len(recent)} recent projects")
