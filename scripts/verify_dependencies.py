"""
Verify required dependency availability before the application starts.

This module is the startup dependency gate imported by jarvis.py. Its job is to
fail fast and clearly when a dependency the application genuinely requires is
missing, while NOT blocking startup on optional features (voice, wake-word,
desktop automation, ML backends) whose absence is handled gracefully at
runtime.

Design rules (startup/packaging fix only):
  * REQUIRED dependencies are lightweight core libraries. They are imported to
    confirm they are actually usable; a missing one is fatal (SystemExit 1)
    with a clear, actionable message.
  * OPTIONAL dependencies are audio / wake-word / desktop-automation / ML
    packages. They are checked by SPEC ONLY (importlib.util.find_spec), which
    never executes the module body. This avoids importing the heavy
    onnxruntime / torch / transformers stack merely to perform a startup check
    (which hangs on some Python builds). A missing optional dependency is
    reported as a warning so the user knows the feature is unavailable, but
    startup continues.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Dependency:
    import_name: str
    pip_name: str
    reason: str
    optional: bool = False


# Core libraries the application cannot function without. All of these are
# lightweight and safe to import at startup (none pull the ML stack).
REQUIRED_DEPENDENCIES: tuple[Dependency, ...] = (
    Dependency("yaml", "PyYAML>=6.0", "YAML configuration parsing"),
    Dependency("ollama", "ollama>=0.4.7", "Local LLM runtime client"),
    Dependency("numpy", "numpy>=1.24", "Audio and numeric utilities"),
    Dependency("requests", "requests>=2.31", "HTTP client for integrations"),
    Dependency("bs4", "beautifulsoup4>=4.12", "HTML/web content parsing"),
    Dependency("dotenv", "python-dotenv>=1.0", "Environment variable loading"),
    Dependency("ddgs", "duckduckgo-search>=6.0", "Web search"),
    Dependency("duckduckgo_search", "duckduckgo-search>=6.0", "Web search"),
    Dependency("psutil", "psutil>=5.9", "System monitoring"),
)

# Optional: voice / wake-word / desktop automation / ML. These may transitively
# reach onnxruntime / torch / transformers. They are NOT imported at startup;
# absence is a clear warning, not a fatal error. The application already
# degrades gracefully when these are missing (e.g. voice falls back to a
# keyboard path, and the Web UI / research pipeline never require them).
OPTIONAL_DEPENDENCIES: tuple[Dependency, ...] = (
    Dependency("sounddevice", "sounddevice>=0.5.1", "Audio capture", optional=True),
    Dependency("soundfile", "soundfile>=0.12.1", "Wave file I/O", optional=True),
    Dependency("openwakeword", "openwakeword>=0.6.0", "Wake-word detection", optional=True),
    Dependency("vosk", "vosk>=0.3.45", "Offline speech recognition", optional=True),
    Dependency("pyttsx3", "pyttsx3>=2.90", "Offline text-to-speech", optional=True),
    Dependency("pyautogui", "pyautogui>=0.9.54", "Desktop automation", optional=True),
    Dependency("pygetwindow", "pygetwindow>=0.0.9", "Window enumeration and focus", optional=True),
)


def _spec_available(import_name: str) -> bool:
    """Lightweight availability probe that does NOT execute module code.

    Uses importlib.util.find_spec so heavy optional packages (which import
    torch / onnxruntime at module top level) are never imported during the
    startup check. This is the feature-level check: we determine whether the
    package is installed without running its body, which avoids the import
    hang seen on some Python builds.
    """
    try:
        return importlib.util.find_spec(import_name) is not None
    except Exception:
        # A broken/partial install: treat as unavailable rather than crash.
        return False


def _import_available(import_name: str) -> bool:
    """Confirm a module is both present and importable (runs for REQUIRED only)."""
    try:
        importlib.import_module(import_name)
        return True
    except Exception:
        return False


def check_dependencies() -> None:
    """Verify startup dependencies without importing heavy optional packages.

    - Required deps are imported (lightweight core libs); a missing one is
      fatal (SystemExit 1) with a clear, actionable message.
    - Optional deps (audio / wake-word / ML) are checked by spec only -- never
      imported at startup. A missing optional dep is reported as a warning so
      the user knows the feature is unavailable, but startup continues.
    """
    missing_required: list[Dependency] = []
    missing_optional: list[Dependency] = []

    # Required: truly verify importability (these are all lightweight).
    for dep in REQUIRED_DEPENDENCIES:
        if not _import_available(dep.import_name):
            missing_required.append(dep)

    # Optional: feature-level probe only -- never import the module body.
    for dep in OPTIONAL_DEPENDENCIES:
        if not _spec_available(dep.import_name):
            missing_optional.append(dep)

    # Diagnostics: surface missing optional deps clearly (not silently hidden).
    for dep in missing_optional:
        sys.stderr.write(
            f"WARNING: optional dependency missing: {dep.pip_name} "
            f"({dep.reason}). Related features will be disabled.\n"
        )
        sys.stderr.flush()

    if missing_required:
        lines = ["ERROR: Missing required dependency(ies):", ""]
        for dep in missing_required:
            lines.append(f"  - {dep.pip_name}: {dep.reason}")
        lines.extend([
            "",
            "Install command:",
            f"  py -m pip install {' '.join(dep.pip_name for dep in missing_required)}",
            "",
            "Jarvis cannot start until these dependencies are installed.",
        ])
        message = "\n".join(lines)
        sys.stderr.write(f"{message}\n")
        sys.stderr.flush()
        raise SystemExit(1)


def main() -> int:
    check_dependencies()
    print("Dependency check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
