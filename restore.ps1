<#
  HRP Digital Marketing Studio - restore the database from a backup.

  Backups are made automatically every night (Platform -> Backups in the app) into storage\backups\
  as dm-YYYYMMDD-HHMMSS-daily.dump (or -manual.dump). This script:
    1. shows the backup it will restore and asks you to type RESTORE,
    2. stops the API, worker and web app (the database container keeps running),
    3. saves a safety copy of the current database (storage\backups\pre-restore-....dump),
    4. restores the backup with pg_restore --clean --if-exists inside the hrp-dm-postgres container
       (in one transaction: if anything fails, the database is left exactly as it was),
    5. optionally restores the media files (-Media, from the matching ...-media.zip),
    6. starts the studio again with start.ps1.

  Usage:  powershell -ExecutionPolicy Bypass -File .\restore.ps1                       (newest backup)
          powershell -ExecutionPolicy Bypass -File .\restore.ps1 -File storage\backups\dm-20261019-023000-daily.dump
          -Media   also restore storage\orgs from the matching media zip
          -Yes     don't ask for confirmation (for scripts)
          -NoStart don't start the studio afterwards
#>
param([string]$File, [switch]$Media, [switch]$Yes, [switch]$NoStart)
$ErrorActionPreference = "Continue"  # podman writes progress to stderr; exit codes are checked instead
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Container = "hrp-dm-postgres"
$BackupDir = Join-Path $Root "storage\backups"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "`n[x] $msg" -ForegroundColor Red; exit 1 }

# Database user and name from .env (MIGRATION_DATABASE_URL); never printed with the password.
$DbUser = "dm_owner"; $DbName = "digital_marketing"
if (Test-Path ".env") {
  $line = Get-Content ".env" | Where-Object { $_ -match '^\s*MIGRATION_DATABASE_URL\s*=' } | Select-Object -First 1
  if ($line -match '://([^:/@]+)(:[^@]*)?@[^/]+/([^?\s]+)') { $DbUser = $Matches[1]; $DbName = $Matches[3] }
}

# ---------------------------------------------------------------- which backup
if (-not $File) {
  $latest = Get-ChildItem $BackupDir -Filter "dm-*.dump" -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
  if (-not $latest) { Fail "No backups found in $BackupDir. Pass one with -File <path>." }
  $File = $latest.FullName
}
if (-not (Test-Path $File)) { Fail "Backup file not found: $File" }
$File = (Resolve-Path $File).Path
$info = Get-Item $File
$fs = [System.IO.File]::OpenRead($File)
$head = New-Object byte[] 5
$null = $fs.Read($head, 0, 5); $fs.Close()
if ([System.Text.Encoding]::ASCII.GetString($head) -ne "PGDMP") { Fail "$File isn't a PostgreSQL backup made by the studio." }
$MediaZip = $File -replace '\.dump$', '-media.zip'
if ($Media -and -not (Test-Path $MediaZip)) { Fail "No media zip next to this backup ($MediaZip)." }

Write-Host "Backup:   $($info.Name)  ($([math]::Round($info.Length / 1MB, 1)) MB, made $($info.LastWriteTime))"
Write-Host "Database: $DbName in the Podman container $Container"
if ($Media) { Write-Host "Media:    $MediaZip -> storage\orgs" }
Write-Host "`nEverything in the database since this backup will be replaced (posts, settings, users...)." -ForegroundColor Yellow
if (-not $Yes) {
  $answer = Read-Host "Type RESTORE to continue"
  if ($answer -cne "RESTORE") { Write-Host "Nothing was changed."; exit 0 }
}

# ---------------------------------------------------------------- stop the app
Step "Stopping the studio (API, worker, web app)"
& (Join-Path $Root "stop.ps1") -AppOnly

Step "Checking the database container"
podman start $Container 2>$null | Out-Null
for ($i = 0; $i -lt 60; $i++) {
  podman exec $Container pg_isready -U $DbUser -d $DbName *> $null
  if ($LASTEXITCODE -eq 0) { break }
  Start-Sleep -Seconds 1
}
if ($LASTEXITCODE -ne 0) { Fail "PostgreSQL isn't running. Start Podman Desktop, then run .\start.ps1 once and try again." }

# ---------------------------------------------------------------- safety copy
Step "Saving a safety copy of the current database"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$safety = Join-Path $BackupDir "pre-restore-$stamp.dump"
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
# Files go in and out of the container with "podman cp": PowerShell pipes would corrupt the binary dump.
podman exec $Container pg_dump -U $DbUser -Fc -f /tmp/pre-restore.dump $DbName
if ($LASTEXITCODE -ne 0) { Fail "Couldn't save a safety copy, so nothing was restored. Start the studio again with .\start.ps1." }
podman cp "$($Container):/tmp/pre-restore.dump" $safety
podman exec $Container rm -f /tmp/pre-restore.dump | Out-Null
Write-Host "Safety copy: $safety"

# ---------------------------------------------------------------- restore
Step "Restoring $($info.Name)"
podman cp $File "$($Container):/tmp/restore.dump"
if ($LASTEXITCODE -ne 0) { Fail "Couldn't copy the backup into the container." }
# Close other connections so DROP statements don't wait.
podman exec $Container psql -q -U $DbUser -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DbName' AND pid <> pg_backend_pid();" | Out-Null
podman exec $Container pg_restore -U $DbUser -d $DbName --clean --if-exists --single-transaction --exit-on-error /tmp/restore.dump
$code = $LASTEXITCODE
podman exec $Container rm -f /tmp/restore.dump | Out-Null
if ($code -ne 0) {
  Fail "The restore failed and was rolled back: the database is unchanged. The safety copy is $safety. Start the studio with .\start.ps1."
}
Write-Host "Database restored." -ForegroundColor Green

if ($Media) {
  Step "Restoring media files"
  Expand-Archive -Path $MediaZip -DestinationPath (Join-Path $Root "storage") -Force
  Write-Host "Media restored into storage\orgs."
}

# ---------------------------------------------------------------- start again
if ($NoStart) {
  Write-Host "`nDone. Start the studio with .\start.ps1 when you're ready."
} else {
  Step "Starting the studio again"
  & (Join-Path $Root "start.ps1")
}
