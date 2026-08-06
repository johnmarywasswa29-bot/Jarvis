"""
Verify required dependency availability before application imports.

This module is the single source of truth for required package metadata.
scan_dependencies.py / audit_dependencies.py reuse these definitions.
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Dependency:
    import_name: str
    pip_name: str
    reason: str


REQUIRED_DEPENDENCIES: tuple[Dependency, ...] = (
    Dependency("yaml", "PyYAML>=6.0", "YAML configuration parsing"),
    Dependency("ollama", "ollama>=0.4.7", "Local LLM runtime client"),
    Dependency("numpy", "numpy>=1.24", "Audio and numeric utilities"),
    Dependency("sounddevice", "sounddevice>=0.5.1", "Audio capture"),
    Dependency("soundfile", "soundfile>=0.12.1", "Wave file I/O"),
    Dependency("pvporcupine", "pvporcupine>=3.0.5", "Wake-word detection engine"),
    Dependency("pvrecorder", "pvrecorder>=1.2.7", "Audio recorder for wake-word detection"),
    Dependency("vosk", "vosk>=0.3.45", "Offline speech recognition"),
    Dependency("requests", "requests>=2.31", "HTTP client for integrations"),
    Dependency("bs4", "beautifulsoup4>=4.12", "HTML/web content parsing"),
    Dependency("dotenv", "python-dotenv>=1.0", "Environment variable loading"),
    Dependency("pyttsx3", "pyttsx3>=2.90", "Offline text-to-speech"),
    Dependency("pyautogui", "pyautogui>=0.9.54", "Desktop automation"),
    Dependency("pygetwindow", "pygetwindow>=0.0.9", "Window enumeration and focus"),
    Dependency("ddgs", "duckduckgo-search>=6.0", "Web search"),
    Dependency("duckduckgo_search", "duckduckgo-search>=6.0", "Web search"),
    Dependency("psutil", "psutil>=5.9", "System monitoring"),
)


def check_dependencies() -> None:
    missing = []
    for dep in REQUIRED_DEPENDENCIES:
        try:
            importlib.import_module(dep.import_name)
        except ImportError:
            missing.append(dep)

    if not missing:
        return

    lines = [
        "ERROR: Missing required dependency(ies):",
        "",
    ]
    for dep in missing:
        lines.append(f"  - {dep.pip_name}: {dep.reason}")
    lines.extend([
        "",
        "Install command:",
        f"  py -m pip install {' '.join(dep.pip_name for dep in missing)}",
        "",
        "Jarvis cannot start until these dependencies are installed.",
    ])
    message = "\n".join(lines)

    sys.stderr.write(f"{message}\n")
    sys.stderr.flush()
    sys.stdout.flush()
    raise SystemExit(1)


def main() -> int:
    check_dependencies()
    print("Dependency check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
