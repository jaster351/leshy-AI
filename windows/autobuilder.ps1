param(
    [ValidateSet('watch','once','install','uninstall','status')]
    [string]$Mode = 'watch',
    [ValidateRange(15, 3600)]
    [int]$IntervalSeconds = 60,
    [ValidateRange(1, 1440)]
    [int]$FailureRetryMinutes = 5,
    [ValidateRange(0, 300)]
    [int]$SettleSeconds = 20
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$BuildScript = Join-Path $PSScriptRoot 'build.ps1'
$RuntimeRoot = Join-Path $RepoRoot '.autobuild'
$LogsRoot = Join-Path $RuntimeRoot 'logs'
$ReleasesRoot = Join-Path $RuntimeRoot 'releases'
$StatePath = Join-Path $RuntimeRoot 'state.json'
$StatusPath = Join-Path $RuntimeRoot 'STATUS.txt'
$MainLog = Join-Path $LogsRoot 'autobuilder.log'
$TaskName = 'Leshy AutoBuilder'
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

New-Item -ItemType Directory -Force -Path $RuntimeRoot, $LogsRoot, $ReleasesRoot | Out-Null

function Write-AutoLog {
    param([string]$Message, [string]$Level = 'INFO')
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Write-Host $line
    Add-Content -LiteralPath $MainLog -Value $line -Encoding UTF8
}

function Write-StatusFile {
    param([string]$Text)
    $body = @(
        'Leshy AutoBuilder',
        '=================',
        ('Updated: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')),
        '',
        $Text
    ) -join [Environment]::NewLine
    Set-Content -LiteralPath $StatusPath -Value $body -Encoding UTF8
}

function Initialize-BuildEnvironment {
    $prepend = New-Object System.Collections.Generic.List[string]

    $pythonCandidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311'),
        'C:\Python312',
        'C:\Python311'
    )
    foreach ($dir in $pythonCandidates) {
        if ($dir -and (Test-Path (Join-Path $dir 'python.exe'))) {
            $prepend.Add($dir)
            $scripts = Join-Path $dir 'Scripts'
            if (Test-Path $scripts) { $prepend.Add($scripts) }
            break
        }
    }

    $gitCandidates = @(
        'C:\Program Files\Git\cmd',
        (Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd')
    )
    foreach ($dir in $gitCandidates) {
        if ($dir -and (Test-Path (Join-Path $dir 'git.exe'))) {
            $prepend.Add($dir)
            break
        }
    }

    if ($prepend.Count -gt 0) {
        $env:Path = (($prepend | Select-Object -Unique) -join ';') + ';' + $env:Path
    }
}

function Get-GitExecutable {
    Initialize-BuildEnvironment
    $cmd = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw 'Git не найден. Установите Git for Windows и повторите запуск AutoBuilder.'
}

function Read-State {
    if (-not (Test-Path $StatePath)) { return $null }
    try {
        return Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        Write-AutoLog "Не удалось прочитать state.json: $($_.Exception.Message)" 'WARN'
        return $null
    }
}

function Save-State {
    param(
        [string]$Sha,
        [string]$Result,
        [string]$Version = '',
        [string]$Installer = '',
        [string]$ErrorText = ''
    )
    $state = [ordered]@{
        sha = $Sha
        result = $Result
        version = $Version
        installer = $Installer
        error = $ErrorText
        timestamp = (Get-Date).ToString('o')
        unix = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    }
    $state | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $StatePath -Encoding UTF8
}

