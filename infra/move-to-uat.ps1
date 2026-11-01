<#
  HRP Digital Marketing Studio - one-time move of this laptop's studio to the cloud UAT (uat-dm.hrpai.in).

  Run it once the cloud is set up (infra\README.md, steps 1-4), from the Digital Marketing folder:
      powershell -ExecutionPolicy Bypass -File .\infra\move-to-uat.ps1

  What it does:
    1. stops the studio on this laptop and switches its automatic work off for good (AUTOMATION_ENABLED=false in
       .env; no start-up shortcut, no wake-up timer), so nothing is posted twice;
    2. copies the whole database (HRP AI LLP, users, connected accounts, posts, history) into Neon UAT (dm-uat), all or nothing;
    3. creates the cloud's own database login (dm_app) with a new random password;
    4. uploads the media library to Cloudflare R2 (re-runnable: files already there are skipped);
    5. hands the connection strings, R2 keys and the laptop's encryption key to Google Secret Manager (directly
       with gcloud, or one at a time through the clipboard into Cloud Shell). Nothing secret is shown on screen.

  The laptop keeps its own copy of everything. To use it again locally (not recommended once UAT is live), set
  AUTOMATION_ENABLED=true in .env and run start.ps1.
#>
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "`n[x] $msg" -ForegroundColor Red; exit 1 }

$Py = Join-Path $Root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { Fail "The Python environment isn't there. Run .\start.ps1 once first." }
if (-not (Test-Path ".env")) { Fail ".env is missing. Run this from the Digital Marketing folder." }

Step "Stopping the studio on this laptop and switching its automatic work off"
& (Join-Path $Root "stop.ps1") -AppOnly
$lines = @(Get-Content ".env")
if ($lines -match '^\s*AUTOMATION_ENABLED\s*=') {
  $lines = $lines -replace '^\s*AUTOMATION_ENABLED\s*=.*$', 'AUTOMATION_ENABLED=false'
} else {
  $lines += ""; $lines += "# The cloud (UAT) runs the scheduled work now; this laptop must not post."; $lines += "AUTOMATION_ENABLED=false"
}
Set-Content -Path ".env" -Value $lines -Encoding ASCII
& (Join-Path $Root "install-autostart.ps1") -Remove | Out-Null
Unregister-ScheduledTask -TaskName "HRP Digital Marketing - wake for posts" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "  done: AUTOMATION_ENABLED=false, no start-up shortcut, no wake-up timer"

Step "Starting the local database (to copy it)"
podman start hrp-dm-postgres 2>$null | Out-Null
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
  podman exec hrp-dm-postgres pg_isready -q 2>$null
  if ($LASTEXITCODE -eq 0) { $ready = $true; break }
  Start-Sleep -Seconds 2
}
if (-not $ready) { Fail "The database container didn't start. Check: podman logs hrp-dm-postgres" }

Step "Checking Python packages"
& $Py -m pip install -q -r "backend\requirements.txt"
if ($LASTEXITCODE -ne 0) { Fail "pip install failed (see above)." }

Push-Location (Join-Path $Root "backend")
& $Py -m app.cli move-to-uat
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) {
  Write-Host "`nThe move stopped (see the message above). Fix it and run this script again: it starts over safely." -ForegroundColor Yellow
  exit $code
}
Write-Host "`nNext: infra\README.md, step 6 (Cloud Shell: ./infra/gcp/secrets.sh uat), then push to GitHub." -ForegroundColor Green
