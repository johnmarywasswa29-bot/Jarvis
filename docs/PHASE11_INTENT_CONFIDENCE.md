# Phase 11 — Intent Confidence Engine

## Overview
Adds a confidence-based decision layer that replaces regex-only routing with a multi-signal classifier while preserving `FastIntentRouter` as one input signal.

## Architecture
```
User prompt
    |
    v
IntentAnalyzer.analyze()
    |
    +-> _keyword_signals()       40% keyword + 40% regex
    +-> _router_signal()         FastIntentRouter output
    +-> _memory_relevance()      long-term memory similarity
    +-> EntityExtractor          required entities
    +-> ConfidenceScorer         historical success + ambiguity
    |
    v
IntentResult {
    intent,
    confidence 0.0-1.0,
    entities,
    strategy,
    explanation,
    source_signals,
    latency_ms
}
    |
    v
ExecutionPolicy.decide()
    |
    +-> confidence >= 0.98 -> execute_immediately
    +-> confidence 0.80-0.97 + destructive -> require_confirmation
    +-> confidence 0.70-0.79 -> ask_clarification
    +-> confidence < 0.70 -> llm_reasoning
```

## Confidence Formula
```
base = keyword * 0.40
     + regex * 0.40
     + entity * 0.15
     + app_lookup * 0.10
     + memory * 0.05

if historical_success is not None:
    base *= max(0.5, min(1.5, 0.5 + historical_success))

base -= ambiguity_penalty * 0.15

confidence = clamp(base, 0.0, 1.0)
```

## Execution Policy
- `>= 0.98`: execute immediately
- `0.80-0.97`: require confirmation for destructive actions
- `0.70-0.79`: ask one clarification question
- `< 0.70`: forward to LLM reasoning

Destructive actions requiring confirmation:
- filesystem.delete
- system_control.shutdown
- system_control.restart
- system_control.format
- system_control.uninstall
- filesystem.overwrite
- email.send

## Learning
`IntentAnalyzer.learn(prompt, success, actual_intent=None)` records outcomes into:
- in-memory `ConfidenceScorer._local` stats
- optional `MemoryManager` semantic memory under tag `intent_stats`

Success increases historical confidence for that intent.
Correction decreases it.
Repeated successes compound faster.

## Logging
Every `analyze()` call appends a JSON line to `logs/intent.log`:
```json
{"ts":"2026-08-01T12:00:00","prompt":"...","intent":"...","confidence":0.99,"strategy":"execute_immediately","entities":{...},"latency_ms":0.12,"explanation":"..."}
```

## Performance
- Target average classification latency: <5 ms
- Benchmarked via `IntentAnalyzer.benchmark(prompts)`
- Tested with 10 mixed prompts

## Files
- `modules/intent/__init__.py`
- `modules/intent/result.py` — `IntentResult`, `ExecutionPolicy`, `ExecutionStrategy`
- `modules/intent/entities.py` — `EntityExtractor`
- `modules/intent/scorer.py` — `ConfidenceScorer`
- `modules/intent/analyzer.py` — `IntentAnalyzer`
- `tests/test_intent_confidence.py` — 70 routing/unit tests
- `logs/intent.log` — runtime decision log

## Backward Compatibility
`FastIntentRouter` is preserved. `IntentAnalyzer` accepts it via `router=` and uses `router.route(prompt)` as one signal among many. Existing tool routing remains functional.

## Verification
- Focused Phase 11 tests: `Ran 70 tests ... OK`
- Combined suite: `Ran 111 tests ... OK`
- No regressions in existing memory, voice, workspace, goals, or pipeline tests.
