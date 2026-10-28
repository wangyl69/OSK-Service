param([ValidateRange(0,8)][int]$LoadWorkers = 0,
      [ValidateRange(0,100)][int]$IdleWakeCount = 40,
      [ValidateSet('fixed','deadline')][string]$IdleWaitMode = 'fixed',
      [ValidateSet('standard','cadence','typing','rapid','rapid200','diagnostics','bursts','idle5s','idle15s')][string]$ScenarioSet = 'standard',
      [ValidateRange(1,30)][int]$ScenarioBatches = 5,
      [ValidateRange(0,100)][int]$IdleSamples = 0,
      [ValidateRange(1,20)][int]$CadenceCycles = 1,
      [ValidateSet('normal','above-normal','highest')][string]$ThreadPriority = 'highest',
      [ValidateSet('normal','highest')][string]$InjectorPriority = 'normal',
      [ValidateRange(0,100)][int]$StopPreHookMs = 0,
      [ValidateRange(0,100)][int]$StopAnyStageMs = 0,
      [switch]$ExtraScenarios,
      [switch]$RequireKernelTrace,
      [switch]$PowerDetails,
      [switch]$Elevate)
$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($Elevate -and !$isAdmin) {
    $launchArguments = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'),
        '-LoadWorkers',"$LoadWorkers",'-IdleWakeCount',"$IdleWakeCount",'-IdleWaitMode',$IdleWaitMode,
        '-ScenarioSet',$ScenarioSet,'-ScenarioBatches',"$ScenarioBatches",'-IdleSamples',"$IdleSamples",'-CadenceCycles',"$CadenceCycles",'-ThreadPriority',$ThreadPriority,'-InjectorPriority',$InjectorPriority,'-StopPreHookMs',"$StopPreHookMs",'-StopAnyStageMs',"$StopAnyStageMs")
    if ($RequireKernelTrace) { $launchArguments += '-RequireKernelTrace' }
    if ($ExtraScenarios) { $launchArguments += '-ExtraScenarios' }
    if ($PowerDetails) { $launchArguments += '-PowerDetails' }
    Write-Output 'Requesting normal Windows UAC elevation for this capture only.'
    $captureProcess = Start-Process -FilePath "$PSHOME/powershell.exe" -Verb RunAs -WindowStyle Hidden -ArgumentList $launchArguments -PassThru -Wait
    exit $captureProcess.ExitCode
}
if ($CadenceCycles -ne 1 -and $ScenarioSet -ne 'cadence') {
    throw 'CadenceCycles requires ScenarioSet cadence.'
}
if ($ExtraScenarios -and $ScenarioSet -eq 'cadence') {
    throw 'ExtraScenarios applies only to the standard scenario set.'
}
function Invoke-Wpr([string[]]$CommandArguments) {
    # WPR writes harmless profile-mode notices to stderr. Windows PowerShell
    # turns those into ErrorRecords; judge native success by the exit code.
    $priorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $lines = @(& wpr.exe @CommandArguments 2>&1 | ForEach-Object { $_.ToString() })
        $code = $LASTEXITCODE
        return [pscustomobject]@{ Lines = $lines; ExitCode = $code }
    } finally { $ErrorActionPreference = $priorPreference }
}
$projectDir = Split-Path $PSScriptRoot -Parent
$runId = 'schedule-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8)
$instance = 'HybridSED-' + [guid]::NewGuid().ToString('N')
$outputDir = Join-Path $projectDir ('logs/real-pipeline-' + $runId)
$null = New-Item -ItemType Directory -Path $outputDir
$profile = (Join-Path $PSScriptRoot 'Scheduling.wprp') + '!HybridScheduling'
$recordingStarted = $false
$metadata = [ordered]@{
    run_label = $runId; wpr_instance = $instance; trace_started = $false
    start_utc = [DateTime]::UtcNow.ToString('o')
    qpc_frequency = [Diagnostics.Stopwatch]::Frequency
    start_qpc = [Diagnostics.Stopwatch]::GetTimestamp()
    is_admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    profile = $profile
    service_idle_wait_mode = $IdleWaitMode
    scenario_set = $ScenarioSet
    scenario_batches = $ScenarioBatches
    idle_samples = $IdleSamples
    cadence_cycles = $CadenceCycles
    thread_priority = $ThreadPriority
    injector_priority = $InjectorPriority
    stop_pre_hook_ms = $StopPreHookMs
    stop_any_stage_ms = $StopAnyStageMs
    power_details = [bool]$PowerDetails
    extra_scenarios = [bool]$ExtraScenarios
}
$pipelineArguments = @((Join-Path $projectDir 'tests/check_real_pipeline.py'),
    '--with-osk', '--quiet', '--sed-trace', '--status-mode', 'interrupt',
    '--mock-status-delay-ms', '200', '--service-idle-wait', $IdleWaitMode,
    '--scenario-set', $ScenarioSet,
    '--scenario-batches', "$ScenarioBatches",
    '--cadence-cycles', "$CadenceCycles",
    '--thread-priority', $ThreadPriority,
    '--injector-priority', $InjectorPriority,
    '--stop-prehook-ms', "$StopPreHookMs",
    '--stop-any-stage-ms', "$StopAnyStageMs",
    '--load-workers', "$LoadWorkers", '--idle-wake-count', "$IdleWakeCount", '--run-label', $runId)
