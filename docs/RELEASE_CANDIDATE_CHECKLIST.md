# Release Candidate Preparation Checklist

Read-only preparation. No production code changes.

## A. Candidate version

**Proposed RC:** `v1.1.0-rc.1`

Basis:
- Current tagged version: `v1.0.0`
- Completed work since v1.0.0:
  - PHASE 5.2 Optimization 1/2 (lazy KnowledgeEngine, lazy ChromaDB)
  - Orchestration subsystem (research → proposal → validation → confirmation → execution)
  - Confirmation API + UI integration
  - End-to-end stabilization audit
- Semver: minor version bump for new subsystem; `-rc.1` suffix for release candidate

## B. Feature-completion status

| Feature | Status |
|---------|--------|
| RuntimeContext + DI factory | COMPLETE |
| Memory system | COMPLETE |
| RAG/knowledge with lazy init | COMPLETE |
| Intent engine | COMPLETE |
| Habit learning | COMPLETE |
| Workflow execution + confirmation gate | COMPLETE |
| Workspace awareness | COMPLETE |
| Proactive assistant | COMPLETE |
| Plugin SDK + calendar plugin | COMPLETE |
| EventBus + telemetry + logger | COMPLETE |
| Ollama health + degraded mode | COMPLETE |
| Orchestration layer | COMPLETE |
| Confirmation API | COMPLETE |
| Confirmation UI | COMPLETE |
| Paper/simulation execution | COMPLETE |
| Security hardening (v1.0) | COMPLETE |
| Timezone modernization | COMPLETE |

## C. Regression status

| Metric | Value |
|--------|-------|
| Total tests | 641 |
| Passed | 641 |
| Failed | 0 |
| Warnings | 1 (chromadb deprecation, external) |
| Duration | ~620.75s |
| Baseline delta | 0 failures introduced |

Test command:
```
cd "/c/Users/User NA/Desktop/jarvis" && . .venv/Scripts/activate && python -m pytest tests/ -q -p no:cacheprovider
```

## D. Performance status

| Metric | Measured |
|--------|----------|
| Cold build_runtime() | ~4520 ms |
| First knowledge access | ~215 ms |
| Second knowledge access | 0.2 ms |
| First orchestration init | ~322 ms |
| Subsequent orchestration | 0.3 ms |
| Duplicate services | None detected |
| Resource leaks | None detected |

## E. Security status

- Security policy documented: `docs/SECURITY.md`
- Supported versions: 1.x
- v1.0 hardening retained:
  - Shell execution restricted to allow-list
  - Calculator uses AST whitelist
  - Filesystem tool enforces allowed-root
  - Intent/LLM logs scrubbed
- No new security concerns introduced in orchestration/confirmation UI
- Calendar tokens remain plaintext (known limitation)

## F. Documentation status

| Document | Status |
|----------|--------|
| README.md | Present |
| CHANGELOG.md | Needs update for v1.1.0-rc.1 |
| COMPATIBILITY.md | Present |
| SECURITY.md | Present |
| API.md | Present |
| CONTRIBUTING.md | Present |
| ORCHESTRATION_STABILIZATION_AUDIT.md | Present |
| POST_INTEGRATION_RELEASE_READINESS_AUDIT.md | Present |
| PHASE5_2_OPTIMIZATION_REPORT.md | Present |
| CONFIRMATION_UI_INTEGRATION_PLAN.md | Present |
| CONFIRMATION_API_UX_PLAN.md | Present |
| INSTALL.md / RUN.md | Not present; covered by README + run.bat |

Gaps:
- CHANGELOG.md needs v1.1.0-rc.1 entry summarizing new subsystems
- No dedicated INSTALL.md; README quick-start covers installation
- No dedicated RUN.md; run.bat + README cover execution

## G. Known limitations

| Limitation | Severity | Notes |
|------------|----------|-------|
| Confirmation persistence across restarts | P3 | Not approved; pending confirmations lost on restart |
| Confirmation expiration/timeouts | P3 | Not approved; pending confirmations remain until acted on |
| Multi-step confirmation UI coverage | P3 | Backend supports; UI exercises single step |
| Paper/simulation-only orchestration | P3 | No live brokerage; by design |
| Plugin isolation in-process only | P3 | No OS-level sandbox |
| Calendar provider tokens plaintext | P3 | No encryption/ACL hardening |
| Windows installer uses PyInstaller EXE | P3 | No NSIS/InnoSetup/WiX wrapper |

## H. P0/P1/P2/P3 status

