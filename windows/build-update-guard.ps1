# Поддержка прямой сборки Windows; AutoBuilder StageOnly использует свой deploy/rollback.

function Get-LocalGitCommit {
  param([string]$RepositoryPath)
  $git = Get-Command git.exe -ErrorAction SilentlyContinue
  if (-not $git) { throw 'Git не найден: нельзя проверить версию исходников.' }
  $commit = @(& $git.Source -C $RepositoryPath rev-parse --verify HEAD 2>&1)
  if ($LASTEXITCODE -ne 0 -or -not $commit) { throw 'Не удалось определить git HEAD.' }
  $value = ([string]$commit[0]).Trim()
  if ($value -notmatch '^[0-9a-fA-F]{40}$') { throw 'Некорректный SHA текущего git-коммита.' }
  $dirty = @(& $git.Source -C $RepositoryPath status --porcelain --untracked-files=no 2>&1)
  if ($LASTEXITCODE -ne 0) { throw 'Не удалось проверить локальные изменения git.' }
  if ($dirty.Count -gt 0) {
    throw 'В репозитории имеются несохранённые отслеживаемые изменения. Обновление автоматически не выполняется.'
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
    throw 'LESHY_BUILD_STATUS_URL должен быть локальным URL http(s)://127.0.0.1:PORT/v1/status.'
  }
  $headers = @{}
  if ($env:LESHY_API_TOKEN) { $headers.Authorization = 'Bearer ' + $env:LESHY_API_TOKEN }
  $until = (Get-Date).AddSeconds($TimeoutSeconds)
  $stable = 0
  $lastNotice = [DateTime]::MinValue
  Write-Host 'Проверка API: ожидание простоя всех ответов Лешего перед обновлением...'
  while ((Get-Date) -lt $until) {
    if (@(Get-LiveProcesses).Count -eq 0) { Write-Host 'Приложение уже остановлено.'; return }
    try {
      $status = Invoke-RestMethod -Uri $uri.AbsoluteUri -Method Get -Headers $headers -TimeoutSec 5 -ErrorAction Stop
    } catch {
      throw ('Не удалось проверить активные диалоги через API. Леший НЕ остановлен. Проверьте локальный порт, HTTPS и LESHY_API_TOKEN: ' + $_.Exception.Message)
    }
    if ([string]$status.name -ne 'Леший' -or -not $status.deployment_activity -or
        [int]$status.deployment_activity.schema -ne 1) {
      throw 'Запущенная версия не поддерживает безопасную проверку deployment_activity. Леший НЕ остановлен. Для первого обновления закройте старую версию вручную.'
    }
    $reportedBrain = [string]$status.brain_dir
    try {
      $actual = [IO.Path]::GetFullPath($reportedBrain).TrimEnd('\')
      $expected = [IO.Path]::GetFullPath($BrainRoot).TrimEnd('\')
    } catch { throw 'API не сообщил корректный путь LeshyBrain. Леший НЕ остановлен.' }
    if (-not [string]::Equals($actual,$expected,[StringComparison]::OrdinalIgnoreCase)) {
      throw 'Локальный API относится к другой установке Лешего. Текущий процесс НЕ остановлен.'
    }
    $activity = $status.deployment_activity
    $canStop = ($activity.idle -eq $true -and
                [int]$activity.active_conversations -eq 0 -and
                $activity.active_inference -eq $false -and
                [int]$activity.waiting_inference -eq 0 -and
                [double]$activity.idle_seconds -ge 8)
    if ($canStop) { $stable++ } else { $stable=0 }
    if ($stable -ge 3) {
      Write-Host 'API подтвердил устойчивый простой. Разрешена остановка для обновления.'
      return
    }
    if (((Get-Date)-$lastNotice).TotalSeconds -ge 10) {
      Write-Host ('Ожидание простоя: ответов=' + $activity.active_conversations +
        ', inference=' + $activity.active_inference + ', очередь=' + $activity.waiting_inference)
      $lastNotice = Get-Date
    }
    Start-Sleep -Seconds 2
  }
  throw ('Леший остаётся занят более ' + $TimeoutSeconds + ' сек. Сборка готова, но приложение НЕ остановлено.')
}

function Save-LeshyBuiltState {
  param([string]$AutoRoot,[string]$Commit,[string]$Version,[string]$LiveRoot)
  if (-not $Commit) { throw 'Без подтверждённого git SHA результат сборки не кэшируется.' }
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
  # Компиляция и installer уже готовы; остановка выполняется только при deploy.
  if ($wasRunning) { Wait-LeshyConversationsIdle -BrainRoot $BrainRoot }
  $backup = Join-Path $AutoRoot 'direct-live-backup'
  if (Test-Path -LiteralPath $backup) {
    throw ('Осталась резервная копия прошлого неудачного обновления: ' + $backup +
      '. Проверьте её вручную; автоматически не удаляем.')
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
    throw ('Не удалось подготовить резервную копию, Леший не остановлен: ' + $_.Exception.Message)
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
    if (-not (Test-Path -LiteralPath $LiveExe -PathType Leaf)) { throw 'Новый Leshy.exe отсутствует после публикации.' }
    if ($wasRunning) {
      Start-Process -FilePath $LiveExe -WorkingDirectory $LiveRoot -ErrorAction Stop | Out-Null
      Start-Sleep -Seconds 3
      if (@(Get-LiveProcesses).Count -eq 0) { throw 'Новый Леший сразу завершился после запуска.' }
      Write-Host 'Леший перезапущен; проверка ответов на реальном устройстве ещё требуется.'
    } else {
      Write-Host 'Обновление установлено. Леший до сборки не работал и не запущен автоматически.'
    }
    Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue
  } catch {
    $reason = $_.Exception.Message
    if ($stopped) {
      Write-Warning ('Обновление не удалось: ' + $reason + '. Восстанавливаю предыдущую версию.')
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
        Write-Warning ('Автоматический откат не завершён. Резервная копия сохранена: ' + $backup +
          ' ; ошибка: ' + $_.Exception.Message)
      }
    }
    throw ('Не удалось установить новую версию: ' + $reason)
  }
}
