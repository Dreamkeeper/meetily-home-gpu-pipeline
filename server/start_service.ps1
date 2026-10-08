# Starts the meeting service unless it is already listening. Run by the Startup-folder entry
# and by the "MeetingService-Watchdog" scheduled task (every 10 min), so a crash self-heals.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
if (Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) { exit 0 }

$env:HF_HUB_OFFLINE = '1'                # models are cached; never download at runtime
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
$env:PYTHONUNBUFFERED = '1'
# Start-Process truncates redirect targets, so keep the previous run's logs as *.prev.log
foreach ($name in 'service.out', 'service.err') {
    $f = Join-Path $root "$name.log"
    if (Test-Path $f) { Move-Item $f (Join-Path $root "$name.prev.log") -Force }
}
Start-Process -FilePath (Join-Path $root '.venv\Scripts\python.exe') -ArgumentList 'service.py' `
    -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $root 'service.out.log') `
    -RedirectStandardError (Join-Path $root 'service.err.log')
