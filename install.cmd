@echo off
rem Set up Daedalus and start it with Docker Compose. install.ps1 does the work.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
exit /b %errorlevel%
