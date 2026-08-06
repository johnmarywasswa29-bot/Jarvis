from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

IMPORTS = [
    ("config", "modules.config", "JarvisConfig"),
    ("logger", "modules.logger", "setup_logger"),
    ("memory", "modules.memory", "JarvisMemory"),
    ("tools", "modules.tools", "ToolRegistry"),
    ("vision", "modules.vision", "VisionModule"),
    ("voice", "modules.voice", "VoiceModule"),
    ("brain", "modules.brain", "JarvisBrain"),
    ("jarvis_main", "jarvis", "JarvisAssistant"),
]


def main() -> int:
    failed = 0
    print("--- Imports ---")
    for label, mod, sym in IMPORTS:
        try:
            m = __import__(mod, fromlist=[sym])
            obj = getattr(m, sym)
            print(f"OK  {label}: {mod}.{sym}")
        except Exception as exc:
            print(f"FAIL {label}: {mod}.{sym} -> {exc}")
            failed += 1

    print(f"\n{len(IMPORTS)-failed}/{len(IMPORTS)} imports OK")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
