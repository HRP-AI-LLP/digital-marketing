<#
  Start HRP Digital Marketing Studio by itself whenever you sign in to Windows.

  After a restart or a shut-down you no longer need to run start.ps1: about a minute after you sign in, the studio
  starts in the background (no browser window) and every post that fell due while the laptop was off goes out,
  oldest first and one at a time per account.

  Usage:   powershell -ExecutionPolicy Bypass -File .\install-autostart.ps1           turn it on
           powershell -ExecutionPolicy Bypass -File .\install-autostart.ps1 -Remove   turn it off

  It adds a shortcut to your Windows Startup folder (no administrator rights needed). The start-up log is
  storage\logs\autostart.log.
#>
param([switch]$Remove)
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Startup = [Environment]::GetFolderPath("Startup")
$Link = Join-Path $Startup "HRP Digital Marketing Studio.lnk"

if ($Remove) {
  Remove-Item $Link -ErrorAction SilentlyContinue
  Write-Host "Done. The studio won't start by itself any more (run .\start.ps1 when you need it)." -ForegroundColor Green
  exit 0
}

$ps = (Get-Command powershell.exe).Source
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($Link)
$sc.TargetPath = $ps
# Wait a minute after sign-in so Wi-Fi and Podman are ready, then start without opening the browser.
$sc.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"Start-Sleep -Seconds 60; & '$Root\start.ps1' -NoBrowser`""
$sc.WorkingDirectory = $Root
$sc.WindowStyle = 7  # minimized
$sc.Description = "Starts HRP Digital Marketing Studio in the background"
$icon = Join-Path $Root "frontend\public\favicon.ico"
if (Test-Path $icon) { $sc.IconLocation = $icon }
$sc.Save()
Write-Host "Done. The studio will start by itself about a minute after you sign in to Windows." -ForegroundColor Green
Write-Host "  Shortcut: $Link"
Write-Host "  Turn off: .\install-autostart.ps1 -Remove"
