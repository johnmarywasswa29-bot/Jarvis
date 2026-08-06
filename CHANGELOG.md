# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-08-06

### Added
- Runtime architecture with centralized `RuntimeContext` factory and lifecycle
- Memory system with persistence, retrieval, and export
- RAG/knowledge integration with embedder cache and offline-safe behavior
- Intent engine with confidence scoring and structured analysis
- Habit learning with detection, scoring, decay, and storage
- Workflow execution engine with history, state management, and recovery
- Workspace awareness with snapshots, project context, and history
- Proactive assistant with reminders, history, and state tracking
- Plugin SDK with discovery, loading, lifecycle, and soft sandboxing
- Calendar plugin with scheduling, free blocks, conflicts, and proactive hooks
- Event bus with typed events, telemetry bridge, and logger adapter
- Startup optimization with lazy embedder loading, profiler, and cache reuse
- Ollama health monitoring with degraded mode, recovery, and background monitor
- Desktop control, code execution, filesystem, calculator, and web search tools
- GUI main window with status indicators and Ollama integration
- Installer scripts and PyInstaller packaging support

### Changed
- Timezone modernization: removed deprecated `datetime.utcnow()` / `datetime.utcfromtimestamp()` usage across production and tests
- Security hardening:
  - Calculator tool replaced `eval()` with AST-based safe math evaluator
  - Desktop control removed shell-injection fallback; allow-list only
  - Filesystem tool enforced allowed-root path checks for list/read/write/delete/move
- Dependency alignment between `requirements.txt` and `pyproject.toml`
- Documentation expanded with phase/system docs and release audit

### Fixed
- Regression suite stabilized at 482 passing tests
- Ollama health integration wired into runtime startup and shutdown
- Memory leak containment across runtime cycles
- SQLite integrity checks and safe persistence behavior

### Security
- Resolved High-risk desktop tool shell-execution path
- Resolved High-risk filesystem arbitrary write/read/move path
- Resolved High-risk calculator code-execution surface via `eval()`

### Known Limitations
- Plugin isolation remains in-process only; no OS-level sandbox
- Calendar provider token storage is plaintext without encryption/ACL hardening
- Windows installer uses PyInstaller EXE; no NSIS/InnoSetup/WiX wrapper yet
- Some optional dependencies require manual enablement for full feature coverage
