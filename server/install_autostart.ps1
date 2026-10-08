# GPU box: start the meeting service at logon and restart it within 10 min if it dies.
# Normal (non-admin) PowerShell, as the user who runs the service. Idempotent.
$ErrorActionPreference = 'Stop'
$vbs = Join-Path $PSScriptRoot 'start_service-hidden.vbs'   # no console flash (powershell -WindowStyle Hidden still flashes)
$action = "wscript.exe //B //Nologo `"$vbs`""

# Watchdog: every 10 minutes, start the service unless it is already listening on 127.0.0.1:8765
schtasks /create /tn MeetingService-Watchdog /sc minute /mo 10 /tr $action /it /f | Out-Null

# Startup folder shortcut: start right after logon instead of waiting for the first watchdog run
$startupDir = [Environment]::GetFolderPath('Startup')
Remove-Item (Join-Path $startupDir 'meeting-service.cmd') -ErrorAction SilentlyContinue   # older installs
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $startupDir 'meeting-service.lnk'))
$lnk.TargetPath = 'wscript.exe'
$lnk.Arguments = "//B //Nologo `"$vbs`""
$lnk.WorkingDirectory = $PSScriptRoot
$lnk.Save()

schtasks /run /tn MeetingService-Watchdog | Out-Null
Write-Host "Installed: task MeetingService-Watchdog and $startupDir\meeting-service.lnk; service starting."
