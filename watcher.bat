@echo off
REM Double-click: checks the streamers in tools\streamers.txt every 30 min and turns
REM every newly finished stream into Shorts (finds the VOD id by itself).
REM Command line: watcher.bat --list | --once | --mode highlights   (see docs/USER_GUIDE.md)
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
