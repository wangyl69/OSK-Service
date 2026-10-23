param([ValidateRange(0,8)][int]$LoadWorkers = 0,
      [ValidateRange(0,100)][int]$IdleWakeCount = 40,
      [ValidateSet('fixed','deadline')][string]$IdleWaitMode = 'fixed',
      [switch]$RequireKernelTrace)
$ErrorActionPreference = 'Stop'
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
}
try {
    $details = Invoke-Wpr @('-profiledetails', $profile)
    $details.Lines | Out-File (Join-Path $outputDir 'wpr-profile.txt') -Encoding utf8
    if ($details.ExitCode -ne 0) { throw 'WPR profile validation failed' }
    $startOutput = Invoke-Wpr @('-start', $profile, '-filemode', '-instancename', $instance)
    $startOutput.Lines | Out-File (Join-Path $outputDir 'wpr-start.txt') -Encoding utf8
    $metadata.wpr_start_exit = $startOutput.ExitCode
    $recordingStarted = $startOutput.ExitCode -eq 0
    $metadata.trace_started = $recordingStarted
    if (!$recordingStarted) {
        Write-Warning 'Kernel recording unavailable. No policy/elevation changes; proceeding with application stage timing only.'
        if ($RequireKernelTrace) { throw 'Kernel trace was required but could not start; see wpr-start.txt' }
    }
    & py -3 (Join-Path $projectDir 'tests/check_real_pipeline.py') --with-osk --quiet --sed-trace --status-mode interrupt --mock-status-delay-ms 200 --service-idle-wait $IdleWaitMode --load-workers $LoadWorkers --idle-wake-count $IdleWakeCount --run-label $runId
    $metadata.pipeline_exit = $LASTEXITCODE
    if ($LASTEXITCODE -ne 0) { throw 'Pipeline failed; partial timing data retained' }
    & py -3 (Join-Path $projectDir 'tests/analyze_latency.py') $outputDir --output (Join-Path $outputDir 'stage-summary.json') | Out-File (Join-Path $outputDir 'stage-summary-console.txt') -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw 'Timing analysis failed' }
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
