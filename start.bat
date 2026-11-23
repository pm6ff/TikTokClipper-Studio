@echo off
cd /d "%~dp0"
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
set PY=
python --version >nul 2>nul && set PY=python
if not defined PY (
  py -3 --version >nul 2>nul && set PY=py -3
)
if not defined PY (
  echo Python was not found. Run install.bat first.
  pause
  exit /b 1
)
%PY% app.py
pause
