:: Jarvis Windows bootstrap
@echo off
echo Setting up Jarvis virtualenv...
cd /d "%~dp0"

uv venv --python 3.11 -q
uv pip install --python .venv/Scripts/python.exe -r requirements.txt -q

:: Download default wake-word asset if missing
if not exist assets\jarvis.ppn (
    echo Downloading Jarvis wake-word asset...
    curl -L -o assets\jarvis.ppn https://github.com/Picovoice/porcupine/raw/master/resources/keyword_files/Windows/Jarvis_en_windows_v3_0_0.ppn
)

echo.
echo Setup complete. Run run.bat to start Jarvis.
echo Make sure Ollama is installed and you have pulled llama3.
pause
