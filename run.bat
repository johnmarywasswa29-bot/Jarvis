@echo off
pushd "%~dp0"
echo.
if not exist .venv\Scripts\python.exe (
    echo Virtualenv not found. Run scripts\setup.bat first.
    pause
    exit /b 1
)
echo Launching Jarvis UI...
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, '.'); import PySide6" 2>nul
if errorlevel 1 (
    echo [WARN] PySide6 missing, falling back to CLI assistant.
    .venv\Scripts\python.exe jarvis.py
    goto :eof
)
echo.
echo Checking Ollama...
curl -s http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
    echo [WARN] Ollama is offline. Jarvis UI will start; chat will use limited/local behavior.
) else (
    echo [INFO] Ollama reachable.
)
.venv\Scripts\python.exe -m ui.main_window
popd