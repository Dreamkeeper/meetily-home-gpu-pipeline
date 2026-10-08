# Laptop: run the watcher at logon (and re-launch every 10 min; a second copy exits
# immediately), and register the meetily-record: link used by the reminder's "Записать" button.
# Normal (non-admin) PowerShell. Idempotent.
$ErrorActionPreference = 'Stop'
$python = (Get-Command python.exe).Source
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
$watcher = Join-Path $PSScriptRoot 'watcher.py'

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$watcher`"" -WorkingDirectory $PSScriptRoot
$triggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"),
    (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10))
)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName MeetingWatcher -Action $action -Trigger $triggers -Settings $settings -Force `
    -Description 'Meetily -> GPU transcription -> meeting notes (restarts itself every 10 min if not running)' | Out-Null

& $python (Join-Path $PSScriptRoot 'reminders.py') register
Start-ScheduledTask -TaskName MeetingWatcher
Write-Host 'Installed: task MeetingWatcher (running) and the meetily-record: link handler.'
