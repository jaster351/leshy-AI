# Hidden Task Scheduler runner: repository update, conditional build and progress.
# Keep ASCII-only for Windows PowerShell 5.1 compatibility.
$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$AutoRoot = Join-Path $RepoRoot '.autobuild'
$LogDir = Join-Path $AutoRoot 'logs'
$LogPath = Join-Path $LogDir 'scheduled-build.log'
$RequestPath = Join-Path $AutoRoot 'update-request.json'
$StatusPath = Join-Path $AutoRoot 'update-status.json'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$mutexName = 'Local\LeshyAutoBuilderCore-' +
    ([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($RepoRoot))).Replace('=','').Replace('/','_').Replace('+','-')
if ($mutexName.Length -gt 240) { $mutexName = $mutexName.Substring(0,240) }
$mutex = New-Object System.Threading.Mutex($false, $mutexName)
$hasMutex = $false
$hasTranscript = $false
$exitCode = 0
$requestId = ''
$sourceVersion = ''
$sourceSha = ''
$deployedVersion = ''

function Publish-UpdateStatus {
    param([string]$State,[string]$Details = '')
    $payload = [ordered]@{
        schema = 1
        request_id = $script:requestId
        state = $State
        message = $Details
        version = $script:sourceVersion
        sha = $script:sourceSha
        deployed_version = $script:deployedVersion
        updated_at = (Get-Date).ToUniversalTime().ToString('o')
        log = $script:LogPath
    }
    $temp = Join-Path $script:AutoRoot ('update-status-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        $payload | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $temp -Encoding UTF8
        Move-Item -LiteralPath $temp -Destination $script:StatusPath -Force -ErrorAction Stop
    } finally {
        Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
    }
}

