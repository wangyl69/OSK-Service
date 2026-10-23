param([string]$Compiler = '')
$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$buildDir = Join-Path $projectDir 'build'
$arguments = @('-S', $projectDir, '-B', $buildDir, '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release')
if ($Compiler) { $arguments += "-DCMAKE_CXX_COMPILER=$Compiler" }
& cmake @arguments
if ($LASTEXITCODE -ne 0) { throw 'CMake configuration failed. Use an x64 C++ developer terminal, or pass -Compiler.' }
& cmake --build $buildDir
if ($LASTEXITCODE -ne 0) { throw 'C++ build failed' }
& ctest --test-dir $buildDir --output-on-failure
if ($LASTEXITCODE -ne 0) { throw 'C++ tests failed' }
