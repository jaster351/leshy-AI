# Helper for direct Windows build and AutoBuilder deployment.

function Get-LocalGitCommit {
  param([string]$RepositoryPath)
  $git = Get-Command git.exe -ErrorAction SilentlyContinue
  if (-not $git) { throw 'Git was not found; cannot verify source revision.' }
  $commit = @(& $git.Source -C $RepositoryPath rev-parse --verify HEAD 2>&1)
  if ($LASTEXITCODE -ne 0 -or -not $commit) { throw 'Cannot determine Git HEAD.' }
  $value = ([string]$commit[0]).Trim()
  if ($value -notmatch '^[0-9a-fA-F]{40}$') { throw 'Invalid Git commit SHA.' }
  $dirty = @(& $git.Source -C $RepositoryPath status --porcelain --untracked-files=no 2>&1)
  if ($LASTEXITCODE -ne 0) { throw 'Cannot check Git working tree.' }
  if ($dirty.Count -gt 0) {
    throw 'Tracked local changes exist. Refusing automatic deployment.'
  }
  return $value.ToLowerInvariant()
}

function Test-LeshyAlreadyBuilt {
  param([string]$Commit,[string]$Version,[string]$AutoRoot,[string]$LiveExe,[string]$Installer)
  if (-not $Commit) { return $false }
  if (-not (Test-Path -LiteralPath $LiveExe -PathType Leaf)) { return $false }
  if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) { return $false }
  $statePath = Join-Path $AutoRoot 'live-state.json'
  if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { return $false }
  try {
    $state = Get-Content -LiteralPath $statePath -Encoding UTF8 -Raw | ConvertFrom-Json
    if ([string]$state.sha -ne $Commit -or [string]$state.version -ne $Version) { return $false }
    # Detect on-disk executable tampering/corruption when a recorded hash exists.
    if ([string]$state.exe_sha256) {
      return ((Get-FileHash -LiteralPath $LiveExe -Algorithm SHA256).Hash -ieq [string]$state.exe_sha256)
    }
    return $true
  } catch { return $false }
}