try {
    # The user-triggered tray check leaves a one-shot request token.
    # Ignore old requests to avoid announcing unrelated scheduled runs.
    if (Test-Path -LiteralPath $RequestPath -PathType Leaf) {
        try {
            $request = Get-Content -LiteralPath $RequestPath -Encoding UTF8 -Raw | ConvertFrom-Json
            $candidate = [string]$request.request_id
            $created = [DateTimeOffset]::Parse([string]$request.created_at)
            if ($candidate -match '^[0-9a-fA-F-]{36}$' -and
                ([DateTimeOffset]::UtcNow - $created.ToUniversalTime()).TotalMinutes -le 5 -and
                ([DateTimeOffset]::UtcNow - $created.ToUniversalTime()).TotalSeconds -ge -30) {
                $requestId = $candidate
            }
        } catch { }
        Remove-Item -LiteralPath $RequestPath -Force -ErrorAction SilentlyContinue
    }

    try {
        $hasMutex = $mutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $hasMutex = $true
    }
    if (-not $hasMutex) {
        if ($requestId) { Publish-UpdateStatus -State 'busy' -Details 'Another build or update is already running.' }
        return
    }

    if ((Test-Path -LiteralPath $LogPath -PathType Leaf) -and
        (Get-Item -LiteralPath $LogPath).Length -gt 10MB) {
        Move-Item -LiteralPath $LogPath -Destination (Join-Path $LogDir 'scheduled-build.previous.log') -Force
    }
    Start-Transcript -LiteralPath $LogPath -Append -ErrorAction Stop | Out-Null
    $hasTranscript = $true
    Publish-UpdateStatus -State 'checking'
    Write-Host ('===== LESHY UPDATE CHECK ' + (Get-Date -Format o) + ' =====')
    Write-Host ('REPOSITORY: ' + $RepoRoot)

    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($git) {
        $gitExe = $git.Source
    } else {
        $gitExe = ''
        $gitCandidates = @('C:\Program Files\Git\cmd\git.exe','C:\Program Files\Git\bin\git.exe')
        if ($env:LOCALAPPDATA) {
            $gitCandidates += (Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd\git.exe')
        }
        foreach ($candidate in $gitCandidates) {
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                $gitExe = $candidate
                break
            }
        }
    }
    if (-not $gitExe) { throw 'Git for Windows could not be located.' }

    Set-Location -LiteralPath $RepoRoot
    foreach ($gitArgs in @(
        @('switch','main'),
        @('fetch','origin'),
        @('pull','--ff-only','origin','main')
    )) {
        Write-Host ('> git ' + ($gitArgs -join ' '))
        & $gitExe @gitArgs
        if ($LASTEXITCODE -ne 0) { throw ('Git operation failed: git ' + ($gitArgs -join ' ')) }
    }

    $sourceVersion = (Get-Content -LiteralPath (Join-Path $RepoRoot 'VERSION') -Raw -Encoding UTF8).Trim()
    if ($sourceVersion -notmatch '^\d+\.\d+\.\d+$') { throw 'The source VERSION is invalid.' }
    $shaLines = @(& $gitExe -C $RepoRoot rev-parse HEAD 2>&1)
    if ($LASTEXITCODE -ne 0 -or $shaLines.Count -lt 1) { throw 'Could not determine source commit SHA.' }
    $sourceSha = ([string]$shaLines[0]).Trim().ToLowerInvariant()
    if ($sourceSha -notmatch '^[0-9a-f]{40}$') { throw 'The source commit SHA is invalid.' }

    $liveRoot = Join-Path $RepoRoot 'dist\Leshy'
    $liveExe = Join-Path $liveRoot 'Leshy.exe'
    $installer = Join-Path $RepoRoot ('dist\LeshySetup-' + $sourceVersion + '.exe')
    $liveState = Join-Path $AutoRoot 'live-state.json'
    if (Test-Path -LiteralPath $liveState -PathType Leaf) {
        try {
            $record = Get-Content -LiteralPath $liveState -Raw -Encoding UTF8 | ConvertFrom-Json
            $deployedVersion = [string]$record.version
        } catch { }
    }

    . (Join-Path $PSScriptRoot 'build-update-guard.ps1')
    $current = Test-LeshyAlreadyBuilt -Commit $sourceSha -Version $sourceVersion -AutoRoot $AutoRoot -LiveExe $liveExe -Installer $installer
    $internalVersion = Join-Path $liveRoot '_internal\VERSION'
    if ($current -and -not (Test-Path -LiteralPath $internalVersion -PathType Leaf)) {
        $current = $false
        Write-Host 'Installed executable version marker missing; rebuild required.'
    }
    if ($current) {
        $installed = (Get-Content -LiteralPath $internalVersion -Raw -Encoding UTF8).Trim()
        if ($installed -ne $sourceVersion) {
            $current = $false
            Write-Host ('Version mismatch: deployed=' + $installed + ', source=' + $sourceVersion)
        }
    }

    $env:LESHY_SCHEDULED_BUILD = '1'
    if ($current) {
        Write-Host ('UP_TO_DATE: ' + $sourceVersion + ' / ' + $sourceSha.Substring(0,10))
        Publish-UpdateStatus -State 'up_to_date'
        Start-LeshyElevated -LiveExe $liveExe -LiveRoot $liveRoot
    } else {
        Write-Host ('UPDATE_REQUIRED: ' + $sourceVersion + ' / ' + $sourceSha.Substring(0,10))
        Publish-UpdateStatus -State 'updating'
        $global:LASTEXITCODE = 0
        & (Join-Path $PSScriptRoot 'build.ps1')
        if (-not $?) { throw 'build.ps1 failed; read scheduled-build.log.' }
        if ($LASTEXITCODE -ne 0) { throw ('build.ps1 exited with code ' + $LASTEXITCODE) }
        if (-not (Test-LeshyAlreadyBuilt -Commit $sourceSha -Version $sourceVersion -AutoRoot $AutoRoot -LiveExe $liveExe -Installer $installer)) {
            throw 'Build finished without confirmed deployed SHA and VERSION.'
        }
        $deployedVersion = $sourceVersion
        Write-Host ('DEPLOYED: ' + $sourceVersion + ' / ' + $sourceSha.Substring(0,10))
        Publish-UpdateStatus -State 'updated'
    }
    # The long-running model installer/downloader must NOT block code builds.
    # Register an independent one-shot task under the exact same trusted
    # principal as this existing scheduled updater, then trigger it.
    # Ignore duplicate starts while it runs and keep application update result
    # independent of optional model download/network errors.
    try {
        $modelScript = Join-Path $PSScriptRoot 'auto-prepare-models.ps1'
        if (Test-Path -LiteralPath $modelScript -PathType Leaf) {
            $modelTaskName = 'Leshy AI - Model Provision (automatic)'
            $modelTask = Get-ScheduledTask -TaskName $modelTaskName -ErrorAction SilentlyContinue
            if (-not $modelTask) {
                $mainTask = Get-ScheduledTask -TaskName 'Leshy AI - Git Build (15 min)' -ErrorAction Stop
                $exe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
                $taskAction = New-ScheduledTaskAction -Execute $exe -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $modelScript + '"') -WorkingDirectory $RepoRoot
                $taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 12) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
                $modelTask = Register-ScheduledTask -TaskName $modelTaskName -Action $taskAction -Principal $mainTask.Principal -Settings $taskSettings -Force -ErrorAction Stop
                Write-Host ('MODEL_TASK_INSTALLED: ' + $modelTaskName)
            }
            if ([string]$modelTask.State -ne 'Running') {
                Start-ScheduledTask -TaskName $modelTaskName -ErrorAction Stop
                Write-Host ('MODEL_TASK_TRIGGERED: ' + $modelTaskName)
            } else {
                Write-Host 'MODEL_TASK_ALREADY_RUNNING'
            }
        }
    } catch {
        # Never turn a successful Leshy release into a failed update because
        # optional model provisioning couldn't be scheduled.
        Write-Warning ('MODEL_TASK_SCHEDULING_ERROR: ' + $_.Exception.Message)
    }
    Write-Host ('SCHEDULED CHECK OK: ' + (Get-Date -Format o))
} catch {
    $exitCode = 1
    $problem = [string]$_.Exception.Message
    Write-Host ('SCHEDULED BUILD FAILED: ' + $problem)
    try { Publish-UpdateStatus -State 'error' -Details $problem } catch { }
} finally {
    Remove-Item Env:LESHY_SCHEDULED_BUILD -ErrorAction SilentlyContinue
    if ($hasTranscript) { try { Stop-Transcript | Out-Null } catch { } }
    if ($hasMutex) { try { $mutex.ReleaseMutex() | Out-Null } catch { } }
    $mutex.Dispose()
}
exit $exitCode
