param([Parameter(Mandatory=$true)][string]$Campaign,
      [ValidateRange(1,24)][int]$TimeoutHours = 3)
$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
$Campaign = (Resolve-Path -LiteralPath $Campaign).Path
$deadline = (Get-Date).AddHours($TimeoutHours)
try {
    # Decode only AFTER every input scenario ends: analysis must not load the measured CPU.
    do {
        if ((Get-Date) -gt $deadline) { throw 'Timed out waiting for campaign; raw evidence retained.' }
        $manifestPath = Join-Path $Campaign 'manifest.json'
        $manifest = $null
        if (Test-Path -LiteralPath $manifestPath) {
            try { $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
        }
        if ($manifest) {
            foreach ($run in $manifest.runs) {
                if ($run.exit_code -ne 0) { throw "Input scenario $($run.round) failed; partial data retained." }
            }
        }
        if (!$manifest -or $manifest.runs.Count -lt 9) { Start-Sleep -Seconds 20 }
    } until ($manifest -and $manifest.runs.Count -eq 9)
    foreach ($run in $manifest.runs) {
        if ($run.captures.Count -ne 1) { throw 'Expected exactly one capture per scenario.' }
        $capture = $run.captures[0]
        & dotnet run --project (Join-Path $PSScriptRoot 'EtlSchedule') -- $capture *> (Join-Path $capture 'kernel-decode-console.txt')
        if ($LASTEXITCODE -ne 0) { throw "ETL decoding failed: $capture" }
        & py -3 (Join-Path $project 'tests/correlate_kernel_waits.py') $capture *> (Join-Path $capture 'kernel-correlation-console.txt')
        if ($LASTEXITCODE -ne 0) { throw "Kernel correlation failed: $capture" }
    }
    & py -3 (Join-Path $project 'tests/analyze_host_scenarios.py') $Campaign *> (Join-Path $Campaign 'analysis-console.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Campaign validation failed; see analysis-console.txt.' }
    $summary = Get-Content (Join-Path $Campaign 'stage-summary.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $lines = @('# Host latency campaign', '', 'Endpoint: mock PLAY; no physical HID/MCU timing.', '',
        "Measured presses: $($summary.measured_presses)", '',
        '| Scenario | Workers | Count | Pipe+Service P99/max ms | DLL to mock P99/max ms | Events >5ms |',
        '|---|---:|---:|---:|---:|---:|')
    foreach ($run in $summary.runs) {
        $pipe = $summary.stages | Where-Object { $_.round -eq $run.round -and $_.stage -eq 'pipe_service' }
        $total = $summary.stages | Where-Object { $_.round -eq $run.round -and $_.stage -eq 'total' }
        $lines += ('| {0} | {1} | {2} | {3:F3}/{4:F3} | {5:F3}/{6:F3} | {7} |' -f
            $run.scenario,$run.workers,$run.presses,($pipe.p99_us/1000),($pipe.max_us/1000),
            ($total.p99_us/1000),($total.max_us/1000),$run.events_over_5ms)
    }
    $lines += @('', 'All stage percentiles: stages.csv. Slow-event timestamps: slow-events.json.',
        'ETL loss and event counts were validated by analyze_host_scenarios.py. Zero observed tails do not prove absence.')
    $lines | Set-Content (Join-Path $Campaign 'REPORT.md') -Encoding UTF8
    'Completed: raw data, ETL validation, stage statistics and report.' | Set-Content (Join-Path $Campaign 'analysis-status.txt') -Encoding UTF8
} catch {
    $_ | Out-String | Set-Content (Join-Path $Campaign 'analysis-status.txt') -Encoding UTF8
    exit 1
}
