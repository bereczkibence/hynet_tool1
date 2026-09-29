@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m acdcopf frontend %*
if errorlevel 1 pause
