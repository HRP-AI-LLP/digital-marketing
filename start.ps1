<#
  HRP Digital Marketing Studio - start everything on this laptop.

  First run:  creates .env with random secrets, starts PostgreSQL (pgvector) and Redis in Podman,
              creates the Python environment, installs web dependencies, migrates the database.
  Every run:  starts the API and the web app (ports API_PORT / WEB_PORT in .env; defaults 8600 / 5180).

  Usage:  powershell -ExecutionPolicy Bypass -File .\start.ps1
#>
$ErrorActionPreference = "Continue"  # native tools (podman, pip) write to stderr; exit codes are checked instead
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Logs = Join-Path $Root "storage\logs"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "`n[x] $msg" -ForegroundColor Red; exit 1 }

function New-Secret([int]$len = 24) {
  $chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789".ToCharArray()
  $bytes = New-Object byte[] $len
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}

# ---------------------------------------------------------------- prerequisites
Step "Checking prerequisites"
$Python = $null
foreach ($v in @("3.13", "3.12", "3.11")) {
  try { & py "-$v" -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $Python = @("py", "-$v"); break } } catch {}
}
if (-not $Python) {
  try { $ver = & python -c "import sys; print(sys.version_info >= (3, 11))"; if ($ver -eq "True") { $Python = @("python") } } catch {}
}
if (-not $Python) { Fail "Python 3.11 or newer is required. Install it from https://www.python.org/downloads/ and run again." }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Fail "Node.js 20 or newer is required. Install it from https://nodejs.org/ and run again." }
if (-not (Get-Command podman -ErrorAction SilentlyContinue)) { Fail "Podman is required. Install Podman Desktop and run again." }
Write-Host "Python: $($Python -join ' ')   Node: $(node --version)   Podman: $(podman --version)"

# ---------------------------------------------------------------- .env
if (-not (Test-Path ".env")) {
  Step "Creating .env with fresh secrets (first run)"
  $ownerPw = New-Secret; $appPw = New-Secret; $adminPw = New-Secret 14
  $envText = (Get-Content ".env.example" -Raw)
  $envText = $envText.Replace("__SECRET_KEY__", (New-Secret 48)).Replace("__DB_OWNER_PASSWORD__", $ownerPw).Replace("__DB_APP_PASSWORD__", $appPw).Replace("__ADMIN_PASSWORD__", $adminPw)
  Set-Content -Path ".env" -Value $envText -Encoding ASCII
  $FirstRun = $true
}
# load .env into this process
Get-Content ".env" | ForEach-Object {
  if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') { [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process") }
}

# ---------------------------------------------------------------- containers
Step "Starting PostgreSQL and Redis in Podman"
podman info *> $null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Starting the Podman machine..."
  podman machine start
  if ($LASTEXITCODE -ne 0) { Fail "Couldn't start the Podman machine. Open Podman Desktop, start it, and run again." }
}

function Ensure-Container($name, [string[]]$runArgs) {
  $exists = podman ps -a --filter "name=^$name$" --format "{{.Names}}"
  if ($exists -eq $name) { podman start $name | Out-Null }
  else { podman run -d --name $name --restart unless-stopped @runArgs | Out-Null }
  if ($LASTEXITCODE -ne 0) { Fail "Couldn't start container $name." }
}

Ensure-Container "hrp-dm-postgres" @(
  "-p", "$($env:DB_PORT):5432",
  "-e", "POSTGRES_USER=dm_owner", "-e", "POSTGRES_PASSWORD=$($env:DB_OWNER_PASSWORD)", "-e", "POSTGRES_DB=digital_marketing",
  "-v", "hrp-dm-pgdata:/var/lib/postgresql/data",
  "docker.io/pgvector/pgvector:0.8.0-pg16"
)
Ensure-Container "hrp-dm-redis" @("-p", "$($env:REDIS_PORT):6379", "-v", "hrp-dm-redisdata:/data", "docker.io/library/redis:7.4-alpine")

