# Set up daedalus and start it with Docker Compose. Outside a checkout, it downloads the files to ~\daedalus first.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$repo = 'nemoe7/daedalus'
# The files that compose.yml needs, and the install scripts for the next run.
$files = 'compose.yml', 'compose.dev.yml', '.env.example', 'config', 'services', 'install.ps1', 'install.cmd'

function Stop-Install([string]$message) {
  Write-Host $message -ForegroundColor Red
  exit 1
}

# True when a native command exits with 0. Windows PowerShell 5.1 can turn native stderr into an error.
function Test-Native([scriptblock]$command) {
  try { & $command *> $null; return $LASTEXITCODE -eq 0 } catch { return $false }
}

$dev = $false
foreach ($arg in $args) {
  if ($arg -eq '--dev') { $dev = $true } else { Stop-Install "Unknown option: $arg. The only option is --dev." }
}

# A checkout has compose.yml beside this script. With irm | iex, there is no script file.
if ($PSScriptRoot -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'compose.yml'))) {
  $dir = $PSScriptRoot
} else {
  $dir = Join-Path $HOME 'daedalus'
}
if ($dev -and -not (Test-Path -LiteralPath (Join-Path $dir 'daedalus'))) {
  Stop-Install '--dev builds the image from the source. Run it in a git checkout.'
}

$needDocker = -not (Get-Command docker -ErrorAction SilentlyContinue)
$needFiles = -not (Test-Path -LiteralPath (Join-Path $dir 'compose.yml'))
$plan = @()
if ($needDocker) { $plan += 'Docker Desktop, with winget (it asks for admin rights, then a restart)' }
if ($needFiles) { $plan += "The daedalus files, to $dir" }
if (-not (Test-Path -LiteralPath (Join-Path $dir '.env'))) { $plan += "The settings file $dir\.env, with a new master key" }

if ($plan.Count -gt 0) {
  Write-Host 'This script installs or downloads:'
  $plan | ForEach-Object { Write-Host "  - $_" }
  $answer = Read-Host 'Continue? [y/N]'
  if ($answer -notmatch '^(y|yes)$') { Stop-Install 'Stopped. Nothing changed.' }
}

if ($needFiles) {
  # The newest v* tag, else main.
  $tag = (Invoke-RestMethod "https://api.github.com/repos/$repo/tags").name |
    Where-Object { $_ -match '^v\d+(\.\d+)+$' } |
    Sort-Object { [version]$_.Substring(1) } |
    Select-Object -Last 1
  $ref = if ($tag) { "refs/tags/$tag" } else { 'refs/heads/main' }
  $tmp = Join-Path ([IO.Path]::GetTempPath()) ([IO.Path]::GetRandomFileName())
  New-Item -ItemType Directory -Path $tmp | Out-Null
  try {
    Invoke-WebRequest "https://github.com/$repo/archive/$ref.zip" -OutFile "$tmp\source.zip" -UseBasicParsing
    Expand-Archive -LiteralPath "$tmp\source.zip" -DestinationPath "$tmp\source"
    $source = (Get-ChildItem -LiteralPath "$tmp\source" -Directory | Select-Object -First 1).FullName
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    # Only the missing files: a new run keeps the files that you changed.
    foreach ($name in $files) {
      if (-not (Test-Path -LiteralPath (Join-Path $dir $name))) {
        Copy-Item -LiteralPath (Join-Path $source $name) -Destination $dir -Recurse
      }
    }
  } finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force
  }
  Write-Host "The files of $(if ($tag) { $tag } else { 'main' }) are in $dir."
}
Set-Location -LiteralPath $dir

if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
$envFile = Join-Path $dir '.env'
$text = [IO.File]::ReadAllText($envFile)
$key = $null
if ($text -match '(?m)^DAEDALUS_MASTER_KEY=\r?$') {
  $bytes = New-Object byte[] 20
  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $key = -join ($bytes | ForEach-Object { $_.ToString('x2') })
  $text = $text -replace '(?m)^DAEDALUS_MASTER_KEY=(\r?)$', ("DAEDALUS_MASTER_KEY=$key" + '$1')
  [IO.File]::WriteAllText($envFile, $text)
}
if ($text -notmatch '(?m)^DAEDALUS_MASTER_KEY=\S{16,}\r?$') {
  Stop-Install "Set DAEDALUS_MASTER_KEY in $envFile`: 16 or more characters, no spaces."
}
New-Item -ItemType Directory -Force -Path '.daedalus-state' | Out-Null

if ($needDocker) {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Start-Process 'https://docs.docker.com/desktop/setup/install/windows-install/'
    Stop-Install 'winget is missing. Install Docker Desktop from the page that opened, then run this command again.'
  }
  winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
  if ($LASTEXITCODE -ne 0) { Stop-Install 'The Docker Desktop install failed.' }
  Write-Host 'Docker Desktop is installed. Restart Windows, start Docker Desktop once, then run this command again.' -ForegroundColor Yellow
  exit 0
}
if (-not (Test-Native { docker compose version })) {
  Stop-Install 'Docker Compose v2 is necessary. Install Docker, then run this script again.'
}
if (-not (Test-Native { docker info })) {
  Stop-Install 'Docker does not run. Start Docker Desktop, wait until it is ready, then run this command again.'
}

$compose = @('compose')
if ($dev) {
  $compose = @('compose', '-f', 'compose.dev.yml')
  # The version under the logo: dev and the commit.
  try { $commit = git rev-parse --short HEAD 2>$null } catch { $commit = $null }
  if ($commit) { $env:DAEDALUS_VERSION = "dev-$commit" }
}
docker @compose up -d
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host 'daedalus runs on http://localhost:3357/v1'
Write-Host 'Dashboard: http://localhost:3357/ (user DAEDALUS_USERNAME or admin, password DAEDALUS_PASSWORD or DAEDALUS_MASTER_KEY)'
if ($key) { Write-Host "Your new master key, also the dashboard password: $key (it is in $envFile)" -ForegroundColor Yellow }
Write-Host "Add your provider API keys to $envFile, then run this script again."
Write-Host 'Make API keys for your clients in the dashboard.'
