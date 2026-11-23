@echo off
cd /d "%~dp0"
echo ================================================================
echo  GPU boost for NVIDIA graphics cards (about 1 GB download)
echo  Only needed once. Skip it if you do not have an NVIDIA card.
echo ================================================================
echo.
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
%PY% -m pip install --upgrade nvidia-cublas-cu12 nvidia-cudnn-cu12
echo.
echo Done. Run start.bat as usual. If the GPU cannot be used, the program
echo will switch to the processor on its own.
pause
