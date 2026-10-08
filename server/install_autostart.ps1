# GPU box: start the meeting service at logon and restart it within 10 min if it dies.
# Normal (non-admin) PowerShell, as the user who runs the service. Idempotent.
$ErrorActionPreference = 'Stop'
$start = Join-Path $PSScriptRoot 'start_service.ps1'
$action = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$start`""

# Watchdog: every 10 minutes, start the service unless it is already listening on 127.0.0.1:8765
schtasks /create /tn MeetingService-Watchdog /sc minute /mo 10 /tr $action /it /f | Out-Null

# Startup folder entry: start right after logon instead of waiting for the first watchdog run
$startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'meeting-service.cmd'
Set-Content -Path $startup -Value "@start `"`" /min $action" -Encoding ascii

schtasks /run /tn MeetingService-Watchdog | Out-Null
Write-Host "Installed: task MeetingService-Watchdog and $startup; service starting."
