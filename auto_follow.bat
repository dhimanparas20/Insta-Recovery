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
echo OPTIONAL: follows people from your old account, slowly (25 a day by default).
echo Automated following is against Instagram's rules and can get the new account restricted.
echo Leave the Chrome window open; close this window to stop.
echo.
%PY% recover.py follow %*
pause
