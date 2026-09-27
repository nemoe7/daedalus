# Build the Daedalus image and start the service with Docker Compose.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
  Write-Error 'Docker Compose v2 is necessary. Install Docker, then run this script again.'
  exit 1
}
New-Item -ItemType Directory -Force -Path '.daedalus-state' | Out-Null
if (-not (Test-Path -LiteralPath '.env')) {
  Write-Host 'No .env file. Daedalus skips each provider that has no API key.'
}
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'Daedalus runs on http://localhost:3357/v1'
Write-Host 'Set a local API key: docker compose exec daedalus daedalus -k'
