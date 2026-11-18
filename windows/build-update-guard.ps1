# Helper for direct Windows build and AutoBuilder deployment.

# Launch with administrator rights via Windows UAC. Never grant Everyone access.
function Start-LeshyElevated {
  param([string]$LiveExe,[string]$LiveRoot)
  $expected = [IO.Path]::GetFullPath((Join-Path $LiveRoot 'Leshy.exe'))
  if (-not [string]::Equals([IO.Path]::GetFullPath($LiveExe),$expected,[StringComparison]::OrdinalIgnoreCase)) {
    throw 'Leshy.exe path does not match dist\Leshy\Leshy.exe.'
  }
  if (-not (Test-Path -LiteralPath $LiveExe -PathType Leaf)) { throw ('Missing Leshy.exe: ' + $LiveExe) }
  Write-Host ('LAUNCH_AS_ADMIN: ' + $LiveExe)
  try {
    $process = Start-Process -FilePath $LiveExe -WorkingDirectory $LiveRoot -Verb RunAs -PassThru -ErrorAction Stop
    if (-not $process -or $process.HasExited) { throw 'Elevated Leshy.exe exited immediately.' }
    Write-Host ('LAUNCH_ELEVATED_PID: ' + $process.Id)
    return $process
  } catch { throw ('UAC was declined or elevated Leshy start failed: ' + $_.Exception.Message) }
}

function Assert-LeshyProcessVisibility {
  # Refuse to overwrite an elevated process if its path is hidden from this user.
  $known = @(Get-LiveProcesses)
  try {
    $all = @(Get-CimInstance Win32_Process -Filter "Name='Leshy.exe'" -ErrorAction Stop)
  } catch { throw 'Cannot inspect running Leshy processes. Run PowerShell as Administrator.' }
  if ($all.Count -gt 0 -and $known.Count -eq 0) {
    throw 'Leshy.exe is running but its executable location could not be verified. Use an elevated PowerShell.'
  }
  foreach ($proc in $all) {
    if (-not $proc.ExecutablePath) {
      throw 'Leshy.exe is running with an unreadable path. Run PowerShell as Administrator before updating.'
    }
  }
  return @($known)
}

function Invoke-AndroidBuild {
  param([string]$RepositoryPath)
  $script = Join-Path $RepositoryPath 'windows\build-android.ps1'
  if (-not (Test-Path -LiteralPath $script -PathType Leaf)) { throw 'Android build script missing.' }
  Write-Host 'ANDROID_STAGE: prepare and compile or reuse APK...'
  & $script -RepositoryPath $RepositoryPath
  if (-not $?) { throw 'Android APK stage failed.' }
}

function Remove-OldLeshyInstallers {
  param([string]$RepositoryPath,[string]$CurrentVersion,[int]$Keep=3)
  $dist = Join-Path $RepositoryPath 'dist'
  if (-not (Test-Path -LiteralPath $dist -PathType Container)) { return }
  $currentName = 'LeshySetup-' + $CurrentVersion + '.exe'
  $files = @(Get-ChildItem -LiteralPath $dist -File -Filter 'LeshySetup-*.exe' |
    Where-Object { $_.Name -match '^LeshySetup-\d+\.\d+\.\d+\.exe
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
    return ([string]$state.sha -eq $Commit -and [string]$state.version -eq $Version)
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
    } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $temp -Encoding UTF8
    Move-Item -LiteralPath $temp -Destination $target -Force
  } finally {
    Remove-Item -LiteralPath $temp -ErrorAction SilentlyContinue
  }
}

function Publish-LeshyProgramSafely {
  param([string]$PackageRoot,[string]$LiveRoot,[string]$LiveExe,
        [string]$BrainRoot,[string]$AutoRoot)
  $wasRunning = @(Assert-LeshyProcessVisibility).Count -gt 0
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
    if ($wasRunning) {
      Start-LeshyElevated -LiveExe $LiveExe -LiveRoot $LiveRoot | Out-Null
      Start-Sleep -Seconds 3
      # The elevated process path can be inaccessible to a non-admin builder.
      Write-Host 'Leshy restarted. On-device chat verification is still required.'
    } else {
      Write-Host 'Update installed; direct build will now start Leshy.exe elevated.'
    }
    Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue
    return $wasRunning
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
          Start-LeshyElevated -LiveExe $LiveExe -LiveRoot $LiveRoot | Out-Null
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
 } |
    Sort-Object LastWriteTimeUtc -Descending)
  $keepPaths = @($files | Select-Object -First $Keep | ForEach-Object { $_.FullName })
  foreach ($file in $files) {
    if ($file.Name -eq $currentName -or $file.FullName -in $keepPaths) { continue }
    try {
      Remove-Item -LiteralPath $file.FullName -Force -ErrorAction Stop
      Write-Host ('OLD_WINDOWS_INSTALLER_REMOVED: ' + $file.Name)
    } catch { Write-Warning ('Could not remove old installer ' + $file.Name + ': ' + $_.Exception.Message) }
  }
}

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
    return ([string]$state.sha -eq $Commit -and [string]$state.version -eq $Version)
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
    } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $temp -Encoding UTF8
    Move-Item -LiteralPath $temp -Destination $target -Force
  } finally {
    Remove-Item -LiteralPath $temp -ErrorAction SilentlyContinue
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
    if ($wasRunning) {
      Start-Process -FilePath $LiveExe -WorkingDirectory $LiveRoot -ErrorAction Stop | Out-Null
      Start-Sleep -Seconds 3
      if (@(Get-LiveProcesses).Count -eq 0) { throw 'The new Leshy process exited immediately.' }
      Write-Host 'Leshy restarted. On-device chat verification is still required.'
    } else {
      Write-Host 'Update installed; Leshy was not running before the update, so no automatic start was performed.'
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
          Start-Process -FilePath $LiveExe -WorkingDirectory $LiveRoot | Out-Null
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
