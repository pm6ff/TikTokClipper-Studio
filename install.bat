@echo off
cd /d "%~dp0"
echo ================================================
echo  Installing the clip maker (internet required)
echo ================================================
echo.

set PY=
python --version >nul 2>nul && set PY=python
if not defined PY (
  py -3 --version >nul 2>nul && set PY=py -3
)
if not defined PY (
  echo Python was not found. Trying to install it with winget...
  winget install -e --id Python.Python.3.13 --accept-package-agreements --accept-source-agreements
  echo.
  echo If the install succeeded: CLOSE this window and run install.bat once more.
  echo If winget is not available: install Python from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" in the first installer window.
  pause
  exit /b 1
)

ffmpeg -version >nul 2>nul
if errorlevel 1 (
  echo ffmpeg was not found. Installing it with winget...
  winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
  echo.
  echo If the install succeeded: CLOSE this window and run install.bat once more.
  pause
  exit /b 1
)

echo Installing Python libraries...
%PY% -m pip install --upgrade yt-dlp faster-whisper numpy deep-translator "av<16"
if errorlevel 1 (
  echo.
  echo Something went wrong. Send a screenshot of this window to the author.
  pause
  exit /b 1
)
echo.
echo All done! Now run start.bat
pause
