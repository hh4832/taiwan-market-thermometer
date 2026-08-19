$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $projectRoot "run_daily_email.bat"
$configPath = Join-Path $projectRoot "config\email_notification.json"
$taskName = "Taiwan Market Thermometer Daily Email"

if (-not (Test-Path $configPath)) {
    throw "Email configuration not found. Run setup_daily_email.bat first."
}

$config = Get-Content -Raw -Encoding UTF8 $configPath | ConvertFrom-Json
$scheduledTime = [string]$config.scheduled_time
if ($scheduledTime -notmatch '^([01]\d|2[0-3]):[0-5]\d$') {
    throw "Invalid scheduled_time in config\email_notification.json."
}

$action = New-ScheduledTaskAction -Execute "$env:COMSPEC" -Argument "/c `"`"$runner`"`"" -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $scheduledTime
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Scheduled task installed: $taskName (Monday-Friday at $scheduledTime)"
