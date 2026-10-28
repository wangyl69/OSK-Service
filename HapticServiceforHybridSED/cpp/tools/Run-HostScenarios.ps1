param([switch]$Elevate,
      [ValidateRange(1,30)][int]$ScenarioBatches = 5,
      [ValidateRange(0,100)][int]$IdleSamples = 0,
      [ValidateRange(0,100)][int]$StopAnyStageMs = 5)
$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($Elevate -and !$isAdmin) {
    $launchArguments = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'),
        '-ScenarioBatches',"$ScenarioBatches",'-IdleSamples',"$IdleSamples",'-StopAnyStageMs',"$StopAnyStageMs")
    $child = Start-Process "$PSHOME/powershell.exe" -Verb RunAs -WindowStyle Hidden -ArgumentList $launchArguments -Wait -PassThru
    exit $child.ExitCode
}
if (!$isAdmin) { throw 'Kernel capture requires administrator; use -Elevate.' }
$project = Split-Path $PSScriptRoot -Parent
$output = Join-Path $project ('logs/host-scenarios-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$null = New-Item -ItemType Directory -Path $output
$manifest = [ordered]@{ scope='All software stages, actual DLL and SED/Pipe to mock PLAY; no physical HID/MCU'; scenario_batches=$ScenarioBatches; idle_samples=$IdleSamples; stop_any_stage_ms=$StopAnyStageMs; runs=@() }
$plan = @(
    @{name='typing';workers=0}, @{name='rapid';workers=0}, @{name='rapid200';workers=0},
    @{name='diagnostics';workers=0}, @{name='rapid';workers=4}, @{name='rapid200';workers=4},
    @{name='bursts';workers=0}, @{name='idle5s';workers=0}, @{name='idle15s';workers=0}
)
foreach ($item in $plan) {
    $before = @(Get-ChildItem (Join-Path $project 'logs') -Directory -Filter 'real-pipeline-schedule-*' | ForEach-Object FullName)
    $number = $manifest.runs.Count + 1
    $profileArguments = @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot 'Capture-Scheduling.ps1'),
        '-RequireKernelTrace','-PowerDetails','-ScenarioSet',$item.name,'-ScenarioBatches',"$ScenarioBatches",'-LoadWorkers',"$($item.workers)",'-IdleWakeCount','0','-StopAnyStageMs',"$StopAnyStageMs")
    if ($item.name -in @('idle5s','idle15s') -and $IdleSamples -gt 0) { $profileArguments += @('-IdleSamples',"$IdleSamples") }
    & powershell @profileArguments *> (Join-Path $output "round-$number.txt")
    $code = $LASTEXITCODE
    $created = @(Get-ChildItem (Join-Path $project 'logs') -Directory -Filter 'real-pipeline-schedule-*' | Where-Object { $_.FullName -notin $before } | ForEach-Object FullName)
    $manifest.runs += [ordered]@{round=$number;scenario=$item.name;workers=$item.workers;exit_code=$code;captures=$created}
    $manifest | ConvertTo-Json -Depth 6 | Out-File (Join-Path $output 'manifest.json') -Encoding utf8
    if ($code -ne 0 -or $created.Count -ne 1) { throw "Scenario $number failed; evidence in $output" }
}
Write-Output "Completed host scenario matrix: $output"