function Wait-LeshyConversationsIdle {
  param([string]$BrainRoot,[int]$TimeoutSeconds=900)
  if (@(Get-LiveProcesses).Count -eq 0) { return }
  $rawUrl = if ($env:LESHY_BUILD_STATUS_URL) { $env:LESHY_BUILD_STATUS_URL } else { 'http://127.0.0.1:8765/v1/status' }
  $uri = $null
  if (-not [Uri]::TryCreate($rawUrl,[UriKind]::Absolute,[ref]$uri) -or
      $uri.Scheme -notin @('http','https') -or
      $uri.Host -notin @('localhost','127.0.0.1','::1') -or
      $uri.AbsolutePath -ne '/v1/status' -or $uri.UserInfo -or $uri.Query -or $uri.Fragment) {
    throw 'LESHY_BUILD_STATUS_URL must be a local http(s) URL ending in /v1/status.'
  }
  $headers = @{}
  if ($env:LESHY_API_TOKEN) { $headers.Authorization = 'Bearer ' + $env:LESHY_API_TOKEN }
  $until = (Get-Date).AddSeconds($TimeoutSeconds)
  $stable = 0
  $lastNotice = [DateTime]::MinValue
  Write-Host 'Checking the local API: waiting for Leshy to finish all responses before deployment...'
  while ((Get-Date) -lt $until) {
    if (@(Get-LiveProcesses).Count -eq 0) { Write-Host 'The application is already stopped.'; return }
    try {
      $status = Invoke-RestMethod -Uri $uri.AbsoluteUri -Method Get -Headers $headers -TimeoutSec 5 -ErrorAction Stop
    } catch {
      throw ('Could not query active conversations. Leshy was NOT stopped. Check local port, HTTPS, and LESHY_API_TOKEN: ' + $_.Exception.Message)
    }
    if ([string]$status.name -ne (-join @([char]0x041B,[char]0x0435,[char]0x0448,[char]0x0438,[char]0x0439)) -or -not $status.deployment_activity -or
        [int]$status.deployment_activity.schema -ne 1) {
      throw 'The running version does not support deployment_activity. Leshy was NOT stopped. For the initial update, close the old version manually.'
    }
    $reportedBrain = [string]$status.brain_dir
    try {
      $actual = [IO.Path]::GetFullPath($reportedBrain).TrimEnd('\')
      $expected = [IO.Path]::GetFullPath($BrainRoot).TrimEnd('\')
    } catch { throw 'The API did not report a valid LeshyBrain path. Leshy was NOT stopped.' }
    if (-not [string]::Equals($actual,$expected,[StringComparison]::OrdinalIgnoreCase)) {
      throw 'The local API points to a different Leshy instance. No process was stopped.'
    }
    $activity = $status.deployment_activity
    $canStop = ($activity.idle -eq $true -and
                [int]$activity.active_conversations -eq 0 -and
                $activity.active_inference -eq $false -and
                [int]$activity.waiting_inference -eq 0 -and
                [double]$activity.idle_seconds -ge 8)
    if ($canStop) { $stable++ } else { $stable=0 }
    if ($stable -ge 3) {
      Write-Host 'The API confirmed a stable idle period. Deployment may proceed.'
      return
    }
    if (((Get-Date)-$lastNotice).TotalSeconds -ge 10) {
      Write-Host ('Waiting for idle: conversations=' + $activity.active_conversations +
        ', inference=' + $activity.active_inference + ', queue=' + $activity.waiting_inference)
      $lastNotice = Get-Date
    }
    Start-Sleep -Seconds 2
  }
  throw ('Leshy remained busy for over ' + $TimeoutSeconds + ' seconds. Build staged but application NOT stopped.')
}

function Save-LeshyBuiltState {
  param([string]$AutoRoot,[string]$Commit,[string]$Version,[string]$LiveRoot)
  if (-not $Commit) { throw 'Cannot cache a build without an authenticated Git revision.' }
  $target = Join-Path $AutoRoot 'live-state.json'
  $temp = Join-Path $AutoRoot ('live-state-' + [Guid]::NewGuid().ToString('N') + '.tmp')
  try {
    [ordered]@{
      sha=$Commit
      version=$Version
      deployed_at=(Get-Date).ToString('o')
      live_root=$LiveRoot
      source='windows/build.ps1'
      exe_sha256=(Get-FileHash -LiteralPath (Join-Path $LiveRoot 'Leshy.exe') -Algorithm SHA256).Hash
    } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $temp -Encoding UTF8
    Move-Item -LiteralPath $temp -Destination $target -Force
  } finally {
    Remove-Item -LiteralPath $temp -ErrorAction SilentlyContinue
  }
}

function Start-LeshyElevated {
  param([string]$LiveExe,[string]$LiveRoot)
  # Always launch the executable from this repository's dist/Leshy directory.
  $exe = [IO.Path]::GetFullPath($LiveExe)
  $dir = [IO.Path]::GetFullPath($LiveRoot)
  $expected = [IO.Path]::GetFullPath((Join-Path $dir 'Leshy.exe'))
  if (-not [string]::Equals($exe,$expected,[StringComparison]::OrdinalIgnoreCase)) {
    throw 'Unexpected Leshy executable path; refusing elevated launch.'
  }
  if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw ('Leshy.exe was not found at: ' + $exe)
  }
  # A user who clicked "Exit" in the tray has explicitly stopped the app.
  # Keep watching/baking new commits, but do not resurrect Leshy in the background.
  $manualStop = Join-Path $dir 'LeshyBrain\manual-stop.flag'
  if ($env:LESHY_SCHEDULED_BUILD -eq '1' -and (Test-Path -LiteralPath $manualStop -PathType Leaf)) {
    Write-Host 'MANUAL_STOP: scheduled restart suppressed until Leshy.exe is launched manually.'
    return
  }

  # Do not create a second instance. If an elevated process hides its path,
  # fail closed instead of assuming that no Leshy process is running.
  $unknown = @()
  $processes = @(Get-CimInstance Win32_Process -Filter "Name='Leshy.exe'" -ErrorAction Stop)
  foreach ($running in $processes) {
    $path = [string]$running.ExecutablePath
    if (-not $path) { $unknown += [int]$running.ProcessId; continue }
    try { $path = [IO.Path]::GetFullPath($path) } catch { $unknown += [int]$running.ProcessId; continue }
    if ([string]::Equals($path,$exe,[StringComparison]::OrdinalIgnoreCase)) {
      Write-Host ('Leshy.exe already running at the requested path (PID ' + $running.ProcessId + ').')
      return
    }
  }
  if ($unknown.Count -gt 0) {
    throw ('Cannot determine the path of Leshy.exe PID(s): ' + ($unknown -join ', ') +
      '. Run PowerShell as administrator and retry; no duplicate process launched.')
  }

  try {
    if ($env:LESHY_SCHEDULED_BUILD -eq '1') {
      # The Windows scheduled task is registered for this user with RunLevel Highest.
      # Verify the already-elevated token and launch silently, without a second UAC prompt.
      $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
      $principal = New-Object Security.Principal.WindowsPrincipal($identity)
      if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'The scheduled build is not running with elevated administrator rights.'
      }
      $launchArgs = '--no-browser --scheduled-start'
      # Session 0 has no interactive desktop. Keep the runtime, not its tray.
      if ([Diagnostics.Process]::GetCurrentProcess().SessionId -eq 0) {
        $launchArgs += ' --no-tray'
      }
      $process = Start-Process -FilePath $exe -WorkingDirectory $dir -ArgumentList $launchArgs -WindowStyle Hidden -PassThru -ErrorAction Stop
    } else {
      # An interactive build still asks Windows for explicit UAC approval.
      $process = Start-Process -FilePath $exe -WorkingDirectory $dir -Verb RunAs -PassThru -ErrorAction Stop
    }
  } catch {
    throw ('Could not launch Leshy.exe with administrator rights: ' + $exe + '. ' + $_.Exception.Message)
  }
  if ($null -eq $process) { throw 'Elevated start did not return a process handle.' }
  try {
    Start-Sleep -Seconds 3
    $process.Refresh()
    if ($process.HasExited) {
      throw ('Elevated Leshy.exe exited shortly after launch. Exit code: ' + $process.ExitCode)
    }
    Write-Host ('Started as administrator: ' + $exe + ' (PID ' + $process.Id + ')')
  } finally {
    $process.Dispose()
  }
}

