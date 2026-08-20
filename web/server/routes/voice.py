"""Voice API routes for Jarvis Web UI - STT and TTS."""
from __future__ import annotations

import asyncio
import base64
import io
import os
import wave
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from web.server.dependencies import get_runtime, get_optional_runtime
from web.server.auth import optional_auth

router = APIRouter(prefix="/api/voice", tags=["voice"])

# Thread pool for blocking STT/TTS operations
_voice_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="voice-")


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)


class STTResponse(BaseModel):
    text: str
    duration_ms: Optional[float] = None


class TTSResponse(BaseModel):
    audio_base64: str
    format: str = "wav"
    sample_rate: int = 16000


def _transcribe_audio_sync(pcm_bytes: bytes, config) -> tuple[str, float]:
    """Synchronous STT transcription (runs in thread pool)."""
    import time
    from modules.config import JarvisConfig
    from modules.voice import VoiceModule
    
    # Create a minimal VoiceModule for STT only
    # This avoids loading TTS/wake word if not needed
    class STTEngine:
        def __init__(self, config):
            self.config = config
            self._vosk_model = None
            self._whisper_model = None
        
        def _load_vosk_model(self):
            from modules.voice import _HAS_VOSK
            if not _HAS_VOSK:
                return None
            try:
                from vosk import Model
                name = self.config.stt_model_name
                models_dir = self.config.stt_models_dir()
                model_path = models_dir / f"vosk-model-{name}"
                if model_path.exists():
                    return Model(str(model_path))
                return None
            except Exception:
                return None
        
        def transcribe(self, pcm: bytes) -> str:
            # Try Whisper first
            try:
                import whisper
                from modules.voice import _HAS_WHISPER
                if _HAS_WHISPER:
                    if self._whisper_model is None:
                        import whisper
                        self._whisper_model = whisper.load_model("base")
                    
                    import tempfile, wave
                    import os
                    repo_dir = Path(__file__).resolve().parent.parent
                    logs_dir = repo_dir / "logs"
                    logs_dir.mkdir(parents=True, exist_ok=True)
                    wav_path = logs_dir / (f"whisper_in_{os.urandom(4).hex()}.wav")
                    
                    try:
                        with wave.open(str(wav_path), "wb") as wf:
                            wf.setnchannels(1)
                            wf.setsampwidth(2)
                            wf.setframerate(16000)
                            wf.writeframes(pcm)
                        
                        result = self._whisper_model.transcribe(str(wav_path), language="en", fp16=False)
                        text = (result.get("text") or "").strip()
                        return text
                    finally:
                        try:
                            wav_path.unlink(missing_ok=True)
                        except Exception:
                            pass
            except Exception:
                pass
            
            # Fallback to Vosk
            try:
                from modules.voice import _HAS_VOSK
                if not _HAS_VOSK:
                    return ""
                from vosk import Model, KaldiRecognizer
                
                if self._vosk_model is None:
                    self._vosk_model = self._load_vosk_model()
                    if self._vosk_model is None:
                        return ""
                
                from vosk import KaldiRecognizer
                rec = KaldiRecognizer(self._vosk_model, 16000)
                rec.SetWords(True)
                rec.AcceptWaveform(pcm)
                final = rec.FinalResult()
                import json
                return json.loads(final).get("text", "").strip()
            except Exception:
                return ""
            
            return ""
        
        def _load_vosk_model(self):
            from modules.voice import _HAS_VOSK
            if not _HAS_VOSK:
                return None
            try:
                from vosk import Model
                name = self.config.stt_model_name
                models_dir = self.config.stt_models_dir()
                model_path = models_dir / f"vosk-model-{name}"
                if model_path.exists():
                    return Model(str(model_path))
                return None
            except Exception:
                return None
    
    start = time.perf_counter()
    engine = STTEngine(config)
    text = engine.transcribe(pcm_bytes)
    duration_ms = (time.perf_counter() - start) * 1000
    return text, duration_ms


