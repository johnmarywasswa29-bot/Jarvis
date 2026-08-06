#!/usr/bin/env bash
set -euo pipefail

ROOT="/c/Users/User NA/Desktop/jarvis"
cd "$ROOT"

echo "[1/6] Creating .venv with uv..."
uv venv --python 3.11 -q

echo "[2/6] Installing deps..."
uv pip install --python .venv/Scripts/python.exe -r requirements.txt -q

echo "[3/6] Creating required folders..."
mkdir -p "$ROOT/assets/stt_models" "$ROOT/logs"

echo "[4/6] Installing Ollama helper..."
if ! command -v ollama >/dev/null 2>&1; then
  echo "Installing Ollama..."
  curl -fsSL https://ollama.ai/install.sh | sh
else
  echo "Ollama already installed."
fi

echo "[5/6] Pulling llama3..."
ollama pull llama3 || echo "[WARN] llama3 pull failed. Retry with: ollama pull llama3"

echo "[6/6] Verifying Python imports..."
.venv/Scripts/python.exe - <<'PY'
import sys
mods = ["pvporcupine", "pvrecorder", "vosk", "sounddevice", "webrtcvad", "soundfile", "ollama", "chromadb", "sentence_transformers", "PIL", "pyautogui", "pygetwindow", "requests", "bs4", "duckduckgo_search", "psutil"]
failed = []
for m in mods:
    try:
        __import__(m)
    except Exception as e:
        failed.append((m, str(e)))
if failed:
    print("MISSING MODULES:")
    for m, e in failed:
        print(f" - {m}: {e}")
    sys.exit(1)
else:
    print("All modules OK.")
PY

echo ""
echo "Setup complete. Start Ollama if not already running."
echo "Then run: run.bat"
