[CmdletBinding()]
param(
    [ValidateSet('All','CollectOnly')][string]$Mode='All',
    [string]$OskDirectory,
    [string]$ProbePath,
    [string]$OutputRoot
)
$ErrorActionPreference='Stop'
if (!$OskDirectory) {
    $OskDirectory=Join-Path $PSScriptRoot 'Apps/OSK'
    if (!(Test-Path -LiteralPath $OskDirectory)) { $OskDirectory=Join-Path $PSScriptRoot '../../../OSK_Prototype' }
}
if (!$ProbePath) {
    $ProbePath=Join-Path $PSScriptRoot 'Diagnostics/PanelProbe.exe'
    if (!(Test-Path -LiteralPath $ProbePath)) { $ProbePath=Join-Path $PSScriptRoot '../build/PanelProbe.exe' }
}
$ProbePath=(Resolve-Path -LiteralPath $ProbePath).Path
$OskDirectory=(Resolve-Path -LiteralPath $OskDirectory).Path
if (!$OutputRoot) { $OutputRoot=Join-Path $PSScriptRoot 'logs' }
$case=Join-Path $OutputRoot ('panel-input-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,6))
$null=New-Item -ItemType Directory -Path $case -Force
$script:hadError=$false
function Save-Json($value,[string]$name) {
    ConvertTo-Json -InputObject $value -Depth 14 | Set-Content -LiteralPath (Join-Path $case $name) -Encoding UTF8
}
function Run-Probe([string]$name,[string]$arguments) {
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=$ProbePath; $info.Arguments=$arguments
    $info.UseShellExecute=$false; $info.CreateNoWindow=$true
    $info.RedirectStandardOutput=$true; $info.RedirectStandardError=$true
    $process=New-Object Diagnostics.Process; $process.StartInfo=$info
    $null=$process.Start()
    $stdout=$process.StandardOutput.ReadToEndAsync(); $stderr=$process.StandardError.ReadToEndAsync()
    if (!$process.WaitForExit(60000)) { $process.Kill(); throw 'Probe timed out; partial evidence is retained.' }
    $stdout.Result | Set-Content -LiteralPath (Join-Path $case ($name+'.json')) -Encoding UTF8
    $stderr.Result | Set-Content -LiteralPath (Join-Path $case ($name+'.stderr.txt')) -Encoding UTF8
    if ($process.ExitCode -ne 0) { throw "Probe failed ($($process.ExitCode)); see $name.stderr.txt" }
    $parsed=$stdout.Result | ConvertFrom-Json
    if ($arguments -and ($parsed.touch_capture.cancelled -or $parsed.touch_capture.report_limit_reached)) {
        Write-Warning "$name capture was cancelled or reached its sample limit. The partial evidence is saved."
    }
    return $parsed
}
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
public static class PanelWindowSnapshot {
    public delegate bool Callback(IntPtr window, IntPtr data);
    [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left,Top,Right,Bottom; }
    [DllImport("user32.dll")] static extern bool EnumWindows(Callback callback,IntPtr data);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window,out uint process);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr window,out Rect rect);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr window);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr window,StringBuilder text,int max);
    public static object[] Read() {
        var result=new List<object>(); var previous=SetThreadDpiAwarenessContext(new IntPtr(-4));
        try { EnumWindows(delegate(IntPtr window,IntPtr data) {
            uint id; GetWindowThreadProcessId(window,out id);
            string name; try { using(var p=Process.GetProcessById((int)id)) name=p.ProcessName; } catch {return true;}
            if(name!="OskDemoApp" && name!="notepad" && name!="SemanticEventDispatcher" && name!="HybridHaptic")return true;
            Rect rect;GetWindowRect(window,out rect);var cls=new StringBuilder(256);GetClassName(window,cls,256);
            result.Add(new {Process=name,PID=id,Handle=window.ToInt64(),Visible=IsWindowVisible(window),
                Foreground=GetForegroundWindow()==window,ClassName=cls.ToString(),DPI=GetDpiForWindow(window),
                PhysicalRect=rect});return true;
        },IntPtr.Zero); } finally {if(previous!=IntPtr.Zero)SetThreadDpiAwarenessContext(previous);}
        return result.ToArray();
    }
}
'@
function Snapshot([string]$name) {
    Save-Json @([PanelWindowSnapshot]::Read()) ($name+'-windows.json')
    $hashes=foreach ($relative in @('OskDemoApp.exe','OskDemoApp.dll','OskWin.dll','AppConfig.json','Resources/osk.bin')) {
        $file=Join-Path $OskDirectory $relative
        if (Test-Path -LiteralPath $file -PathType Leaf) {
            @{File=$relative;Bytes=(Get-Item -LiteralPath $file).Length;SHA256=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash}
        }
    }
    Save-Json @($hashes) ($name+'-osk-hashes.json')
    $destination=Join-Path $case ($name+'-osk');$null=New-Item -ItemType Directory -Path $destination -Force
    foreach ($relative in @('AppConfig.json','Resources/osk.bin')) {
        $file=Join-Path $OskDirectory $relative
        if (Test-Path -LiteralPath $file -PathType Leaf) {Copy-Item -LiteralPath $file -Destination $destination}
    }
    $logs=Join-Path $OskDirectory 'logs'
    if (Test-Path -LiteralPath $logs) {
        $latest=Get-ChildItem -LiteralPath $logs -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($latest) {
            $target=Join-Path $destination 'latest-session';$null=New-Item -ItemType Directory -Path $target -Force
            foreach ($file in @(Get-ChildItem -LiteralPath $latest.FullName -File)) {
                try {Copy-Item -LiteralPath $file.FullName -Destination $target -ErrorAction Stop}
                catch {Write-Warning "Could not copy $($file.Name): $_"}
            }
        }
    }
}
try {
    Write-Host 'Read-only touch diagnostic. No driver, firmware or OSK settings are changed.'
    Write-Host 'Baseline tests the PRIMARY display. Do not type confidential text during capture.'
    Save-Json @{StartedAt=(Get-Date).ToString('o');Mode=$Mode;OskDirectory=$OskDirectory;
        OS=[Environment]::OSVersion.VersionString;PowerShell=$PSVersionTable.PSVersion.ToString();
        ProbeSHA256=(Get-FileHash -LiteralPath $ProbePath -Algorithm SHA256).Hash} 'case.json'
    Snapshot 'before'
    $metadata=Run-Probe 'panel-metadata' ''
    # Bound slow PnP providers; do not let driver metadata block touch capture.
    $worker=Join-Path $PSScriptRoot 'Collect-PanelDevices.ps1'
    $pnpInfo=New-Object Diagnostics.ProcessStartInfo
    $pnpInfo.FileName=(Join-Path $PSHOME 'powershell.exe')
    $pnpInfo.Arguments='-NoProfile -ExecutionPolicy Bypass -File "'+$worker+'" -CaseDirectory "'+$case+'"'
    $pnpInfo.UseShellExecute=$false;$pnpInfo.CreateNoWindow=$true
    $pnp=New-Object Diagnostics.Process;$pnp.StartInfo=$pnpInfo
    $null=$pnp.Start()
    if (!$pnp.WaitForExit(30000)) {
        $pnp.Kill();$pnp.WaitForExit()
        'PnP provider exceeded 30 seconds. Partial metadata retained; not evidence of a failed panel.' |
            Set-Content -LiteralPath (Join-Path $case 'pnp-timeout.txt') -Encoding UTF8
    }
    $pnp.Dispose()
    if ($Mode -eq 'All') {
        Write-Host 'STEP 1: Exit OSK_Prototype and other touch test apps. Press Enter when ready.'
        $null=Read-Host
        if (Get-Process OskDemoApp -ErrorAction SilentlyContinue) {throw 'OskDemoApp is still running. Exit it and run this collector again.'}
        Write-Host 'A 30-second target screen will open. Tap each red target several times as it moves (5 targets, 6 seconds each).'
        $baseline=Run-Probe 'baseline-touch' '--capture-seconds 30 --show-targets'
        Write-Host "Baseline: raw reports=$($baseline.touch_capture.sample_count), Windows touch pointers=$($baseline.touch_capture.windows_touch_pointer_count)."
        $answer=Read-Host 'Did normal Windows touch work after closing OSK? Enter yes/no/offset/unknown'
        Save-Json @{NormalWindowsTouch=$answer} 'baseline-observation.json'
        Write-Host 'STEP 2: Start-OSK.bat, open Notepad, keep visible A/S/D keys. Return here and press Enter.'
        $null=Read-Host
        Snapshot 'osk-start'
        if (Get-Process OskDemoApp -ErrorAction SilentlyContinue) {
            Write-Host 'For the next 20 seconds: switch to Notepad, click its editor, then finger-tap A/S/D (one per second). Do not use Replay Play.'
            $null=Run-Probe 'osk-touch' '--capture-seconds 20'
            $answer=Read-Host 'Describe key highlight, typed text, and whether touched/highlighted keys matched'
            Save-Json @{OskTouchResult=$answer} 'osk-observation.json'
        } else {Save-Json @{Skipped=$true;Reason='OskDemoApp not running'} 'osk-observation.json'}
    }
    Snapshot 'after'
} catch {
    $script:hadError=$true
    $_ | Out-String | Set-Content -LiteralPath (Join-Path $case 'collector-error.txt') -Encoding UTF8
    Write-Warning "Collection was incomplete: $_"
} finally {
    $files=Get-ChildItem -LiteralPath $case -Recurse -File | ForEach-Object {
        @{File=$_.FullName.Substring($case.Length+1);Bytes=$_.Length;SHA256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash}
    }
    Save-Json @($files) 'evidence-hashes.json'
    $zip=$case+'.zip';Compress-Archive -LiteralPath $case -DestinationPath $zip
    Write-Host "Saved: $zip"
    Write-Host 'Send this ZIP with your observations. Zero samples alone does not prove a broken panel.'
}
if ($script:hadError) {exit 1}
