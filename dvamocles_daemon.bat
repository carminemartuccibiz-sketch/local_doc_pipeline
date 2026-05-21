@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dvamocles_daemon.ps1"
exit /b %ERRORLEVEL%
