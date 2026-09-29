@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m acdcopf dashboard %*
if errorlevel 1 pause