def _generate_tts_audio_sync(text: str, config) -> tuple[bytes, str, int]:
    """Synchronous TTS generation using VoiceService (runs in thread pool)."""
    import tempfile
    import os
    from modules.voice_service import create_voice_service
    
    # Create VoiceService for TTS
    voice_service = create_voice_service(config)
    
    try:
        # Generate audio using VoiceService
        audio_data = voice_service.synthesize(text)
        
        # Get audio format info
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        
        try:
            with open(tmp_path, "wb") as f:
                f.write(audio_data)
            
            with wave.open(tmp_path, "rb") as wf:
                sample_rate = wf.getframerate()
                channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
            
            return audio_data, "wav", sample_rate
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    finally:
        voice_service.shutdown()


@router.post("/stt", response_model=STTResponse)
async def speech_to_text(
    request: Request,
    audio: UploadFile = File(...),
    runtime = Depends(get_runtime),
    _auth = Depends(optional_auth)
):
    """Transcribe uploaded audio to text using existing STT pipeline. Optional auth."""
    if runtime is None or runtime.config is None:
        raise HTTPException(status_code=503, detail="Runtime not initialized")
    
    # Validate file
    content_type = audio.content_type or ""
    if not content_type.startswith("audio/"):
        raise HTTPException(status_code=400, detail="File must be audio")
    
    # Read audio data
    audio_data = await audio.read()
    
    # Limit upload size (max 10MB)
    if len(audio_data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio file too large (max 10MB)")
    
    if len(audio_data) == 0:
        raise HTTPException(status_code=400, detail="Empty audio file")
    
    # Convert to PCM if needed (expect raw PCM or WAV)
    pcm_bytes = audio_data
    
    # If it's a WAV file, extract PCM
    if audio.content_type in ("audio/wav", "audio/x-wav", "audio/wave") or audio.filename.endswith(".wav"):
        import wave
        import io
        try:
            with wave.open(io.BytesIO(audio_data), "rb") as wf:
                if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != 16000:
                    # Resample/convert if needed - for now just extract raw
                    frames = wf.readframes(wf.getnframes())
                    audio_data = frames
        except Exception:
            pass
    
    # Run STT in thread pool
    loop = asyncio.get_event_loop()
    try:
        text, duration_ms = await loop.run_in_executor(
            _voice_executor,
            _transcribe_audio_sync,
            audio_data,
            runtime.config
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"STT failed: {exc}")
    
    return STTResponse(text=text, duration_ms=duration_ms)


@router.post("/tts", response_model=TTSResponse)
async def text_to_speech(
    request: Request,
    tts_req: TTSRequest,
    runtime = Depends(get_runtime),
    _auth = Depends(optional_auth)
):
    """Generate speech audio from text using existing TTS pipeline. Optional auth."""
    if runtime is None or runtime.config is None:
        raise HTTPException(status_code=503, detail="Runtime not initialized")
    
    text = tts_req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text cannot be empty")
    
    if len(text) > 5000:
        raise HTTPException(status_code=400, detail="Text too long (max 5000 chars)")
    
    # Generate TTS in thread pool
    loop = asyncio.get_event_loop()
    try:
        audio_data, fmt, sample_rate = await loop.run_in_executor(
            _voice_executor,
            _generate_tts_audio_sync,
            text,
            runtime.config
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TTS failed: {exc}")
    
    # Encode as base64 for transport
    audio_base64 = base64.b64encode(audio_data).decode("ascii")
    
    return TTSResponse(
        audio_base64=audio_base64,
        format="wav",
        sample_rate=sample_rate
    )


@router.get("/status")
async def voice_status(request: Request, runtime = Depends(get_optional_runtime), _auth = Depends(optional_auth)):
    """Get voice system status. Optional auth."""
    if runtime is None or runtime.config is None:
        return {"available": False, "reason": "Runtime not initialized"}
    
    from modules.voice import _HAS_TTS, _HAS_VOSK, _HAS_WHISPER, _HAS_WEBRTCVAD, _HAS_PYAUDIO
    
    return {
        "available": True,
        "tts": _HAS_TTS,
        "stt_vosk": _HAS_VOSK,
        "stt_whisper": _HAS_WHISPER,
        "microphone": _HAS_PYAUDIO and _HAS_WEBRTCVAD,
        "wake_word": True  # openWakeWord available
    }