@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m acdcopf backend %*
if errorlevel 1 pause
