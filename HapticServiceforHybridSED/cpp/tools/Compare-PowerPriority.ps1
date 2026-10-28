param([switch]$Elevate)
$ErrorActionPreference = 'Stop'
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (!$admin) {
    if (!$Elevate) { throw 'Use -Elevate or an administrator PowerShell.' }
    $child = Start-Process "$PSHOME/powershell.exe" -Verb RunAs -WindowStyle Hidden -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"'+$PSCommandPath+'"')) -PassThru -Wait
    exit $child.ExitCode
}
$project = Split-Path $PSScriptRoot -Parent
$output = Join-Path $project ('logs/power-priority-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$null = New-Item -ItemType Directory -Path $output
function Power([string[]]$Arguments) {
    $value = & powercfg @Arguments
    if ($LASTEXITCODE -ne 0) { throw "powercfg failed: $Arguments" }
    return $value
}
Add-Type @'
using System.Runtime.InteropServices;
public static class PowerSourceProbe {
 [StructLayout(LayoutKind.Sequential)] public struct Status {public byte ACLineStatus,BatteryFlag,BatteryLifePercent,SystemStatusFlag;public uint BatteryLifeTime,BatteryFullLifeTime;}
 [DllImport("kernel32.dll")] static extern bool GetSystemPowerStatus(out Status s);
 public static Status Read(){Status s;if(!GetSystemPowerStatus(out s))throw new System.Exception("GetSystemPowerStatus failed");return s;}
}
'@
$originalText = Power @('/getactivescheme')
$original = [regex]::Match(($originalText -join ' '),'[0-9a-fA-F-]{36}').Value
if (!$original) { throw 'Cannot identify original scheme' }
$temporary = [guid]::NewGuid().ToString()
$manifest = [ordered]@{original_scheme=$original;temporary_scheme=$temporary;restored=$false;temporary_deleted=$false;runs=@()}
$manifest | ConvertTo-Json -Depth 12 | Out-File (Join-Path $output 'manifest.json') -Encoding utf8
$created = $false
$plan = @(
    @('original','above-normal'), @('original','highest'), @('original','highest'), @('original','above-normal'),
    @('reduced-parking','above-normal'), @('reduced-parking','above-normal'), @('original','above-normal'))
try {
    $null = Power @('/duplicatescheme',$original,$temporary)
    $created = $true
    # Modify only the duplicate. No affinity, EPP, frequency or priority changes here.
    foreach ($setting in @('0cc5b647-c1df-4637-891a-dec35c318583','0cc5b647-c1df-4637-891a-dec35c318584')) {
        $null = Power @('/setacvalueindex',$temporary,'SUB_PROCESSOR',$setting,'100')
        $null = Power @('/setdcvalueindex',$temporary,'SUB_PROCESSOR',$setting,'100')
    }
    Power @('/qh',$original,'SUB_PROCESSOR') | Out-File (Join-Path $output 'original-processor-policy.txt') -Encoding utf8
    Power @('/qh',$temporary,'SUB_PROCESSOR') | Out-File (Join-Path $output 'temporary-processor-policy.txt') -Encoding utf8
    foreach ($case in $plan) {
        $scheme = if ($case[0] -eq 'original') { $original } else { $temporary }
        $null = Power @('/setactive',$scheme)
        $before = @(Get-ChildItem (Join-Path $project 'logs') -Directory -Filter 'real-pipeline-schedule-*' | ForEach-Object Name)
        $entry = [ordered]@{power_case=$case[0];priority=$case[1];scheme=$scheme;source_before=[PowerSourceProbe]::Read()}
        $manifest.runs += $entry
        & "$PSHOME/powershell.exe" -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'Capture-Scheduling.ps1') -RequireKernelTrace -PowerDetails -ScenarioSet cadence -CadenceCycles 1 -LoadWorkers 0 -IdleWakeCount 0 -ThreadPriority $case[1] *> (Join-Path $output ("run-"+$manifest.runs.Count+'.txt'))
        $entry.exit_code = $LASTEXITCODE
        $entry.source_after = [PowerSourceProbe]::Read()
        $new = @(Get-ChildItem (Join-Path $project 'logs') -Directory -Filter 'real-pipeline-schedule-*' | Where-Object { $_.Name -notin $before })
        if ($new.Count -eq 1) { $entry.capture = $new[0].FullName }
        $manifest | ConvertTo-Json -Depth 12 | Out-File (Join-Path $output 'manifest.json') -Encoding utf8
        if ($entry.exit_code -ne 0) { throw 'Capture failed; see preserved run log.' }
        if ($entry.source_before.ACLineStatus -ne $entry.source_after.ACLineStatus) { throw 'Power source changed during paired test; do not pool results.' }
    }
} finally {
    $null = Power @('/setactive',$original)
    $actual = Power @('/getactivescheme')
    $manifest.restored = ($actual -join ' ') -match [regex]::Escape($original)
    if ($created -and $manifest.restored -and $temporary -ne $original) {
        $null = Power @('/delete',$temporary)
        $manifest.temporary_deleted = $true
    }
    $manifest | ConvertTo-Json -Depth 12 | Out-File (Join-Path $output 'manifest.json') -Encoding utf8
    Write-Output "Pairing manifest: $output"
}
