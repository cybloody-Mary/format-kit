@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
set "PYTHONUTF8=1"
if exist "%~dp0runtime\python.exe" (
    "%~dp0runtime\python.exe" "%~dp0server.py"
    if errorlevel 1 pause
    exit /b
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0server.py"
    if errorlevel 1 pause
    exit /b
)
where python >nul 2>nul
if not errorlevel 1 (
    python "%~dp0server.py"
    if errorlevel 1 pause
    exit /b
)
echo Python 3.10 or newer is required. Install it from https://www.python.org/downloads/windows/
pause
exit /b 1
