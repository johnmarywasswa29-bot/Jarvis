"""Jarvis chat panel."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ui.theme import (
    BORDER,
    CHAT_ASSISTANT_BUBBLE,
    CHAT_USER_BUBBLE,
    CODE_BG,
    PRIMARY,
    SPACING_MD,
    SPACING_SM,
    SURFACE,
    TEXT_ON_PRIMARY,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_TERTIARY,
)

BUBBLE_USER = f"""
border-radius: 14px;
padding: 10px 14px;
background: {CHAT_USER_BUBBLE};
color: #fff;
font-size: 14px;
border: none;
"""

BUBBLE_ASSISTANT = f"""
border-radius: 14px;
padding: 10px 14px;
background: {CHAT_ASSISTANT_BUBBLE};
color: {TEXT_PRIMARY};
border: 1px solid {BORDER};
font-size: 14px;
"""

FOCUS_BORDER = f"QTextEdit:focus {{ border-color: {PRIMARY}; }}"


class ChatPanel(QFrame):
    sendMessage = Signal(str)
    regenerateRequested = Signal()
    stopGenerationRequested = Signal()
    uploadFileRequested = Signal()

    appendAssistant = Signal(str, bool)
    appendUser = Signal(str)
    appendSystem = Signal(str)

    def __init__(self, parent=None, *, primary=None, secondary=None, tertiary=None):
        super().__init__(parent)
        if primary is None:
            primary = TEXT_PRIMARY
        if secondary is None:
            secondary = TEXT_SECONDARY
        if tertiary is None:
            tertiary = TEXT_TERTIARY
        self._theme = {"primary": primary, "secondary": secondary, "tertiary": tertiary}
        self._build_ui()

        self.appendAssistant.connect(self._append_bubble)
        self.appendUser.connect(self.append_user)
        self.appendSystem.connect(self.append_system)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        root.setSpacing(SPACING_SM)

        header = QHBoxLayout()
        title = QLabel("Conversation")
        title.setObjectName("title")
        title_word = QLabel("Conversation")
        title.setStyleSheet("font-size: 16px; font-weight: 600; color: transparent; background: transparent; border: none;")
        title_word.setText("Conversation")
        header.addWidget(title)
        header.addStretch(1)
        root.addLayout(header)

        self._history = QScrollArea()
        self._history.setObjectName("chat_history")
        self._history.setWidgetResizable(True)
        self._history.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        container = QWidget()
        self._history_layout = QVBoxLayout(container)
        self._history_layout.setObjectName("history_layout")
        self._history_layout.setContentsMargins(0, 0, 0, 0)
        self._history_layout.setSpacing(SPACING_SM)
        self._history_layout.addStretch(1)
        self._history.setWidget(container)
        root.addWidget(self._history, 1)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(SPACING_SM)

        self._regenerate_btn = self._icon(controls, "Regenerate")
        self._stop_btn = self._icon(controls, "Stop")
        self._attach_btn = self._icon(controls, "Attach")

        controls.addStretch(1)
        root.addLayout(controls)

        self._input = QTextEdit()
        self._input.setObjectName("chat_input")
        self._input.setPlaceholderText("Message Jarvis...")
        self._input.setFixedHeight(80)
        self._input.setStyleSheet(
            f"QTextEdit {{ background: {SURFACE}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER}; border-radius: 12px; padding: 10px; }}"
            f"QTextEdit:focus {{ border-color: {PRIMARY}; }}"
        )
        root.addWidget(self._input)

        send_row = QHBoxLayout()
        send_row.addStretch(1)
        send_btn = QPushButton("Send")
        send_btn.setStyleSheet(f"background: {PRIMARY}; color: {TEXT_ON_PRIMARY}; border: none; padding: 8px 18px; border-radius: 10px; font-weight: 600;")
        send_btn.clicked.connect(self._on_send)
        send_row.addWidget(send_btn)
        root.addLayout(send_row)

        self._input.installEventFilter(self)

    def _last_history_item(self):
        items = self._history_layout
        if items is None:
            return None
        count = items.count()
        if count == 0:
            return None
        return items.itemAt(count - 1)

    def _scroll_to_bottom(self):
        self._history.verticalScrollBar().setValue(self._history.verticalScrollBar().maximum())

    def _append_bubble(self, text: str, streaming: bool = False) -> None:
        widget = self._bubble(text, role="assistant" if not streaming else "assistant", streaming=streaming)
        self._history_layout.insertWidget(self._history_layout.count() - 1, widget)
        self._scroll_to_bottom()

    def append_assistant(self, text: str, streaming: bool = False):
        widget = self._bubble(text, role="assistant", streaming=streaming)
        self._history_layout.insertWidget(self._history_layout.count() - 1, widget)
        self._scroll_to_bottom()

    def append_user(self, text: str):
        widget = self._bubble(text, role="user")
        self._history_layout.insertWidget(self._history_layout.count() - 1, widget)
        self._scroll_to_bottom()

    def append_system(self, text: str):
        w = QLabel(f"<!-- {text} -->")
        style = f"color: {self._theme['tertiary']}; font-size: 11px; background: transparent; border: none;"
        if hasattr(w, "setStyleSheet"):
            w.setStyleSheet(style)
        self._history_layout.insertWidget(self._history_layout.count() - 1, w)
        return w

    def _bubble(self, text, *, role="assistant", streaming=False):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        style = BUBBLE_USER if role == "user" else BUBBLE_ASSISTANT
        label.setStyleSheet(style)
        label.setFixedWidth(520 if role == "user" else 640)
        return label

    def _icon(self, layout, text):
        btn = QPushButton(text)
        btn.setFlat(True)
        btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {TEXT_SECONDARY}; border: 1px solid transparent; border-radius: 16px; padding: 6px 10px; }} QPushButton:hover {{ color: {PRIMARY}; border-color: {BORDER}; }}")
        layout.addWidget(btn)
        return btn

    def _on_send(self):
        text = self._input.toPlainText().strip()
        if not text:
            return
        self.sendMessage.emit(text)
        self._input.clear()

    def eventFilter(self, obj, ev):
        if obj is self._input and ev.type() == ev.Type.KeyPress:
            from PySide6.QtGui import QKeyEvent
            kev = ev.type()
            if hasattr(ev, 'key') and ev.key() == Qt.Key_Return and (ev.modifiers() & Qt.ShiftModifier) == Qt.NoModifier:
                return True
        return super().eventFilter(obj, ev)
