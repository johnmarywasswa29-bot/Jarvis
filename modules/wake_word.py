from __future__ import annotations

import abc
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from modules.config import JarvisConfig

logger = logging.getLogger("wake_word")


@dataclass
class WakeWordConfig:
    """Configuration for wake word engines."""
    engine: str = "openwakeword"  # "openwakeword" or "porcupine"
    model_name: str = "hey_jarvis"
    threshold: float = 0.5
    inference_framework: str = "onnx"  # "onnx" or "tflite" (Windows: onnx only)
    model_dir: Optional[Path] = None


class WakeWordEngine(abc.ABC):
    """Abstract base class for wake word detection engines."""

    def __init__(self, config: JarvisConfig, wake_config: WakeWordConfig) -> None:
        self.config = config
        self.wake_config = wake_config
        self._initialized = False

    @property
    @abc.abstractmethod
    def sample_rate(self) -> int:
        """Sample rate required by the engine (Hz)."""
        pass

    @property
    @abc.abstractmethod
    def frame_length(self) -> int:
        """Number of samples per frame."""
        pass

    @abc.abstractmethod
    def initialize(self) -> bool:
        """Initialize the engine. Returns True on success."""
        pass

    @abc.abstractmethod
    def process_frame(self, audio_frame: bytes) -> bool:
        """Process one audio frame. Returns True if wake word detected."""
        pass

    @abc.abstractmethod
    def shutdown(self) -> None:
        """Release resources."""
        pass

    @property
    def is_initialized(self) -> bool:
        return self._initialized


class OpenWakeWordEngine(WakeWordEngine):
    """Wake word detection using openWakeWord."""

    def __init__(self, config: JarvisConfig, wake_config: WakeWordConfig) -> None:
        super().__init__(config, wake_config)
        self._model = None
        self._oww = None

    @property
    def sample_rate(self) -> int:
        return 16000

    @property
    def frame_length(self) -> int:
        # openWakeWord expects 80ms frames at 16kHz = 1280 samples
        return 1280

    def initialize(self) -> bool:
        try:
            import openwakeword
            from openwakeword.model import Model
            from openwakeword.utils import download_models
            self._oww = openwakeword
        except Exception as exc:
            logger.error("openWakeWord not available: %s", exc)
            return False

        # Determine model directory
        if self.wake_config.model_dir:
            model_dir = self.wake_config.model_dir
        else:
            model_dir = self.config.project_root / "data" / "wake_words"

        model_dir.mkdir(parents=True, exist_ok=True)

        # Download models if needed
        try:
            logger.info("Downloading openWakeWord models to %s", model_dir)
            download_models(model_names=[], target_directory=str(model_dir))
        except Exception as exc:
            logger.warning("Model download failed (may already exist): %s", exc)

        # Find the model file
        model_name = self.wake_config.model_name
        model_path = None

        # Check common locations
        search_paths = [
            model_dir / f"{model_name}.onnx",
            model_dir / f"{model_name}.tflite",
            model_dir / f"{model_name}",
        ]

        for p in search_paths:
            if p.exists():
                model_path = str(p)
                break

        # If not found, try the default model name mapping
        if model_path is None:
            # openWakeWord uses specific naming
            for p in model_dir.glob("*.onnx"):
                if model_name.replace("_", "") in p.stem.lower():
                    model_path = str(p)
                    break
            if model_path is None:
                for p in model_dir.glob("*.tflite"):
                    if model_name.replace("_", "") in p.stem.lower():
                        model_path = str(p)
                        break

        if model_path is None:
            # Try to use built-in model by name
            logger.info("Model file not found, attempting to load built-in '%s'", model_name)
            model_path = model_name

        try:
            logger.info("Loading openWakeWord model: %s (framework: %s)", model_path, self.wake_config.inference_framework)
            self._model = Model(
                wakeword_models=[model_path],
                inference_framework=self.wake_config.inference_framework,
                enable_speex_noise_suppression=False,
                vad_threshold=0.0,  # We use our own VAD
            )
            self._initialized = True
            logger.info("openWakeWord engine initialized successfully")
            return True
        except Exception as exc:
            logger.error("Failed to initialize openWakeWord model: %s", exc)
            return False

    def process_frame(self, audio_frame: bytes) -> bool:
        if not self._initialized or self._model is None:
            return False

        try:
            import numpy as np
            # Convert bytes to numpy array
            audio_np = np.frombuffer(audio_frame, dtype=np.int16)
            # openWakeWord expects float32 in range [-1, 1]
            audio_float = audio_np.astype(np.float32) / 32768.0
            prediction = self._model.predict(audio_float)
            # Check if any model exceeds threshold
            for model_name, scores in prediction.items():
                if scores[-1] >= self.wake_config.threshold:
                    logger.debug("Wake word detected: %s (score: %.3f)", model_name, scores[-1])
                    return True
            return False
        except Exception as exc:
            logger.error("openWakeWord prediction error: %s", exc)
            return False

    def shutdown(self) -> None:
        if self._model is not None:
            try:
                # openWakeWord doesn't have explicit shutdown, but we can clear reference
                self._model = None
            except Exception:
                pass
        self._initialized = False
        logger.info("openWakeWord engine shut down")


