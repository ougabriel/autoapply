# uninstall_auto_loop_task.ps1 - remove the hourly auto-loop scheduled task.
$ErrorActionPreference = "Stop"
$TaskName = "JobApplyAutoLoop"
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'."
} else {
    Write-Host "No scheduled task '$TaskName' found."
}
