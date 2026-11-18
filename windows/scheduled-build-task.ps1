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

    # The monitor compares source SHA+VERSION against the deployed build
    # before launching any compiler, installer, or Android toolchain.
    . (Join-Path $RepoRoot 'windows\build-update-guard.ps1')
    $liveRoot = Join-Path $RepoRoot 'dist\Leshy'
    $liveExe = Join-Path $liveRoot 'Leshy.exe'
    $autoRoot = Join-Path $RepoRoot '.autobuild'
    $version = (Get-Content -LiteralPath (Join-Path $RepoRoot 'VERSION') -Raw -Encoding UTF8).Trim()
    $headResult = @(& $gitExe -C $RepoRoot rev-parse HEAD 2>&1)
    if ($LASTEXITCODE -ne 0 -or $headResult.Count -lt 1) { throw 'Cannot determine local Git HEAD.' }
    $head = ([string]$headResult[0]).Trim()
    if ($head -notmatch '^[0-9a-fA-F]{40}
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
) { throw ('Invalid source Git commit: ' + $head) }
    $installer = Join-Path $RepoRoot ('dist\LeshySetup-' + $version + '.exe')
    $current = Test-LeshyAlreadyBuilt -Commit $head -Version $version -AutoRoot $autoRoot -LiveExe $liveExe -Installer $installer
    $installedVersion = Join-Path $liveRoot '_internal\VERSION'
    if ($current -and (Test-Path -LiteralPath $installedVersion -PathType Leaf)) {
        $versionInExe = (Get-Content -LiteralPath $installedVersion -Raw -Encoding UTF8).Trim()
        if ($versionInExe -ne $version) {
            Write-Host ('DEPLOYED_VERSION_MISMATCH: ' + $versionInExe + ' != ' + $version)
            $current = $false
        }
    }

    $env:LESHY_SCHEDULED_BUILD = '1'
    if (-not $current) {
        Write-Host ('UPDATE_REQUIRED: source_sha=' + $head + ' source_version=' + $version)
        $global:LASTEXITCODE = 0
        & (Join-Path $RepoRoot 'windows\build.ps1')
        if (-not $?) { throw 'build.ps1 failed (PowerShell error).' }
        if ($LASTEXITCODE -ne 0) {
            throw ('build.ps1 failed with exit code ' + $LASTEXITCODE)
        }
        if (-not (Test-LeshyAlreadyBuilt -Commit $head -Version $version -AutoRoot $autoRoot -LiveExe $liveExe -Installer $installer)) {
            throw 'Build returned without a matching deployed source SHA and version.'
        }
        Write-Host ('DEPLOYED: ' + $version + ' / ' + $head.Substring(0,10))
    } else {
        Write-Host ('UP_TO_DATE: ' + $version + ' / ' + $head.Substring(0,10))
        # Process can be restarted after a crash without recompilation.
        # A deliberate "Exit" in the tray is respected by Start-LeshyElevated.
        Start-LeshyElevated -LiveExe $liveExe -LiveRoot $liveRoot
    }
    Write-Host ('SCHEDULED CHECK OK: ' + (Get-Date -Format o))
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
