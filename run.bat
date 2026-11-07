@echo off
setlocal EnableExtensions
pushd "%~dp0"
if exist ".venv\Scripts\python.exe" goto :run
set "PYTHON_CMD="
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
    python -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
    echo Install 64-bit Python 3.12, then run this file again.
    goto :fail
)
%PYTHON_CMD% -m venv .venv
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail
:run
".venv\Scripts\python.exe" -c "from PySide6 import QtWidgets,QtMultimedia,QtSvg; import tzlocal,tzdata" >nul 2>&1
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :fail
)
".venv\Scripts\python.exe" main.py
if errorlevel 1 goto :fail
popd
exit /b 0
:fail
echo Launch failed. Read the error above.
pause
popd
exit /b 1
