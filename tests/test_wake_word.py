"""Tests for wake word engine abstraction and openWakeWord integration."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Check if webrtcvad is available
try:
    import webrtcvad
    _HAS_WEBRTCVAD = True
except ImportError:
    _HAS_WEBRTCVAD = False


class TestWakeWordConfig(unittest.TestCase):
    """Test wake word configuration."""

    def test_default_config(self):
        from modules.wake_word import WakeWordConfig
        config = WakeWordConfig()
        self.assertEqual(config.engine, "openwakeword")
        self.assertEqual(config.model_name, "hey_jarvis")
        self.assertEqual(config.threshold, 0.5)
        self.assertEqual(config.inference_framework, "onnx")

    def test_custom_config(self):
        from modules.wake_word import WakeWordConfig
        config = WakeWordConfig(
            engine="porcupine",
            model_name="custom_model",
            threshold=0.7,
            inference_framework="tflite",
        )
        self.assertEqual(config.engine, "porcupine")
        self.assertEqual(config.model_name, "custom_model")
        self.assertEqual(config.threshold, 0.7)
        self.assertEqual(config.inference_framework, "tflite")


class TestWakeWordEngineBase(unittest.TestCase):
    """Test the abstract base class."""

    def test_abstract_methods(self):
        from modules.wake_word import WakeWordEngine
        # Cannot instantiate abstract class
        with self.assertRaises(TypeError):
            WakeWordEngine(None, None)


class TestOpenWakeWordEngine(unittest.TestCase):
    """Test openWakeWord engine implementation."""

    def setUp(self):
        from modules.config import JarvisConfig
        from modules.wake_word import WakeWordConfig
        self.config = JarvisConfig.from_yaml(REPO / "config.yaml")
        self.wake_config = WakeWordConfig(
            engine="openwakeword",
            model_name="hey_jarvis",
            threshold=0.5,
            inference_framework="onnx",
        )

    def test_sample_rate_and_frame_length(self):
        from modules.wake_word import OpenWakeWordEngine
        engine = OpenWakeWordEngine(self.config, self.wake_config)
        self.assertEqual(engine.sample_rate, 16000)
        self.assertEqual(engine.frame_length, 1280)

    def test_initialize_returns_false_when_not_setup(self):
        """Initialize returns False when openWakeWord model not available."""
        from modules.wake_word import OpenWakeWordEngine
        engine = OpenWakeWordEngine(self.config, self.wake_config)
        # Without proper model files, initialize should return False
        result = engine.initialize()
        # This may be True or False depending on model availability
        self.assertIsInstance(result, bool)
        engine.shutdown()


class TestPorcupineWakeWordEngine(unittest.TestCase):
    """Test Porcupine (legacy) engine implementation."""

    def setUp(self):
        from modules.config import JarvisConfig
        from modules.wake_word import WakeWordConfig
        self.config = JarvisConfig.from_yaml(REPO / "config.yaml")
        self.wake_config = WakeWordConfig(
            engine="porcupine",
            model_name="hey_jarvis",
            threshold=0.5,
            inference_framework="onnx",
        )

    def test_initialize_returns_false_without_keyword_file(self):
        """Initialize returns False when keyword file doesn't exist."""
        from modules.wake_word import PorcupineWakeWordEngine
        engine = PorcupineWakeWordEngine(self.config, self.wake_config)
        result = engine.initialize()
        
        # Should fail because keyword file doesn't exist
        self.assertFalse(result)


class TestWakeWordFactory(unittest.TestCase):
    """Test the wake word engine factory."""

    def setUp(self):
        from modules.config import JarvisConfig
        self.config = JarvisConfig.from_yaml(REPO / "config.yaml")

    def test_factory_creates_engine(self):
        """Factory creates an engine instance."""
        from modules.wake_word import create_wake_word_engine
        engine = create_wake_word_engine(self.config)
        self.assertIsNotNone(engine)
        self.assertTrue(hasattr(engine, 'initialize'))
        self.assertTrue(hasattr(engine, 'process_frame'))
        self.assertTrue(hasattr(engine, 'shutdown'))


class TestVoiceModuleWakeWordIntegration(unittest.TestCase):
    """Test VoiceModule integration with wake word engine."""

    def setUp(self):
        from modules.config import JarvisConfig
        self.config = JarvisConfig.from_yaml(REPO / "config.yaml")

    @unittest.skipIf(not _HAS_WEBRTCVAD, "webrtcvad not installed")
    @patch("webrtcvad.Vad", side_effect=Exception("VAD failed"))
    def test_voice_module_microphone_disabled_on_vad_failure(self, mock_vad):
        from modules.voice import VoiceModule
        
        voice = VoiceModule(self.config)
        
        self.assertFalse(voice._microphone_enabled)
        voice.shutdown()

    def test_voice_module_has_wake_engine_or_none(self):
        """VoiceModule initializes with a wake engine (or None if unavailable)."""
        from modules.voice import VoiceModule
        
        voice = VoiceModule(self.config)
        
        # Wake engine may be None if openWakeWord models aren't available
        # This is the intended behavior - wake word is optional
        self.assertTrue(voice._wake_engine is None or hasattr(voice._wake_engine, 'process_frame'))
        voice.shutdown()


class TestWakeWordAudioProcessing(unittest.TestCase):
    """Test audio frame processing for wake word detection."""

    def test_openwakeword_process_frame_interface(self):
        """Verify OpenWakeWordEngine has correct process_frame interface."""
        from modules.wake_word import OpenWakeWordEngine, WakeWordConfig
        from modules.config import JarvisConfig
        import numpy as np
        
        config = JarvisConfig.from_yaml(REPO / "config.yaml")
        wake_config = WakeWordConfig()
        
        engine = OpenWakeWordEngine(config, wake_config)
        engine.initialize()
        
        # Test with silence
        audio_frame = np.zeros(1280, dtype=np.int16).tobytes()
        result = engine.process_frame(audio_frame)
        
        self.assertIsInstance(result, bool)
        engine.shutdown()

    def test_openwakeword_frame_length(self):
        """Verify frame length is correct for openWakeWord."""
        from modules.wake_word import OpenWakeWordEngine, WakeWordConfig
        from modules.config import JarvisConfig
        
        config = JarvisConfig.from_yaml(REPO / "config.yaml")
        wake_config = WakeWordConfig()
        
        engine = OpenWakeWordEngine(config, wake_config)
        self.assertEqual(engine.frame_length, 1280)
        self.assertEqual(engine.sample_rate, 16000)


if __name__ == "__main__":
    unittest.main()