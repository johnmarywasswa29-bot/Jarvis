# Jarvis - Local Desktop AI Assistant

A privacy-first, always-on desktop assistant that listens for a wake word,
talks to you, searches the web, controls your computer, runs Python code,
and organizes files -- all locally when possible.

## Architecture

Microphone -> Speech-to-Text -> LLM Brain -> Tools / TTS -> Speaker

Wake Word: `pvporcupine` (offline, configurable keyword)
STT      : `vosk` + webrtcvad VAD
LLM      : `ollama` + `llama3` (fully local)
TTS      : `pyttsx3`
Memory   : JSON-backed session memory with keyword search
Vision   : Screenshot capture; limited description unless extended

## Quick Start (Windows)

1. Install Ollama from https://ollama.ai and pull llama3:
     ollama pull llama3

2. Install Python 3.11+ with uv (pip alternative):
     python -m pip install uv

3. Run Jarvis installer from project folder:
     bash scripts/install.sh

4. Start Jarvis:
     run.bat

5. Place your custom wake-word `.ppn` at:
     assets/jarvis.ppn

   Get keywords at https://picovoice.ai/platform/porcupine/
   Optional PICOVOICE_ACCESS_KEY env var (not required for local checks).

6. Say "Jarvis" and a command, e.g.:
     Jarvis, search for the latest AI papers today.
     Jarvis, what is on my screen?
     Jarvis, open Notepad.
     Jarvis, calculate the first 100 fibonacci numbers.

## Features

### Goal Manager
- Structured goals with steps, priority, and metadata.
- Persists goals to `memory/goals.json` across restarts.
- Planner injects active goals into prompts for goal-aware planning.
- API: `modules.goals.GoalManager`, `Goal`, `GoalStatus`.
- Task Queue completes update goal progress by matching `goal_id` and `step_id`.

### Task Queue
- Priority execution with status transitions and retries.
- Supports task dependencies and cycle detection.
- Scheduler tick promotes time-based `WAITING` tasks.
- Persists state to SQLite-backed storage.
- API: `task_queue.TaskQueue`, `Task`, `TaskStatus`, `TaskPriority`.

### Planner V3
- Validated structured plans with estimated duration and confidence.
- Enforces unique step IDs and dependency consistency.
- Legacy plan compatibility via `Plan.to_legacy()`.

## Documentation

See `docs/` for module-level notes:
- `TASK_QUEUE.md`
- `GOAL_MANAGER.md`
- `PLANNER.md`

## Release Candidate

v1.1.0 adds orchestration, proposal, research, simulation, and confirmation subsystems, with related tests under `tests/`.
