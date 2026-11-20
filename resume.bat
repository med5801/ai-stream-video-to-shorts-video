@echo off
REM RESUME: continue clipping half-finished streams from their last clip.
REM   resume.bat                      every half-finished VOD of the last 4 days
REM   resume.bat 2932113307           only this VOD
REM   resume.bat --list               only shows the status
cd /d "%~dp0tools"
set "PYTHONUTF8=1"
chcp 65001 >nul
set "VENV=%~dp0.venv"
set "NV=%VENV%\Lib\site-packages\nvidia"
set "PATH=%NV%\cublas\bin;%NV%\cudnn\bin;%PATH%"
set "PY=%VENV%\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" resume.py %*
pause
