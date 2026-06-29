param(
    [string]$TaskName = "AI视频识别平台",
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot ".."))
)

$ErrorActionPreference = "Stop"
$Supervisor = Join-Path $ProjectRoot "scripts\run_supervisor.ps1"
if (-not (Test-Path $Supervisor)) {
    throw "Supervisor script not found: $Supervisor"
}

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Supervisor`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 0)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "AI视频识别平台后端和前端守护启动任务" -Force | Out-Null
Write-Host "Installed scheduled task: $TaskName"
Write-Host "Start manually: Start-ScheduledTask -TaskName `"$TaskName`""
