param([switch]$Elevate)
$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($Elevate -and !$isAdmin) {
    $argsForCapture = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'))
    $child = Start-Process "$PSHOME/powershell.exe" -Verb RunAs -WindowStyle Hidden -ArgumentList $argsForCapture -Wait -PassThru
    exit $child.ExitCode
}
if (!$isAdmin) { throw 'Kernel comparison requires administrator capture; use -Elevate.' }
$project = Split-Path $PSScriptRoot -Parent
$output = Join-Path $project ('logs/injector-priority-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$null = New-Item -ItemType Directory -Path $output
$manifest = [ordered]@{ scope='Sender thread only; SED/Service Highest, Normal processes, unchanged power/affinity'; runs=@() }
foreach ($level in @('normal','highest','highest','normal')) {
    $before = @(Get-ChildItem (Join-Path $project 'logs') -Directory -Filter 'real-pipeline-schedule-*' | ForEach-Object FullName)
    $number = $manifest.runs.Count + 1
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'Capture-Scheduling.ps1') -RequireKernelTrace -PowerDetails -ScenarioSet cadence -CadenceCycles 1 -LoadWorkers 4 -IdleWakeCount 0 -InjectorPriority $level *> (Join-Path $output "round-$number.txt")
    $code = $LASTEXITCODE
    $created = @(Get-ChildItem (Join-Path $project 'logs') -Directory -Filter 'real-pipeline-schedule-*' | Where-Object { $_.FullName -notin $before } | ForEach-Object FullName)
    $manifest.runs += [ordered]@{ round=$number; injector_priority=$level; exit_code=$code; captures=$created }
    $manifest | ConvertTo-Json -Depth 6 | Out-File (Join-Path $output 'manifest.json') -Encoding utf8
    if ($code -ne 0 -or $created.Count -ne 1) { throw "Capture round $number failed; evidence in $output" }
}
Write-Output "Completed sender priority ABBA: $output"
