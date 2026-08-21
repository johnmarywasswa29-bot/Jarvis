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
    
    def run_agent(
        self,
        objective: str,
        *,
        max_iterations: int = 5,
        pipeline: Any = None,
        planner: Any = None,
        confirm_fn: Any = None,
    ) -> Any:
        """Run a user objective through the bounded runtime AgentLoop.

        Reuses the EXISTING runtime subsystems (self._ctx.agent_loop,
        self.tools, self.permissions) and the EXISTING proposal-generation
        path (ResearchPipeline -> ResearchPlanner.plan -> to_proposal) to
        obtain a VALIDATED Proposal, then executes it exclusively through
        ctx.agent_loop.run(...) which delegates to the real ProposalExecutor
        behind the existing PermissionManager confirmation gate.

        No second execution engine, no automatic confirmation, no autonomous
        background execution. confirm_fn defaults to the existing
        PermissionManager.confirm (human-in-the-loop).
        """
        from research.pipeline import ResearchPipeline
        from research.planner import ResearchPlanner

        pipeline = pipeline or ResearchPipeline(self.config, self.tools)
        planner = planner or ResearchPlanner(
            self.config, self.tools, self.permissions
        )

        findings = pipeline.research(objective)
        plan = planner.plan(findings)
        proposal = planner.to_proposal(plan)

        confirm = confirm_fn or self.permissions.confirm
        result = self._ctx.agent_loop.run(
            objective,
            proposal,
            confirm_fn=confirm,
            max_iterations=max_iterations,
        )

        # Surface a concise, non-secret summary to the operator.
        print(f"[agent] status={result.status.value}")
        print(f"[agent] iterations={len(result.iterations)}")
        if result.final_verification is not None:
            print(f"[agent] verification={result.final_verification.status.value}")
        if result.message:
            print(f"[agent] message={result.message}")
        return result

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

    # Phase F: bounded AgentLoop-backed CLI command. Runs synchronously in the
    # foreground (no autonomous background execution) and reuses the existing
    # runtime + confirmation gate.
    if len(sys.argv) > 1 and sys.argv[1] == "agent":
        max_iterations = 5
        args = sys.argv[2:]
        if args and args[0] == "--max-iterations":
            try:
                max_iterations = int(args[1])
                args = args[2:]
            except (IndexError, ValueError):
                print("--max-iterations requires an integer", file=sys.stderr)
                return 2
        objective = " ".join(args).strip()
        if not objective:
            print("usage: python jarvis.py agent \"<objective>\" [--max-iterations N]",
                  file=sys.stderr)
            return 2
        try:
            assistant = JarvisAssistant()
        except Exception as exc:
            _log_startup_crash(exc)
            return 1
        result = assistant.run_agent(objective, max_iterations=max_iterations)
        assistant.cleanup()
        if result is None:
            return 1
        # DONE is the only fully-successful outcome.
        return 0 if result.status.value == "done" else 1

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