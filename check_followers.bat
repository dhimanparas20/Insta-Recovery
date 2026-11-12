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
echo Checking who doesn't follow your new account back...
echo (If Chrome asks, sign in to your NEW account. Only lists are read; nothing is followed or unfollowed.)
%PY% recover.py followers %*
pause
