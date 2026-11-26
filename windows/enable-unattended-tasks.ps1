# Explicit one-time migration of existing scheduled tasks to a password
# logon. Run locally as the existing task owner in elevated PowerShell 5.1.
# No autologon, SYSTEM, S4U, service install, password file or remote logon.
# Existing task actions, time limits, recurrence, ACLs and enabled state remain.
[CmdletBinding()]
param([PSCredential]$Credential)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$admin=New-Object Security.Principal.WindowsPrincipal($identity)
if(-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
    throw 'Run in elevated Windows PowerShell as the existing task owner.'
}
function Resolve-TaskSid([string]$Name){
    if($Name -match '^S-1-'){return ([Security.Principal.SecurityIdentifier]::new($Name)).Value}
    return ([Security.Principal.NTAccount]::new($Name).Translate([Security.Principal.SecurityIdentifier])).Value
}
$spec=@(
    @{Name='Leshy AI - Git Build (15 min)';Delay='PT3M';Required=$true},
    @{Name='Leshy AI - Model Provision (automatic)';Delay='';Required=$false}
)
$service=New-Object -ComObject 'Schedule.Service'
$service.Connect()
$folder=$service.GetFolder('\')
$plans=@()
# Validate every selected task before modifying any registration.
foreach($item in $spec){
    $existing=Get-ScheduledTask -TaskPath '\' -TaskName $item.Name -ErrorAction SilentlyContinue
    if(-not $existing){
        if($item.Required){throw ('Required scheduled task not installed: '+$item.Name)}
        Write-Host ('Not installed (optional): '+$item.Name)
        continue
    }
    $registered=$folder.GetTask($item.Name)
    if(-not $registered.Enabled){
        Write-Warning ('Disabled task left unchanged: '+$item.Name)
        continue
    }
    $definition=$registered.Definition
    if((Resolve-TaskSid ([string]$definition.Principal.UserId)) -ne $identity.User.Value){
        throw ('Task belongs to another account; refusing migration: '+$item.Name)
    }
    if([int]$definition.Principal.LogonType -notin @(1,3)){
        throw ('Unexpected existing logon type; refusing migration: '+$item.Name)
    }
    if([int]$definition.Settings.MultipleInstances -ne 2){
        throw ('Task does not use IgnoreNew; review before migration: '+$item.Name)
    }
    $hasBoot=$false
    foreach($trigger in $definition.Triggers){
        if([int]$trigger.Type -eq 8 -and $trigger.Enabled){$hasBoot=$true}
    }
    if([int]$definition.Principal.LogonType -eq 1 -and
       ($hasBoot -or -not $item.Delay) -and $definition.Settings.StartWhenAvailable){
        Write-Host ('Already unattended: '+$item.Name)
        continue
    }
    $plans+=@{Name=$item.Name;Delay=$item.Delay;HasBoot=$hasBoot;Definition=$definition}
}
# The existing build runner normally registers its optional on-demand model
# task by copying the parent principal. A Password principal needs a local
# password at registration, so provision that same task here when missing.
$modelName='Leshy AI - Model Provision (automatic)'
$modelScript=Join-Path $PSScriptRoot 'auto-prepare-models.ps1'
$needsModel=(-not (Get-ScheduledTask -TaskPath '\' -TaskName $modelName -ErrorAction SilentlyContinue)) -and
    (Test-Path -LiteralPath $modelScript -PathType Leaf)
if(-not $plans.Count -and -not $needsModel){Write-Host 'No task registrations require changes.';return}
if(-not $Credential){
    $Credential=Get-Credential -UserName $identity.Name -Message 'Enter the existing Windows task owner password (not the PIN).'
}
if(-not $Credential -or (Resolve-TaskSid $Credential.UserName) -ne $identity.User.Value){
    throw 'Credential must belong to the current existing task owner.'
}
$password=$null
try{
    $password=$Credential.GetNetworkCredential().Password
    if([string]::IsNullOrEmpty($password)){throw 'A nonempty Windows account password is required.'}
    foreach($plan in $plans){
        $definition=$plan.Definition
        # TASK_LOGON_PASSWORD=1; TASK_TRIGGER_BOOT=8.
        $definition.Principal.LogonType=1
        $definition.Settings.StartWhenAvailable=$true
        if($plan.Delay -and -not $plan.HasBoot){
            $boot=$definition.Triggers.Create(8)
            $boot.Delay=$plan.Delay
            $boot.Enabled=$true
        }
        # TASK_UPDATE (4) + TASK_DONT_ADD_PRINCIPAL_ACE (16) +
        # TASK_IGNORE_REGISTRATION_TRIGGERS (32).
        # Never create a replacement task, start a new instance or modify ACLs.
        $null=$folder.RegisterTaskDefinition($plan.Name,$definition,52,$identity.Name,$password,1,$null)
        $verified=$folder.GetTask($plan.Name).Definition
        if([int]$verified.Principal.LogonType -ne 1 -or
           (Resolve-TaskSid ([string]$verified.Principal.UserId)) -ne $identity.User.Value){
            throw ('Task principal verification failed: '+$plan.Name)
        }
        if($plan.Delay){
            $verifiedBoot=$false
            foreach($t in $verified.Triggers){if([int]$t.Type -eq 8 -and $t.Enabled){$verifiedBoot=$true}}
            if(-not $verifiedBoot){throw ('Startup trigger verification failed: '+$plan.Name)}
        }
        Write-Host ('Unattended registration verified: '+$plan.Name)
    }
    if($needsModel){
        # No trigger: the existing updater starts this task on demand.
        $repoRoot=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
        $exe=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $action=New-ScheduledTaskAction -Execute $exe -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$modelScript+'"') -WorkingDirectory $repoRoot
        $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 12) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
        # No -Force: never overwrite a task created concurrently.
        $null=Register-ScheduledTask -TaskPath '\' -TaskName $modelName -Action $action -Settings $settings -User $identity.Name -Password $password -RunLevel Highest -ErrorAction Stop
        $model=$folder.GetTask($modelName).Definition
        if([int]$model.Principal.LogonType -ne 1 -or (Resolve-TaskSid ([string]$model.Principal.UserId)) -ne $identity.User.Value){
            throw 'Model task principal verification failed.'
        }
        Write-Host 'Existing on-demand model provision mechanism registered for unattended execution.'
    }
}finally{
    $password=$null
    $Credential=$null
}
Write-Host 'Registration complete. Existing schedules remain active; verify health after a planned reboot without signing in.'
