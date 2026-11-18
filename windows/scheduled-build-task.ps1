# Run by the Windows Task Scheduler every 15 minutes through wscript.exe.
# Performs the owner's original command sequence, with logging and a shared mutex.
$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$AutoRoot = Join-Path $RepoRoot '.autobuild'
$LogDir = Join-Path $AutoRoot 'logs'
$LogPath = Join-Path $LogDir 'scheduled-build.log'
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

# Share the lock with autobuilder-core.ps1, so GUI builds and scheduled builds
# cannot edit the same checkout / publish the same dist\Leshy simultaneously.
$mutexName = 'Local\LeshyAutoBuilderCore-' +
    ([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($RepoRoot))).Replace('=','').Replace('/','_').Replace('+','-')
if ($mutexName.Length -gt 240) { $mutexName = $mutexName.Substring(0,240) }
$mutex = New-Object System.Threading.Mutex($false, $mutexName)
$hasMutex = $false
$hasTranscript = $false
$exitCode = 0
try {
    try {
        $hasMutex = $mutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $hasMutex = $true
    }
    if (-not $hasMutex) {
        # Another AutoBuilder run owns the shared lock; ignore this scheduled tick.
        return
    }

    # One current log and one previous log; no infinite accumulation of check logs.
    if ((Test-Path -LiteralPath $LogPath) -and
        (Get-Item -LiteralPath $LogPath).Length -gt 10MB) {
        Move-Item -LiteralPath $LogPath -Destination (Join-Path $LogDir 'scheduled-build.previous.log') -Force
    }
    Start-Transcript -LiteralPath $LogPath -Append -ErrorAction Stop | Out-Null
    $hasTranscript = $true
    Write-Host ("===== LESHY SCHEDULED BUILD " + (Get-Date -Format o) + " =====")
    Write-Host ("REPOSITORY: " + $RepoRoot)

    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if (-not $git) {
        foreach ($path in @(
            'C:\Program Files\Git\cmd\git.exe',
            'C:\Program Files\Git\bin\git.exe',
            (Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd\git.exe')
        )) {
            if ($path -and (Test-Path -LiteralPath $path -PathType Leaf)) {
                $git = Get-Item -LiteralPath $path
                break
            }
        }
    }
    if (-not $git) { throw 'git.exe not found. Install Git for Windows for this user.' }
    $gitExe = if ($git.Source) { $git.Source } else { $git.FullName }

    Set-Location -LiteralPath $RepoRoot

    # Equivalent to: git switch main; git fetch origin; git pull --ff-only origin main
    foreach ($gitArgs in @(
        @('switch','main'),
        @('fetch','origin'),
        @('pull','--ff-only','origin','main')
    )) {
        Write-Host ('> git ' + ($gitArgs -join ' '))
        & $gitExe @gitArgs
        if ($LASTEXITCODE -ne 0) {
            throw ('git ' + ($gitArgs -join ' ') + ' failed with exit code ' + $LASTEXITCODE)
        }
    }

    # Equivalent to: Get-Content .\VERSION; git rev-parse --short HEAD
    Write-Host '> Get-Content .\VERSION'
    Get-Content -LiteralPath (Join-Path $RepoRoot 'VERSION')
    Write-Host '> git rev-parse --short HEAD'
    & $gitExe rev-parse --short HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Could not read Git HEAD.' }

    # build.ps1 has its own commit/version cache and skips an unchanged build.
    Write-Host '> .\windows\build.ps1'
    $env:LESHY_SCHEDULED_BUILD = '1'
    $global:LASTEXITCODE = 0
    & (Join-Path $RepoRoot 'windows\build.ps1')
    if ($LASTEXITCODE -ne 0) {
        throw ('build.ps1 failed with exit code ' + $LASTEXITCODE)
    }
    Write-Host ('SCHEDULED BUILD OK: ' + (Get-Date -Format o))
} catch {
    $exitCode = 1
    Write-Host ('SCHEDULED BUILD FAILED: ' + $_.Exception.Message)
} finally {
    Remove-Item Env:LESHY_SCHEDULED_BUILD -ErrorAction SilentlyContinue
    if ($hasTranscript) { try { Stop-Transcript | Out-Null } catch { } }
    if ($hasMutex) { try { $mutex.ReleaseMutex() | Out-Null } catch { } }
    $mutex.Dispose()
}
exit $exitCode
