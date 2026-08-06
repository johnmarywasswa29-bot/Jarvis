"""Intent confidence analyzer."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from modules.intent.entities import EntityExtractor
from modules.intent.result import ExecutionPolicy, ExecutionStrategy, IntentResult
from modules.intent.scorer import ConfidenceScorer

logger = logging.getLogger("intent")

_INTENT_LOG_PATH = Path("logs/intent.log")


class IntentAnalyzer:
    def __init__(
        self,
        *,
        router: Any = None,
        memory: Any = None,
        execution_policy: Optional[ExecutionPolicy] = None,
    ) -> None:
        self.router = router
        self.memory = memory
        self.policy = execution_policy or ExecutionPolicy()
        self.scorer = ConfidenceScorer(memory=memory)
        self.extractor = EntityExtractor()
        _INTENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    def analyze(self, prompt: str) -> IntentResult:
        t0 = time.perf_counter()
        text = (prompt or "").strip()
        low = text.lower()
        keyword_score, regex_score, candidate_intent, candidate_entities = self._keyword_signals(low, text)
        router_signal = self._router_signal(text)
        if router_signal:
            candidate_intent = router_signal.get("intent", candidate_intent)
            if not candidate_entities and router_signal.get("entities"):
                candidate_entities = router_signal["entities"]
        memory_score = self._memory_relevance(text)
        historical = self.scorer.historical(candidate_intent)
        entity_score = self._entity_confidence(candidate_intent, candidate_entities)
        app_lookup_score = self._app_lookup_confidence(candidate_intent, candidate_entities)
        ambiguity_penalty = self._ambiguity(low, candidate_intent)
        confidence = self.scorer.score(
            keyword_score=keyword_score,
            regex_score=regex_score,
            entity_score=entity_score,
            app_lookup_score=app_lookup_score,
            memory_score=memory_score,
            ambiguity_penalty=ambiguity_penalty,
            historical_success=historical,
        )
        result = IntentResult(
            intent=candidate_intent or "llm.chat",
            confidence=confidence,
            entities=candidate_entities or {},
            explanation=self._explain(keyword_score, regex_score, entity_score, app_lookup_score, memory_score, ambiguity_penalty, historical),
            source_signals={
                "keyword": keyword_score,
                "regex": regex_score,
                "entity": entity_score,
                "app_lookup": app_lookup_score,
                "memory": memory_score,
                "historical": historical,
                "ambiguity": ambiguity_penalty,
            },
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )
        result.strategy = self.policy.decide(result)
        self._log(text, result)
        return result

    def learn(self, prompt: str, success: bool, actual_intent: Optional[str] = None) -> None:
        if actual_intent:
            self.scorer.record(actual_intent, success)
            return
        try:
            result = self.analyze(prompt)
            self.scorer.record(result.intent, success)
        except Exception:
            pass

    def _router_signal(self, prompt: str) -> Optional[dict[str, Any]]:
        if self.router is None:
            return None
        try:
            return self.router.route(prompt)
        except Exception:
            return None

    def _keyword_signals(self, low: str, original: str) -> tuple[float, float, str, dict[str, Any]]:
        scores: list[tuple[float, float, str, dict[str, Any]]] = []
        if low.startswith("list files") or low.startswith("list folder") or low.startswith("show files"):
            source = original.split(" ", 3)[-1] if " " in original else "."
            scores.append((0.85, 0.9, "filesystem.list", {"source": source}))
        elif low.startswith("read ") or low.startswith("open file "):
            source = original.split(" ", 2)[-1] if " " in original else "."
            scores.append((0.9, 0.95, "filesystem.read", {"source": source}))
        elif low.startswith("write ") or low.startswith("create "):
            source = original.split(" ", 2)[-1] if " " in original else "output.txt"
            scores.append((0.85, 0.9, "filesystem.write", {"source": source}))
        elif low.startswith("delete "):
            source = original.split(" ", 1)[1].strip() if " " in original else ""
            scores.append((0.7, 0.8, "filesystem.delete", {"source": source}))
        elif low.startswith("screenshot") or "take screenshot" in low or "capture screen" in low:
            scores.append((0.95, 0.95, "desktop.screenshot", {"action": "screenshot"}))
        elif low.startswith("click at "):
            scores.append((0.95, 0.95, "desktop.click", {"action": "click"}))
        elif low.startswith("type "):
            text = original.split(" ", 1)[1] if " " in original else ""
            scores.append((0.9, 0.9, "desktop.type", {"text": text}))
        elif low.startswith("press "):
            key = original.split(" ", 1)[1].strip() if " " in original else "enter"
            scores.append((0.9, 0.9, "desktop.press", {"key": key}))
        elif low.startswith("open ") or low.startswith("launch "):
            app = original.split(" ", 1)[1].strip() if " " in original else ""
            scores.append((0.9, 0.9, "desktop.open_application", {"application": app}))
        elif low.startswith("switch to "):
            app = original.split(" ", 2)[-1].strip() if " " in original else ""
            scores.append((0.85, 0.85, "desktop.open_application", {"application": app}))
        elif low.startswith("close window"):
            scores.append((0.9, 0.9, "desktop.click", {"action": "close"}))
        elif low.startswith("full screen") or low.startswith("minimize window"):
            scores.append((0.9, 0.9, "desktop.click", {"action": low.split(" ")[0]}))
        elif low.startswith("lock screen"):
            scores.append((0.9, 0.95, "system_control.shutdown", {"action": "lock"}))
        elif low.startswith("sleep"):
            scores.append((0.9, 0.95, "system_control.restart", {"action": "sleep"}))
        elif low.startswith("run python") or low.startswith("python script") or low.startswith("execute python"):
            scores.append((0.8, 0.85, "code_execution.run", {"code": original}))

        if low.startswith("search ") or low.startswith("look up ") or "search for" in low or low.startswith("find "):
            scores.append((0.85, 0.85, "web_search.search", {"query": original}))
        if low.startswith("calculate ") or low.startswith("compute ") or low.startswith("what is "):
            expr = original.split(" ", 1)[1].strip() if " " in original else ""
            if self._looks_like_math(expr):
                scores.append((0.85, 0.9, "calculator.evaluate", {"expression": expr}))
        if "volume up" in low:
            scores.append((0.95, 0.95, "system_control.volume_up", {"action": "volume_up"}))
        if "volume down" in low:
            scores.append((0.95, 0.95, "system_control.volume_down", {"action": "volume_down"}))
        if "mute" in low:
            scores.append((0.95, 0.95, "system_control.mute", {"action": "mute"}))
        if low.startswith("shut down") or low.startswith("shutdown"):
            scores.append((0.9, 0.95, "system_control.shutdown", {"action": "shutdown"}))
        if low.startswith("restart"):
            scores.append((0.9, 0.95, "system_control.restart", {"action": "restart"}))
        if low.startswith("organize my day") or low.startswith("organize today") or low.startswith("plan my day"):
            scores.append((0.65, 0.65, "planning.organize_day", {"query": original}))

        if not scores:
            return 0.0, 0.0, "llm.chat", {}
        best = max(scores, key=lambda x: (x[0] + x[1]))
        return best

    @staticmethod
    def _looks_like_math(expr: str) -> bool:
        expr = expr.strip()
        if not expr:
            return False
        expr = expr.replace("×", "*").replace("x", "*").lower()
        tokens = re.findall(r"[0-9]+(?:\.[0-9]+)?|[+\-*/%^]|plus|minus|times|divided|by|mod|power", expr)
        numeric = sum(1 for t in tokens if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", t))
        ops = len(tokens) - numeric
        return (numeric + ops) >= 2 and numeric > 0

    def _entity_confidence(self, intent: str, entities: dict[str, Any]) -> float:
        if not entities:
            return 0.2
        if intent == "desktop.open_application":
            return 0.95 if entities.get("application") else 0.3
        if intent in {"filesystem.delete", "filesystem.read", "filesystem.write", "filesystem.list"}:
            return 0.95 if entities.get("source") else 0.3
        if intent == "web_search.search":
            return 0.95 if entities.get("query") else 0.3
        if intent in {"system_control.shutdown", "system_control.restart", "system_control.mute", "system_control.volume_up", "system_control.volume_down"}:
            return 0.95
        if intent == "calculator.evaluate":
            expr = (entities.get("expression") or "").strip()
            return 0.95 if expr else 0.3
        return 0.7

    def _app_lookup_confidence(self, intent: str, entities: dict[str, Any]) -> float:
        if intent != "desktop.open_application":
            return 0.8
        app = (entities.get("application") or "").strip().lower()
        known = ["chrome", "firefox", "edge", "notepad", "vscode", "code", "spotify", "discord", "teams", "zoom", "explorer", "calculator", "terminal", "cmd", "powershell", "word", "excel", "powerpoint", "outlook"]
        if not app:
            return 0.2
        return 0.95 if any(app == k or app.startswith(k) for k in known) else 0.5

    def _memory_relevance(self, prompt: str) -> float:
        if self.memory is None:
            return 0.0
        try:
            hits = self.memory.search(prompt, limit=3)
            if not hits:
                return 0.0
            best = hits[0]
            score = float(best.get("score", 0.0))
            return min(1.0, max(0.0, score))
        except Exception:
            return 0.0

    def _ambiguity(self, low: str, intent: str) -> float:
        if not intent or intent == "llm.chat":
            return 0.0
        if low.count(" ") <= 1 and intent in {"desktop.open_application", "system_control.shutdown", "system_control.restart"}:
            return 0.05
        if intent == "filesystem.delete":
            return 0.1
        if intent == "planning.organize_day":
            return 0.2
        return 0.0

    def _explain(self, keyword_score: float, regex_score: float, entity_score: float, app_lookup_score: float, memory_score: float, ambiguity_penalty: float, historical: float) -> str:
        parts = []
        if keyword_score > 0:
            parts.append(f"keyword={keyword_score:.2f}")
        if regex_score > 0:
            parts.append(f"regex={regex_score:.2f}")
        if entity_score > 0:
            parts.append(f"entity={entity_score:.2f}")
        if app_lookup_score > 0:
            parts.append(f"app={app_lookup_score:.2f}")
        if memory_score > 0:
            parts.append(f"memory={memory_score:.2f}")
        if ambiguity_penalty > 0:
            parts.append(f"ambiguity={ambiguity_penalty:.2f}")
        parts.append(f"hist={historical:.2f}")
        return "; ".join(parts) if parts else "no strong signal"

    def _log(self, prompt: str, result: IntentResult) -> None:
        try:
            entry = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
                "intent": result.intent,
                "confidence": round(result.confidence, 4),
                "strategy": result.strategy.value,
                "entities": result.entities,
                "latency_ms": round(result.latency_ms, 3),
                "explanation": result.explanation,
            }
            line = json.dumps(entry, ensure_ascii=False)
            with open(_INTENT_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as exc:
            logger.debug("Intent log failed: %s", exc)

    def benchmark(self, prompts: list[str]) -> dict[str, Any]:
        times: list[float] = []
        for prompt in prompts:
            t0 = time.perf_counter()
            self.analyze(prompt)
            times.append(time.perf_counter() - t0)
        return {
            "count": len(times),
            "avg_ms": round((sum(times) / len(times)) * 1000, 3) if times else 0.0,
            "max_ms": round(max(times) * 1000, 3) if times else 0.0,
            "min_ms": round(min(times) * 1000, 3) if times else 0.0,
        }
