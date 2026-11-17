param([Parameter(Mandatory=$true)][string]$CaseDirectory)
$ErrorActionPreference='Stop'
function Save-DeviceJson($value,[string]$name) {
    ConvertTo-Json -InputObject $value -Depth 12 | Set-Content -LiteralPath (Join-Path $CaseDirectory $name) -Encoding UTF8
}
try {
    $metadata=Get-Content -LiteralPath (Join-Path $CaseDirectory 'panel-metadata.json') -Raw | ConvertFrom-Json
    $devices=@(Get-PnpDevice -PresentOnly -Class HIDClass | Select-Object Status,FriendlyName,InstanceId,Problem)
    Save-DeviceJson $devices 'hid-devices.json'
    $patterns=@($metadata.digitizer_collections | ForEach-Object {'VID_{0:X4}&PID_{1:X4}' -f $_.vid,$_.pid})
    $nodes=@{};$parents=@()
    foreach ($device in $devices) {
        $matchesPanel=$device.FriendlyName -match 'touch|digitizer|wacom|I2C|\u89e6\u6478'
        foreach ($pattern in $patterns) {if ($device.InstanceId -match [regex]::Escape($pattern)) {$matchesPanel=$true}}
        if (!$matchesPanel) {continue}
        $id=$device.InstanceId
        for ($depth=0;$depth -lt 8 -and $id -and !$nodes.ContainsKey($id);$depth++) {
            $nodes[$id]=$true
            $properties=@(Get-PnpDeviceProperty -InstanceId $id -ErrorAction SilentlyContinue |
                Where-Object {$_.KeyName -match 'DEVPKEY_Device_(Parent|Service|EnumeratorName|BusTypeGuid|BusReportedDeviceDesc|DriverVersion|DriverProvider|ProblemCode|HardwareIds)$'} |
                Select-Object KeyName,Data)
            $parents+=@{InstanceId=$id;Properties=$properties}
            Save-DeviceJson $parents 'digitizer-parent-chains.json'
            $id=($properties | Where-Object KeyName -eq 'DEVPKEY_Device_Parent' | Select-Object -First 1).Data
        }
    }
    Save-DeviceJson $parents 'digitizer-parent-chains.json'
} catch {
    $_ | Out-String | Set-Content -LiteralPath (Join-Path $CaseDirectory 'pnp-error.txt') -Encoding UTF8
    exit 1
}
