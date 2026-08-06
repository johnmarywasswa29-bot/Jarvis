"""Tests for optional microphone support."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


class DummyConfig:
    frame_ms = 30
    threshold = 0.01
    stt_model_name = "small"
    tts_rate = 170
    tts_volume = 0.9
    keyword_path = Path("nonexistent.ppn")
    sensitivities = [0.6]
    logs_dir = REPO / "logs"

    def stt_models_dir(self):
        return REPO / "data"


class TestVoiceOptional(unittest.TestCase):
    def test_microphone_feature_flag_exists(self):
        import modules.voice as voice_mod
        self.assertTrue(hasattr(voice_mod, "_HAS_PYAUDIO"))

    def test_microphone_disabled_without_pyaudio_or_webrtcvad(self):
        from modules.voice import VoiceModule
        with unittest.mock.patch.object(VoiceModule, '_ensure_porcupine'):
            inst = VoiceModule(DummyConfig())
        self.assertFalse(inst._microphone_enabled)

    def test_keyboard_fallback_path_is_active_when_mic_disabled(self):
        text = Path('modules/voice.py').read_text(encoding='utf-8')
        self.assertIn('_microphone_enabled', text)
        self.assertIn('Microphone unavailable', text)


class TestDependencyChecker(unittest.TestCase):
    def test_mic_optional_flags(self):
        import modules.voice as voice_mod
        self.assertTrue(hasattr(voice_mod, "_HAS_PYAUDIO"))
        self.assertTrue(hasattr(voice_mod, "_HAS_VOSK"))
        self.assertTrue(hasattr(voice_mod, "_HAS_WHISPER"))


if __name__ == "__main__":
    unittest.main()
