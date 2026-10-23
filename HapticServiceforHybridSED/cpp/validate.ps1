param([string]$Compiler = '', [switch]$Extended)
$ErrorActionPreference = 'Stop'
$workspace = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$sedRoot = Join-Path $workspace 'HybridSedPrototype_ExtraInfo'
$priorRollForward = $env:DOTNET_ROLL_FORWARD
try {
    $env:DOTNET_ROLL_FORWARD = 'Major'
    & dotnet build (Join-Path $sedRoot 'HybridSedPrototype.slnx')
    if ($LASTEXITCODE -ne 0) { throw 'C# build failed' }
    & dotnet test (Join-Path $sedRoot 'HybridSedPrototype.slnx') --no-build
    if ($LASTEXITCODE -ne 0) { throw 'C# tests failed' }
    if (!(Test-Path (Join-Path $PSScriptRoot 'build/CMakeCache.txt')) -and !$Compiler) {
        $candidate = Join-Path $env:LOCALAPPDATA 'HybridSedCppTools/llvm-mingw-20261006-ucrt-x86_64/bin/clang++.exe'
        if (Test-Path $candidate) { $Compiler = $candidate }
    }
    if ($Compiler) { & (Join-Path $PSScriptRoot 'build.ps1') -Compiler $Compiler }
    else { & (Join-Path $PSScriptRoot 'build.ps1') }
    & py -3 (Join-Path $PSScriptRoot 'tests/check_integration.py') (Join-Path $sedRoot 'src/SemanticEventDispatcher/bin/Debug/net8.0/SemanticEventDispatcher.dll') --run-label synthetic
    if ($LASTEXITCODE -ne 0) { throw 'End-to-end mock validation failed' }
    & py -3 (Join-Path $PSScriptRoot 'tests/check_service_features.py')
    if ($LASTEXITCODE -ne 0) { throw 'Service feature/latency-policy validation failed' }
    & py -3 (Join-Path $PSScriptRoot 'tests/check_async_status.py')
    if ($LASTEXITCODE -ne 0) { throw 'Asynchronous interrupt feedback validation failed' }
    & py -3 (Join-Path $PSScriptRoot 'tests/check_integration.py') (Join-Path $sedRoot 'src/SemanticEventDispatcher/bin/Debug/net8.0/SemanticEventDispatcher.dll') --osk-dll (Join-Path $workspace 'OSK_Prototype/Resources/LrSendInput64.dll') --run-label osk-dll
    if ($LASTEXITCODE -ne 0) { throw 'Actual OSK native injector integration failed' }
    if ($Extended) {
        & py -3 (Join-Path $PSScriptRoot 'tests/check_real_pipeline.py') --with-osk --run-label extended --quiet
        if ($LASTEXITCODE -ne 0) { throw 'Real pipeline latency/soak failed' }
        & py -3 (Join-Path $PSScriptRoot 'tests/check_fault_latch.py')
        if ($LASTEXITCODE -ne 0) { throw 'FAULT latch/backoff validation failed' }
    }
    Write-Output 'PASS: C# build/tests, C++ build/tests, direct-pipe end-to-end mock.'
} finally {
    $env:DOTNET_ROLL_FORWARD = $priorRollForward
}