Write-Host -NoNewline "Waiting for PostgreSQL"
for ($i = 0; $i -lt 60; $i++) {
  podman exec hrp-dm-postgres pg_isready -U dm_owner -d digital_marketing *> $null
  if ($LASTEXITCODE -eq 0) { break }
  Write-Host -NoNewline "."; Start-Sleep -Seconds 1
}
if ($LASTEXITCODE -ne 0) { Fail "PostgreSQL didn't become ready. Check: podman logs hrp-dm-postgres" }
Write-Host " ready"

# App role (non-superuser, so row-level security applies to it) + pgvector. Idempotent.
$sql = @"
DO `$`$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dm_app') THEN
    CREATE ROLE dm_app LOGIN PASSWORD '$($env:DB_APP_PASSWORD)';
  ELSE
    ALTER ROLE dm_app WITH LOGIN PASSWORD '$($env:DB_APP_PASSWORD)';
  END IF;
END `$`$;
CREATE EXTENSION IF NOT EXISTS vector;
GRANT USAGE ON SCHEMA public TO dm_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO dm_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO dm_app;
ALTER DEFAULT PRIVILEGES FOR ROLE dm_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dm_app;
ALTER DEFAULT PRIVILEGES FOR ROLE dm_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO dm_app;
"@
$sql | podman exec -i hrp-dm-postgres psql -q -v ON_ERROR_STOP=1 -U dm_owner -d digital_marketing
if ($LASTEXITCODE -ne 0) { Fail "Database setup failed." }

# ---------------------------------------------------------------- backend
Step "Preparing the API (Python)"
$Venv = Join-Path $Root "backend\.venv"
$Py = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $Py)) {
  $pyExe = $Python[0]; $pyArgs = @(); if ($Python.Length -gt 1) { $pyArgs = $Python[1..($Python.Length - 1)] }
  & $pyExe @pyArgs -m venv $Venv
}
$reqHash = (Get-FileHash "backend\requirements.txt").Hash
$stamp = Join-Path $Venv "requirements.sha"
if (-not (Test-Path $stamp) -or (Get-Content $stamp) -ne $reqHash) {
  & $Py -m pip install --upgrade pip -q
  & $Py -m pip install -q -r "backend\requirements.txt"
  if ($LASTEXITCODE -ne 0) { Fail "Installing Python packages failed." }
  Set-Content $stamp $reqHash
}
$pwStamp = Join-Path $Venv "playwright-chromium.ok"
if (-not (Test-Path $pwStamp)) {
  Write-Host "Installing the Chromium browser used to render images and videos (one time, ~150 MB)..."
  & $Py -m playwright install chromium
  if ($LASTEXITCODE -eq 0) { Set-Content $pwStamp "ok" } else { Write-Host "[!] Chromium install failed; media rendering won't work until it succeeds (re-run start.ps1)." -ForegroundColor Yellow }
}

Step "Migrating the database"
Push-Location backend
& $Py -m alembic upgrade head
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { Fail "Database migration failed." }

# ---------------------------------------------------------------- frontend
Step "Preparing the web app (Node)"
if (-not (Test-Path "frontend\node_modules")) {
  Push-Location frontend; npm ci; $code = $LASTEXITCODE; Pop-Location
  if ($code -ne 0) { Fail "Installing web packages failed." }
}

# ---------------------------------------------------------------- run
Step "Starting the API and the web app"
& (Join-Path $Root "stop.ps1") -AppOnly
if (-not $env:API_PORT) { $env:API_PORT = "8600" }
if (-not $env:WEB_PORT) { $env:WEB_PORT = "5180" }
Start-Sleep -Seconds 1
foreach ($p in @($env:API_PORT, $env:WEB_PORT)) {
  $busy = Get-NetTCPConnection -LocalPort ([int]$p) -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($busy) {
    $owner = (Get-Process -Id $busy.OwningProcess -ErrorAction SilentlyContinue).ProcessName
    Fail "Port $p is already used by '$owner' (PID $($busy.OwningProcess)). Close it, or set API_PORT / WEB_PORT to a free port in .env and run again."
  }
}
$api = Start-Process -FilePath $Py -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", $env:API_PORT `
  -WorkingDirectory (Join-Path $Root "backend") -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput "$Logs\api.log" -RedirectStandardError "$Logs\api.err.log"
