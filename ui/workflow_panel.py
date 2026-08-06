"""WorkflowPanel: real-time workflow view with progress, cancel, pause, resume, retry."""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot
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

from workflows.state import WorkflowState, WorkflowStep, StepStatus
from workflows.manager import WorkflowManager


class _RunWorkerSignals(QObject):
    step_update = Signal(object)
    finished = Signal(object)
    failed = Signal(str)


class _RunWorker(QThread):
    def __init__(self, mgr: WorkflowManager, state: WorkflowState, parent=None):
        super().__init__(parent)
        self._mgr = mgr
        self._state = state
        self.signals = _RunWorkerSignals(parent=self)

    def run(self):
        try:
            state = self._state
            for idx, step in enumerate(state.steps):
                if self.isInterruptionRequested():
                    break
                step.status = StepStatus.RUNNING
                step.updated_at = self._now()
                self.signals.step_update.emit(state)
                result = self._mgr.run(state)
                state = result
                self.signals.step_update.emit(state)
                if state.status in {StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.CANCELLED}:
                    break
            self.signals.finished.emit(state)
        except Exception as exc:
            self.signals.failed.emit(f"{exc}")

    @staticmethod
    def _now() -> str:
        from datetime import datetime, UTC
        return datetime.now(UTC).replace(tzinfo=None).isoformat()


class WorkflowPanel(QWidget):
    def __init__(self, mgr: Optional[WorkflowManager] = None, parent=None):
        super().__init__(parent)
        self.mgr = mgr
        self.state: Optional[WorkflowState] = None
        self.worker: Optional[_RunWorker] = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        self.title = QLabel("Workflows")
        self.title.setStyleSheet("font-size:16px; font-weight:600;")
        header.addWidget(self.title)
        header.addStretch(1)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh_list)
        header.addWidget(self.refresh_btn)
        root.addLayout(header)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        root.addWidget(self.progress)

        meta = QHBoxLayout()
        self.status_label = QLabel("Idle")
        self.step_label = QLabel("-")
        self.eta_label = QLabel("-")
        meta.addWidget(self.status_label)
        meta.addWidget(self.step_label)
        meta.addWidget(self.eta_label)
        root.addLayout(meta)

        actions = QHBoxLayout()
        self.run_btn = QPushButton("Run demo")
        self.run_btn.clicked.connect(self._run_demo)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self._pause)
        self.resume_btn = QPushButton("Resume")
        self.resume_btn.clicked.connect(self._resume)
        self.retry_btn = QPushButton("Retry failed")
        self.retry_btn.clicked.connect(self._retry_failed)
        for b in [self.run_btn, self.cancel_btn, self.pause_btn, self.resume_btn, self.retry_btn]:
            actions.addWidget(b)
        root.addLayout(actions)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        root.addWidget(self.log)

    def _append(self, text: str):
        self.log.append(text)

    def set_manager(self, mgr: WorkflowManager):
        self.mgr = mgr

    def _refresh_list(self):
        if not self.mgr:
            return
        workflows = self.mgr.list_workflows()
        self._append(f"Loaded {len(workflows)} workflows")
        for wf in workflows[-10:]:
            self._append(f"- {wf.get('name')} :: {wf.get('status')}")

    def _run_demo(self):
        if not self.mgr:
            self._append("No WorkflowManager")
            return
        self._append("Planning demo workflow...")
        t0 = self._now_float()
        state = self.mgr.create("demo workflow")
        self.state = state
        elapsed = self._now_float() - t0
        self._append(f"Planned in {elapsed*1000:.1f} ms; steps={len(state.steps)}")
        self._launch_run(state)

    def _launch_run(self, state: WorkflowState):
        if self.worker and self.worker.isRunning():
            self._append("Already running")
            return
        self.worker = _RunWorker(self.mgr, state)
        self.worker.signals.step_update.connect(self._on_step_update)
        self.worker.signals.finished.connect(self._on_run_finished)
        self.worker.signals.failed.connect(self._on_run_failed)
        self.worker.start()
        self._append("Run started")

    def _on_step_update(self, state: WorkflowState):
        self.state = state
        done = sum(1 for s in state.steps if s.status in {StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.CANCELLED})
        total = max(len(state.steps), 1)
        self.progress.setValue(int(done / total * 100))
        current = next((s for s in state.steps if s.status == StepStatus.RUNNING), None)
        self.step_label.setText(f"Step {done+1}/{total}")
        self.status_label.setText(state.status.name)
        if current:
            self._append(f"Running: {current.description or current.tool}")
        failed = next((s for s in state.steps if s.status == StepStatus.FAILED), None)
        if failed:
            self._append(f"Failed: {failed.tool}: {failed.error}")

    def _on_run_finished(self, state: WorkflowState):
        self.state = state
        self.progress.setValue(100 if state.status == StepStatus.COMPLETED else self.progress.value())
        self.status_label.setText(state.status.name)
        self._append(f"Workflow finished: {state.status.name}")

    def _on_run_failed(self, err: str):
        self._append(f"Run error: {err}")

    def _cancel(self):
        if not self.state or not self.mgr:
            return
        self.mgr.cancel(self.state.workflow_id)
        self._append("Cancelled")

    def _pause(self):
        self._append("Pause requested; future hook")

    def _resume(self):
        self._append("Resume requested; future hook")

    def _retry_failed(self):
        if not self.state or not self.mgr:
            return
        failed = next((s for s in self.state.steps if s.status == StepStatus.FAILED), None)
        if not failed:
            self._append("No failed step")
            return
        failed.status = StepStatus.PENDING
        failed.retry_count = 0
        failed.error = ""
        self._launch_run(self.state)
        self._append("Retrying failed step")

    @staticmethod
    def _now_float() -> float:
        import time
        return time.perf_counter()
