@echo off
setlocal

title Tool1 (acdcopf) Dashboard

set "SCRIPT_DIR=%~dp0"
set "VENV_PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"

echo Starting Tool1 (acdcopf) Dashboard...
echo.

if exist "%VENV_PYTHON%" (
    call "%VENV_PYTHON%" -m acdcpf_opf.start_dashboard %*
) else (
    call python -m acdcpf_opf.start_dashboard %*
)

set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo Tool1 (acdcopf) Dashboard could not start. Exit code: %EXIT_CODE%
    echo Check the message above, then press any key to close this window.
    pause >nul
)

endlocal & exit /b %EXIT_CODE%