function Test-RepositoryClean {
    param([string]$Git)
    $lines = @(& $Git -C $RepoRoot status --porcelain 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось получить git status.' }
    if ($lines.Count -eq 0) { return $true }
    Write-AutoLog ('Локальные изменения не трогаю. AutoBuilder ждёт чистый main: ' + (($lines | Select-Object -First 8) -join ' | ')) 'WARN'
    Write-StatusFile "PAUSED: в репозитории есть локальные изменения.`n`n$($lines -join [Environment]::NewLine)"
    return $false
}

function Get-HeadSha {
    param([string]$Git, [string]$Ref = 'HEAD')
    $value = (& $Git -C $RepoRoot rev-parse $Ref 2>$null | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or -not $value) { throw "Не удалось определить SHA: $Ref" }
    return $value.Trim()
}

function Update-RemoteMain {
    param([string]$Git)
    & $Git -C $RepoRoot fetch origin main --quiet
    if ($LASTEXITCODE -ne 0) { throw 'git fetch origin main завершился ошибкой.' }
    return Get-HeadSha -Git $Git -Ref 'origin/main'
}

function Wait-RemoteSettle {
    param([string]$Git, [string]$InitialSha)
    if ($SettleSeconds -le 0) { return $InitialSha }
    $current = $InitialSha
    for ($i = 0; $i -lt 6; $i++) {
        Start-Sleep -Seconds $SettleSeconds
        $next = Update-RemoteMain -Git $Git
        if ($next -eq $current) { return $next }
        Write-AutoLog "Во время ожидания появился ещё один commit: $($next.Substring(0,8)). Жду стабилизации main."
        $current = $next
    }
    return $current
}

function Pull-MainFastForward {
    param([string]$Git)
    $branch = (& $Git -C $RepoRoot rev-parse --abbrev-ref HEAD 2>$null | Select-Object -First 1).Trim()
    if ($branch -ne 'main') {
        throw "AutoBuilder работает только с main. Текущая ветка: $branch"
    }
    if (-not (Test-RepositoryClean -Git $Git)) { return $false }

    & $Git -C $RepoRoot pull --ff-only origin main
    if ($LASTEXITCODE -ne 0) {
        throw 'git pull --ff-only не выполнен. AutoBuilder не будет делать merge/rebase автоматически.'
    }
    return $true
}

function Invoke-LeshyBuild {
    param([string]$Sha)

    Initialize-BuildEnvironment
    $version = (Get-Content -LiteralPath (Join-Path $RepoRoot 'VERSION') -Raw -Encoding UTF8).Trim()
    $shortSha = $Sha.Substring(0, 8)
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $buildLog = Join-Path $LogsRoot ("build-{0}-{1}-{2}.log" -f $version, $shortSha, $stamp)

    Write-AutoLog "Начинаю сборку Leshy $version, commit $shortSha"
    Write-StatusFile "BUILDING: Leshy $version`nCommit: $Sha`nLog: $buildLog"

    $exitCode = 1
    try {
        & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $BuildScript 2>&1 | Tee-Object -FilePath $buildLog
        $exitCode = $LASTEXITCODE
    } catch {
        Add-Content -LiteralPath $buildLog -Value ("AutoBuilder exception: " + $_.Exception.ToString()) -Encoding UTF8
        $exitCode = 1
    }

    if ($exitCode -ne 0) {
        $message = "Сборка commit $shortSha завершилась ошибкой. Код: $exitCode. Лог: $buildLog"
        Write-AutoLog $message 'ERROR'
        Save-State -Sha $Sha -Result 'failure' -Version $version -ErrorText $message
        Write-StatusFile "FAILED: Leshy $version`nCommit: $Sha`nLog: $buildLog"
        return $false
    }

    $installer = Join-Path $RepoRoot ("dist\LeshySetup-{0}.exe" -f $version)
    if (-not (Test-Path $installer)) {
        $message = "build.ps1 завершился без ошибки, но установщик не найден: $installer"
        Write-AutoLog $message 'ERROR'
        Save-State -Sha $Sha -Result 'failure' -Version $version -ErrorText $message
        Write-StatusFile "FAILED: установщик не найден.`nExpected: $installer`nLog: $buildLog"
        return $false
    }

    $archiveName = "LeshySetup-{0}-{1}.exe" -f $version, $shortSha
    $archivePath = Join-Path $ReleasesRoot $archiveName
    Copy-Item -LiteralPath $installer -Destination $archivePath -Force

    $hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash
    $readyText = @(
        "READY: Leshy $version",
        "Commit: $Sha",
        "Installer: $installer",
        "Archive: $archivePath",
        "SHA256: $hash",
        "Build log: $buildLog"
    ) -join [Environment]::NewLine

    Save-State -Sha $Sha -Result 'success' -Version $version -Installer $installer
    Write-StatusFile $readyText
    Write-AutoLog "Готов Leshy $version ($shortSha): $installer"
    return $true
}

function Should-RetryFailedBuild {
    param($State, [string]$Sha)
    if ($null -eq $State) { return $true }
    if ($State.sha -ne $Sha) { return $true }
    if ($State.result -eq 'success') { return $false }
    if ($State.result -ne 'failure') { return $true }
    try {
        $elapsed = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [int64]$State.unix
        return $elapsed -ge ($FailureRetryMinutes * 60)
    } catch {
        return $true
    }
}

function Invoke-CheckAndBuild {
    $git = Get-GitExecutable
    Set-Location $RepoRoot

    if (-not (Test-RepositoryClean -Git $git)) { return }

    $localSha = Get-HeadSha -Git $git
    $remoteSha = Update-RemoteMain -Git $git

    if ($remoteSha -ne $localSha) {
        Write-AutoLog "Обнаружены изменения main: local $($localSha.Substring(0,8)) -> remote $($remoteSha.Substring(0,8))"
        $remoteSha = Wait-RemoteSettle -Git $git -InitialSha $remoteSha
        if (-not (Pull-MainFastForward -Git $git)) { return }
        $localSha = Get-HeadSha -Git $git
        Write-AutoLog "main обновлён до $($localSha.Substring(0,8))"
    }

    $state = Read-State
    if ($state -and $state.sha -eq $localSha -and $state.result -eq 'success') {
        return
    }
    if (-not (Should-RetryFailedBuild -State $state -Sha $localSha)) {
        return
    }

    [void](Invoke-LeshyBuild -Sha $localSha)
}

function Install-AutoBuilderTask {
    if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
        throw 'Модуль ScheduledTasks недоступен в этой Windows.'
    }
    $quotedScript = '"' + $PSCommandPath + '"'
    $arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $quotedScript -Mode watch -IntervalSeconds $IntervalSeconds -FailureRetryMinutes $FailureRetryMinutes -SettleSeconds $SettleSeconds"
    $action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $arguments -WorkingDirectory $RepoRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description 'Следит за origin/main проекта Леший, делает fast-forward pull и автоматически собирает проверенный Windows installer.' -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Write-AutoLog "Задача '$TaskName' установлена и запущена. Проверка каждые $IntervalSeconds сек."
    Write-StatusFile "RUNNING AS SCHEDULED TASK`nTask: $TaskName`nInterval: $IntervalSeconds sec"
}

