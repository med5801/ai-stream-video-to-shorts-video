@echo off
REM Watches the streamers in tools\streamers.txt and processes their finished streams.
REM   watcher.bat --list              only lists their latest VODs
REM   watcher.bat --once              one check, then exit
REM   watcher.bat --mode highlights   only download clips (no brain, no render)
cd /d "%~dp0tools"
set "PYTHONUTF8=1"
chcp 65001 >nul
set "VENV=%~dp0.venv"
set "NV=%VENV%\Lib\site-packages\nvidia"
set "PATH=%NV%\cublas\bin;%NV%\cudnn\bin;%PATH%"
set "PY=%VENV%\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" watcher.py %*
pause
