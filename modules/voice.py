from __future__ import annotations

import asyncio
import os
import queue
import struct
import threading
import time
import hashlib
import urllib.request
from pathlib import Path
from typing import Awaitable, Callable, Optional

import numpy as np
import sounddevice as sd
try:
    import webrtcvad
    _HAS_WEBRTCVAD = True
except Exception:
    _HAS_WEBRTCVAD = False

try:
    import pyaudio
    _HAS_PYAUDIO = True
except Exception:
    _HAS_PYAUDIO = False

from modules.config import JarvisConfig
from modules.logger import get_logger
from modules.wake_word import create_wake_word_engine, WakeWordEngine

logger = get_logger("voice")

if not _HAS_PYAUDIO:
    logger.warning("PyAudio not available; microphone input disabled.")

try:
    import pvporcupine
    import pvrecorder
    _HAS_PORCUPINE = True
except Exception as exc:  # pragma: no cover
    _HAS_PORCUPINE = False
    logger.warning("pvporcupine not available: %s", exc)

try:
    import pyttsx3
    _HAS_TTS = True
except Exception:
    _HAS_TTS = False

try:
    from vosk import Model, KaldiRecognizer
    _HAS_VOSK = True
except Exception:
    _HAS_VOSK = False

try:
    import whisper
    _HAS_WHISPER = True
except Exception:
    _HAS_WHISPER = False


class _Beep:
    """Cross-platform terminal bell alternative using playsound hook."""
    def __init__(self) -> None:
        self._start = 0.0
    
    def _tone(self, sample_rate: int = 44100) -> bytes:
        t = np.linspace(0, 0.15, int(sample_rate * 0.15), False)
        freq = 880 if self._high else 440
        tone = np.sin(freq * t * 2 * np.pi) * 0.3
        return (tone * 32767).astype(np.int16).tobytes()
    
    def play(self, high: bool = False) -> None:
        self._high = high
        sd.play(np.frombuffer(self._tone(), dtype=np.int16), 44100, blocking=False)


