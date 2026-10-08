# Toolchain for building the Meetily fork on Windows (see meetily docs/BUILDING.md + CI workflow).
# Machine-wide installers raise a UAC prompt each.
$log = Join-Path $PSScriptRoot 'install_toolchain.log'
function Step($name, [scriptblock]$body) {
    Add-Content $log "=== $name $(Get-Date -Format s)"
    try { & $body *>&1 | Out-String | Add-Content $log; Add-Content $log "--- $name exit=$LASTEXITCODE" }
    catch { Add-Content $log "--- $name FAILED: $_" }
}
$w = @('--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity', '-e')

Step 'GitHub CLI' { winget install --id GitHub.cli @w }
Step 'Rustup'     { winget install --id Rustlang.Rustup @w }
Step 'CMake'      { winget install --id Kitware.CMake @w }
Step 'LLVM'       { winget install --id LLVM.LLVM @w }
Step 'VS Build Tools (C++)' {
    winget install --id Microsoft.VisualStudio.2022.BuildTools @w --override `
        '--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended'
}
Step 'Vulkan SDK' { winget install --id KhronosGroup.VulkanSDK @w }
Step 'pnpm'       { npm install -g pnpm@9.15.9 }
Step 'rust stable msvc' {
    & "$env:USERPROFILE\.cargo\bin\rustup.exe" default stable-x86_64-pc-windows-msvc
}
Add-Content $log "=== ALL DONE $(Get-Date -Format s)"
