# GPU box: pull the latest pipeline code and restart the service when it changed.
# Normal (non-admin) PowerShell. Usage: .\update.ps1  [-Force]   (-Force restarts even if a job is running)
# From the laptop:  ssh gpu-box "powershell -NoProfile -ExecutionPolicy Bypass -File <repo>\server\update.ps1"
param([switch]$Force)
$ErrorActionPreference = 'Stop'
$server = $PSScriptRoot
$repo = Split-Path $server
$uv = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter uv.exe -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty FullName   # the WinGet link doesn't work over SSH

$before = git -C $repo rev-parse HEAD
git -C $repo pull --ff-only --quiet
if ($LASTEXITCODE) { throw 'git pull failed (local changes on the GPU box?)' }
$after = git -C $repo rev-parse HEAD
if ($before -eq $after) { Write-Host "Already up to date ($($after.Substring(0, 7)))."; exit 0 }

$changed = git -C $repo diff --name-only $before $after
Write-Host "Updated $($before.Substring(0, 7)) -> $($after.Substring(0, 7)):"; $changed | ForEach-Object { Write-Host "  $_" }
if ($changed -contains 'server/pyproject.toml') {
    Write-Host 'pyproject.toml changed: uv sync'
    & $uv sync --project $server --quiet
}
if (-not ($changed | Where-Object { $_ -like 'server/*' })) { Write-Host 'No server changes; service not restarted.'; exit 0 }

# Don't interrupt a transcription job
$running = Get-ChildItem (Join-Path $server 'jobs') -Directory -ErrorAction SilentlyContinue | Where-Object {
    $st = Join-Path $_.FullName 'state.json'
    (Test-Path $st) -and ((Get-Content $st -Raw | ConvertFrom-Json).status -in 'queued', 'running')
}
if ($running -and -not $Force) {
    Write-Host "A job is queued/running ($($running.Name -join ', ')); restart skipped. Run again later or use -Force."
    exit 0
}
Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
Start-Sleep 2
schtasks /run /tn MeetingService-Watchdog | Out-Null
Write-Host 'Service restarted.'