class VoiceModule:
    """Handles wake word, VAD, STT, TTS and continuous listen loop."""
    
    def __init__(self, config: JarvisConfig) -> None:
        self.config = config
        self._beep = _Beep()
        self._wake_engine: Optional[WakeWordEngine] = None
        self._recorder = None
        self._vad = None
        self._microphone_enabled = _HAS_PYAUDIO and _HAS_WEBRTCVAD
        if self._microphone_enabled:
            try:
                self._vad = webrtcvad.Vad(config.frame_ms // 10 if config.frame_ms >= 10 else 3)
                self._vad.set_mode(2)
            except Exception as exc:
                logger.warning("VAD init failed, disabling microphone input: %s", exc)
                self._microphone_enabled = False
        else:
            logger.warning("Microphone input disabled: PyAudio=%s, webrtcvad=%s", _HAS_PYAUDIO, _HAS_WEBRTCVAD)
        self._wake_queue = asyncio.Queue()
        self._tts_engine = None
        self._audio_thread = None
        self._stop_event = threading.Event()
        
        self._setup_tts()
        self._init_wake_engine()

    # ---------- TTS ----------
    def _setup_tts(self) -> None:
        if not _HAS_TTS:
            return
        try:
            self._tts_engine = pyttsx3.init()
            self._tts_engine.setProperty("rate", self.config.tts_rate)
            self._tts_engine.setProperty("volume", self.config.tts_volume)
            logger.info("TTS initialized via pyttsx3")
        except Exception as exc:
            logger.error("TTS init failed: %s", exc)

    def _speak_sync(self, text: str) -> None:
        if self._tts_engine is None:
            logger.info("[TTS MISSING] %s", text)
            return
        try:
            self._tts_engine.say(text)
            self._tts_engine.runAndWait()
        except Exception as exc:
            logger.error("TTS error: %s", exc)

    def speak(self, text: str) -> None:
        if threading.current_thread() is threading.main_thread():
            self._speak_sync(text)
        else:
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(self._speak_sync, text)

    def beep(self, high: bool = False) -> None:
        try:
            self._beep.play(high=high)
        except Exception as exc:
            logger.debug("Beep skipped: %s", exc)

    # ---------- Wake Word Engine ----------
    def _init_wake_engine(self) -> None:
        """Initialize the wake word engine (openWakeWord or Porcupine fallback)."""
        self._wake_engine = create_wake_word_engine(self.config)
        if not self._wake_engine.initialize():
            logger.warning("Wake word engine initialization failed, disabling wake word detection")
            self._wake_engine = None
        else:
            logger.info("Wake word engine initialized: %s", type(self._wake_engine).__name__)

    # ---------- Porcupine / fallback (legacy) ----------
    def _ensure_porcupine(self) -> None:
        """Legacy method for backward compatibility. Delegates to wake engine."""
        if self._wake_engine is not None and isinstance(self._wake_engine, PorcupineWakeWordEngine):
            # Already initialized via _init_wake_engine
            return
        # Fallback to old behavior if explicitly called
        logger.warning("Legacy _ensure_porcupine called; using wake engine abstraction instead")
        self._init_wake_engine()

    # ---------- STT ----------
    def _load_vosk_model(self):
        if not _HAS_VOSK:
            return None
        if getattr(self, "_stt_unavailable", False):
            return None
        name = self.config.stt_model_name
        models_dir = self.config.stt_models_dir()
        model_path = models_dir / f"vosk-model-{name}"
        if model_path.exists():
            return Model(str(model_path))
        logger.info("Downloading Vosk model %s to %s", name, model_path)
        zip_name = f"vosk-model-{name}-0.22"
        urls = [
            f"https://github.com/alphacep/vosk-api/releases/download/v0.3.45/{zip_name}.zip",
            f"https://alphacephei.com/vosk/models/vosk-model-{name}.zip",
        ]
        zip_path = models_dir / f"{zip_name}.zip"
        models_dir.mkdir(parents=True, exist_ok=True)
        last_err = None
        for url in urls:
            try:
                urllib.request.urlretrieve(url, zip_path)
                last_err = None
                break
            except Exception as exc:
                last_err = exc
                logger.warning("Vosk download failed from %s: %s", url, exc)
        if last_err is not None:
            self._stt_unavailable = True
            logger.warning("Vosk model unavailable; speech-to-text disabled until manual download.")
            return None
        import zipfile
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(models_dir)
        zip_path.unlink()
        logger.info("Vosk model unpacked")
        if not model_path.exists():
            extracted = next(models_dir.glob("vosk-model-*"), None)
            if extracted and extracted.is_dir():
                extracted.rename(model_path)
        return Model(str(model_path))

    def _record_until_silence(
        self, sample_rate: int = 16000, max_seconds: float = 8.0
    ) -> Optional[bytes]:
        """Record audio frames while VAD speech is detected, then return raw PCM."""
        frame_ms = self.config.frame_ms
        frame_len = int(sample_rate * (frame_ms / 1000))
        frames_per_block = int(1000 / frame_ms)
        silence_limit = int(0.8 / (frame_ms / 1000))
        threshold = self.config.threshold
        
        q: queue.Queue = queue.Queue()
        voiced = []
        silent = 0
        energy = 0.0
        start_t = time.time()
        
        def callback(indata, frames, t_info, status):  # type: ignore
            q.put(bytes(indata))
            
        blocksize = frame_len
        stream = sd.RawInputStream(
            samplerate=sample_rate,
            blocksize=blocksize,
            dtype="int16",
            channels=1,
            callback=callback,
        )
        stream.start()
        try:
            while time.time() - start_t < max_seconds:
                data = q.get()
                if len(data) != frame_len * 2:
                    continue
                
                np_data = np.frombuffer(data, dtype=np.int16)
                frame_rms = float(np.sqrt(np.mean(np_data.astype(np.float32) ** 2)))
                energy = 0.9 * energy + 0.1 * frame_rms
                
                is_speech = bool(self._vad.is_speech(np_data.tobytes(), sample_rate)) and frame_rms > threshold
                if is_speech:
                    voiced.append(np_data.tobytes())
                    silent = 0
                else:
                    silent += 1
                    if 0 < silent <= silence_limit:
                        voiced.append(np_data.tobytes())
                    
                if silent > silence_limit * 2 and len(voiced) > 0:
                    break
        finally:
            stream.stop()
            stream.close()
        
        return b"".join(voiced) if voiced else None

    def _transcribe(self, pcm: bytes) -> str:
        # Prefer Whisper when available; it's model-backed and offline-capable.
        if _HAS_WHISPER:
            try:
                model = getattr(self, "_whisper_model", None)
                if model is None:
                    logger.info("Loading Whisper model...")
                    model = whisper.load_model("base")
                    self._whisper_model = model
                    logger.info("Whisper model loaded")
                import tempfile, wave
                repo_dir = Path(__file__).resolve().parent.parent
                logs_dir = Path(self.config.logs_dir) if hasattr(self.config, 'logs_dir') else repo_dir / "logs"
                logs_dir.mkdir(parents=True, exist_ok=True)
                wav_path = logs_dir / ("whisper_in_" + os.urandom(4).hex() + ".wav")
                try:
                    with wave.open(str(wav_path), "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(16000)
                        wf.writeframes(pcm)
                    try:
                        result = model.transcribe(str(wav_path), language="en", fp16=False)
                        text = (result.get("text") or "").strip()
                    except Exception as exc:
                        text = ""
                        logger.warning("Whisper transcription failed: %s", exc)
                finally:
                    try:
                        wav_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                return text
            except Exception as exc:
                logger.warning("Whisper transcription failed: %s", exc)
        
        if not _HAS_VOSK:
            return ""
        model = getattr(self, "_vosk_model", None)
        if model is None:
            model = self._load_vosk_model()
            if model is None:
                return ""
            setattr(self, "_vosk_model", model)
        
        rec = KaldiRecognizer(model, 16000)
        rec.SetWords(True)
        rec.AcceptWaveform(pcm)
        final = rec.FinalResult()
        try:
            import json
            return json.loads(final).get("text", "").strip()
        except Exception:
            return ""

    # ---------- Listening Loop ----------
    async def listen_loop(self, out_queue: "asyncio.Queue[Optional[str]]") -> None:
        """Continuously listen, detect wake word, capture one command, put transcript."""
        logger.info("Voice listen loop started")

        sample_rate = 16000

        if not self._microphone_enabled:
            logger.warning("Microphone unavailable; keyboard input fallback active.")
            print("[Jarvis] Microphone unavailable. Type a command and press Enter (Ctrl+C to stop):")
            try:
                while True:
                    line = await asyncio.to_thread(input, "> ")
                    if line is not None:
                        line = line.strip()
                    if line:
                        await out_queue.put(line)
            except asyncio.CancelledError:
                logger.info("Listen loop cancelled")
                return

        # Determine frame size for wake word engine
        if self._wake_engine is not None:
            frame_len = self._wake_engine.frame_length
            engine_sr = self._wake_engine.sample_rate
        else:
            frame_len = 1280
            engine_sr = 16000

        def read_wake_block() -> tuple[int, bytes, bool]:
            """Return (frame_length, pcm, kw_detected)"""
            pcm = sd.rec(int(frame_len / engine_sr * engine_sr), samplerate=engine_sr, channels=1, dtype="int16", blocking=True)
            pcm_bytes = np.ascontiguousarray(pcm).tobytes()
            if self._wake_engine is not None:
                kw = self._wake_engine.process_frame(pcm_bytes)
            else:
                kw = False
            return frame_len, pcm_bytes, kw

        # Warm-up
        sd.play(np.zeros(480, dtype=np.int16), engine_sr, blocking=True)

        while True:
            try:
                # 1) If no wake engine -> passive listen
                if self._wake_engine is None:
                    await asyncio.sleep(2.0)
                    pcm = self._record_until_silence(sample_rate, max_seconds=5.0)
                    if pcm:
                        text = self._transcribe(pcm)
                        if text:
                            await out_queue.put(text)
                    continue

                # 2) Wake word engine path
                detected = False
                while not detected:
                    frame_len, pcm_bytes, kw = await asyncio.to_thread(read_wake_block)
                    if kw:
                        detected = True

                logger.info("Wake word detected")
                self.beep(high=True)

                # 3) After wake -> capture one command and STT
                pcm = await asyncio.to_thread(self._record_until_silence, sample_rate, 6.0)
                if pcm:
                    text = self._transcribe(pcm)
                    if text:
                        await out_queue.put(text)
            except asyncio.CancelledError:
                logger.info("Listen loop cancelled")
                break
            except Exception as exc:
                logger.error("Listen loop error: %s", exc, exc_info=True)
                await asyncio.sleep(1.0)

    def shutdown(self) -> None:
        if self._wake_engine is not None:
            self._wake_engine.shutdown()
            self._wake_engine = None
        if self._tts_engine is not None:
            try:
                self._tts_engine.stop()
            except Exception:
                pass