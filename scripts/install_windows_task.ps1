param(
    [string]$TaskName = "ChaoguAlertDaily",
    [string]$StartTime = "14:50",
    [string]$ConfigPath = "config.toml",
    [string]$DataSource = "auto"
)

$ErrorActionPreference = "Stop"
$ScriptPath = (Resolve-Path (Join-Path $PSScriptRoot "run_daily_scan.ps1")).Path
$ConfigFullPath = (Resolve-Path (Join-Path (Split-Path -Parent $PSScriptRoot) $ConfigPath)).Path
$PowerShellExe = (Get-Command powershell -ErrorAction Stop).Source
$ArgumentString = "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`" -ConfigPath `"$ConfigFullPath`" -DataSource `"$DataSource`" -SendEmail"

$Action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $ArgumentString
$Trigger = New-ScheduledTaskTrigger -Daily -At $StartTime

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Description "Run A-share alert scan after market close" `
    -Force | Out-Null

Write-Host "Scheduled task created:" $TaskName