$worker = Start-Process -FilePath $Py -ArgumentList "-m", "app.worker" `
  -WorkingDirectory (Join-Path $Root "backend") -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput "$Logs\worker.log" -RedirectStandardError "$Logs\worker.err.log"
$web = Start-Process -FilePath "npm.cmd" -ArgumentList "run", "dev" -WorkingDirectory (Join-Path $Root "frontend") `
  -WindowStyle Hidden -PassThru -RedirectStandardOutput "$Logs\web.log" -RedirectStandardError "$Logs\web.err.log"
# Optional public HTTPS address (Cloudflare Tunnel) for sign-in callbacks and Instagram/Threads media links.
$tunnel = $null
if ($env:CLOUDFLARE_TUNNEL_TOKEN) {
  $cf = Join-Path $Root "tools\cloudflared.exe"
  if (-not (Test-Path $cf)) {
    Write-Host "Downloading cloudflared (first time only)..."
    New-Item -ItemType Directory -Force -Path (Join-Path $Root "tools") | Out-Null
    try {
      Invoke-WebRequest "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile $cf -UseBasicParsing
    } catch { Write-Host "[!] Couldn't download cloudflared: $($_.Exception.Message)" -ForegroundColor Yellow }
  }
  if (Test-Path $cf) {
    $env:TUNNEL_TOKEN = $env:CLOUDFLARE_TUNNEL_TOKEN  # read by cloudflared; keeps the token off the command line
    $tunnel = Start-Process -FilePath $cf -ArgumentList "tunnel", "--no-autoupdate", "run" -WindowStyle Hidden -PassThru `
      -RedirectStandardOutput "$Logs\tunnel.log" -RedirectStandardError "$Logs\tunnel.err.log"
  }
}
$pids = "$($api.Id)`n$($worker.Id)`n$($web.Id)"
if ($tunnel) { $pids += "`n$($tunnel.Id)" }
Set-Content (Join-Path $Root "storage\.pids") $pids

Write-Host -NoNewline "Waiting for the API"
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
  try { $r = Invoke-RestMethod "http://127.0.0.1:$($env:API_PORT)/api/v1/health" -TimeoutSec 2; if ($r.status -eq "ok") { $ok = $true; break } } catch {}
  Write-Host -NoNewline "."; Start-Sleep -Seconds 1
}
if (-not $ok) { Fail "The API didn't start. See storage\logs\api.err.log" }
Write-Host " ready"

Write-Host "`nHRP Digital Marketing Studio is running." -ForegroundColor Green
Write-Host "  Web app:  http://localhost:$($env:WEB_PORT)"
Write-Host "  API docs: http://127.0.0.1:$($env:API_PORT)/api/docs"
Write-Host "  Worker:   runs scheduled posting, crawling and media jobs (log: storage\logs\worker.err.log)"
if ($tunnel) {
  Write-Host "  HTTPS:    $($env:PUBLIC_BASE_URL) (Cloudflare Tunnel; sign-in callbacks and media links only; log: storage\logs\tunnel.err.log)"
} elseif ($env:CLOUDFLARE_TUNNEL_TOKEN) {
  Write-Host "  HTTPS:    tunnel NOT running - Facebook, Instagram and Threads sign-in will fail. See storage\logs\tunnel.err.log" -ForegroundColor Yellow
}
Write-Host "  Logs:     storage\logs\"
Write-Host "  Stop:     .\stop.ps1"
if ($FirstRun) {
  Write-Host "`nFirst sign-in" -ForegroundColor Yellow
  Write-Host "  Email:    $($env:PLATFORM_ADMIN_EMAIL)"
  Write-Host "  Password: $($env:PLATFORM_ADMIN_PASSWORD)"
  Write-Host "  You'll be asked to choose your own password. The temporary one is also in .env (PLATFORM_ADMIN_PASSWORD)."
}
Start-Process "http://localhost:$($env:WEB_PORT)"
