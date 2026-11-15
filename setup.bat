@echo off
REM One-time setup: Python packages, TwitchDownloader, FFmpeg.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
pause
