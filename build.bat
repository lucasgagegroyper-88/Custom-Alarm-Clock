@echo off
setlocal EnableExtensions
pushd "%~dp0"
set "PYTHON_CMD="
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
    python -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
    echo ERROR: Install 64-bit Python 3.12 from python.org and enable its launcher or PATH option.
    goto :fail
)
%PYTHON_CMD% -c "import sys,struct; assert sys.platform == 'win32', 'Build on Windows'; assert sys.version_info >= (3,10), 'Python 3.10 or newer required'; assert struct.calcsize('P') == 8, '64-bit Python required'"
if errorlevel 1 goto :fail

echo [1/6] Creating the build environment...
if not exist ".venv\Scripts\python.exe" (
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 goto :fail
)
echo [2/6] Installing pinned dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
if errorlevel 1 goto :fail

echo [3/6] Checking Qt plugins and audio decoder support...
".venv\Scripts\python.exe" scripts\check_runtime.py --windows
if errorlevel 1 goto :fail

echo [4/6] Running automated tests without any alarm media...
set "QT_QPA_PLATFORM=offscreen"
".venv\Scripts\python.exe" -m pytest -q
if errorlevel 1 goto :fail
set "QT_QPA_PLATFORM="

echo [5/6] Building the single-file Windows application...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean CustomAlarmClock.spec
if errorlevel 1 goto :fail

echo [6/6] Checking the build result and executable startup...
if not exist "dist\CustomAlarmClock.exe" (
    echo ERROR: PyInstaller did not create dist\CustomAlarmClock.exe.
    goto :fail
)
".venv\Scripts\python.exe" scripts\smoke_executable.py "dist\CustomAlarmClock.exe"
if errorlevel 1 goto :fail
echo.
echo SUCCESS: %CD%\dist\CustomAlarmClock.exe
for %%I in ("dist\CustomAlarmClock.exe") do echo Size: %%~zI bytes
echo Run the executable to create your alarms. Python is not needed on the target PC.
if /i not "%~1"=="--no-pause" pause
popd
exit /b 0

:fail
echo.
echo BUILD FAILED. Read the error above. No successful build is being claimed.
if /i not "%~1"=="--no-pause" pause
popd
exit /b 1
