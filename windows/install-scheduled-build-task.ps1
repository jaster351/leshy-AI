param(
    [switch]$Uninstall
)

# Run ONCE in an elevated PowerShell window under the Windows account
# that owns the local checkout and Python/Git environment.
$ErrorActionPreference = 'Stop'
$TaskName = 'Leshy AI - Git Build (15 min)'
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$RunnerScript = Join-Path $PSScriptRoot 'scheduled-build-task.ps1'
$RunnerVbs = Join-Path $PSScriptRoot 'scheduled-build-hidden.vbs'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principalCheck = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principalCheck.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script once in PowerShell opened as Administrator.'
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host ("Removed Windows scheduled task: " + $TaskName)
    return
}

foreach ($path in @($RunnerScript, $RunnerVbs, (Join-Path $RepoRoot 'windows\build.ps1'))) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw ('Required file not found: ' + $path)
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot '.git'))) {
    throw ('Not a local Git checkout: ' + $RepoRoot)
}

# wscript.exe is a GUI launcher; the PowerShell child window stays hidden.
$wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'
$action = New-ScheduledTaskAction -Execute $wscript -Argument ('//B //Nologo "' + $RunnerVbs + '"') -WorkingDirectory $RepoRoot

# ScheduledTasks exposes repetition arguments only for -Once triggers.
# With no RepetitionDuration, Task Scheduler repeats every 15 minutes indefinitely.
# Also start on user logon. Register-ScheduledTask replaces any earlier task.
$every15Minutes = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15)
$onLogon = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name

# Use the local admin account without saving its password in this task.
$existingTask = Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction SilentlyContinue
if ($existingTask -and [string]$existingTask.Principal.LogonType -eq 'Password') {
    throw 'Existing unattended task must not be overwritten. Use windows/enable-unattended-tasks.ps1 to manage its registration.'
}
$runAs = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Description 'Check origin/main and run windows/build.ps1 every 15 minutes, elevated and without visible console windows.' -Action $action -Trigger @($every15Minutes, $onLogon) -Principal $runAs -Settings $settings -Force -ErrorAction Stop | Out-Null

Write-Host ("Scheduled task installed: " + $TaskName)
Write-Host ("Run as: " + $identity.Name + ' (highest privileges)')
Write-Host ("Repository: " + $RepoRoot)
Write-Host 'Interval: every 15 minutes; also on user logon'
Write-Host 'Display: completely hidden (wscript.exe)'
Write-Host ("Log: " + (Join-Path $RepoRoot '.autobuild\logs\scheduled-build.log'))
Write-Host 'Note: this task runs only while the selected Windows user is signed in.'

# First check runs now; subsequent checks are driven by the Windows schedule.
Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
Write-Host 'First scheduled run started.'
