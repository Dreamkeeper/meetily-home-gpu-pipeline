# Build the Meetily fork (Vulkan) on Windows, mirroring .github/workflows/build-windows.yml.
$ErrorActionPreference = 'Stop'
$repo = Join-Path $PSScriptRoot '..\meetily' | Resolve-Path
# Fresh environment: tools were installed after this shell started
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
$env:VULKAN_SDK = [Environment]::GetEnvironmentVariable('VULKAN_SDK', 'Machine')
# whisper-rs-sys uses bindgen 0.69, which emits an opaque whisper_full_params with libclang 23
# (and its bundled bindings are Linux-only); use libclang 18 from the PyPI 'libclang' wheel.
$env:LIBCLANG_PATH = Join-Path $PSScriptRoot 'libclang18'
if (-not (Test-Path (Join-Path $env:LIBCLANG_PATH 'libclang.dll'))) {
    Write-Host '=== fetching libclang 18 (PyPI wheel libclang==18.1.1)'
    New-Item -ItemType Directory -Force $env:LIBCLANG_PATH | Out-Null
    python -m pip download libclang==18.1.1 --only-binary=:all: --platform win_amd64 --no-deps -d $env:LIBCLANG_PATH -q
    $wheel = Get-ChildItem $env:LIBCLANG_PATH -Filter 'libclang-*.whl' | Select-Object -First 1
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($wheel.FullName)
    $entry = $zip.Entries | Where-Object { $_.FullName -like '*/libclang.dll' } | Select-Object -First 1
    [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $env:LIBCLANG_PATH 'libclang.dll'), $true)
    $zip.Dispose()
}
$env:CARGO_TERM_COLOR = 'never'
Write-Host "VULKAN_SDK=$env:VULKAN_SDK"; cargo --version; cmake --version | Select-Object -First 1; pnpm --version

Set-Location $repo
Write-Host '=== llama-helper sidecar (CPU-only, as in CI)'
cargo build --release -p llama-helper
if ($LASTEXITCODE) { throw 'llama-helper build failed' }
New-Item -ItemType Directory -Force frontend\src-tauri\binaries | Out-Null
Copy-Item target\release\llama-helper.exe frontend\src-tauri\binaries\llama-helper-x86_64-pc-windows-msvc.exe -Force

Set-Location (Join-Path $repo 'frontend')
Write-Host '=== pnpm install'
pnpm install --frozen-lockfile
if ($LASTEXITCODE) { throw 'pnpm install failed' }

Write-Host '=== tauri build (vulkan)'
# Updater artifacts need the upstream signing key; a local fork build skips them.
pnpm tauri build --config (Join-Path $PSScriptRoot 'tauri.fork.conf.json') -- --features vulkan
if ($LASTEXITCODE) { throw 'tauri build failed' }
Get-ChildItem -Recurse (Join-Path $repo 'target\release\bundle') -Include *.exe, *.msi | Select-Object FullName, Length
Write-Host '=== BUILD OK'
