# Compatibility Audit — Jarvis v1.0

## Host Environment
- OS: Windows 10
- Python: 3.14.6

## Installed Core Runtime Dependencies
| Package | Installed Version | Status |
|---------|------------------|--------|
| PySide6 | 6.11.1 | Installed |
| watchdog | 6.0.0 | Installed |
| GitPython | 3.1.55 | Installed |
| SpeechRecognition | 3.17.0 | Installed |
| Pillow | 12.3.0 | Installed |
| opencv-python | 5.0.0.93 | Installed |
| ollama | 0.6.2 | Installed |
| pyttsx3 | 2.99 | Installed |
| PyAudio | Not installed | Optional, may be omitted if PortAudio unavailable |
| webrtcvad | Not installed | Optional, only used if present |
| chromadb | Not installed | Optional |
| sentence-transformers | Not installed | Optional |
| PyInstaller | 6.21.0 | Packaging tool |

## Dependency Analysis
### PySide6
- Wheel available for 3.14 on Windows.
- Verified: `ui.main_window` imports and `JarvisWindow` class loads without runtime error apart from display server requirements.

### watchdog / GitPython / SpeechRecognition / Pillow / opencv-python
- Verified installed and importable.
- SpeechRecognition depends on runtime audio capture backends; currently functional in fallback voice mode.

### Audio: PyAudio
- Installation blocked: `portaudio.h` missing, source build fails.
- Hardware is available through `sounddevice`/PortAudio runtime already.
- Recommendation: keep voice input optional; omit PyAudio from v1.0 release if build artifacts do not ship headers.

### AI Components: chromadb / sentence-transformers
- Optional; knowledge engine falls back to SQLite retrieval when unavailable.
- Install only for offline vector-mode deployments.

## PyInstaller + webrtcvad Root Cause
Failure pattern:
- `ImportErrorWhenRunningHook: Failed to import module __PyInstaller_hooks_0_webrtcvad`

Exact chain:
- `modules/voice.py` contains a top-level `try: import webrtcvad`.
- PyInstaller performs AST-level module discovery; conditional import is still discovered as an import statement.
- PyInstaller loads `pyinstaller-hooks-contrib` stdhook `hook-webrtcvad.py`.
- That hook unconditionally calls `copy_metadata('webrtcvad')`.
- Current virtualenv provides the fork distribution `webrtcvad-wheels`, which exposes the module `webrtcvad` but does not provide the `webrtcvad` package metadata expected by the stdhook.
- Missing metadata raises inside the hook during analysis, aborting packaging.

Root cause classification:
- Packaging-level incompatibility between current `webrtcvad-wheels` package layout and `pyinstaller-hooks-contrib` stdhook contract, not a runtime code defect.

Does Jarvis require webrtcvad?
- No. Runtime uses sounddevice playback and VAD capability is optional; if `webrtcvad` is absent, `_HAS_WEBRTCVAD` is False and those features are bypassed.

Can it be excluded?
- The current source import is top-level, so PyInstaller discovers it even under conditional import. For packaging, it either needs to be truly deferred past analysis time or isolated.

Is a custom hook needed?
- If staying on PyInstaller, yes. A custom hook or runtime-hidden import is needed to prevent stdhook execution when the optional package is absent or packaged as a fork wheel.

## Python Version Recommendation

| Candidate | Wheel coverage | PyInstaller stability | Audio/AI stack maturity | Verdict |
|-----------|---------------|----------------------|------------------------|---------|
| Python 3.12.x | broad | stable | best tested | preferred v1.0 release target |
| Python 3.13.x | improving | improving | acceptable | acceptable but watch AI/audio wheels |
| Python 3.14.x | latest | riskier | thinner | retain for active development only |

Recommendation:
- Use Python 3.12 for release-ready Jarvis v1.0.
- Keep 3.14 as active-development interpreter until packaging/audio regression surface shrinks.
- Rationale: dependency wheel availability, PyInstaller compatibility, and lower packaging-block risk outweigh the marginal runtime benefits of 3.14 for this release.

## Release-Readiness Verdict
- Backend: stable.
- UI: import verified; runtime launch depends on UI display backend availability.
- Voice: optional, should degrade gracefully.
- Packaging: blocked by PyInstaller + optional-dependency packaging mismatch; fix scope is packaging/hooks, not architecture.
