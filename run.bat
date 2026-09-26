@echo off
rem ============================================================
rem  pyPOS launcher for Windows — just double-click this file.
rem  First run creates a private Python environment (.venv) that
rem  only pyPOS uses, then starts your register in the browser.
rem ============================================================
title pyPOS
setlocal
cd /d "%~dp0"

rem ---- find Python: prefer the python.org launcher `py`, fall back to `python`.
set "PYTHON="
where py >nul 2>nul && set "PYTHON=py -3"
if not defined PYTHON (
  where python >nul 2>nul && set "PYTHON=python"
)
if not defined PYTHON (
  echo.
  echo  pyPOS needs Python 3.10+, but it was not found on this PC.
  echo.
  echo  To install it:
  echo    * Windows Store: open "python" in the app search and install
  echo      the Python 3.1x package (during setup tick "Add python.exe
  echo      to PATH").
  echo    * Direct download: https://www.python.org/downloads/
  echo.
  echo  After installing, run this file again.
  pause
  exit /b 1
)

set PY=.venv\Scripts\python.exe

if not exist "%PY%" (
  echo First run - setting up a private Python environment for pyPOS...
  %PYTHON% -m venv .venv
  if errorlevel 1 (
    echo.
    echo  Could not create the environment. Python 3.10+ must be installed
    echo  (see message above), then run this file again.
    pause
    exit /b 1
  )
)

"%PY%" -c "import flask" >nul 2>&1
if errorlevel 1 (
  echo Installing Flask into the local environment (one-time, needs internet)...
  "%PY%" -m pip install --quiet -r requirements.txt
  if errorlevel 1 (
    echo.
    echo  Could not install Flask. Check your internet connection and run
    echo  this file again.
    pause
    exit /b 1
  )
)

echo.
echo  Starting pyPOS - your browser will open http://127.0.0.1:8420
echo  Keep this window open while using the register. To shut the
echo  register down, close this window or press Ctrl+C here.
echo.
"%PY%" main.py %*
pause