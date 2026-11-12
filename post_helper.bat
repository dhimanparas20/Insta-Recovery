@echo off
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY (
  echo Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/
  echo During setup, tick "Add python.exe to PATH". Then double-click this file again.
  pause
  exit /b 1
)
echo Starting the post helper. Leave this window open while you post from the hub (Posts tab).
echo Close it or press Ctrl+C when you are done.
%PY% recover.py post %*
pause
