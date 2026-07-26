[CmdletBinding()]
param(
    [string]$TaskName = "netmon",
    [string]$PythonPath = "python.exe",
    [string]$InstallRoot = ""
)

$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Installing the netmon startup task requires elevation. Run PowerShell as Administrator."
    exit 1
}

if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    $InstallRoot = Join-Path $PSScriptRoot ".."
}
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$LogDirectory = Join-Path $InstallRoot "data\logs"
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null

$python = (Get-Command $PythonPath -ErrorAction Stop).Source
$arguments = "-m netmon service-run --working-dir `"$InstallRoot`" --log-dir `"$LogDirectory`""
$action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $InstallRoot
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable
$taskPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $taskPrincipal `
    -Description "netmon home network monitoring service" `
    -Force | Out-Null

Write-Output "Installed scheduled task '$TaskName'. Logs: $LogDirectory"
