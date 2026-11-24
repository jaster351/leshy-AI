# Однократная авторизация доверенного Windows-узла через браузер.
param([string]$ClientId, [switch]$Disconnect, [switch]$EnableMentor, [switch]$Register)
$ErrorActionPreference = 'Stop'
if ($Register) {
    if ($Disconnect -or $EnableMentor -or $ClientId) { throw 'Регистрация приложения выполняется отдельно от авторизации и отключения' }
    $url = 'https://github.com/settings/apps/new?name=Leshy-LiveMentor-jaster351&description=Private%20LiveMentor%20exchange&url=https%3A%2F%2Fgithub.com%2Fjaster351%2Fleshy-AI&public=false&webhook_active=false&contents=write'
    Write-Host 'В форме GitHub проверьте права Contents: Read and write, Metadata: Read-only, остальные — No access.'
    Write-Host 'Включите Device Flow и срок действия пользовательских токенов; установите приложение только на AI-mentor.'
    Start-Process $url
    return
}
$BrainRoot = Join-Path (Join-Path (Split-Path -Parent $PSScriptRoot) 'dist\Leshy') 'LeshyBrain'
$root = Join-Path $BrainRoot 'ai-mentor'
try {
    if (-not (Test-Path -LiteralPath $BrainRoot -PathType Container)) { throw 'Сначала установите Лешего' }
    if ((Get-Item -LiteralPath $BrainRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Недопустимый каталог LeshyBrain' }
    if (-not (Test-Path -LiteralPath $root)) { New-Item -ItemType Directory -Path $root | Out-Null }
    if ((Get-Item -LiteralPath $root -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Недопустимый каталог наставника' }
    . (Join-Path $PSScriptRoot 'mentor-oauth-common.ps1')
    $lock = Enter-MentorCredentialLock $root
    try {
        if ($Disconnect) {
            foreach ($name in @('github_oauth.dat','github_token.dat')) {
                $path = Join-Path $root $name
                if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
            }
            Write-Host 'Локальная авторизация удалена. Для отзыва разрешения откройте настройки GitHub.'
            return
        }
        if ($ClientId -cnotmatch '^[A-Za-z0-9_.-]{8,100}$') { throw 'Требуется Client ID отдельного GitHub App' }
        $device = Invoke-MentorOAuth '/login/device/code' @{client_id=$ClientId;scope='offline_access'}
        if ($device.error -or $device.verification_uri -cne 'https://github.com/login/device' -or $device.user_code -cnotmatch '^[A-Z0-9]{4}-[A-Z0-9]{4}$' -or -not $device.device_code -or $device.expires_in -lt 1 -or $device.expires_in -gt 1800 -or $device.interval -lt 5 -or $device.interval -gt 60) { throw 'Некорректный ответ GitHub для устройства' }
        Write-Host 'Откройте https://github.com/login/device и введите показанный код. Проверьте название своего GitHub App и права.'
        Write-Host ('Код: ' + $device.user_code)
        try { Start-Process 'https://github.com/login/device' } catch { Write-Host 'Откройте адрес вручную в доверенном браузере.' }
        $clock = [Diagnostics.Stopwatch]::StartNew()
        $interval = [int]$device.interval
        while ($clock.Elapsed.TotalSeconds + $interval -lt [int]$device.expires_in) {
            Start-Sleep -Seconds $interval
            $grant = Invoke-MentorOAuth '/login/oauth/access_token' @{client_id=$ClientId;device_code=[string]$device.device_code;grant_type='urn:ietf:params:oauth:grant-type:device_code'}
            if ($grant.error -eq 'authorization_pending') { continue }
            if ($grant.error -eq 'slow_down') {
                $interval = [Math]::Max($interval+5,[int]$grant.interval)
                continue
            }
            if ($grant.error) { throw 'Авторизация отклонена или истекла' }
            Assert-MentorAppAccess ([string]$grant.access_token)
            Save-MentorOAuthGrant $root $ClientId $grant
            if ($EnableMentor) {
                $path = Join-Path $root 'config.json'
                if (Test-Path -LiteralPath $path) {
                    $item = Get-Item -LiteralPath $path -Force
                    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -gt 65536) { throw 'Недопустимая конфигурация' }
                    $cfg = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
                    if ($cfg.repository -cne 'jaster351/AI-mentor') { throw 'Существующая конфигурация другого репозитория' }
                } else {
                    $cfg = [PSCustomObject]@{repository='jaster351/AI-mentor';batch_id='';batch_sha256='';approved_export_sha256='';approved_review_sha256='';review_download_enabled=$false}
                }
                foreach ($key in @('enabled','auto_queue','auto_publish')) { $cfg | Add-Member -NotePropertyName $key -NotePropertyValue $true -Force }
                $tmp = Join-Path $root ('.config-' + [Guid]::NewGuid().ToString('N') + '.tmp')
                try {
                    [IO.File]::WriteAllText($tmp,($cfg | ConvertTo-Json -Depth 8),(New-Object Text.UTF8Encoding($false)))
                    if ([IO.File]::Exists($path)) { [IO.File]::Replace($tmp,$path,$null) } else { [IO.File]::Move($tmp,$path) }
                } finally { if ([IO.File]::Exists($tmp)) { [IO.File]::Delete($tmp) } }
                Write-Host 'Автоматическая учебная очередь включена. Обычное самообучение не изменялось.'
            }
            Write-Host 'GitHub App подключён только к закрытому AI-mentor. Токен сохранён локально.'
            return
        }
        throw 'Время авторизации истекло'
    } finally { $lock.Dispose() }
} catch {
    # Не выводим серверный ответ, токены, запросы или исключения сетевого клиента.
    Write-Error 'Авторизация не завершена. Проверьте Client ID, Device Flow, срок кода и права приложения на AI-mentor.'
    exit 1
}
