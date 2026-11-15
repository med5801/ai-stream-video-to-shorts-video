@echo off
REM Double-click: asks for a Twitch VOD id or link.
REM Command line: run.bat 2929768251 [options]   (see docs/USER_GUIDE.md)
cd /d "%~dp0tools"
set "PYTHONUTF8=1"
chcp 65001 >nul
set "VENV=%~dp0.venv"
set "NV=%VENV%\Lib\site-packages\nvidia"
set "PATH=%NV%\cublas\bin;%NV%\cudnn\bin;%PATH%"
set "PY=%VENV%\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "ARGS=%*"
if "%ARGS%"=="" set /p "ARGS=Twitch VOD id or link: "
if "%ARGS%"=="" exit /b 1
"%PY%" run.py %ARGS%
pause
