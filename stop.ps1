<#
  Stops the API and web app. Without -AppOnly it also stops the PostgreSQL and Redis containers
  (your data is kept in Podman volumes).
#>
param([switch]$AppOnly)
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $Root "storage\.pids"
if (Test-Path $pidFile) {
  Get-Content $pidFile | Where-Object { $_ } | ForEach-Object {
    taskkill /PID $_ /T /F 2>$null | Out-Null
  }
  Remove-Item $pidFile -ErrorAction SilentlyContinue
}
if (-not $AppOnly) {
  podman stop hrp-dm-postgres hrp-dm-redis 2>$null | Out-Null
  Write-Host "Stopped the app and the database containers."
}
