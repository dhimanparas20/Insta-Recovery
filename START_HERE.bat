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
echo Building your recovery hub from your Instagram export.
echo (Put the zip file(s) from Instagram in the "export" folder first, or drag them onto this file.)
echo.
%PY% recover.py build %*
pause
