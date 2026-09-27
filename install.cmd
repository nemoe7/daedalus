@echo off
rem Build the Daedalus image and start the service with Docker Compose.
setlocal
cd /d "%~dp0"

docker compose version >nul 2>&1
if errorlevel 1 (
  echo Docker Compose v2 is necessary. Install Docker, then run this script again. 1>&2
  exit /b 1
)
if not exist .daedalus-state mkdir .daedalus-state
if not exist .env echo No .env file. Daedalus skips each provider that has no API key.
findstr /r /b /c:"DAEDALUS_MASTER_KEY=................" .env >nul 2>&1
if errorlevel 1 (
  echo Add DAEDALUS_MASTER_KEY to .env: 16 or more characters, no spaces. 1>&2
  exit /b 1
)
docker compose up -d --build
if errorlevel 1 exit /b 1
echo Daedalus runs on http://localhost:3357/v1
echo Dashboard: http://localhost:3357/ (user admin, password DAEDALUS_MASTER_KEY)
echo Set a local API key: docker compose exec daedalus daedalus key
