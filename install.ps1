# Set up daedalus and start it with Docker Compose. Outside a checkout, it downloads the files to ~\daedalus first.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$repo = 'nemoe7/daedalus'

function Stop-Install([string]$message) {
  Write-Host $message -ForegroundColor Red
  exit 1
}

function Confirm-Profile([string]$question) {
  $answer = Read-Host "$question [y/N]"
  return $answer -match '^(y|yes)$'
}

# True when a native command exits with 0. Windows PowerShell 5.1 can turn native stderr into an error.
function Test-Native([scriptblock]$command) {
  try { & $command *> $null; return $LASTEXITCODE -eq 0 } catch { return $false }
}

$dev = $false
$prod = $false
# Support: --dev, --dev=on/off, --dev on/off, --no-dev, --prod
$i = 0
while ($i -lt $args.Count) {
  $arg = $args[$i]
  switch -Regex ($arg) {
    '^--dev$' {
      $nxt = if ($i + 1 -lt $args.Count) { $args[$i + 1] } else { $null }
      switch ($nxt) {
        { $_ -in 'off','0','false','no','prod' } { $prod = $true; $i++ }
        { $_ -in 'on','1','true','yes','dev' } { $dev = $true; $i++ }
        default { $dev = $true }
      }
    }
    '^--dev=(.*)$' {
      $val = $Matches[1].ToLower()
      switch ($val) {
        { $_ -in 'off','0','false','no','prod' } { $prod = $true }
        { $_ -in 'on','1','true','yes','dev','' } { $dev = $true }
        default { Stop-Install "Unknown --dev value: $val. Use --dev, --dev=off, --no-dev, or --prod." }
      }
    }
    '^--no-dev$|^--prod$' { $prod = $true }
    default { Stop-Install "Unknown option: $arg. Options: --dev, --dev=off, --no-dev, --prod." }
  }
  $i++
}

# A checkout has compose.yml beside this script. With irm | iex, there is no script file.
if ($PSScriptRoot -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'compose.yml'))) {
  $dir = $PSScriptRoot
} else {
  $dir = Join-Path $HOME 'daedalus'
}

# Persistent dev flag: .daedalus-dev marker auto-selects dev unless --prod/--no-dev given.
if ((Test-Path -LiteralPath (Join-Path $dir '.daedalus-dev')) -and -not $prod) { $dev = $true }

if ($dev -and $prod) { Stop-Install '--dev and --prod/--no-dev cannot be used together.' }
if ($dev -and -not (Test-Path -LiteralPath (Join-Path $dir 'daedalus'))) {
  Stop-Install '--dev builds the image from the source. Run it in a git checkout.'
}
# Make --dev persistent: create marker; --prod/--no-dev/--dev=off removes it.
if ($dev) { New-Item -ItemType Directory -Force -Path $dir | Out-Null; New-Item -ItemType File -Force -Path (Join-Path $dir '.daedalus-dev') | Out-Null }
if ($prod) { Remove-Item -LiteralPath (Join-Path $dir '.daedalus-dev') -Force -ErrorAction SilentlyContinue; $dev = $false }

# Files to pull: only pull compose.dev.yml when --dev or marker present
# Ordered checks: compose -> .env -> profiles -> required service dirs
if ($dev) {
  $baseFiles = 'compose.yml', 'compose.dev.yml', '.env.example', 'config', 'install.ps1', 'install.cmd'
} else {
  $baseFiles = 'compose.yml', '.env.example', 'config', 'install.ps1', 'install.cmd'
}
# Service mapping: search -> services/searxng, tailscale -> services/tailscale

$needDocker = -not (Get-Command docker -ErrorAction SilentlyContinue)

# Ordered check 1: compose?
$missingCompose = $false
foreach ($f in $baseFiles) {
  if ($f -in 'compose.yml','compose.dev.yml') {
    if (-not (Test-Path -LiteralPath (Join-Path $dir $f))) { $missingCompose = $true }
  }
}
# Ordered check 2: .env?
$missingEnv = -not (Test-Path -LiteralPath (Join-Path $dir '.env'))

# Ordered check 3: read .env for COMPOSE_PROFILES
$profiles = ""
if (Test-Path -LiteralPath (Join-Path $dir '.env')) {
  $envLine = Select-String -Path (Join-Path $dir '.env') -Pattern '^COMPOSE_PROFILES=' | Select-Object -Last 1
  if ($envLine) {
    $profiles = $envLine.Line.Split('=',2)[1].Trim('"').Trim("'")
  }
}
$profilesSpaced = $profiles -replace ',', ' '

$requiredServiceDirs = @()
if (-not (Test-Path -LiteralPath (Join-Path $dir 'config'))) { $requiredServiceDirs += 'config' }
if ($profilesSpaced -match '\bsearch\b') {
  if (-not (Test-Path -LiteralPath (Join-Path $dir 'services/searxng'))) { $requiredServiceDirs += 'services/searxng' }
}
if ($profilesSpaced -match '(^|\s)tailscale(\s|$)') {
  if (-not (Test-Path -LiteralPath (Join-Path $dir 'services/tailscale'))) { $requiredServiceDirs += 'services/tailscale' }
}
if ($profilesSpaced -match '(^|\s)tailscale-openwebui(\s|$)') {
  if (-not (Test-Path -LiteralPath (Join-Path $dir 'services/tailscale-openwebui'))) { $requiredServiceDirs += 'services/tailscale-openwebui' }
}

