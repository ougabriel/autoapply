# install_auto_loop_task.ps1 - register the hourly auto-loop with Task Scheduler.
# Runs auto_loop_tick.ps1 every hour at :23 (off-minute by design, matching the WAT).
# Survives logoff/reboot. Re-run to update.
#
# Usage (from the project root):
#   powershell -ExecutionPolicy Bypass -File scripts\install_auto_loop_task.ps1

$ErrorActionPreference = "Stop"
$TaskName = "JobApplyAutoLoop"
$Root     = Split-Path -Parent $PSScriptRoot
$Tick     = Join-Path $Root "scripts\auto_loop_tick.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Tick`""

# Hourly at :23 past the hour.
$trigger = New-ScheduledTaskTrigger -Daily -At 12:23am
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At 12:23am `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)).Repetition

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "jobapply-AI hourly autonomous apply loop" -Force | Out-Null

Write-Host "Installed scheduled task '$TaskName' (hourly at :23)."
Write-Host "It calls the local app to start a batch. Keep 'python -m app.main' running."
Write-Host "Remove with: scripts\uninstall_auto_loop_task.ps1"
