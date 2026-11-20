@echo off
REM LIVE MODE: cuts chat spikes while the stream is running.
REM   live.bat xqc                    clips only
REM   live.bat xqc --render           also makes a Short from every clip
REM   live.bat xqc --render --resume  continue after the last clip of a previous run
cd /d "%~dp0tools"
set "PYTHONUTF8=1"
chcp 65001 >nul
set "VENV=%~dp0.venv"
set "NV=%VENV%\Lib\site-packages\nvidia"
set "PATH=%NV%\cublas\bin;%NV%\cudnn\bin;%PATH%"
set "PY=%VENV%\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "ARGS=%*"
if "%ARGS%"=="" set /p "ARGS=Twitch username (e.g. xqc): "
if "%ARGS%"=="" exit /b 1
"%PY%" live.py %ARGS%
pause
