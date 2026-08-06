"""Jarvis settings panel."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
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


class SettingsPanel(QFrame):
    settingsChanged = Signal(str, object)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING_MD, SPACING_MD, SPACING_MD, SPACING_MD)
        root.setSpacing(SPACING_MD)

        title = QLabel("Settings")
        title.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {TEXT_PRIMARY}; background: transparent; border: none;")
        root.addWidget(title)

        self._mic_row = None
        for label_text, hint in [
            ("Model", "llama3 + Ollama"),
            ("Voice", "System TTS device"),
            ("Memory retention", "30 days"),
            ("Theme", "Dark"),
        ]:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            label = QLabel(label_text)
            label.setStyleSheet(f"color: {TEXT_SECONDARY}; background: transparent; border: none;")
            inp = QLineEdit(hint)
            inp.setStyleSheet(f"QLineEdit {{ background: {SURFACE}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px; }} QLineEdit:focus {{ border-color: {PRIMARY}; }}")
            row.addWidget(label)
            row.addWidget(inp, 1)
            root.addLayout(row)
            if label_text == "Voice":
                try:
                    from modules.voice import VoiceModule
                    self._mic_row = row
                    if not getattr(VoiceModule, "_HAS_PYAUDIO", False):
                        label.setText("Voice")
                        inp.setText("Microphone unavailable")
                        inp.setEnabled(False)
                        inp.setToolTip("PyAudio is not installed. Voice input is disabled.")
                except Exception:
                    pass

        try:
            from modules.voice import VoiceModule
            if not getattr(VoiceModule, "_HAS_PYAUDIO", False) and self._mic_row is not None:
                label = self._mic_row.itemAt(0).widget()
                inp = self._mic_row.itemAt(1).widget()
                if label and inp:
                    label.setText("Voice")
                    inp.setText("Microphone unavailable")
                    inp.setEnabled(False)
                    inp.setToolTip("PyAudio is not installed. Voice input is disabled.")
        except Exception:
            pass