function Uninstall-AutoBuilderTask {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-AutoLog "Задача '$TaskName' удалена."
    } else {
        Write-Host "Задача '$TaskName' не установлена."
    }
}

function Show-AutoBuilderStatus {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Write-Host "Scheduled task: $($task.State)"
    } else {
        Write-Host 'Scheduled task: not installed'
    }
    if (Test-Path $StatusPath) {
        Write-Host ''
        Get-Content -LiteralPath $StatusPath -Encoding UTF8
    } else {
        Write-Host 'Сборок AutoBuilder ещё не выполнял.'
    }
}

# Защита от двух одновременно запущенных наблюдателей.
$mutexName = 'Local\LeshyAutoBuilder-main'
$mutex = New-Object System.Threading.Mutex($false, $mutexName)
$hasMutex = $false
try {
    if ($Mode -in @('watch','once')) {
        $hasMutex = $mutex.WaitOne(0)
        if (-not $hasMutex) {
            throw 'AutoBuilder уже запущен в другом процессе.'
        }
    }

    switch ($Mode) {
        'install' {
            Install-AutoBuilderTask
            break
        }
        'uninstall' {
            Uninstall-AutoBuilderTask
            break
        }
        'status' {
            Show-AutoBuilderStatus
            break
        }
        'once' {
            Invoke-CheckAndBuild
            break
        }
        'watch' {
            Write-AutoLog "AutoBuilder запущен. Репозиторий: $RepoRoot; интервал: $IntervalSeconds сек."
            while ($true) {
                try {
                    Invoke-CheckAndBuild
                } catch {
                    Write-AutoLog $_.Exception.Message 'ERROR'
                    Write-StatusFile ("ERROR: " + $_.Exception.Message)
                }
                Start-Sleep -Seconds $IntervalSeconds
            }
        }
    }
} finally {
    if ($hasMutex) {
        try { $mutex.ReleaseMutex() } catch { }
    }
    $mutex.Dispose()
}
