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

        Operator-facing improvements (Phase I, safety-preserving):
          #1 iteration/replan visibility via the existing agent.* EventBus
             events (observation only; no new execution path).
          #2 read-only, non-blocking preflight readiness WARN (Ollama /
             egress); never blocks or fails-open the safety path.
          #3 machine-readable result artifact at logs/agent_last.json
             (non-secret AgentLoopResult fields only).

        No second execution engine, no automatic confirmation, no autonomous
        background execution. confirm_fn defaults to the existing
        PermissionManager.confirm (human-in-the-loop).
        """
        import json as _json
        import socket as _socket

        from core.events import EventType
        from research.pipeline import ResearchPipeline
        from research.planner import PlanValidationError, ResearchPlanner

        # --- #2 Preflight readiness (READ-ONLY, non-blocking, WARN only) ---
        for warn in agent_readiness_warnings(self.config):
            print(f"[agent] readiness warning: {warn}", file=sys.stderr)

        pipeline = pipeline or ResearchPipeline(self.config, self.tools)
        planner = planner or ResearchPlanner(
            self.config, self.tools, self.permissions
        )

        # Generate/obtain a VALIDATED Proposal through the existing research
        # path. If research yields no usable evidence, the planner fails safe
        # with PlanValidationError — catch it here and report a clean operator
        # message (no traceback). The fail-safe is preserved: no valid proposal
        # => no execution.
        try:
            findings = pipeline.research(objective)
            plan = planner.plan(findings)
            proposal = planner.to_proposal(plan)
        except PlanValidationError as exc:
            print(
                f"Research could not produce a valid plan: {exc}. No action was taken.",
                file=sys.stderr,
            )
            write_agent_artifact(REPO, objective, None)
            return None

        confirm = confirm_fn or self.permissions.confirm

        # --- #1 Surface existing agent.* EventBus events to the operator ---
        bus = self._ctx.event_bus
        seen: list = []

        def _on_agent_event(ev):
            t = ev.event_type.value
            p = ev.payload or {}
            if t == EventType.AGENT_ITERATION_STARTED.value:
                seen.append(t)
                print(f"[agent] iteration {p.get('iteration')} started")
            elif t == EventType.AGENT_EXECUTION_COMPLETED.value:
                seen.append(t)
                print(f"[agent] execution completed: {p.get('status')}")
            elif t == EventType.AGENT_VERIFICATION_COMPLETED.value:
                seen.append(t)
                print(f"[agent] verification: {p.get('status')}")
            elif t == EventType.AGENT_REPLAN_COMPLETED.value:
                seen.append(t)
                print(f"[agent] replan: {p.get('replan_status')}")
            elif t == EventType.AGENT_ABORTED.value:
                seen.append(t)
                print(f"[agent] aborted: {p.get('status')}")
            elif t == EventType.AGENT_COMPLETED.value:
                seen.append(t)
                print(f"[agent] completed: {p.get('status')}")

        for _et in (
            EventType.AGENT_ITERATION_STARTED,
            EventType.AGENT_EXECUTION_COMPLETED,
            EventType.AGENT_VERIFICATION_COMPLETED,
            EventType.AGENT_REPLAN_COMPLETED,
            EventType.AGENT_ABORTED,
            EventType.AGENT_COMPLETED,
        ):
            bus.subscribe(_et, _on_agent_event)

        try:
            result = self._ctx.agent_loop.run(
                objective,
                proposal,
                confirm_fn=confirm,
                max_iterations=max_iterations,
            )
        finally:
            for _et in (
                EventType.AGENT_ITERATION_STARTED,
                EventType.AGENT_EXECUTION_COMPLETED,
                EventType.AGENT_VERIFICATION_COMPLETED,
                EventType.AGENT_REPLAN_COMPLETED,
                EventType.AGENT_ABORTED,
                EventType.AGENT_COMPLETED,
            ):
                try:
                    bus.unsubscribe(_et, _on_agent_event)
                except Exception:
                    pass

        # --- #3 Machine-readable result artifact (non-secret fields only) ---
        write_agent_artifact(REPO, objective, result)

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


# ---------------------------------------------------------------------------- #
# Phase I module-level helpers (read-only / observation only; no gate changes).
# Kept module-level (not methods) so run_agent works on minimal test shims that
# only expose config/tools/permissions/_ctx, without requiring those helpers.
# ---------------------------------------------------------------------------- #
def agent_readiness_warnings(config: Any) -> list[str]:
    """READ-ONLY, non-blocking preflight checks.

    Returns human-readable warnings. NEVER blocks execution or fails open any
    safety gate; research/planning proceeds regardless. If a check is
    unreliable, it reports that instead of asserting readiness.
    """
    import socket as _socket

    warnings: list[str] = []

    # Ollama (LLM) reachability — parse host/port from config.
    base = getattr(config, "llm_base_url", "") or ""
    host, port = "localhost", 11434
    try:
        from urllib.parse import urlparse

        parsed = urlparse(base)
        if parsed.hostname:
            host = parsed.hostname
        if parsed.port:
            port = parsed.port
    except Exception:
        pass
    try:
        with _socket.create_connection((host, port), timeout=3):
            pass  # reachable
    except Exception as exc:  # noqa: BLE001 - readiness is best-effort
        warnings.append(
            f"Ollama at {host}:{port} appears unreachable ({type(exc).__name__}); "
            "LLM-dependent planning may fail."
        )

    # Outbound research connectivity (best-effort egress probe).
    egress_ok = False
    tried: list[str] = []
    for probe_host in ("duckduckgo.com", "github.com"):
        tried.append(probe_host)
        try:
            with _socket.create_connection((probe_host, 443), timeout=3):
                egress_ok = True
                break
        except Exception:
            continue
    if egress_ok:
        pass  # available
    else:
        warnings.append(
            "Outbound connectivity appears unavailable "
            f"(could not reach {', '.join(tried)}); web research may fail."
        )

    return warnings


def write_agent_artifact(repo: Any, objective: str, result: Any) -> None:
    """Write a non-secret JSON artifact of the run (logs/agent_last.json).

    Contains only existing AgentLoopResult fields plus the objective. Never
    writes secrets, credentials, or raw tool arguments. Best-effort: a write
    failure must never break the agent run.
    """
    import json as _json
    from pathlib import Path as _Path

    try:
        out_dir = repo / "logs"
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {"objective": objective}
        if result is not None and hasattr(result, "to_dict"):
            payload.update(result.to_dict())
        else:
            payload["status"] = "research_failed"
            payload["message"] = "no valid plan produced; no action taken"
        (_Path(out_dir) / "agent_last.json").write_text(
            _json.dumps(payload, indent=2, default=str),
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        # Best-effort artifact; never fail the operator-facing run.
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