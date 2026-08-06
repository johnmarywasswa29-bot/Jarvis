# Jarvis v1.0 Release

Release Date: 2026-08-06
Version: 1.0.0

## Release Checklist

- [x] Full regression suite green: 482 passed, 0 failed
- [x] Security hardening verified
- [x] Startup optimization verified
- [x] Ollama health integration verified
- [x] LICENSE added
- [x] CHANGELOG.md created
- [x] Hardcoded absolute paths removed from config.yaml
- [x] Sensitive prompt logging removed
- [x] Documentation complete

## System Requirements

- OS: Windows 10 or later
- Python: 3.11 or later
- RAM: 16 GB recommended
- Disk: 5 GB free for models and dependencies
- Optional: Ollama with llama3 model for local LLM

## Installation

1. Clone or extract the Jarvis repository.
2. Install dependencies:
   ```
   python -m pip install -r requirements.txt
   ```
3. Ensure Ollama is installed and running (if using local LLM).
4. Configure `config.yaml` as needed.
5. Run:
   ```
   python jarvis.py
   ```
   Or use the packaged executable in `Release/Jarvis.exe`.

## Upgrade Notes

- v1.0 introduces centralized runtime factory (`build_runtime`).
- `JarvisConfig.from_yaml()` now expects relative paths; absolute hardcoded paths are removed.
- Intent logs no longer contain raw user prompts for privacy.
- Calculator tool uses AST-based safe evaluation; `eval()` is removed.
- Desktop control tool uses an allow-list; arbitrary shell targets are blocked.
- Filesystem tool enforces allowed-root checks.

## Known Limitations

- Plugin isolation remains in-process only; no OS-level sandbox.
- Calendar provider token storage is plaintext without encryption.
- Windows installer uses PyInstaller EXE; no NSIS/InnoSetup/WiX wrapper yet.
- Optional dependencies require manual enablement for full feature coverage.

## Final Benchmark Summary

| Metric | Value |
|--------|-------|
| Cold startup | ~11258 ms |
| Warm startup | ~105 ms |
| Embedder first touch | ~25787 ms |
| Event bus publish avg | ~24 µs |
| Telemetry avg | ~10 µs |
| Full regression | 482 passed, 0 failed |

## Remaining Technical Debt

- Missing Windows installer wrapper (NSIS/InnoSetup/WiX)
- Missing `assets/jarvis.ico` and `assets/jarvis.ppn`
- Plugin sandbox remains soft
- Calendar token ACL hardening pending
- Subprocess resource limits missing in execution manager

## License

MIT License — see `LICENSE` file.
