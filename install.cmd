@echo off
rem Pull the Daedalus image, or build it from the source with --dev, and start Docker Compose.
setlocal
cd /d "%~dp0"

set "COMPOSE=docker compose"
if /i "%~1"=="--dev" (
  set "COMPOSE=docker compose -f compose.dev.yml"
  rem The version under the logo: dev and the commit.
  for /f %%c in ('git rev-parse --short HEAD 2^>nul') do set "DAEDALUS_VERSION=dev-%%c"
) else if not "%~1"=="" (
  echo Unknown option: %~1. The only option is --dev. 1>&2
  exit /b 1
)

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
%COMPOSE% up -d
if errorlevel 1 exit /b 1
echo Daedalus runs on http://localhost:3357/v1
echo Dashboard: http://localhost:3357/ (user DAEDALUS_USERNAME or admin, password DAEDALUS_PASSWORD or DAEDALUS_MASTER_KEY)
echo Make API keys for your clients in the dashboard.