# Include the 500Hz target/burst phase where the 2026-10-26 queue tail occurred.
if ($ExtraScenarios) { $pipelineArguments += '--extra-scenarios' }
if ($PowerDetails) { $pipelineArguments += '--cpu-snapshots' }
if ($IdleSamples -gt 0) { $pipelineArguments += @('--idle-samples', "$IdleSamples") }
$metadata.pipeline_arguments = $pipelineArguments
try {
    $details = Invoke-Wpr @('-profiledetails', $profile)
    $details.Lines | Out-File (Join-Path $outputDir 'wpr-profile.txt') -Encoding utf8
    if ($details.ExitCode -ne 0) { throw 'WPR profile validation failed' }
    $startArguments = @('-start', $profile)
    if ($PowerDetails) { $startArguments += @('-start', 'Power') }
    $startArguments += @('-filemode', '-instancename', $instance)
    & powercfg /getactivescheme | Out-File (Join-Path $outputDir 'power-scheme.txt') -Encoding utf8
    $startOutput = Invoke-Wpr $startArguments
    $startOutput.Lines | Out-File (Join-Path $outputDir 'wpr-start.txt') -Encoding utf8
    $metadata.wpr_start_exit = $startOutput.ExitCode
    $recordingStarted = $startOutput.ExitCode -eq 0
    $metadata.trace_started = $recordingStarted
    if (!$recordingStarted) {
        if ($RequireKernelTrace) {
            Write-Warning 'Kernel recording unavailable; required capture stopped before keyboard injection. No policy/elevation changes.'
            throw 'Kernel trace was required but could not start; see wpr-start.txt'
        }
        Write-Warning 'Kernel recording unavailable. No policy/elevation changes; proceeding with application stage timing only.'
    }
    & py -3 @pipelineArguments
    $metadata.pipeline_exit = $LASTEXITCODE
    if ($LASTEXITCODE -ne 0) { throw 'Pipeline failed; partial timing data retained' }
    & py -3 (Join-Path $projectDir 'tests/analyze_latency.py') $outputDir --output (Join-Path $outputDir 'stage-summary.json') | Out-File (Join-Path $outputDir 'stage-summary-console.txt') -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw 'Timing analysis failed' }
    & py -3 (Join-Path $projectDir 'tests/analyze_wait_timeline.py') $outputDir | Out-File (Join-Path $outputDir 'wait-timeline-console.txt') -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw 'Wait timeline validation failed' }
} finally {
    if ($recordingStarted) {
        $stopOutput = Invoke-Wpr @('-stop', (Join-Path $outputDir 'scheduler.etl'), '-instancename', $instance)
        $stopOutput.Lines | Out-File (Join-Path $outputDir 'wpr-stop.txt') -Encoding utf8
        $metadata.wpr_stop_exit = $stopOutput.ExitCode
        if ($stopOutput.ExitCode -ne 0) {
            # Only cancel the unique instance this script successfully started.
            $cancelOutput = Invoke-Wpr @('-cancel', '-instancename', $instance)
            $cancelOutput.Lines | Out-File (Join-Path $outputDir 'wpr-cancel.txt') -Encoding utf8
        }
    }
    $metadata.end_utc = [DateTime]::UtcNow.ToString('o')
    $metadata.end_qpc = [Diagnostics.Stopwatch]::GetTimestamp()
    $metadata | ConvertTo-Json -Depth 5 | Out-File (Join-Path $outputDir 'capture.json') -Encoding utf8
    Write-Output "Capture metadata and results: $outputDir"
}
