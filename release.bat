@echo off
pushd "%~dp0"
if not exist installer mkdir installer
.venv\Scripts\python.exe installer\build.py release
popd
