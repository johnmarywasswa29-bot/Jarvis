"""
Jarvis - Local Desktop AI Assistant
Run: python jarvis.py
"""
from __future__ import annotations

import sys
import time
import logging
import asyncio
import signal
from pathlib import Path

# Ensure repo root is importable when launching from anywhere
REPO = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.verify_dependencies import check_dependencies
check_dependencies()

from modules.config import JarvisConfig
from modules.voice import VoiceModule
from modules.brain_graph import JarvisBrain
from modules.memory import JarvisMemory
from modules.vision import VisionModule
from modules.logger import setup_logger
from modules.permission_manager import PermissionManager
from core import events as core_events
from runtime.runtime import build_runtime, stop_runtime as rt_stop
from modules.tools import ToolRegistry

logger = logging.getLogger("jarvis")


class JarvisAssistant:
    def __init__(self) -> None:
        self.config = JarvisConfig.from_yaml(REPO / "config.yaml")
        setup_logger(REPO / "logs")
        logger.info("Jarvis initializing...")

        # P0-2: build every subsystem through the single authoritative factory.
        self._ctx = build_runtime(config=self.config, repo=REPO)
        ctx = self._ctx

        self.memory = ctx.chat_memory or JarvisMemory(self.config)
        self.permissions = ctx.permission_manager or PermissionManager()
        self.tools = ctx.tool_registry or ToolRegistry(self.config)
        self.voice = VoiceModule(self.config)
        # Use runtime's chat_memory (JarvisMemoryV2) for brain, not fallback
        self.brain = JarvisBrain(self.config, self.tools, ctx.chat_memory or self.memory)
        self.vision = VisionModule(self.config)

        # Expose the full runtime for any subsystem that wants it.
        self.runtime = ctx

        self.running = False
        
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)
    
    def _on_signal(self, signum, _) -> None:
        logger.info("Signal %s received, shutting down", signum)
        self.running = False
    
    async def start(self) -> None:
        logger.info("Jarvis online. Say wake word to begin.")
        self.voice.speak("Jarvis online. Say my name to start.")
        
        self.running = True
        audio_queue: asyncio.Queue[str] = asyncio.Queue()
        
        loop = asyncio.get_event_loop()
        
        # Start listener in background
        listener_task = loop.create_task(self.voice.listen_loop(audio_queue))
        
        try:
            while self.running:
                transcript = await audio_queue.get()
                if not transcript:
                    continue
                
                logger.info("Heard: %s", transcript)
                
                if "jarvis" in transcript.lower():
                    self.voice.beep(high=True)
                else:
                    self.voice.beep(high=False)
                    continue
                
                # Capture context / vision if available
                context_parts: list[str] = []
                
                if "what is on my screen" in transcript.lower() or "what do you see" in transcript.lower():
                    logger.info("Running vision analysis...")
                    vis = self.vision.analyze_screenshot()
                    context_parts.append(f"Current screen: {vis}")
                
                response = self.brain.run(transcript, extra_context=context_parts)
                
                self.memory.add_message("user", transcript)
                self.memory.add_message("assistant", response)
                
                logger.info("Jarvis: %s", response)
                print(f"Jarvis: {response}")  # Console output for keyboard fallback mode
                self.voice.speak(response)
        finally:
            listener_task.cancel()
            self.cleanup()
    
    def cleanup(self) -> None:
        logger.info("Tearing down Jarvis...")
        try:
            rt_stop(self._ctx)
        except Exception as exc:
            logger.warning("runtime stop error: %s", exc)
        try:
            self.voice.shutdown()
        except Exception:
            pass
        try:
            self.memory.shutdown()
        except Exception:
            pass


def _log_startup_crash(exc: BaseException) -> None:
    import logging
    import os
    import platform
    import traceback
    from datetime import datetime
    from pathlib import Path

    repo = REPO
    log_path = repo / "logs" / "startup_crash.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            f"timestamp: {datetime.now().isoformat()}\n"
            f"python_version: {sys.version}\n"
            f"executable: {sys.executable}\n"
            f"cwd: {os.getcwd()}\n"
            f"platform: {platform.platform()}\n"
            f"sys_path:\n"
        )
        body = header + "\n".join(f"- {p}" for p in sys.path) + "\n\n"
        body += "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        log_path.write_text(body, encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        logging.getLogger("jarvis").critical("Startup crash", exc_info=True)
    except Exception:
        pass
    try:
        sys.stderr.write(body)
    except Exception:
        pass
    try:
        sys.stderr.flush()
        sys.stdout.flush()
    except Exception:
        pass


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--diagnose":
        def _raise():
            raise RuntimeError("diagnostics-probe")
        try:
            _raise()
        except Exception as exc:
            _log_startup_crash(exc)
        return 0
    try:
        assistant = JarvisAssistant()
        try:
            asyncio.run(assistant.start())
        except KeyboardInterrupt:
            pass
        assistant.cleanup()
        return 0
    except Exception as exc:
        _log_startup_crash(exc)
        try:
            assistant.cleanup()
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())