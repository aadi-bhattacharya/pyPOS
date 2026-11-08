@echo off
rem Windows source launcher. For zero command-window flicker, double-click
rem "Start pyPOS.vbs" directly or use the installed native pyPOS.exe.
rem Dependency verification lives in start_source.py: import flask, waitress, qrcode.
setlocal
wscript.exe "%~dp0Start pyPOS.vbs"
if errorlevel 1 (
  echo Windows Script Host could not launch pyPOS.
  echo Please open "Start pyPOS.vbs" directly or run python main.py.
  pause
)