class PorcupineWakeWordEngine(WakeWordEngine):
    """Legacy wake word detection using Picovoice Porcupine."""

    def __init__(self, config: JarvisConfig, wake_config: WakeWordConfig) -> None:
        super().__init__(config, wake_config)
        self._porc = None

    @property
    def sample_rate(self) -> int:
        if self._porc:
            return self._porc.sample_rate
        return 16000

    @property
    def frame_length(self) -> int:
        if self._porc:
            return self._porc.frame_length
        return 512

    def initialize(self) -> bool:
        try:
            import pvporcupine
        except Exception as exc:
            logger.error("Porcupine not available: %s", exc)
            return False

        kw_path = Path(self.config.keyword_path)
        if not kw_path.is_absolute():
            kw_path = self.config.project_root / kw_path

        if not kw_path.exists():
            logger.warning("Wake-word file missing: %s", kw_path)
            return False

        try:
            access_key = os.getenv("PICOVOICE_ACCESS_KEY", "")
            self._porc = pvporcupine.create(
                access_key=access_key,
                keyword_paths=[str(kw_path)],
                sensitivities=self.config.sensitivities,
            )
            self._initialized = True
            logger.info("Porcupine wake word loaded")
            return True
        except Exception as exc:
            logger.error("Porcupine init failed: %s", exc)
            return False

    def process_frame(self, audio_frame: bytes) -> bool:
        if not self._initialized or self._porc is None:
            return False
        try:
            kw_idx = self._porc.process(audio_frame)
            return kw_idx >= 0
        except Exception as exc:
            logger.error("Porcupine process error: %s", exc)
            return False

    def shutdown(self) -> None:
        if self._porc is not None:
            try:
                self._porc.delete()
            except Exception:
                pass
            self._porc = None
        self._initialized = False
        logger.info("Porcupine engine shut down")


def create_wake_word_engine(config: JarvisConfig) -> WakeWordEngine:
    """Factory function to create the appropriate wake word engine."""
    import os

    # Check for explicit engine selection via config or env
    engine_name = os.getenv("JARVIS_WAKE_WORD_ENGINE", "").lower()
    if not engine_name:
        # Default to openWakeWord if available, else Porcupine
        try:
            import openwakeword
            engine_name = "openwakeword"
        except ImportError:
            engine_name = "porcupine"

    wake_config = WakeWordConfig(
        engine=engine_name,
        model_name=getattr(config, "openwakeword_model", "hey_jarvis"),
        threshold=getattr(config, "openwakeword_threshold", 0.5),
        inference_framework=getattr(config, "openwakeword_inference", "onnx"),
    )

    if engine_name == "porcupine":
        logger.info("Using Porcupine wake word engine")
        return PorcupineWakeWordEngine(config, wake_config)
    else:
        logger.info("Using openWakeWord wake word engine")
        return OpenWakeWordEngine(config, wake_config)


# Need to import os for the factory
import os