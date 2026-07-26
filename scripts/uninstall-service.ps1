[CmdletBinding()]
param(
    [string]$TaskName = "netmon"
)

$ErrorActionPreference = "Stop"
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Removed scheduled task '$TaskName'. Database and configuration were left intact."
} else {
    Write-Output "Scheduled task '$TaskName' was not installed; nothing to remove."
}
