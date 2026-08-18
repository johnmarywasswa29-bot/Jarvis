:: Jarvis Windows bootstrap
@echo off
echo Setting up Jarvis virtualenv...
cd /d "%~dp0"

uv venv --python 3.11 -q
uv pip install --python .venv/Scripts/python.exe -r requirements.txt -q

:: openWakeWord models will be downloaded automatically on first run

echo.
echo Setup complete. Run run.bat to start Jarvis.
echo Make sure Ollama is installed and you have pulled llama3.
pause