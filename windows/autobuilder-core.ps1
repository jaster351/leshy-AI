param(
    [ValidateSet('repo-update','full-update','startup','launch','stop','status')]
    [string]$Mode = 'status'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $RepoRoot

$RuntimeRoot = Join-Path $RepoRoot '.autobuild'
$LogsRoot = Join-Path $RuntimeRoot 'logs'
$LiveRoot = Join-Path $RepoRoot 'dist\Leshy'
$LiveExe = Join-Path $LiveRoot 'Leshy.exe'
$BrainRoot = Join-Path $LiveRoot 'LeshyBrain'
$PackageRoot = Join-Path $RuntimeRoot 'package-dist\Leshy'
$BackupRoot = Join-Path $RuntimeRoot 'live-backup'
$BuildScript = Join-Path $PSScriptRoot 'build.ps1'
$LiveStatePath = Join-Path $RuntimeRoot 'live-state.json'
$UiStatusPath = Join-Path $RuntimeRoot 'ui-status.json'
$CoreLog = Join-Path $LogsRoot 'autobuilder-gui.log'
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

New-Item -ItemType Directory -Force -Path $RuntimeRoot, $LogsRoot | Out-Null

function Write-CoreLog {
    param([string]$Message, [string]$Level = 'INFO')
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Add-Content -LiteralPath $CoreLog -Value $line -Encoding UTF8
    Write-Host $line
}

function Write-UiStatus {
    param([string]$State, [string]$Message = '', [string]$Sha = '', [string]$Version = '')
    $payload = [ordered]@{
        state = $State
        message = $Message
        sha = $Sha
        version = $Version
        updated_at = (Get-Date).ToString('o')
        live_root = $LiveRoot
        brain_root = $BrainRoot
    }
    $payload | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $UiStatusPath -Encoding UTF8
}

function Initialize-Environment {
    $prepend = New-Object System.Collections.Generic.List[string]
    foreach ($dir in @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311'),
        'C:\Python312',
        'C:\Python311'
    )) {
        if ($dir -and (Test-Path (Join-Path $dir 'python.exe'))) {
            $prepend.Add($dir)
            $scripts = Join-Path $dir 'Scripts'
            if (Test-Path $scripts) { $prepend.Add($scripts) }
            break
        }
    }
    foreach ($dir in @(
        'C:\Program Files\Git\cmd',
        'C:\Program Files\Git\bin',
        (Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd')
    )) {
        if ($dir -and (Test-Path (Join-Path $dir 'git.exe'))) {
            $prepend.Add($dir)
            break
        }
    }
    if ($prepend.Count -gt 0) {
        $env:Path = (($prepend | Select-Object -Unique) -join ';') + ';' + $env:Path
    }
}

function Get-Git {
    Initialize-Environment
    $cmd = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in @(
        'C:\Program Files\Git\cmd\git.exe',
        'C:\Program Files\Git\bin\git.exe',
        (Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd\git.exe')
    )) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    throw 'Git for Windows was not found.'
}

function Invoke-GitCapture {
    param(
        [string]$Git,
        [string[]]$Arguments,
        [switch]$AllowFailure
    )
    $raw = @(& $Git -C $RepoRoot @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    $lines = @($raw | ForEach-Object { [string]$_ })
    $text = ($lines -join [Environment]::NewLine).Trim()
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        $detail = if ($text) { ': ' + ($text -replace '[\r\n]+',' | ') } else { '' }
        throw ('git ' + ($Arguments -join ' ') + ' failed with exit code ' + $exitCode + $detail)
    }
    return [pscustomobject]@{
        ExitCode = $exitCode
        Lines = $lines
        Text = $text
    }
}

function Get-HeadSha {
    param([string]$Git, [string]$Ref = 'HEAD', [switch]$AllowMissing)
    $result = Invoke-GitCapture -Git $Git -Arguments @('rev-parse','--verify',$Ref) -AllowFailure:$AllowMissing
    if ($result.ExitCode -ne 0 -or -not $result.Text) {
        if ($AllowMissing) { return '' }
        throw ('Could not resolve Git ref: ' + $Ref)
    }
    return (($result.Lines | Select-Object -First 1).Trim())
}

function Get-CurrentBranch {
    param([string]$Git)
    $result = Invoke-GitCapture -Git $Git -Arguments @('branch','--show-current') -AllowFailure
    if ($result.ExitCode -eq 0 -and $result.Text) {
        return (($result.Lines | Select-Object -First 1).Trim())
    }
    $fallback = Invoke-GitCapture -Git $Git -Arguments @('symbolic-ref','--quiet','--short','HEAD') -AllowFailure
    if ($fallback.ExitCode -eq 0 -and $fallback.Text) {
        return (($fallback.Lines | Select-Object -First 1).Trim())
    }
    return ''
}

function Assert-CleanMain {
    param([string]$Git)

    $inside = Invoke-GitCapture -Git $Git -Arguments @('rev-parse','--is-inside-work-tree') -AllowFailure
    if ($inside.ExitCode -ne 0 -or $inside.Text.Trim().ToLowerInvariant() -ne 'true') {
        throw ('AutoBuilder repository is not a Git work tree: ' + $RepoRoot)
    }

    $status = Invoke-GitCapture -Git $Git -Arguments @('status','--porcelain')
    $changes = @($status.Lines | Where-Object { $_.Trim() })
    if ($changes.Count -gt 0) {
        throw ('Local repository changes are present. AutoBuilder will not overwrite them: ' + (($changes | Select-Object -First 8) -join ' | '))
    }

    $branch = Get-CurrentBranch -Git $Git
    if (-not $branch) {
        $head = Get-HeadSha -Git $Git
        $localMain = Get-HeadSha -Git $Git -Ref 'refs/heads/main' -AllowMissing
        if ($localMain -and $head -eq $localMain) {
            $switchResult = Invoke-GitCapture -Git $Git -Arguments @('switch','main') -AllowFailure
            if ($switchResult.ExitCode -eq 0) {
                $branch = Get-CurrentBranch -Git $Git
                Write-CoreLog 'Repository was on detached HEAD at main; attached back to main.' 'WARN'
            }
        }
    }

    if (-not $branch) {
        $head = Get-HeadSha -Git $Git -AllowMissing
        throw ('Could not determine current Git branch. Repository=' + $RepoRoot + '; HEAD=' + $head)
    }
    if ($branch -ne 'main') {
        throw ('AutoBuilder requires main branch. Current: ' + $branch)
    }
}

function Invoke-RepoUpdate {
    $git = Get-Git
    Assert-CleanMain -Git $git
    $before = Get-HeadSha -Git $git
    Write-UiStatus -State 'repo-update' -Message 'Fetching origin/main' -Sha $before

    $fetch = Invoke-GitCapture -Git $git -Arguments @('fetch','origin','main','--quiet') -AllowFailure
    if ($fetch.ExitCode -ne 0) {
        throw ('git fetch origin main failed: ' + $fetch.Text)
    }
    $remote = Get-HeadSha -Git $git -Ref 'origin/main'
    if ($remote -ne $before) {
        $pull = Invoke-GitCapture -Git $git -Arguments @('pull','--ff-only','origin','main') -AllowFailure
        if ($pull.ExitCode -ne 0) {
            throw ('git pull --ff-only origin main failed: ' + $pull.Text)
        }
    }
    $after = Get-HeadSha -Git $git
    Write-CoreLog ('Repository update: ' + $before.Substring(0,8) + ' -> ' + $after.Substring(0,8))
    Write-UiStatus -State 'repo-ready' -Message 'Repository is up to date' -Sha $after
    return $after
}

function Normalize-PathText {
    param([string]$Value)
    if (-not $Value) { return '' }
    try { return [System.IO.Path]::GetFullPath($Value).TrimEnd('\').ToLowerInvariant() } catch { return '' }
}

function Get-LeshyProcesses {
    $target = Normalize-PathText $LiveExe
    if (-not $target) { return @() }
    $result = @()
    try {
        foreach ($proc in @(Get-CimInstance Win32_Process -Filter "Name='Leshy.exe'" -ErrorAction SilentlyContinue)) {
            if ((Normalize-PathText ([string]$proc.ExecutablePath)) -eq $target) { $result += $proc }
        }
    } catch { }
    return @($result)
}

function Stop-Leshy {
    $processes = @(Get-LeshyProcesses)
    if ($processes.Count -eq 0) {
        Write-CoreLog 'Leshy is already stopped.'
        return
    }
    Write-UiStatus -State 'stopping' -Message 'Stopping Leshy before live update'
    foreach ($proc in $processes) {
        Stop-Process -Id ([int]$proc.ProcessId) -Force -ErrorAction SilentlyContinue
    }
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline) {
        if (@(Get-LeshyProcesses).Count -eq 0) { break }
        Start-Sleep -Milliseconds 250
    }
    if (@(Get-LeshyProcesses).Count -gt 0) { throw 'Could not stop the live Leshy process.' }
    Write-CoreLog 'Leshy stopped.'
}

function Start-Leshy {
    if (@(Get-LeshyProcesses).Count -gt 0) {
        Write-CoreLog 'Leshy is already running.'
        return
    }
    if (-not (Test-Path -LiteralPath $LiveExe)) { throw ('Live executable was not found: ' + $LiveExe) }
    Start-Process -FilePath $LiveExe -WorkingDirectory $LiveRoot | Out-Null
    Write-CoreLog ('Leshy started: ' + $LiveExe)
}

function Read-LiveState {
    if (-not (Test-Path -LiteralPath $LiveStatePath)) { return $null }
    try { return Get-Content -LiteralPath $LiveStatePath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return $null }
}

function Save-LiveState {
    param([string]$Sha, [string]$Version)
    [ordered]@{
        sha = $Sha
        version = $Version
        deployed_at = (Get-Date).ToString('o')
        live_root = $LiveRoot
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $LiveStatePath -Encoding UTF8
}

function Invoke-Build {
    param([string]$Sha)
    Initialize-Environment
    $version = (Get-Content -LiteralPath (Join-Path $RepoRoot 'VERSION') -Raw -Encoding UTF8).Trim()
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $shortSha = $Sha.Substring(0,8)
    $buildLog = Join-Path $LogsRoot ('live-build-{0}-{1}-{2}.log' -f $version, $shortSha, $stamp)
    Write-CoreLog ('Starting live build ' + $version + ' at ' + $shortSha)
    Write-UiStatus -State 'building' -Message ('Building Leshy ' + $version) -Sha $Sha -Version $version
    & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $BuildScript 2>&1 | Tee-Object -FilePath $buildLog | Out-Host
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) { throw ('build.ps1 failed with exit code ' + $exitCode + '. Log: ' + $buildLog) }
    $packageExe = Join-Path $PackageRoot 'Leshy.exe'
    if (-not (Test-Path -LiteralPath $packageExe)) { throw ('Build completed without package executable: ' + $packageExe) }
    return $version
}

function Copy-ProgramTree {
    param([string]$Source, [string]$Destination)
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    foreach ($entry in @(Get-ChildItem -LiteralPath $Source -Force -ErrorAction SilentlyContinue)) {
        if ($entry.Name -ieq 'LeshyBrain') { continue }
        Copy-Item -LiteralPath $entry.FullName -Destination $Destination -Recurse -Force
    }
}

function Clear-ProgramTree {
    param([string]$Root)
    if (-not (Test-Path -LiteralPath $Root)) { return }
    foreach ($entry in @(Get-ChildItem -LiteralPath $Root -Force -ErrorAction SilentlyContinue)) {
        if ($entry.Name -ieq 'LeshyBrain') { continue }
        Remove-Item -LiteralPath $entry.FullName -Recurse -Force -ErrorAction Stop
    }
}

function Deploy-Package {
    param([string]$Sha, [string]$Version)
    if (-not (Test-Path -LiteralPath (Join-Path $PackageRoot 'Leshy.exe'))) { throw 'Package tree is missing.' }
    Write-UiStatus -State 'deploying' -Message 'Updating dist\Leshy while preserving LeshyBrain' -Sha $Sha -Version $Version
    New-Item -ItemType Directory -Force -Path $LiveRoot | Out-Null
    Remove-Item -LiteralPath $BackupRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
    Copy-ProgramTree -Source $LiveRoot -Destination $BackupRoot
    try {
        Clear-ProgramTree -Root $LiveRoot
        Copy-ProgramTree -Source $PackageRoot -Destination $LiveRoot
        if (-not (Test-Path -LiteralPath $LiveExe)) { throw 'Live deployment did not produce Leshy.exe.' }
        Save-LiveState -Sha $Sha -Version $Version
        Remove-Item -LiteralPath $BackupRoot -Recurse -Force -ErrorAction SilentlyContinue
        Write-CoreLog ('Live deployment completed. LeshyBrain preserved at ' + $BrainRoot)
    } catch {
        $deployError = $_.Exception.Message
        Write-CoreLog ('Live deployment failed; restoring previous program files: ' + $deployError) 'ERROR'
        try {
            Clear-ProgramTree -Root $LiveRoot
            Copy-ProgramTree -Source $BackupRoot -Destination $LiveRoot
        } catch {
            Write-CoreLog ('Rollback failed: ' + $_.Exception.Message) 'ERROR'
        }
        throw $deployError
    }
}

function Invoke-FullUpdate {
    $sha = Invoke-RepoUpdate
    $wasRunning = (@(Get-LeshyProcesses).Count -gt 0)
    Stop-Leshy
    try {
        $version = Invoke-Build -Sha $sha
        Deploy-Package -Sha $sha -Version $version
        Start-Leshy
        Write-UiStatus -State 'ready' -Message 'Live update completed' -Sha $sha -Version $version
        Write-CoreLog ('Full live update completed: ' + $version + ' ' + $sha.Substring(0,8))
    } catch {
        Write-CoreLog $_.Exception.Message 'ERROR'
        if ($wasRunning -or (Test-Path -LiteralPath $LiveExe)) {
            try { Start-Leshy } catch { Write-CoreLog ('Could not restore old Leshy: ' + $_.Exception.Message) 'ERROR' }
        }
        throw
    }
}

function Invoke-Startup {
    $sha = Invoke-RepoUpdate
    $state = Read-LiveState
    $deployedSha = if ($state) { [string]$state.sha } else { '' }
    $needsBuild = (-not (Test-Path -LiteralPath $LiveExe)) -or ($deployedSha -ne $sha)
    if ($needsBuild) {
        Write-CoreLog ('Startup detected a pending live build. Deployed=' + $deployedSha + '; repo=' + $sha)
        Stop-Leshy
        try {
            $version = Invoke-Build -Sha $sha
            Deploy-Package -Sha $sha -Version $version
        } catch {
            Write-CoreLog $_.Exception.Message 'ERROR'
            if (Test-Path -LiteralPath $LiveExe) {
                try { Start-Leshy } catch { }
            }
            throw
        }
    }
    Start-Leshy
    $state = Read-LiveState
    $versionText = if ($state) { [string]$state.version } else { '' }
    Write-UiStatus -State 'ready' -Message 'Startup update check completed; Leshy started' -Sha $sha -Version $versionText
}

function Get-CoreStatus {
    $git = $null
    $head = ''
    $remote = ''
    $branch = ''
    $errorText = ''
    try {
        $git = Get-Git
        $head = Get-HeadSha -Git $git -AllowMissing
        $branch = Get-CurrentBranch -Git $git
        $fetch = Invoke-GitCapture -Git $git -Arguments @('fetch','origin','main','--quiet') -AllowFailure
        if ($fetch.ExitCode -eq 0) { $remote = Get-HeadSha -Git $git -Ref 'origin/main' -AllowMissing }
        elseif ($fetch.Text) { $errorText = $fetch.Text }
    } catch {
        $errorText = $_.Exception.Message
    }
    $state = Read-LiveState
    [ordered]@{
        repo_sha = $head
        remote_sha = $remote
        branch = $branch
        git_error = $errorText
        deployed_sha = if ($state) { [string]$state.sha } else { '' }
        deployed_version = if ($state) { [string]$state.version } else { '' }
        live_exists = (Test-Path -LiteralPath $LiveExe)
        leshy_running = (@(Get-LeshyProcesses).Count -gt 0)
        live_root = $LiveRoot
        brain_root = $BrainRoot
    } | ConvertTo-Json -Depth 4
}

$mutex = New-Object System.Threading.Mutex($false, 'Local\LeshyAutoBuilderCore')
$hasMutex = $false
try {
    if ($Mode -ne 'status') {
        $hasMutex = $mutex.WaitOne(0)
        if (-not $hasMutex) { throw 'Another AutoBuilder operation is already running.' }
    }
    switch ($Mode) {
        'repo-update' { [void](Invoke-RepoUpdate) }
        'full-update' { Invoke-FullUpdate }
        'startup' { Invoke-Startup }
        'launch' { Start-Leshy; Write-UiStatus -State 'ready' -Message 'Leshy started' }
        'stop' { Stop-Leshy; Write-UiStatus -State 'ready' -Message 'Leshy stopped' }
        'status' { Get-CoreStatus }
    }
    exit 0
} catch {
    Write-CoreLog $_.Exception.Message 'ERROR'
    Write-UiStatus -State 'error' -Message $_.Exception.Message
    Write-Error $_.Exception.Message
    exit 1
} finally {
    if ($hasMutex) { try { $mutex.ReleaseMutex() } catch { } }
    $mutex.Dispose()
}