| ID | Classification | Description |
|----|----------------|-------------|
| — | P0 BLOCKER | None |
| — | P1 HIGH | None |
| 1 | P2 MEDIUM | No end-to-end runtime integration test for full user request → RuntimeContext → orchestration → UI → confirmation → execution path |
| 2 | P2 MEDIUM | No UI test for multiple pending confirmations with independent approve/reject targeting correct IDs |
| 3 | P2 MEDIUM | No UI test for event arriving after confirmation already terminal |
| 4 | P3 LOW | No pending-count indicator in sidebar/status bar |
| 5 | P3 LOW | Confirmation panel does not auto-switch on first pending event |

## I. Release artifact cleanliness

Artifacts that should NOT be included in release:

| Path | Type | Action |
|------|------|--------|
| `.venv/` | Virtualenv | Excluded by .gitignore |
| `__pycache__/` | Python cache | Excluded by .gitignore |
| `build/`, `build_test/` | PyInstaller intermediates | Should be in .gitignore or cleaned |
| `dist/`, `dist_test/` | PyInstaller outputs | Should be in .gitignore or cleaned |
| `Release/Jarvis.exe` | Built installer artifact | 280 MB binary; should not be in repo |
| `data/rc_*/` | Runtime session data | Should be ignored |
| `data/knowledge_data/` | Runtime data | Should be ignored |
| `logs/` | Runtime logs | Partially ignored |
| `tests/tmp_*/` | Test temp data | Should be ignored |
| `*.pyc`, `*.wav`, `*.mp3`, `*.pcm` | Generated/media | Excluded by .gitignore |
| `pyinstaller.log`, `pyinstaller_debug.log` | Build logs | Should be ignored |
| `tmp/` | Temp directory | Should be ignored |
| `agent/` | Empty directory | Should be ignored or removed |
| `=6.0` | Unknown file | Should be removed or explained |
| `how --stat v1.0.0` | Unknown directory | Should be removed or explained |

Current .gitignore covers: `__pycache__/`, `.venv/`, `assets/*`, `logs/*`, `memory/*`, `.vscode/`, `.idea/`, `.DS_Store`, `Thumbs.db`, `*.wav`, `*.mp3`, `*.pcm`

Missing from .gitignore:
- `build/`
- `build_test/`
- `dist/`
- `dist_test/`
- `Release/`
- `data/rc_*/`
- `data/knowledge_data/`
- `tests/tmp_*/`
- `pyinstaller.log`
- `pyinstaller_debug.log`
- `tmp/`
- `agent/`
- `=6.0`
- `how --stat v1.0.0`

## J. Installation/run verification

**Installation:**
- Python 3.11+ required (3.14 active in dev)
- `uv` or `pip` for dependencies
- Ollama + llama3 model required for LLM features
- `run.bat` launches UI with PySide6 fallback to CLI
- `release.bat` triggers PyInstaller build

**Current run command documented:**
```
.venv\Scripts\python.exe -m ui.main_window
```

**Test command documented:**
```
python -m pytest tests/ -q -p no:cacheprovider
```

## K. Final release checklist

- [ ] Update CHANGELOG.md with v1.1.0-rc.1 entries
- [ ] Update README.md if architecture sections reference old paths
- [ ] Expand .gitignore to exclude build artifacts, data dirs, temp files
- [ ] Remove or explain stray files: `=6.0`, `how --stat v1.0.0`, `agent/`
- [ ] Clean untracked artifacts before tagging: `build/`, `dist/`, `Release/`, `data/rc_*/`, `tests/tmp_*/`, `tmp/`
- [ ] Verify `release.bat` produces clean installer from clean tree
- [ ] Tag `v1.1.0-rc.1` after documentation updates
- [ ] Publish release notes summarizing:
  - Lazy initialization optimizations
  - Orchestration layer
  - Confirmation API + UI
  - 641-test baseline
  - Performance measurements
  - Known limitations

## L. Items explicitly deferred to next development cycle

- Confirmation persistence across restarts
- Confirmation expiration/timeouts
- Multi-step workflow confirmation UI
- Pending-count badge in sidebar/status bar
- Auto-switch to confirmation panel
- End-to-end runtime integration test (P2)
- Multiple pending confirmation UI test (P2)
- Terminal-event UI test (P2)
- OS-level plugin sandbox
- Subprocess resource limits
- Optional OS keyring for tokens
- Complete yaml.safe_load() migration
- NSIS/InnoSetup/WiX installer wrapper

---

**Release-readiness classification: RELEASE READY with documentation cleanup required before tagging.**

No production code changes needed. No P0/P1 blockers. 3 P2 test gaps remain but do not block RC.