function Publish-LeshyProgramSafely {
  param([string]$PackageRoot,[string]$LiveRoot,[string]$LiveExe,
        [string]$BrainRoot,[string]$AutoRoot)
  $wasRunning = @(Get-LiveProcesses).Count -gt 0
  # Build and installer are ready; stop only at deployment stage.
  if ($wasRunning) { Wait-LeshyConversationsIdle -BrainRoot $BrainRoot }
  $backup = Join-Path $AutoRoot 'direct-live-backup'
  if (Test-Path -LiteralPath $backup) {
    throw ('A previous deployment backup exists: ' + $backup +
      '. Check it manually; it will not be removed automatically.')
  }
  New-Item -ItemType Directory -Path $backup -Force | Out-Null
  $hasBackup = $false
  try {
    foreach ($entry in @(Get-ChildItem -LiteralPath $LiveRoot -Force -ErrorAction SilentlyContinue)) {
      if ($entry.Name -ieq 'LeshyBrain') { continue }
      Copy-Item -LiteralPath $entry.FullName -Destination $backup -Recurse -Force -ErrorAction Stop
      $hasBackup = $true
    }
  } catch {
    Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue
    throw ('Could not prepare a backup. Leshy was not stopped: ' + $_.Exception.Message)
  }
  $stopped = $false
  try {
    $stopped = $true
    Stop-LiveLeshy
    Clear-LiveProgramFiles
    foreach ($entry in @(Get-ChildItem -LiteralPath $PackageRoot -Force)) {
      if ($entry.Name -ieq 'LeshyBrain') { continue }
      Copy-Item -LiteralPath $entry.FullName -Destination $LiveRoot -Recurse -Force -ErrorAction Stop
    }
    if (-not (Test-Path -LiteralPath $LiveExe -PathType Leaf)) { throw 'New Leshy.exe missing after deployment.' }
    # Launch after *every* successful direct build, even on a fresh installation.
    Start-LeshyElevated -LiveExe $LiveExe -LiveRoot $LiveRoot
    if ($env:LESHY_SCHEDULED_BUILD -eq '1' -and (Test-Path -LiteralPath (Join-Path $BrainRoot 'manual-stop.flag'))) {
      Write-Host 'Deployment completed; user requested that Leshy remain stopped.'
    } else {
      Write-Host 'Deployment completed; elevated Leshy.exe is running.'
    }
    Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue
  } catch {
    $reason = $_.Exception.Message
    if ($stopped) {
      Write-Warning ('Deployment failed: ' + $reason + '. Restoring previous version.')
      try {
        Stop-LiveLeshy
        Clear-LiveProgramFiles
        if ($hasBackup) {
          foreach ($entry in @(Get-ChildItem -LiteralPath $backup -Force)) {
            Copy-Item -LiteralPath $entry.FullName -Destination $LiveRoot -Recurse -Force -ErrorAction Stop
          }
        }
        if ($wasRunning -and (Test-Path -LiteralPath $LiveExe -PathType Leaf)) {
          Start-LeshyElevated -LiveExe $LiveExe -LiveRoot $LiveRoot
        }
        Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue
      } catch {
        Write-Warning ('Rollback did not complete; backup kept at: ' + $backup +
          ' ; error: ' + $_.Exception.Message)
      }
    }
    throw ('Could not install the updated version: ' + $reason)
  }
}