# For update: if any base file missing or .env missing or required service dir missing, need files
$needFiles = $false
if ($missingCompose) { $needFiles = $true }
if ($missingEnv) { $needFiles = $true }
foreach ($f in $baseFiles) {
  if (-not (Test-Path -LiteralPath (Join-Path $dir $f))) { $needFiles = $true; break }
}
if ($requiredServiceDirs.Count -gt 0) { $needFiles = $true }

# Build final files list for copy: base + required service dirs
$files = $baseFiles
foreach ($d in $requiredServiceDirs) {
  if ($files -notcontains $d) { $files += $d }
}
# Ensure services parent if any service dir required
if ($requiredServiceDirs.Count -gt 0 -and $files -notcontains 'services') { $files += 'services' }

$plan = @()
if ($needDocker) { $plan += 'Docker Desktop, with winget (it asks for admin rights, then a restart)' }
if ($missingCompose) { $plan += "The daedalus files, to $dir" }
elseif ($needFiles) { $plan += "The daedalus files, to $dir" }
if ($missingEnv) { $plan += "The settings file $dir\.env, with a new master key" }

if ($plan.Count -gt 0) {
  Write-Host 'This script installs or downloads:'
  $plan | ForEach-Object { Write-Host "  - $_" }
  $answer = Read-Host 'Continue? [y/N]'
  if ($answer -notmatch '^(y|yes)$') { Stop-Install 'Stopped. Nothing changed.' }
}

$profileList = ''
if ($missingEnv) {
  $selectedProfiles = @()
  if (Confirm-Profile 'Install Headroom?') { $selectedProfiles += 'headroom' }
  if (Confirm-Profile 'Install Open WebUI?') { $selectedProfiles += 'webui' }
  if (Confirm-Profile 'Install Tailscale for Daedalus?') { $selectedProfiles += 'tailscale' }
  if ($selectedProfiles -contains 'webui') {
    if (Confirm-Profile 'Install Tika?') { $selectedProfiles += 'tika' }
    if (Confirm-Profile 'Enable SearXNG search?') { $selectedProfiles += 'search' }
    if (Confirm-Profile 'Install Tailscale for Open WebUI?') { $selectedProfiles += 'tailscale-openwebui' }
  }
  $profileList = $selectedProfiles -join ','
  $profileServiceDirs = @()
  if ($selectedProfiles -contains 'search') { $profileServiceDirs += 'services/searxng' }
  if ($selectedProfiles -contains 'tailscale') { $profileServiceDirs += 'services/tailscale' }
  if ($selectedProfiles -contains 'tailscale-openwebui') { $profileServiceDirs += 'services/tailscale-openwebui' }
  foreach ($d in $profileServiceDirs) {
    if (-not (Test-Path -LiteralPath (Join-Path $dir $d))) {
      if ($requiredServiceDirs -notcontains $d) { $requiredServiceDirs += $d }
      if ($files -notcontains $d) { $files += $d }
      $needFiles = $true
    }
  }
  if ($requiredServiceDirs.Count -gt 0 -and $files -notcontains 'services') { $files += 'services' }
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

# .env handling: never overwrite existing .env, backup before any modification
$envFile = Join-Path $dir '.env'
if (-not (Test-Path -LiteralPath '.env')) {
  if (Test-Path -LiteralPath '.env.example') {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
  } else {
    Stop-Install "Missing .env.example in $dir, cannot create .env"
  }
} else {
  # Backup existing .env before any in-place edit to prevent data loss
  Copy-Item -LiteralPath '.env' -Destination '.env.bak' -Force
}
$text = [IO.File]::ReadAllText($envFile)
if ($missingEnv) {
  if ($text -match '(?m)^COMPOSE_PROFILES=') {
    $text = [regex]::Replace($text, '(?m)^COMPOSE_PROFILES=[^\r\n]*', "COMPOSE_PROFILES=$profileList")
  } else {
    $newline = if ($text.Contains("`r`n")) { "`r`n" } else { "`n" }
    if ($text.Length -gt 0 -and -not $text.EndsWith("`n")) { $text += $newline }
    $text += "COMPOSE_PROFILES=$profileList$newline"
  }
  [IO.File]::WriteAllText($envFile, $text)
}
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
  Stop-Install "Set DAEDALUS_MASTER_KEY in $envFile`: 16 or more characters, no spaces. Your .env was backed up to $dir\.env.bak if it existed"
}
# Generate WEBUI_SECRET_KEY for safety if empty (keeps Open WebUI logins after update)
if ($text -match '(?m)^WEBUI_SECRET_KEY=\r?$') {
  $bytes = New-Object byte[] 32
  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $webuiKey = -join ($bytes | ForEach-Object { $_.ToString('x2') })
  $text = $text -replace '(?m)^WEBUI_SECRET_KEY=(\r?)$', ("WEBUI_SECRET_KEY=$webuiKey" + '$1')
  [IO.File]::WriteAllText($envFile, $text)
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
