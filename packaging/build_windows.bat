@echo off
REM Build PixMatch.exe (Windows, one-folder).
setlocal enabledelayedexpansion

set "HERE=%~dp0"
set "ROOT=%HERE%.."
cd /d "%ROOT%"

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

echo >> Using:
"%PY%" --version

"%PY%" -m pip install --quiet -e ".[dev]"
"%PY%" -m pip install --quiet imageio-ffmpeg

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

"%PY%" -m PyInstaller packaging\PixMatch.spec --noconfirm
if errorlevel 1 (
    echo BUILD FAILED
    exit /b 1
)

echo.
echo >> Done:  dist\PixMatch\PixMatch.exe
endlocal
